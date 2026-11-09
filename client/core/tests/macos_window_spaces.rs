//! Unit test for `set_fullscreen_overlay`: AppKit is main-thread-only and the
//! libtest harness runs each test on a spawned thread, so this test declares
//! `harness = false` (see Cargo.toml) and runs through its own `main()` on
//! the real main thread.

use objc2::{ClassType as _, MainThreadMarker, MainThreadOnly as _};
use objc2_app_kit::{
    NSBackingStoreType, NSScreenSaverWindowLevel, NSWindow, NSWindowCollectionBehavior,
    NSWindowStyleMask,
};
use objc2_foundation::{NSPoint, NSRect, NSSize};

use leng_core::platform::macos::set_fullscreen_overlay;

fn main() {
    let mtm = MainThreadMarker::new().expect("test binary runs on the main thread");
    let window = unsafe {
        NSWindow::initWithContentRect_styleMask_backing_defer(
            NSWindow::alloc(mtm),
            NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(120.0, 80.0)),
            NSWindowStyleMask::Borderless,
            NSBackingStoreType::Buffered,
            false,
        )
    };
    // SAFETY: `&*window` is a live NSWindow pointer.
    unsafe { set_fullscreen_overlay(&*window as *const NSWindow as *mut std::os::raw::c_void) };
    for flag in [
        NSWindowCollectionBehavior::CanJoinAllSpaces,
        NSWindowCollectionBehavior::FullScreenAuxiliary,
        NSWindowCollectionBehavior::Stationary,
    ] {
        assert!(
            window.collectionBehavior().contains(flag),
            "expected {flag:?} in collection behavior, got {:?}",
            window.collectionBehavior()
        );
    }
    assert_eq!(
        window.level(),
        NSScreenSaverWindowLevel,
        "expected the screen-saver window level, got {}",
        window.level()
    );
    assert!(
        window
            .styleMask()
            .contains(NSWindowStyleMask::NonactivatingPanel),
        "expected a non-activating panel style mask, got {:?}",
        window.styleMask()
    );
    let class: &objc2::runtime::AnyClass = unsafe { objc2::msg_send![&window, class] };
    assert_eq!(
        class,
        objc2_app_kit::NSPanel::class(),
        "the HUD window must become an NSPanel: a plain NSWindow is never admitted into a \
         foreign app's fullscreen Space regardless of collection behavior or window level"
    );
}
