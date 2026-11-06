//! macOS platform integration: the frontmost application, the browser tab
//! via `osascript`, Accessibility permission state, and the global key
//! monitor feeding the double-tap trigger.
//!
//! `run_event_loop` owns the run loop (headless usage); for the tauri app
//! (Task 14) the same monitor installing is split out as
//! [`install_event_monitor`] / [`install_dismiss_monitor`], which install
//! their monitor and return, leaving the run loop to tauri. That split was
//! explicitly authorized in the Task 14 brief.
//!
//! Nothing here is unit-testable (AppKit / Accessibility / the scripting
//! bridge are system services); the pure part of the contract — the
//! AppleScript output parser — lives in [`crate::platform::source::parse_tab`]
//! and is covered by `tests/source_parse.rs`. The rest is verified against
//! the manual checklist (PROJECT.md §11.2 item 7).

use std::os::raw::{c_char, c_uchar, c_void};
use std::process::Command;
use std::ptr::NonNull;
use std::time::{Duration, Instant};

use block2::RcBlock;
use objc2::MainThreadMarker;
use objc2_app_kit::{
    NSApplication, NSEvent, NSEventMask, NSEventModifierFlags, NSEventType, NSWorkspace,
};

use crate::platform::source::{
    parse_tab, run_bounded_process, source_kind, tab_script, BrowserKind, SourceInfo,
};

/// Virtual keycode of ANSI `C` (kVK_ANSI_C). Layout-independent: on a non-US
/// layout the physical C key produces e.g. a Cyrillic character, so matching
/// `NSEvent.characters` ("c") would silently miss the trigger.
const KEY_CODE_C: u16 = 8;

/// Virtual keycode of Escape (kVK_Escape).
const KEY_CODE_ESCAPE: u16 = 53;

// Bound for a single `osascript` run. A stuck script would otherwise block
// until the AppleEvent timeout (~2 minutes); 1.5 s keeps the whole
// capture-to-HUD path far inside anything a user would notice.
const BROWSER_TAB_BUDGET: Duration = Duration::from_millis(1500);

#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    // Declared in <ApplicationServices/HIServices/Accessibility.h>. Passing
    // NULL options is the documented promptless check (equivalent to
    // kAXTrustedCheckOptionPrompt = false).
    fn AXIsProcessTrustedWithOptions(options: *const c_void) -> c_uchar;
    // Declared in <HIServices/Accessibility.h> and
    // <HIServices/AXAttributeConstants.h> (both shipped inside the
    // ApplicationServices umbrella). CFStringRef/AXUIElementRef arrive as
    // opaque pointers and are released with CFRelease.
    fn AXUIElementCreateApplication(pid: i32) -> *const c_void;
    fn AXUIElementCopyAttributeValue(
        element: *const c_void,
        attribute: *const c_void,
        value: *mut *const c_void,
    ) -> i32;
    // (kAXFocusedWindow / kAXTitleAttribute are macros over CFSTR in the
    // headers — no link-time symbols — so the attribute names are built as
    // CFStrings at call sites, see `cf_attribute`.)
}

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFRelease(cf: *const c_void);
    fn CFStringCreateWithCString(
        allocator: *const c_void,
        c_str: *const c_char,
        encoding: u32,
    ) -> *const c_void;
    fn CFStringGetLength(string: *const c_void) -> isize;
    fn CFStringGetMaximumSizeForEncoding(length: isize, encoding: u32) -> isize;
    fn CFStringGetCString(
        string: *const c_void,
        buffer: *mut c_char,
        buffer_size: isize,
        encoding: u32,
    ) -> c_uchar;
    fn CFDictionaryCreate(
        allocator: *const c_void,
        keys: *const *const c_void,
        values: *const *const c_void,
        count: isize,
        key_callbacks: *const c_void,
        value_callbacks: *const c_void,
    ) -> *const c_void;
    // kCFBooleanTrue, kCFCopyStringDictionaryKeyCallBacks and
    // kCFTypeDictionaryValueCallBacks are exported variables holding the
    // singleton references/structs (CoreFoundation/CFNumber.h,
    // CFDictionary.h).
    static kCFBooleanTrue: *const c_void;
}

/// kCFStringEncodingUTF8 (CoreFoundation/CFString.h).
const K_UTF8_ENCODING: u32 = 0x0800_0100;

