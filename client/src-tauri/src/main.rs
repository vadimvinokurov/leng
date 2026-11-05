//! Leng desktop client (Tauri 2): tray, permission onboarding, HUD window,
//! and the glue between the global event monitors and `leng_core::pipeline`.
//!
//! Run-loop ruling (Task 14): tauri runs `-[NSApplication run]` itself, so
//! `leng_core::platform::macos::run_event_loop` (which would start its own
//! loop) is NOT used here. Instead the authorized split of that module —
//! `install_event_monitor` / `install_dismiss_monitor` — is called inside
//! `.setup()`, which runs on the real main thread before tauri's loop takes
//! over, and the monitors deliver through it for the app lifetime.
//!
//! Everything here is GUI glue (window/tray/event wiring): not unit-testable,
//! verified against PROJECT.md §11.2 item 7 (manual checklist). The pure
//! contracts it consumes — `DoubleTap`, `capture`, `pipeline::process`,
//! `parse_tab` — are covered by `client/core/tests/`.

use std::process::Command as StdCommand;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use leng_core::api::BackendClient;
use leng_core::capture::capture;
use leng_core::config::{resolve, update_decision, CliOverrides, StdEnv, UpdateDecision};
use leng_core::guard::GenerationGuard;
use leng_core::pipeline::{process, Hud, HudPayload};
use leng_core::platform::macos::{
    install_dismiss_monitor, install_event_monitor, permission_granted, source_info, DismissEvent,
};
use leng_core::platform::source::SourceInfo;
use leng_core::trigger::DoubleTap;
use leng_core::types::Source;
use tauri::menu::{MenuBuilder, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Emitter, Listener, Manager, PhysicalPosition, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as _};
use tauri_plugin_clipboard_manager::ClipboardExt as _;
use tauri_plugin_updater::UpdaterExt;

/// Label of the on-demand HUD webview window.
const HUD_LABEL: &str = "hud";
/// Label of the Accessibility onboarding window.
const PERMISSION_LABEL: &str = "permission";
/// Webview -> Rust: the HUD page has finished loading and listens.
const READY_EVENT: &str = "leng:ready";
/// Rust -> webview: the pipeline payload, as the serialized `HudPayload` JSON
/// string (variant names `response` / `error` pinned in leng-core).
const PAYLOAD_EVENT: &str = "leng:payload";

/// Config and clipboard state shared between the trigger paths.
struct AppState {
    /// None when no config source (flags/config.json/env) resolves: the
    /// error surfaces at the first trigger, not as a startup panic.
    api: Option<Arc<BackendClient>>,
    client_name: String,
    /// The resolved backend base URL, for the updater endpoint (`None`
    /// when the config did not resolve).
    base_url: Option<String>,
    /// Last clipboard text captured, for the fresh-copy filter.
    previous_clipboard: Mutex<Option<String>>,
    /// Last payload shown, replayed when a freshly opened HUD page signals
    /// it is ready (the emit could otherwise race the page load).
    last_payload: Mutex<Option<String>>,
    /// Token source closing the late-arriver-wins race: each trigger takes
    /// a token; only the newest may render its payload.
    generation: Mutex<GenerationGuard>,
    /// Guard so monitors are installed at most once.
    trigger_installed: Mutex<bool>,
}

