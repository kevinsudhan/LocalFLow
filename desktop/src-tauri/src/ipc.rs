//! Tauri commands - the bridge between the React UI and everything else.
//!
//! The webview never speaks to the Python backend directly. It calls `invoke`
//! here, and this module forwards to the backend over the authenticated local
//! socket. That keeps the backend's port and token out of the renderer.

use std::sync::Arc;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Manager, State};

use crate::backend::Backend;
use crate::hotkey::{HotkeyManager, HotkeySpec};
use crate::orchestrator::{LastInsertion, Orchestrator};
use crate::winctx::ContextCollector;

/// Methods the UI is allowed to forward. Mirrors the backend's own allowlist so
/// a compromised renderer cannot reach anything the UI does not legitimately use.
const ALLOWED: &[&str] = &[
    "system.ping",
    "system.info",
    "system.hardware",
    "system.catalogues",
    "system.privacy",
    "system.export",
    "system.import",
    "system.wipe",
    "settings.get",
    "settings.update",
    "settings.reset",
    "audio.devices",
    "audio.rescan",
    "asr.models",
    "asr.load",
    "asr.unload",
    "asr.warm",
    "llm.status",
    "llm.test",
    "llm.warm",
    "llm.unload",
    "llm.pull",
    "llm.pull_cancel",
    "llm.delete",
    "llm.show",
    "process.text",
    "process.command",
    "vocabulary.list",
    "vocabulary.add",
    "vocabulary.update",
    "vocabulary.delete",
    "vocabulary.export",
    "vocabulary.import",
    "corrections.list",
    "corrections.observe",
    "corrections.delete",
    "corrections.toggle",
    "corrections.clear",
    "snippets.list",
    "snippets.add",
    "snippets.update",
    "snippets.delete",
    "history.list",
    "history.get",
    "history.delete",
    "history.clear",
    "history.edit",
    "history.stats",
    "metrics.quality",
    "profiles.list",
    "profiles.upsert",
    "profiles.delete",
];

#[derive(Debug, Serialize)]
pub struct UiError {
    pub code: String,
    pub message: String,
    pub detail: String,
}

impl From<crate::backend::BackendError> for UiError {
    fn from(value: crate::backend::BackendError) -> Self {
        Self { code: value.code, message: value.message, detail: value.detail }
    }
}

#[tauri::command]
pub async fn backend_call(
    method: String,
    params: Option<Value>,
    backend: State<'_, Arc<Backend>>,
    orchestrator: State<'_, Arc<Orchestrator>>,
) -> Result<Value, UiError> {
    if !ALLOWED.contains(&method.as_str()) {
        return Err(UiError {
            code: "forbidden".into(),
            message: format!("'{method}' is not available to the interface."),
            detail: String::new(),
        });
    }

    let result = backend
        .call(&method, params.unwrap_or_else(|| json!({})))
        .await
        .map_err(UiError::from)?;

    // Hotkeys, HUD placement and injection live in Rust; re-read them whenever
    // the user changes settings.
    if method == "settings.update" || method == "settings.reset" {
        let orchestrator = Arc::clone(&orchestrator);
        tauri::async_runtime::spawn(async move {
            orchestrator.refresh_settings().await;
        });
    }
    Ok(result)
}

#[tauri::command]
pub fn backend_ready(backend: State<'_, Arc<Backend>>) -> bool {
    backend.is_connected()
}

#[tauri::command]
pub fn start_dictation(orchestrator: State<'_, Arc<Orchestrator>>) {
    orchestrator.toggle_from_ui();
}

#[tauri::command]
pub fn cancel_dictation(orchestrator: State<'_, Arc<Orchestrator>>) {
    orchestrator.cancel();
}

#[tauri::command]
pub fn undo_dictation(orchestrator: State<'_, Arc<Orchestrator>>) {
    orchestrator.undo_last();
}

#[tauri::command]
pub fn set_paused(paused: bool, orchestrator: State<'_, Arc<Orchestrator>>) -> bool {
    orchestrator.set_paused(paused);
    paused
}

#[tauri::command]
pub fn is_paused(orchestrator: State<'_, Arc<Orchestrator>>) -> bool {
    orchestrator.is_paused()
}