/// The callback structs of `CFDictionaryCreate`: one CFIndex version plus
/// five function pointers, opaque on our side (CoreFoundation/CFDictionary.h).
#[repr(C)]
struct CFCallbackStruct {
    version: isize,
    slots: [usize; 5],
}

// Exported singleton structs; their storage lives in CoreFoundation, the
// layout above is only passed by reference.
extern "C" {
    static kCFCopyStringDictionaryKeyCallBacks: CFCallbackStruct;
    static kCFTypeDictionaryValueCallBacks: CFCallbackStruct;
}

/// kAXErrorSuccess (HIServices/AXError.h).
const K_AX_ERROR_SUCCESS: i32 = 0;

/// The active application context: frontmost app, plus a title when it is
/// available — for browsers the scripted tab (title + URL), for every other
/// app the focused window's title via the AX API (PDF readers, IDEs, ... —
/// PROJECT.md §6). Failures leave the field empty: the norm, not an error.
pub fn source_info() -> SourceInfo {
    let app = active_app_name();
    match source_kind(&app) {
        BrowserKind::Chrome | BrowserKind::Safari => {
            let (title, url) = match browser_tab_info(&app) {
                Some((title, url)) => (Some(title), Some(url)),
                None => (None, None),
            };
            SourceInfo { app, title, url }
        }
        BrowserKind::Other => SourceInfo {
            app,
            title: window_title(),
            url: None,
        },
    }
}

/// Whether the running process has been granted Accessibility permission.
///
/// The promptless check (no system dialog is raised); the tauri app owns
/// prompting (`request_accessibility_permission`).
pub fn permission_granted() -> bool {
    // SAFETY: no options dictionary is passed (promptless), no data retained
    // by the callee.
    unsafe { AXIsProcessTrustedWithOptions(std::ptr::null()) != 0 }
}

/// Request the Accessibility permission with the system's own prompt.
///
/// When the permission is missing, macOS raises its «Leng wants to control
/// your computer» alert and adds the app to the Accessibility list on
/// consent — the app entry then exists no matter where the "+"-button of
/// the current System Settings hides. Returns whether the permission is
/// granted at call time (the state after consent is read by the regular
/// re-checks). Called from the onboarding paths only; the hot-path
/// trigger check stays promptless ([`permission_granted`]).
///
/// Not unit-testable (TCC is a system service, PROJECT.md §11.3); verified
/// against the manual checklist.
pub fn request_accessibility_permission() -> bool {
    // SAFETY: a single-entry dictionary over borrowed CFString/CFBoolean
    // singletons, released after the call; nothing outlives it.
    unsafe {
        let key = CFStringCreateWithCString(
            std::ptr::null(),
            c"AXTrustedCheckOptionPrompt".as_ptr(),
            K_UTF8_ENCODING,
        );
        if key.is_null() {
            eprintln!(
                "leng: cannot build the prompt options; falling back to the promptless check"
            );
            return permission_granted();
        }
        let keys = [key];
        let values = [kCFBooleanTrue];
        let options = CFDictionaryCreate(
            std::ptr::null(),
            keys.as_ptr(),
            values.as_ptr(),
            1,
            &kCFCopyStringDictionaryKeyCallBacks as *const CFCallbackStruct as *const c_void,
            &kCFTypeDictionaryValueCallBacks as *const CFCallbackStruct as *const c_void,
        );
        let trusted = if options.is_null() {
            permission_granted()
        } else {
            AXIsProcessTrustedWithOptions(options) != 0
        };
        if !options.is_null() {
            CFRelease(options);
        }
        CFRelease(key);
        trusted
    }
}

/// Localized name of the frontmost application, or a stable placeholder when
/// no app is frontmost (e.g. a login screen). Must be called on the main thread.
pub fn active_app_name() -> String {
    // NSWorkspace is main-thread-only (enforced by the bindings through their
    // main-thread-only type); the calls below are all safe generated bindings.
    let workspace = NSWorkspace::sharedWorkspace();
    let Some(app) = workspace.frontmostApplication() else {
        return "unknown".to_string();
    };
    app.localizedName()
        .map_or_else(|| "unknown".to_string(), |name| name.to_string())
}