fn main() {
    let resolved = resolve_backend_config();
    tauri::Builder::default()
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            open_accessibility_settings,
            check_permission,
            render_hud
        ])
        .manage(AppState {
            api: resolved.as_ref().map(|cfg| {
                Arc::new(BackendClient::new(
                    &cfg.base_url,
                    &cfg.token,
                    env!("CARGO_PKG_VERSION"),
                ))
            }),
            base_url: resolved.as_ref().map(|cfg| cfg.base_url.clone()),
            client_name: resolve_client_name(),
            previous_clipboard: Mutex::new(None),
            last_payload: Mutex::new(None),
            generation: Mutex::new(GenerationGuard::default()),
            trigger_installed: Mutex::new(false),
        })
        .setup(|app| {
            build_tray(app.handle())?;
            // Register the launch agent so the client survives reboots.
            if let Err(err) = app.autolaunch().enable() {
                eprintln!("leng: autostart registration failed: {err}");
            }
            ensure_trigger(app.handle());
            spawn_update_check(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri runtime failed")
        .run(|_app, event| {
            if let tauri::RunEvent::ExitRequested { code, api, .. } = event {
                // tauri's default exits when the last window closes; the
                // tray-only state (HUD dismissed) must keep the client alive
                // for the hotkey and the tray. A coded exit (the Quit menu)
                // passes through untouched.
                if code.is_none() {
                    api.prevent_exit();
                }
            }
        });
}

/// Tray menu: a manual self-probe over the same pipeline the hotkey uses,
/// plus quit. Created at startup, before the run loop.
fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let trigger_item =
        MenuItem::with_id(app, "trigger-now", "Триггер сейчас", true, None::<&str>).unwrap();
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>).unwrap();
    let menu = MenuBuilder::new(app)
        .item(&trigger_item)
        .separator()
        .item(&quit_item)
        .build()
        .unwrap();
    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))
        .expect("embedded tray icon must decode");
    TrayIconBuilder::with_id("leng-tray")
        .icon(icon)
        .tooltip("Leng")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "trigger-now" => trigger_now(app, true),
            "update-now" => update_now(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)
        .expect("tray creation must succeed");
    Ok(())
}

/// Config resolution for the GUI: flags > config.json next to the
/// executable > LENG_* env (the same chain as the CLI, `leng_core::config`).
/// Unresolved config is a first-trigger error, not a startup panic.
fn resolve_backend_config() -> Option<leng_core::config::ResolvedConfig> {
    let config_path = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(std::path::PathBuf::from))
        .map(|dir| dir.join("config.json"));
    let overrides = CliOverrides {
        base_url: None,
        token: None,
    };
    match resolve(overrides, config_path.as_deref(), &StdEnv) {
        Ok(resolved) => Some(resolved),
        Err(err) => {
            eprintln!("leng: config unresolved: {err}");
            None
        }
    }
}

fn non_empty_env(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
}

/// `LENG_CLIENT_NAME` or the machine hostname (so backend history can tell
/// machines apart). Falls back to a placeholder when unresolvable.
fn resolve_client_name() -> String {
    if let Some(name) = non_empty_env("LENG_CLIENT_NAME") {
        return name;
    }
    match hostname::get() {
        Ok(name) => name.to_string_lossy().into_owned(),
        Err(_) => "unknown".to_string(),
    }
}

fn missing_config_message() -> String {
    "нет конфига: положи config.json (backend_url и api_token) рядом с \
     исполнимым или задай LENG_BACKEND_URL и LENG_API_TOKEN"
        .to_string()
}

/// Fire-and-forget update check at startup: asks the server's public
/// health endpoint for its version and, when it differs from ours, adds
/// «Обновить приложение до vX.Y.Z» to the tray. Any error leaves the
/// client quietly working (no update offer); nothing blocks startup.
fn spawn_update_check(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        let some_api = {
            let state = app.state::<AppState>();
            state.api.clone()
        };
        let Some(api) = some_api else {
            return;
        };
        let health = match api.health().await {
            Ok(health) => health,
            Err(_) => return,
        };
        if let UpdateDecision::Offer { version } =
            update_decision(env!("CARGO_PKG_VERSION"), &health.version)
        {
            if let Err(err) = offer_update_in_tray(&app, &version) {
                eprintln!("leng: failed to offer update in tray: {err}");
            }
        }
    });
}

/// Rebuild the tray menu with the update item in front. The menu event
/// handler (registered with the tray at startup) already routes ids.
fn offer_update_in_tray(app: &AppHandle, version: &str) -> tauri::Result<()> {
    let update_item = MenuItem::with_id(
        app,
        "update-now",
        format!("Обновить приложение до v{version}"),
        true,
        None::<&str>,
    )?;
    rebuild_tray_menu(app, &update_item)
}

