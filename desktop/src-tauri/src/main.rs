// LocalFlow - privacy-first local voice dictation for Windows.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod autostart;
mod backend;
mod hotkey;
mod hud;
mod inject;
mod ipc;
mod orchestrator;
mod tray;
mod winctx;

use std::sync::mpsc;
use std::sync::Arc;

use serde_json::{json, Value};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, WindowEvent};

use backend::Backend;
use hotkey::{HotkeyEvent, HotkeyManager};
use hud::{Hud, HudPayload, HudState};
use orchestrator::Orchestrator;
use tray::{TrayState, TRAY_ID};
use winctx::ContextCollector;

fn main() {
    init_logging();

    let backend = Arc::new(Backend::new());
    let collector = Arc::new(ContextCollector::new());
    let hotkeys = Arc::new(HotkeyManager::new());

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Arc::clone(&backend))
        .manage(Arc::clone(&collector))
        .manage(Arc::clone(&hotkeys))
        .invoke_handler(tauri::generate_handler![
            ipc::backend_call,
            ipc::backend_ready,
            ipc::start_dictation,
            ipc::cancel_dictation,
            ipc::undo_dictation,
            ipc::set_paused,
            ipc::is_paused,
            ipc::last_insertion,
            ipc::validate_hotkey,
            ipc::hotkey_probe,
            ipc::probe_context,
            ipc::insert_text,
            ipc::set_autostart,
            ipc::get_autostart,
            ipc::open_main_window,
            ipc::hide_main_window,
            ipc::quit_app,
            ipc::app_paths,
            ipc::open_path,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let orchestrator = Arc::new(Orchestrator::new(
                handle.clone(),
                Arc::clone(&backend),
                Arc::clone(&collector),
                Arc::clone(&hotkeys),
            ));
            app.manage(Arc::clone(&orchestrator));

            wire_backend_events(&handle, &backend, &orchestrator);
            build_tray(&handle, &orchestrator)?;
            start_hotkeys(&handle, &hotkeys, &orchestrator);
            spawn_backend(handle.clone(), Arc::clone(&backend), Arc::clone(&orchestrator));

            // Boot to the tray unless the user launched us explicitly.
            let minimized = std::env::args().any(|a| a == "--minimized");
            if !minimized {
                show_main_window(&handle, "dashboard");
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the settings window hides it; LocalFlow keeps running in
            // the tray, which is what a background utility should do.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to start LocalFlow")
        .run(|_app, event| {
            // Closing the last window normally ends a Tauri app. LocalFlow is a
            // background utility, so it keeps running in the tray - unless the
            // user actually chose Quit.
            if let tauri::RunEvent::ExitRequested { api, .. } = event {
                if !is_exiting() {
                    api.prevent_exit();
                }
            }
        });
}

fn init_logging() {
    let level = std::env::var("LOCALFLOW_LOG").unwrap_or_else(|_| "info".into());
    let mut builder = env_logger::Builder::new();
    builder.parse_filters(&level).format_timestamp_secs();

    // A GUI-subsystem binary has no console, so stderr is discarded: without a
    // file, everything the shell logs is lost, and a shortcut that silently
    // fails to fire leaves no trace at all. Log beside the backend's own file.
    if let Some(dir) = dirs::data_dir().map(|d| d.join("LocalFlow").join("logs")) {
        let _ = std::fs::create_dir_all(&dir);
        if let Ok(file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("desktop.log"))
        {
            builder.target(env_logger::Target::Pipe(Box::new(file)));
        }
    }
    let _ = builder.try_init();
}

/// Forward backend events to the webview, and act on the ones that drive the UI.
fn wire_backend_events(app: &AppHandle, backend: &Arc<Backend>, orchestrator: &Arc<Orchestrator>) {
    let handle = app.clone();
    let orchestrator = Arc::clone(orchestrator);
    backend.set_handler(Arc::new(move |event: String, data: Value| {
        match event.as_str() {
            "audio.level" => {
                let level = data.get("level").and_then(Value::as_f64).unwrap_or(0.0);
                let peak = data.get("peak").and_then(Value::as_f64).unwrap_or(0.0);
                Hud::update_level(&handle, level, peak);
            }
            "session.partial" => {
                if let Some(text) = data.get("text").and_then(Value::as_str) {
                    Hud::update_partial(&handle, text);
                }
            }
            "session.autostop" => orchestrator.on_auto_stop(),
            "settings.changed" => {
                // Hotkeys, HUD placement and injection all live in Rust.
                // They are re-read when the change arrives through the
                // interface, but a settings change can also originate in
                // the backend itself, and then Rust would keep acting on a
                // stale copy until the next restart.
                let orchestrator = Arc::clone(&orchestrator);
                tauri::async_runtime::spawn(async move {
                    orchestrator.refresh_settings().await;
                });
            }
            "error" => {
                let message = data
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("Something went wrong.");
                log::warn!("backend error: {message}");
                let _ = handle.emit("localflow:error", data.clone());
            }
            _ => {}
        }
        // Everything reaches the UI too, prefixed so listeners can filter.
        //
        // Tauri rejects event names containing a dot, and reports that by
        // returning an error rather than panicking - so discarding the result
        // here silently dropped every backend event, all of which are named
        // after a dotted RPC method. Rewrite the separator and say so when a
        // forward fails, rather than letting the interface go quietly deaf.
        let name = format!("backend:{}", event.replace('.', "-"));
        if let Err(err) = handle.emit(&name, data) {
            log::warn!("could not forward {name} to the interface: {err}");
        }
    }));
}

fn build_tray(app: &AppHandle, orchestrator: &Arc<Orchestrator>) -> tauri::Result<()> {
    let menu = tray::build_menu(app, false)?;
    let for_menu = Arc::clone(orchestrator);
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(tauri::image::Image::from_bytes(include_bytes!("../icons/tray-active.png"))?)
        .tooltip("LocalFlow - ready")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| {
            tray::handle_menu_event(app, event, &for_menu);
        })
        .on_tray_icon_event(|tray, event| {
            tray::handle_tray_event(tray, event);
        })
        .build(app)?;
    Ok(())
}

fn start_hotkeys(app: &AppHandle, hotkeys: &Arc<HotkeyManager>, orchestrator: &Arc<Orchestrator>) {
    let (tx, rx) = mpsc::channel::<HotkeyEvent>();
    if let Err(message) = hotkeys.configure("Ctrl+Space", "Ctrl+Shift+Z", "Escape", true) {
        log::error!("default hotkey rejected: {message}");
    }
    match hotkeys.start(tx) {
        Ok(()) => Arc::clone(orchestrator).run_hotkey_loop(rx),
        Err(message) => {
            log::error!("{message}");
            let _ = app.emit(
                "localflow:error",
                json!({
                    "code": "hotkey_failed",
                    "message": message,
                    "fatal": false,
                }),
            );
        }
    }
}

fn spawn_backend(app: AppHandle, backend: Arc<Backend>, orchestrator: Arc<Orchestrator>) {
    tauri::async_runtime::spawn(async move {
        let resource_dir = app.path().resource_dir().ok();
        let paths = match backend::resolve_paths(resource_dir) {
            Ok(paths) => paths,
            Err(err) => {
                let message = err.to_string();
                log::error!("{message}");
                *backend.last_error.lock() = Some(message.clone());
                let _ = app.emit(
                    "localflow:error",
                    json!({ "code": "backend_missing", "message": message, "fatal": true }),
                );
                return;
            }
        };

        if let Err(err) = backend.start(paths.python, paths.backend_dir).await {
            let message = format!("{err:#}");
            log::error!("backend failed to start: {message}");
            *backend.last_error.lock() = Some(message.clone());
            let _ = app.emit(
                "localflow:error",
                json!({ "code": "backend_failed", "message": message, "fatal": true }),
            );
            return;
        }

        let _ = app.emit("localflow:backend-ready", json!({ "ready": true }));
        orchestrator.refresh_settings().await;
        tray::set_state(&app, TrayState::Active);
    });
}

/// Show (and focus) the settings window on a particular route.
pub fn show_main_window(app: &AppHandle, route: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        let _ = app.emit("localflow:navigate", json!({ "route": route }));
    }
}