/// The active browser tab as `(title, url)` for browsers we can script.
///
/// Routes the app name to its AppleScript and parses the script's stdout.
/// Waits at most 1.5 s (`BROWSER_TAB_BUDGET`) for `osascript` — the process
/// is killed on expiry — so a stuck script cannot freeze the (main-thread)
/// caller. Other apps, a failed script (browser absent, no front window), a
/// timeout and a denied Apple-events permission all yield `None`.
pub fn browser_tab_info(app_name: &str) -> Option<(String, String)> {
    let script = tab_script(app_name)?;
    let mut cmd = Command::new("osascript");
    cmd.arg("-e").arg(script);
    let output = run_bounded_process(cmd, BROWSER_TAB_BUDGET)?;
    if !output.status.success() {
        return None;
    }
    parse_tab(&String::from_utf8_lossy(&output.stdout))
}

/// The frontmost application's process identifier, or `None` when no app is
/// frontmost. Main thread only, like every `NSWorkspace` call here.
pub fn frontmost_pid() -> Option<i32> {
    let workspace = NSWorkspace::sharedWorkspace();
    Some(workspace.frontmostApplication()?.processIdentifier())
}

/// Title of the frontmost application's focused window, via the AX API.
///
/// The generic source-title route for non-browser apps (PROJECT.md §6: PDF
/// readers, IDEs, ...): Preview titles carry the file name ("doc.pdf"),
/// IDE windows carry the open buffer. Uses only `kAXFocusedWindow` /
/// `kAXTitleAttribute` on the app element — no per-app routing — and needs
/// the same Accessibility permission the event monitor already requires.
///
/// Main thread only. Returns `None` on any failure (no frontmost app, a
/// window without a title, a non-window element): per §6 an empty field is
/// the norm, not an error. `AXUIElementCopyAttributeValue` is synchronous
/// and short — an AX roundtrip to the target app — which stays inside the
/// trigger path's budget in practice.
pub fn window_title() -> Option<String> {
    let pid = frontmost_pid()?;
    // SAFETY: a C API of unowned, non-retained pointers. The created app
    // element and the copied attribute value are released below; the CFString
    // globals are process-wide constants, and every string handed to
    // `cf_string_to_string` is only read before its release there.
    unsafe {
        let element = AXUIElementCreateApplication(pid);
        if element.is_null() {
            return None;
        }
        let focused_attr = cf_attribute(b"AXFocusedWindow\0");
        let title_attr = cf_attribute(b"AXTitle\0");
        let mut window: *const c_void = std::ptr::null();
        let error = AXUIElementCopyAttributeValue(element, focused_attr, &mut window);
        let mut title: *const c_void = std::ptr::null();
        if error == K_AX_ERROR_SUCCESS && !window.is_null() {
            AXUIElementCopyAttributeValue(window, title_attr, &mut title);
        }
        let text = if !title.is_null() {
            cf_string_to_string(title)
        } else {
            None
        };
        if !title.is_null() {
            CFRelease(title);
        }
        if !window.is_null() {
            CFRelease(window);
        }
        CFRelease(focused_attr);
        CFRelease(title_attr);
        CFRelease(element);
        text
    }
}

/// A CFString for a NUL-terminated byte literal. The caller releases it.
///
/// SAFETY: `name` must be NUL-terminated. The returned string is owned.
unsafe fn cf_attribute(name: &[u8]) -> *const c_void {
    // SAFETY: a valid NUL-terminated C string; NULL allocator is
    // kCFAllocatorDefault. The returned CFString is owned by the caller.
    CFStringCreateWithCString(
        std::ptr::null(),
        name.as_ptr() as *const c_char,
        K_UTF8_ENCODING,
    )
}

/// A CFString's contents as a Rust `String`, or `None` on any conversion
/// failure. Does not release the string.
///
/// SAFETY: `string` must be a valid CFStringRef pointer; the caller releases
/// it if it owns the copy.
unsafe fn cf_string_to_string(string: *const c_void) -> Option<String> {
    let length = CFStringGetLength(string);
    let size = CFStringGetMaximumSizeForEncoding(length, K_UTF8_ENCODING) + 1;
    let mut buffer: Vec<c_char> = Vec::with_capacity(size as usize);
    let ok = CFStringGetCString(string, buffer.as_mut_ptr(), size, K_UTF8_ENCODING);
    if ok == 0 {
        return None;
    }
    std::ffi::CStr::from_ptr(buffer.as_ptr())
        .to_string_lossy()
        .into_owned()
        .into()
}