fn rebuild_tray_menu(app: &AppHandle, update_item: &MenuItem<tauri::Wry>) -> tauri::Result<()> {
    let trigger_item = MenuItem::with_id(app, "trigger-now", "Триггер сейчас", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = MenuBuilder::new(app)
        .item(&trigger_item)
        .separator()
        .item(update_item)
        .separator()
        .item(&quit_item)
        .build()?;
    let Some(tray) = app.tray_by_id("leng-tray") else {
        eprintln!("leng: tray not found for the update offer");
        return Ok(());
    };
    tray.set_menu(Some(menu))
}

/// The update click: check again through the updater plugin (endpoint is
/// this server's mirror), download and install, restart. Failures surface
/// as a HUD error payload; the running version keeps working.
fn update_now(app: &AppHandle) {
    let base_url = {
        let state = app.state::<AppState>();
        state.base_url.clone()
    };
    let Some(base_url) = base_url else {
        show_payload(app, HudPayload::Error(missing_config_message()));
        return;
    };
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(err) = run_update(&handle, &base_url).await {
            show_payload_now(&handle, HudPayload::Error(format!("update failed: {err}")));
        }
        // On success `run_update` has restarted the process.
    });
}

async fn run_update(app: &AppHandle, base_url: &str) -> Result<(), String> {
    let endpoint = tauri::Url::parse(&format!(
        "{}/v1/clients/latest.json",
        base_url.trim_end_matches('/')
    ))
    .map_err(|err| err.to_string())?;
    let updater = app
        .updater_builder()
        .endpoints(vec![endpoint])
        .map_err(|err| err.to_string())?
        .build()
        .map_err(|err| err.to_string())?;
    let Some(update) = updater.check().await.map_err(|err| err.to_string())? else {
        return Err("no update available".into());
    };
    update
        .download_and_install(|_chunk, _total| {}, || {})
        .await
        .map_err(|err| err.to_string())?;
    app.restart()
}

/// Immediate (synchronous) error payload from an async context: a fresh
/// generation supersedes older ones, same as `show_payload`.
fn show_payload_now(app: &AppHandle, payload: HudPayload) {
    let handle = app.clone();
    let inner = handle.clone();
    let _ = handle.run_on_main_thread(move || {
        show_payload(&inner, payload);
    });
}

/// Install the monitors when Accessibility is already granted, otherwise
/// open the onboarding window and wait for the «Проверить снова» command.
/// Called at startup and after each re-check; idempotent.
fn ensure_trigger(app: &AppHandle) {
    if permission_granted() {
        install_trigger(app);
    } else {
        eprintln!("leng: Accessibility permission not granted; trigger not installed");
        open_permission_window(app);
    }
}

/// One-time installation of the double-Cmd+C trigger and the mouse-down
/// dismissal monitor. Main thread only (AppKit requirement).
fn install_trigger(app: &AppHandle) {
    let state = app.state::<AppState>();
    let mut installed = state.trigger_installed.lock().unwrap();
    if *installed {
        return;
    }
    let double_tap = Mutex::new(DoubleTap::default());
    let trigger_handle = app.clone();
    install_event_monitor(move |at: Instant| {
        if double_tap.lock().unwrap().press(at) {
            trigger_now(&trigger_handle, false);
        }
    });
    // Dismissal for a window that never takes focus: Escape or a click
    // outside the HUD frame close it.
    let dismiss_handle = app.clone();
    install_dismiss_monitor(move |dismiss| dismiss_hud(&dismiss_handle, dismiss));
    *installed = true;
}

/// The whole capture path, from one trigger to the shown payload.
///
/// Runs on the main thread (both callers — the global monitor handler and
/// the tray menu handler — deliver there), which is what `source_info()` and
/// window creation require. `open_hud` puts up the spinner before the async
/// pipeline replaces it with the payload.
///
/// The tray menu path probes with `probe = true`: a manual probe must give
/// visible feedback unconditionally, so the fresh-copy filter is bypassed
/// (the hotkey path keeps it) and an empty clipboard yields an error
/// payload instead of a silent no-op.
fn trigger_now(app: &AppHandle, probe: bool) {
    let clipboard = match app.clipboard().read_text() {
        Ok(text) => Some(text),
        Err(err) => {
            show_payload(
                app,
                HudPayload::Error(format!("clipboard read failed: {err}")),
            );
            return;
        }
    };
    let state = app.state::<AppState>();
    let mut previous = state.previous_clipboard.lock().unwrap();
    let filter_previous: Option<&str> = if probe { None } else { previous.as_deref() };
    let Some(text) = capture(filter_previous, clipboard.as_deref()) else {
        if probe {
            show_payload(app, HudPayload::Error("clipboard is empty".into()));
        }
        return;
    };

    let Some(api) = state.api.clone() else {
        // Deliberately not recording the marker: a retry after fixing the
        // environment re-triggers the same text.
        show_payload(app, HudPayload::Error(missing_config_message()));
        return;
    };

    let SourceInfo {
        app: app_name,
        title,
        url,
    } = source_info();
    let source = Source {
        app: app_name,
        title,
        url,
    };
    let client_name = state.client_name.clone();
    // Supersede any in-flight earlier trigger: only this generation's
    // payload will be rendered.
    let token = state.generation.lock().unwrap().start();
    open_hud(app);
    let hud = HudWindow {
        app: app.clone(),
        token,
    };
    *previous = Some(text.clone());
    tauri::async_runtime::spawn(async move {
        process(&text, api.as_ref(), &hud, &source, &client_name).await;
    });
}

