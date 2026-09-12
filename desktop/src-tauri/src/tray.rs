//! System tray icon and menu.

use std::sync::Arc;

use tauri::image::Image;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconEvent};
use tauri::{AppHandle, Runtime};

use crate::orchestrator::Orchestrator;

pub const TRAY_ID: &str = "localflow-tray";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayState {
    Active,
    Paused,
    Recording,
    Processing,
}

impl TrayState {
    fn icon_bytes(self) -> &'static [u8] {
        match self {
            TrayState::Active => include_bytes!("../icons/tray-active.png"),
            TrayState::Paused => include_bytes!("../icons/tray-paused.png"),
            TrayState::Recording => include_bytes!("../icons/tray-recording.png"),
            TrayState::Processing => include_bytes!("../icons/tray-processing.png"),
        }
    }

    fn tooltip(self) -> &'static str {
        match self {
            TrayState::Active => "LocalFlow - ready",
            TrayState::Paused => "LocalFlow - paused",
            TrayState::Recording => "LocalFlow - listening",
            TrayState::Processing => "LocalFlow - processing",
        }
    }
}

pub fn build_menu<R: Runtime>(app: &AppHandle<R>, paused: bool) -> tauri::Result<Menu<R>> {
    let dictate = MenuItem::with_id(app, "dictate", "Start dictation", true, None::<&str>)?;
    let hands_free = MenuItem::with_id(app, "hands_free", "Hands-free mode", true, None::<&str>)?;
    let pause = MenuItem::with_id(
        app,
        "pause",
        if paused { "Resume dictation" } else { "Pause dictation" },
        true,
        None::<&str>,
    )?;

    let history = MenuItem::with_id(app, "nav:history", "History", true, None::<&str>)?;
    let vocabulary = MenuItem::with_id(app, "nav:vocabulary", "Vocabulary", true, None::<&str>)?;
    let snippets = MenuItem::with_id(app, "nav:snippets", "Snippets", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "nav:settings", "Settings", true, None::<&str>)?;
    let diagnostics = MenuItem::with_id(app, "nav:diagnostics", "Diagnostics", true, None::<&str>)?;

    let help = Submenu::with_id_and_items(
        app,
        "help",
        "Help",
        true,
        &[
            &MenuItem::with_id(app, "nav:about", "About LocalFlow", true, None::<&str>)?,
            &MenuItem::with_id(app, "open:logs", "Open log folder", true, None::<&str>)?,
        ],
    )?;

    let quit = MenuItem::with_id(app, "quit", "Quit LocalFlow", true, None::<&str>)?;

    Menu::with_items(
        app,
        &[
            &dictate,
            &hands_free,
            &pause,
            &PredefinedMenuItem::separator(app)?,
            &history,
            &vocabulary,
            &snippets,
            &settings,
            &diagnostics,
            &PredefinedMenuItem::separator(app)?,
            &help,
            &quit,
        ],
    )
}

pub fn set_state(app: &AppHandle, state: TrayState) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    if let Ok(icon) = Image::from_bytes(state.icon_bytes()) {
        let _ = tray.set_icon(Some(icon));
    }
    let _ = tray.set_tooltip(Some(state.tooltip()));
}

pub fn handle_menu_event(app: &AppHandle, event: MenuEvent, orchestrator: &Arc<Orchestrator>) {
    let id = event.id().0.as_str();
    match id {
        "quit" => {
            crate::shutdown_and_exit(app.clone());
        }
        "dictate" => orchestrator.toggle_from_ui(),
        "hands_free" => {
            let orchestrator = Arc::clone(orchestrator);
            tauri::async_runtime::spawn(async move {
                let _ = orchestrator.set_hands_free().await;
            });
        }
        "pause" => {
            let paused = !orchestrator.is_paused();
            orchestrator.set_paused(paused);
            if let Ok(menu) = build_menu(app, paused) {
                if let Some(tray) = app.tray_by_id(TRAY_ID) {
                    let _ = tray.set_menu(Some(menu));
                }
            }
        }
        "open:logs" => {
            if let Some(dir) = dirs::data_dir() {
                let logs = dir.join("LocalFlow").join("logs");
                let _ = std::fs::create_dir_all(&logs);
                let _ = tauri_plugin_opener::open_path(logs.to_string_lossy().to_string(), None::<&str>);
            }
        }
        other => {
            if let Some(route) = other.strip_prefix("nav:") {
                crate::show_main_window(app, route);
            }
        }
    }
}

pub fn handle_tray_event(tray: &TrayIcon, event: TrayIconEvent) {
    if let TrayIconEvent::Click { button, button_state, .. } = event {
        if button == MouseButton::Left && button_state == MouseButtonState::Up {
            crate::show_main_window(tray.app_handle(), "dashboard");
        }
    }
}