/// Observe-only global key monitor for `Cmd+C`, then the AppKit event loop.
///
/// The global monitor sees key presses delivered to *other* applications —
/// the HUD itself never takes focus — and events are never consumed. Each
/// observed `Cmd+C` press calls `on_double_tap(Instant::now())`; press
/// pairing into a trigger is the `DoubleTap` state machine (Task 11), wired
/// by the tauri app (Task 14). Requires Accessibility permission; without it
/// the monitor silently sees nothing. Never returns.
pub fn run_event_loop(on_double_tap: impl Fn(Instant) + Send + 'static) -> ! {
    install_event_monitor(on_double_tap);
    let marker = MainThreadMarker::new().expect("run_event_loop must be called on the main thread");
    NSApplication::sharedApplication(marker).run();
    // Unreachable: -[NSApplication run] only returns through termination.
    unreachable!("[NSApplication run] is not expected to return")
}

/// Install the global `Cmd+C` monitor and return immediately, leaving the
/// application run loop to the caller.
///
/// Non-blocking counterpart of [`run_event_loop`] for hosts that supply
/// their own run loop: the tauri app (Task 14) already runs
/// `-[NSApplication run]` through tauri's runtime, so `run_event_loop` must
/// not be used there — instead the monitor is installed inside tauri's
/// `.setup()` (which runs on the real main thread before the loop), and
/// tauri's loop keeps the process alive. Installation is the same observe-only
/// monitor (Authorization: the run-loop split was explicitly approved for
/// Task 14).
///
/// Requires Accessibility permission; without it the monitor silently sees
/// nothing. Must be called on the main thread (AppKit requirement).
pub fn install_event_monitor(on_double_tap: impl Fn(Instant) + Send + 'static) {
    // The AppKit calls below are main-thread-only (enforced by their
    // `MainThreadMarker`/main-thread-only receivers). The system copies the
    // block, so it outlives this stack block for the whole app lifetime;
    // AppKit hands the handler a valid event pointer on every call.
    let handler = RcBlock::new(move |event: NonNull<NSEvent>| {
        // SAFETY: the pointer is non-null by construction and AppKit keeps
        // the event valid for the duration of the handler call.
        let event: &NSEvent = unsafe { &*event.as_ptr() };
        let pressed_cmd_c = event
            .modifierFlags()
            .contains(NSEventModifierFlags::Command)
            && event.keyCode() == KEY_CODE_C;
        if pressed_cmd_c {
            on_double_tap(Instant::now());
        }
    });

    MainThreadMarker::new().expect("install_event_monitor must be called on the main thread");
    // Observe-only: the returned monitor token is never removed — the HUD
    // app keeps the monitor for its whole lifetime.
    NSEvent::addGlobalMonitorForEventsMatchingMask_handler(NSEventMask::KeyDown, &handler);
}

/// What a global dismissal event was.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DismissEvent {
    /// Left mouse-down in another application.
    Click,
    /// Escape key-down in another application.
    Escape,
}

/// Observe-only global monitor covering the HUD's dismissal contract.
///
/// The HUD window does not take focus on macOS (tauri `focusable(false)`
/// makes the window never key), so it never receives an Escape keydown or a
/// real blur through its webview. The dismissal contract ("close on Escape
/// and on losing focus") is therefore met by watching global events instead:
/// this monitor reports Escape presses and left mouse-downs delivered to
/// *other* applications, and the tauri side decides (the click needs a
/// inside/outside geometry check against the HUD frame).
///
/// Requires Accessibility permission like [`install_event_monitor`]. Must be
/// called on the main thread.
pub fn install_dismiss_monitor(on_dismiss: impl Fn(DismissEvent) + Send + 'static) {
    let handler = RcBlock::new(move |event: NonNull<NSEvent>| {
        // SAFETY: same non-null / handler-lifetime argument as above.
        let event: &NSEvent = unsafe { &*event.as_ptr() };
        let dismiss = match event.r#type() {
            NSEventType::LeftMouseDown => Some(DismissEvent::Click),
            NSEventType::KeyDown if event.keyCode() == KEY_CODE_ESCAPE => {
                Some(DismissEvent::Escape)
            }
            _ => None,
        };
        if let Some(dismiss) = dismiss {
            on_dismiss(dismiss);
        }
    });

    MainThreadMarker::new().expect("install_dismiss_monitor must be called on the main thread");
    NSEvent::addGlobalMonitorForEventsMatchingMask_handler(
        NSEventMask::KeyDown | NSEventMask::LeftMouseDown,
        &handler,
    );
}