/// Close the HUD on a dismissal event: Escape closes it from anywhere; a
/// click closes it when the click landed outside its frame. A click inside
/// the HUD is not delivered to the global monitor, so the geometry check is
/// a cheap guard rather than the deciding factor.
fn dismiss_hud(app: &AppHandle, dismiss: DismissEvent) {
    let Some(window) = app.get_webview_window(HUD_LABEL) else {
        return;
    };
    if dismiss == DismissEvent::Escape {
        dismiss_destroy(app, &window);
        return;
    }
    let (Ok(cursor), Ok(origin), Ok(size)) = (
        app.cursor_position(),
        window.outer_position(),
        window.outer_size(),
    ) else {
        // Without the frame we cannot tell inside from outside; leave the
        // window open rather than closing it on a stray click.
        return;
    };
    let inside = (origin.x..origin.x + size.width as i32).contains(&(cursor.x as i32))
        && (origin.y..origin.y + size.height as i32).contains(&(cursor.y as i32));
    if !inside {
        dismiss_destroy(app, &window);
    }
}

/// Destroy the HUD and mark its session dead: starting a fresh generation
/// makes every token taken before the dismissal stale, so an in-flight
/// trigger's `commit_payload` drops its payload instead of resurrecting the
/// just-closed window (`open_hud` from the async worker path was the bug —
/// dismissal must supersede late payloads).
fn dismiss_destroy(app: &AppHandle, window: &tauri::WebviewWindow) {
    app.state::<AppState>().generation.lock().unwrap().start();
    let _ = window.destroy();
}

/// Open the HUD at the current cursor position in its loading (spinner)
/// state; the payload (or an error) replaces it once the pipeline settles.
///
/// An existing HUD window is REUSED with a page reload instead of being
/// destroyed and rebuilt: `destroy` removes the label from the manager
/// asynchronously, so an immediate rebuild can race it ("label already
/// exists" — hit by back-to-back tray probes). A reload resets the page to
/// the same fresh loading state while the webview object (and its
/// `leng:ready` replay listener) survives.
fn open_hud(app: &AppHandle) {
    let state = app.state::<AppState>();
    *state.last_payload.lock().unwrap() = None;

    if let Some(existing) = app.get_webview_window(HUD_LABEL) {
        if let Ok(cursor) = app.cursor_position() {
            let _ = existing.set_position(PhysicalPosition::new(
                cursor.x.round() as i32,
                cursor.y.round() as i32,
            ));
        }
        let _ = existing.eval("window.location.reload()");
        let _ = existing.show();
        return;
    }
    let window = WebviewWindowBuilder::new(app, HUD_LABEL, WebviewUrl::App("hud.html".into()))
        .title("Leng")
        .inner_size(380.0, 260.0)
        .resizable(false)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .focusable(false)
        .focused(false)
        .visible(false)
        .build();
    let window = match window {
        Ok(window) => window,
        Err(err) => {
            eprintln!("leng: failed to open HUD window: {err}");
            return;
        }
    };
    if let Ok(cursor) = app.cursor_position() {
        let _ = window.set_position(PhysicalPosition::new(
            cursor.x.round() as i32,
            cursor.y.round() as i32,
        ));
    }
    // The emit in `HudWindow::show` can race the page load; the page signals
    // readiness after registering its listener, and the stored payload is
    // replayed then.
    let replay_window = window.clone();
    let ready_handle = app.clone();
    window.listen(READY_EVENT, move |_event| {
        let payload = {
            let state = ready_handle.state::<AppState>();
            let mut stored = state.last_payload.lock().unwrap();
            stored.take()
        };
        if let Some(payload) = payload {
            let _ = replay_window.emit(PAYLOAD_EVENT, payload);
        }
    });
    let _ = window.show();
}