#[tauri::command]
pub fn last_insertion(orchestrator: State<'_, Arc<Orchestrator>>) -> Option<LastInsertion> {
    orchestrator.last_insertion()
}

/// Validate a shortcut string before the user commits to it.
#[tauri::command]
pub fn validate_hotkey(shortcut: String) -> Result<String, String> {
    HotkeySpec::parse(&shortcut)
        .map(|spec| spec.describe())
        .ok_or_else(|| {
            format!("'{shortcut}' is not a shortcut LocalFlow can register. Try something \
                     like Ctrl+Space, Alt+D or F9.")
        })
}

/// What the keyboard hook currently sees. Drives the shortcut tester, which
/// needs to distinguish "the hook is not installed", "it is installed but
/// disabled", and "the key arrives without its modifiers".
#[tauri::command]
pub fn hotkey_probe(hotkeys: State<'_, Arc<HotkeyManager>>) -> crate::hotkey::HotkeyProbe {
    hotkeys.probe()
}

/// Live snapshot of the foreground window - used by the onboarding check and
/// the diagnostics panel.
#[tauri::command]
pub async fn probe_context(
    collector: State<'_, Arc<ContextCollector>>,
) -> Result<Value, UiError> {
    let collector = Arc::clone(&collector);
    let context = tauri::async_runtime::spawn_blocking(move || {
        collector.capture(std::time::Duration::from_millis(700))
    })
    .await
    .map_err(|e| UiError {
        code: "probe_failed".into(),
        message: "Could not inspect the focused window.".into(),
        detail: e.to_string(),
    })?;

    Ok(json!({
        "exe": context.exe,
        "window_title": context.window_title,
        "selected_text": context.selected_text,
        "text_before": context.text_before,
        "text_after": context.text_after,
        "control_type": context.control_type,
        "is_password": context.is_password,
        "has_uia_text": context.has_uia_text,
        "caret": context.caret,
        "monitor": context.monitor,
    }))
}

/// Type text into the focused window. Used by the onboarding "try it" step and
/// by History > Reuse.
#[tauri::command]
pub async fn insert_text(
    text: String,
    orchestrator: State<'_, Arc<Orchestrator>>,
) -> Result<Value, UiError> {
    if text.trim().is_empty() {
        return Err(UiError {
            code: "empty".into(),
            message: "There is no text to insert.".into(),
            detail: String::new(),
        });
    }
    let options = orchestrator.settings().inject.clone();
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        crate::inject::insert_text(&text, &options)
    })
    .await
    .map_err(|e| UiError {
        code: "insert_failed".into(),
        message: "Could not insert the text.".into(),
        detail: e.to_string(),
    })?;

    if outcome.ok {
        Ok(serde_json::to_value(outcome).unwrap_or(Value::Null))
    } else {
        Err(UiError {
            code: "insert_failed".into(),
            message: outcome.message,
            detail: String::new(),
        })
    }
}

#[tauri::command]
pub fn set_autostart(enabled: bool) -> Result<bool, String> {
    crate::autostart::set_enabled(enabled).map(|_| enabled)
}

#[tauri::command]
pub fn get_autostart() -> bool {
    crate::autostart::is_enabled()
}

#[tauri::command]
pub fn open_main_window(app: AppHandle, route: Option<String>) {
    crate::show_main_window(&app, route.as_deref().unwrap_or("dashboard"));
}

#[tauri::command]
pub fn hide_main_window(app: AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
}

#[tauri::command]
pub fn quit_app(app: AppHandle) {
    crate::shutdown_and_exit(app);
}

#[tauri::command]
pub fn app_paths() -> Value {
    let data = dirs::data_dir().map(|d| d.join("LocalFlow")).unwrap_or_default();
    json!({
        "data_dir": data.to_string_lossy(),
        "logs_dir": data.join("logs").to_string_lossy(),
        "models_dir": data.join("models").to_string_lossy(),
        "audio_dir": data.join("audio").to_string_lossy(),
    })
}

#[tauri::command]
pub fn open_path(path: String) -> Result<(), String> {
    tauri_plugin_opener::open_path(path, None::<&str>).map_err(|e| e.to_string())
}