/// Set once the user has asked to quit, so `ExitRequested` stops blocking exit.
static EXITING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn is_exiting() -> bool {
    EXITING.load(std::sync::atomic::Ordering::SeqCst)
}

/// Tear everything down in the right order, then exit.
///
/// Exit goes through `AppHandle::exit`, which posts to the event loop, rather
/// than `std::process::exit`. Calling the latter from a worker thread races the
/// window teardown and panics inside tao with "cannot move state from
/// Destroyed" - the event loop must be the thing that ends the process.
pub fn shutdown_and_exit(app: AppHandle) {
    if EXITING.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return; // already quitting
    }

    Hud::update(&app, HudPayload::new(HudState::Idle));
    Hud::hide(&app);

    if let Some(hotkeys) = app.try_state::<Arc<HotkeyManager>>() {
        hotkeys.stop();
    }
    if let Some(collector) = app.try_state::<Arc<ContextCollector>>() {
        collector.shutdown();
    }

    let backend = app.try_state::<Arc<Backend>>().map(|b| Arc::clone(&b));
    tauri::async_runtime::spawn(async move {
        if let Some(backend) = backend {
            // Bounded: a wedged child must not stop the app from quitting.
            let _ = tokio::time::timeout(
                std::time::Duration::from_secs(5),
                backend.shutdown(),
            )
            .await;
        }
        app.exit(0);
    });
}