/// The pipeline's HUD sink for one trigger's generation: payloads of a stale
/// trigger are dropped instead of overwriting the newer trigger's content.
struct HudWindow {
    app: AppHandle,
    token: u64,
}

impl Hud for HudWindow {
    fn show(&self, payload: HudPayload) {
        commit_payload(&self.app, self.token, payload);
    }
}

/// Commit a payload for `token`'s generation. A stale trigger (a newer one
/// already started) is dropped silently; the current one stores the payload
/// (for the ready replay) and emits it to the HUD page, opening the window
/// first when no window exists yet.
fn commit_payload(app: &AppHandle, token: u64, payload: HudPayload) {
    if !app
        .state::<AppState>()
        .generation
        .lock()
        .unwrap()
        .is_current(token)
    {
        return;
    }
    if app.get_webview_window(HUD_LABEL).is_none() {
        // Only reachable while the token is current (the check above drops
        // everything after a dismissal, which starts a fresh generation).
        // Current token + no window means the window was destroyed without
        // a dismissal — e.g. `open_hud`'s builder failed at trigger time and
        // nothing else has run since — so opening it here is the fallback
        // that still surfaces this payload.
        open_hud(app);
    }
    let json = serde_json::to_string(&payload).expect("HudPayload serialization cannot fail");
    let state = app.state::<AppState>();
    *state.last_payload.lock().unwrap() = Some(json.clone());
    // Delivery for a just-opened page is covered by the stored-payload replay
    // on `READY_EVENT` in `open_hud`.
    let _ = app.emit_to(HUD_LABEL, PAYLOAD_EVENT, json);
}

/// Immediate (synchronous) payloads — clipboard failure, missing config, an
/// empty probe clipboard: they run on the main thread with no in-flight
/// task of their own, so each takes a fresh generation and supersedes any
/// older one.
fn show_payload(app: &AppHandle, payload: HudPayload) {
    let token = app.state::<AppState>().generation.lock().unwrap().start();
    commit_payload(app, token, payload);
}

/// Open (or un-hide) the Accessibility onboarding window.
fn open_permission_window(app: &AppHandle) {
    if let Some(existing) = app.get_webview_window(PERMISSION_LABEL) {
        let _ = existing.show();
        let _ = existing.set_focus();
        return;
    }
    let window = WebviewWindowBuilder::new(
        app,
        PERMISSION_LABEL,
        WebviewUrl::App("permission.html".into()),
    )
    .title("Leng — требуется разрешение")
    .inner_size(440.0, 240.0)
    .resizable(false)
    .build();
    if let Err(err) = window {
        eprintln!("leng: failed to open permission window: {err}");
    }
}

/// Webview -> Rust: parse the `HudPayload` wire JSON and render the HUD
/// content fragment (pure `leng_core::render`, golden-tested in leng-core).
/// Invalid JSON is an error for the page to display, not a panic.
#[tauri::command]
fn render_hud(payload_json: String) -> Result<String, String> {
    let payload = HudPayload::from_wire_json(&payload_json).map_err(|err| err.to_string())?;
    Ok(leng_core::render::render_hud_html(&payload))
}

/// «Открыть настройки» — send the user to the Accessibility pane.
#[tauri::command]
fn open_accessibility_settings() {
    let _ = StdCommand::new("open")
        .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        .spawn();
}

/// «Проверить снова» — re-run the permission check; on success close the
/// onboarding window and install the trigger on the main thread.
#[tauri::command]
fn check_permission(app: AppHandle) -> bool {
    if !permission_granted() {
        return false;
    }
    if let Some(window) = app.get_webview_window(PERMISSION_LABEL) {
        let _ = window.close();
    }
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || install_trigger(&handle));
    true
}
