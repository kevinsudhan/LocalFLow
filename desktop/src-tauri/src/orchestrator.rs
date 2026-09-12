//! Session orchestration: hotkey -> record -> transcribe -> insert.
//!
//! This is the piece that makes LocalFlow feel like part of the keyboard. It
//! owns the state machine, remembers which window the user was dictating into
//! (the HUD and the settings window must never become the insertion target),
//! and runs context collection *concurrently* with recording so the two do not
//! serialise.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Receiver;
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};

use crate::backend::Backend;
use crate::hotkey::{HotkeyEvent, HotkeyManager};
use crate::hud::{Hud, HudPayload, HudState};
use crate::inject::{self, InjectMethod, InjectOptions};
use crate::tray::{self, TrayState};
use crate::winctx::{ContextCollector, WindowContext};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    PushToTalk,
    Toggle,
    HandsFree,
}

impl Mode {
    fn parse(value: &str) -> Self {
        match value {
            "toggle" => Self::Toggle,
            "hands_free" => Self::HandsFree,
            _ => Self::PushToTalk,
        }
    }
}

#[derive(Debug, Clone)]
pub struct RuntimeSettings {
    pub mode: Mode,
    pub tap_toggle_ms: u64,
    pub hands_free_silence_ms: u64,
    pub inject: InjectOptions,
    pub replace_selection: bool,
    pub hud_position: String,
    pub hud_offset: i32,
    pub hotkey: String,
    pub undo_hotkey: String,
    pub cancel_key: String,
    pub hotkeys_enabled: bool,
}

impl Default for RuntimeSettings {
    fn default() -> Self {
        Self {
            mode: Mode::PushToTalk,
            tap_toggle_ms: 220,
            hands_free_silence_ms: 1800,
            inject: InjectOptions::default(),
            replace_selection: true,
            hud_position: "bottom_center".into(),
            hud_offset: 72,
            hotkey: "Ctrl+Space".into(),
            undo_hotkey: "Ctrl+Shift+Z".into(),
            cancel_key: "Escape".into(),
            hotkeys_enabled: true,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct LastInsertion {
    pub text: String,
    pub method: String,
    pub chars: usize,
    pub hwnd: isize,
    pub history_id: Option<i64>,
}

#[derive(Default)]
struct SessionState {
    active: bool,
    latched: bool,
    session_id: String,
    started: Option<Instant>,
    context: WindowContext,
    app_name: String,
}

pub struct Orchestrator {
    app: AppHandle,
    backend: Arc<Backend>,
    collector: Arc<ContextCollector>,
    hotkeys: Arc<HotkeyManager>,
    settings: Mutex<RuntimeSettings>,
    session: Mutex<SessionState>,
    last_insertion: Mutex<Option<LastInsertion>>,
    paused: AtomicBool,
    busy: AtomicBool,
}

impl Orchestrator {
    pub fn new(
        app: AppHandle,
        backend: Arc<Backend>,
        collector: Arc<ContextCollector>,
        hotkeys: Arc<HotkeyManager>,
    ) -> Self {
        Self {
            app,
            backend,
            collector,
            hotkeys,
            settings: Mutex::new(RuntimeSettings::default()),
            session: Mutex::new(SessionState::default()),
            last_insertion: Mutex::new(None),
            paused: AtomicBool::new(false),
            busy: AtomicBool::new(false),
        }
    }

    pub fn settings(&self) -> RuntimeSettings {
        self.settings.lock().clone()
    }

    pub fn is_paused(&self) -> bool {
        self.paused.load(Ordering::Relaxed)
    }

    pub fn last_insertion(&self) -> Option<LastInsertion> {
        self.last_insertion.lock().clone()
    }

    /// Pull the settings that affect hotkeys, the HUD and injection.
    pub async fn refresh_settings(self: &Arc<Self>) {
        let Ok(value) = self.backend.call("settings.get", json!({})).await else {
            return;
        };
        let mut settings = RuntimeSettings::default();
        if let Some(hotkeys) = value.get("hotkeys") {
            settings.mode = Mode::parse(str_of(hotkeys, "mode", "push_to_talk").as_str());
            settings.tap_toggle_ms = num_of(hotkeys, "tap_toggle_ms", 220.0) as u64;
            settings.hands_free_silence_ms = num_of(hotkeys, "hands_free_silence_ms", 1800.0) as u64;
            settings.hotkey = str_of(hotkeys, "primary", "Ctrl+Space");
            settings.undo_hotkey = str_of(hotkeys, "undo_hotkey", "Ctrl+Shift+Z");
            settings.cancel_key = str_of(hotkeys, "cancel_key", "Escape");
            settings.hotkeys_enabled = bool_of(hotkeys, "enabled", true);
        }
        if let Some(injection) = value.get("injection") {
            settings.inject = InjectOptions {
                method: InjectMethod::parse(&str_of(injection, "method", "auto")),
                clipboard_threshold: num_of(injection, "clipboard_threshold", 0.0) as usize,
                restore_clipboard: bool_of(injection, "restore_clipboard", true),
                restore_delay_ms: num_of(injection, "restore_delay_ms", 300.0) as u64,
                key_delay_ms: num_of(injection, "key_delay_ms", 1.0) as u64,
                focus_settle_ms: num_of(injection, "focus_settle_ms", 25.0) as u64,
            };
            settings.replace_selection = bool_of(injection, "replace_selection", true);
        }
        if let Some(appearance) = value.get("appearance") {
            settings.hud_position = str_of(appearance, "hud_position", "bottom_center");
            settings.hud_offset = num_of(appearance, "hud_offset", 72.0) as i32;
        }

        let applied = self.hotkeys.configure(
            &settings.hotkey,
            &settings.undo_hotkey,
            &settings.cancel_key,
            settings.hotkeys_enabled && !self.is_paused(),
        );
        if let Err(message) = applied {
            let _ = self.app.emit(
                "localflow:error",
                json!({ "code": "hotkey_invalid", "message": message }),
            );
        }
        *self.settings.lock() = settings;
    }

    /// Consume hotkey events forever.
    pub fn run_hotkey_loop(self: Arc<Self>, rx: Receiver<HotkeyEvent>) {
        std::thread::Builder::new()
            .name("localflow-session".into())
            .spawn(move || {
                let mut down_at = Instant::now();
                while let Ok(event) = rx.recv() {
                    log::info!("hotkey event: {event:?}");
                    // Let the interface react the instant the chord is seen -
                    // the shortcut tester needs proof the hook is alive, and a
                    // held key with no feedback is indistinguishable from a
                    // broken one.
                    let name = match event {
                        HotkeyEvent::Down => "down",
                        HotkeyEvent::Up => "up",
                        HotkeyEvent::Cancel => "cancel",
                        HotkeyEvent::Undo => "undo",
                    };
                    let _ = self.app.emit("localflow:hotkey", json!({ "event": name }));
                    match event {
                        HotkeyEvent::Down => {
                            down_at = Instant::now();
                            self.on_hotkey_down();
                        }
                        HotkeyEvent::Up => {
                            let held = down_at.elapsed();
                            self.on_hotkey_up(held);
                        }
                        HotkeyEvent::Cancel => self.cancel(),
                        HotkeyEvent::Undo => self.undo_last(),
                    }
                }
            })
            .expect("failed to start the session thread");
    }

    fn on_hotkey_down(self: &Arc<Self>) {
        if self.is_paused() {
            log::info!("hotkey ignored: dictation is paused");
            return;
        }
        let mode = self.settings().mode;
        let active = self.session.lock().active;

        if active {
            // Second press in toggle / hands-free mode ends the dictation.
            if matches!(mode, Mode::Toggle | Mode::HandsFree) {
                self.stop(false);
            }
            return;
        }
        self.start();
    }

    fn on_hotkey_up(self: &Arc<Self>, held: Duration) {
        let settings = self.settings();
        let mut session = self.session.lock();
        if !session.active {
            return;
        }
        match settings.mode {
            Mode::PushToTalk => {
                // A quick tap latches recording on, so the user can release the
                // keys and keep talking - the single most requested affordance
                // for long dictations.
                if held < Duration::from_millis(settings.tap_toggle_ms) && !session.latched {
                    session.latched = true;
                    drop(session);
                    Hud::update(
                        &self.app,
                        HudPayload::with_message(
                            HudState::Listening,
                            "Locked - press again to finish",
                        ),
                    );
                    return;
                }
                drop(session);
                self.stop(false);
            }
            Mode::Toggle | Mode::HandsFree => {}
        }
    }

    fn start(self: &Arc<Self>) {
        if self.busy.swap(true, Ordering::AcqRel) {
            log::warn!("hotkey ignored: a previous dictation is still starting");
            return;
        }
        log::info!("starting dictation");
        let this = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            let result = this.start_inner().await;
            if let Err(message) = result {
                log::error!("dictation failed to start: {message}");
                this.fail(&message);
            }
            this.busy.store(false, Ordering::Release);
        });
    }

    async fn start_inner(self: &Arc<Self>) -> Result<(), String> {
        let settings = self.settings();

        // Context collection and session start are independent; run the UIA
        // query on its thread while the backend opens the microphone.
        let collector = Arc::clone(&self.collector);
        let context_handle = tauri::async_runtime::spawn_blocking(move || {
            collector.capture(Duration::from_millis(450))
        });

        let mode = match settings.mode {
            Mode::HandsFree => "hands_free",
            Mode::Toggle => "toggle",
            Mode::PushToTalk => "push_to_talk",
        };

        let context = context_handle.await.unwrap_or_else(|_| WindowContext::default());
        let payload = json!({
            "context": context_to_json(&context),
            "mode": mode,
            "silence_ms": settings.hands_free_silence_ms,
        });

        let response = self
            .backend
            .call("session.start", payload)
            .await
            .map_err(|e| e.message)?;

        let session_id = response
            .get("session_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();

        {
            let mut session = self.session.lock();
            session.active = true;
            session.latched = false;
            session.session_id = session_id;
            session.started = Some(Instant::now());
            session.app_name = context.window_title.clone();
            session.context = context.clone();
        }

        self.hotkeys.set_session_active(true);
        tray::set_state(&self.app, TrayState::Recording);
        Hud::show(&self.app, &context, &settings.hud_position, settings.hud_offset);
        Hud::update(&self.app, HudPayload::new(HudState::Listening));
        Ok(())
    }

    pub fn stop(self: &Arc<Self>, _from_auto: bool) {
        if self.busy.swap(true, Ordering::AcqRel) {
            return;
        }
        let this = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            if let Err(message) = this.stop_inner().await {
                this.fail(&message);
            }
            this.busy.store(false, Ordering::Release);
        });
    }

    async fn stop_inner(self: &Arc<Self>) -> Result<(), String> {
        let (active, context) = {
            let session = self.session.lock();
            (session.active, session.context.clone())
        };
        if !active {
            return Ok(());
        }

        self.hotkeys.set_session_active(false);
        tray::set_state(&self.app, TrayState::Processing);
        Hud::update(&self.app, HudPayload::new(HudState::Processing));

        let result = self
            .backend
            .call("session.stop", json!({ "context": context_to_json(&context) }))
            .await;

        {
            let mut session = self.session.lock();
            session.active = false;
            session.latched = false;
        }

        let result = match result {
            Ok(value) => value,
            Err(err) => {
                tray::set_state(&self.app, TrayState::Active);
                return Err(err.message);
            }
        };

        if result.get("cancelled").and_then(Value::as_bool).unwrap_or(false) {
            self.finish_idle();
            return Ok(());
        }

        // A voice command replaces insertion entirely.
        if let Some(command) = result.get("command").filter(|c| !c.is_null()) {
            self.handle_command(command.clone()).await;
            return Ok(());
        }

        let text = result
            .get("insert_text")
            .and_then(Value::as_str)
            .filter(|t| !t.is_empty())
            .or_else(|| result.get("text").and_then(Value::as_str))
            .unwrap_or("");

        if text.trim().is_empty() {
            let had_speech = result.get("had_speech").and_then(Value::as_bool).unwrap_or(true);
            Hud::update(
                &self.app,
                HudPayload::with_message(
                    HudState::Error,
                    if had_speech { "Nothing recognised" } else { "No speech detected" },
                ),
            );
            self.schedule_hide(1100);
            tray::set_state(&self.app, TrayState::Active);
            let _ = self.app.emit("localflow:result", result);
            return Ok(());
        }

        Hud::update(&self.app, HudPayload::new(HudState::Inserting));
        self.insert(text, &context, &result).await;
        let _ = self.app.emit("localflow:result", result);
        Ok(())
    }

    async fn insert(self: &Arc<Self>, text: &str, context: &WindowContext, result: &Value) {
        let settings = self.settings();
        let mut options = settings.inject.clone();

        // A per-application override from the backend wins over the global
        // default: terminals want typed input, Electron apps want a paste.
        if let Some(method) = result
            .get("diagnostics")
            .and_then(|d| d.get("context"))
            .and_then(|c| c.get("injection_method"))
            .and_then(Value::as_str)
        {
            if method != "auto" {
                options.method = InjectMethod::parse(method);
            }
        }

        let target = context.hwnd;
        let text_owned = text.to_string();
        let app = self.app.clone();

        let outcome = tauri::async_runtime::spawn_blocking(move || {
            // The HUD is non-activating, but a settings window or an app the
            // user clicked could have taken focus; put the target back in front.
            if target != 0 {
                crate::winctx::focus_window(target);
                std::thread::sleep(Duration::from_millis(12));
            }
            let _ = &app;
            inject::insert_text(&text_owned, &options)
        })
        .await;

        if let Ok(ref outcome) = outcome {
            // Which path the text actually took is the first thing anyone
            // needs when it arrives mangled, and it was not recorded
            // anywhere.
            log::info!(
                "inserted via {} ({} chars, {} ms, clipboard_preserved={}){}",
                outcome.method,
                outcome.chars,
                outcome.elapsed_ms,
                outcome.clipboard_preserved,
                if outcome.ok { String::new() } else { format!(": {}", outcome.message) },
            );
        }

        match outcome {
            Ok(outcome) if outcome.ok => {
                *self.last_insertion.lock() = Some(LastInsertion {
                    text: text.to_string(),
                    method: outcome.method.clone(),
                    chars: outcome.chars,
                    hwnd: target,
                    history_id: None,
                });
                if !outcome.clipboard_preserved {
                    // Borrowing the clipboard is normally invisible because
                    // it is handed straight back. When it held an image or
                    // files it cannot be, and destroying it silently would
                    // be worse than saying so.
                    let _ = self.app.emit(
                        "localflow:error",
                        json!({
                            "code": "clipboard_replaced",
                            "message": "Your clipboard held an image or files, so LocalFlow could not put it back after pasting.",
                        }),
                    );
                }
                let _ = self.backend.call("session.inserted", json!({ "inserted": true })).await;
                Hud::update(&self.app, HudPayload::new(HudState::Success));
                self.schedule_hide(650);
                tray::set_state(&self.app, TrayState::Active);
            }
            Ok(outcome) => {
                self.fail(&outcome.message);
            }
            Err(err) => {
                self.fail(&format!("Could not insert the text: {err}"));
            }
        }
    }

    async fn handle_command(self: &Arc<Self>, command: Value) {
        let action = command.get("action").and_then(Value::as_str).unwrap_or("");
        let instruction = command.get("instruction").and_then(Value::as_str).unwrap_or("");
        let last = self.last_insertion();

        let message = match action {
            "undo" => {
                self.undo_last();
                "Undone"
            }
            "cancel" => "Cancelled",
            "new_paragraph" | "new_line" => {
                let text = if action == "new_paragraph" { "\n\n" } else { "\n" };
                let options = self.settings().inject.clone();
                let owned = text.to_string();
                let _ = tauri::async_runtime::spawn_blocking(move || {
                    inject::insert_text(&owned, &options)
                })
                .await;
                "Inserted"
            }
            "delete_last_sentence" => {
                let count = last
                    .as_ref()
                    .map(|l| last_sentence_len(&l.text))
                    .unwrap_or(0);
                let _ = tauri::async_runtime::spawn_blocking(move || {
                    inject::send_key_repeat(inject::VK_BACKSPACE, count)
                })
                .await;
                "Deleted"
            }
            "select_last" => {
                let count = last.as_ref().map(|l| l.chars).unwrap_or(0);
                let _ = tauri::async_runtime::spawn_blocking(move || {
                    inject::send_shift_key_repeat(inject::VK_LEFT_ARROW, count)
                })
                .await;
                "Selected"
            }
            "delete_last" => {
                // Undo removes the insertion as one unit when it was pasted;
                // otherwise remove exactly the characters we sent.
                self.undo_last();
                "Deleted"
            }
            "select_last_word" => {
                let count = last
                    .as_ref()
                    .map(|l| last_word_len(&l.text))
                    .unwrap_or(0);
                let _ = tauri::async_runtime::spawn_blocking(move || {
                    inject::send_shift_key_repeat(inject::VK_LEFT_ARROW, count)
                })
                .await;
                "Selected"
            }
            "capitalize_last" | "make_list" | "replace_last" => {
                let replacement = if action == "replace_last" {
                    command
                        .get("argument")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string()
                } else {
                    String::new()
                };
                match self.transform_last(action, &replacement, instruction, last).await {
                    Ok(()) => "Done",
                    Err(message) => {
                        self.fail(&message);
                        return;
                    }
                }
            }
            "rewrite" | "restyle" | "translate" => {
                match self.rewrite_last(instruction, last).await {
                    Ok(()) => "Rewritten",
                    Err(message) => {
                        self.fail(&message);
                        return;
                    }
                }
            }
            _ => "Done",
        };

        Hud::update(&self.app, HudPayload::with_message(HudState::Success, message));
        self.schedule_hide(900);
        tray::set_state(&self.app, TrayState::Active);
    }

    /// Replace the last insertion with a transformed version of itself.
    ///
    /// "capitalize that" and "replace that with ..." are computed locally -
    /// there is nothing for a language model to decide. "make this a list"
    /// needs the pipeline, because deciding what the items are is exactly the
    /// judgement the list detector already encodes.
    async fn transform_last(
        self: &Arc<Self>,
        action: &str,
        replacement: &str,
        instruction: &str,
        last: Option<LastInsertion>,
    ) -> Result<(), String> {
        let Some(last) = last else {
            return Err("There is nothing to change yet.".into());
        };

        let new_text = match action {
            "capitalize_last" => capitalise(&last.text),
            "replace_last" => {
                if replacement.trim().is_empty() {
                    return Err("I did not catch what to replace it with.".into());
                }
                // Run the replacement through the pipeline so it is punctuated
                // and cased like any other dictation.
                let processed = self
                    .backend
                    .call(
                        "process.text",
                        json!({ "text": replacement, "context": { "exe": "" } }),
                    )
                    .await
                    .map_err(|e| e.message)?;
                processed
                    .get("text")
                    .and_then(Value::as_str)
                    .filter(|t| !t.trim().is_empty())
                    .unwrap_or(replacement)
                    .to_string()
            }
            _ => {
                let response = self
                    .backend
                    .call(
                        "process.command",
                        json!({
                            "instruction": if instruction.is_empty() {
                                "Reformat this as a list, one item per line, \
                                 preserving every item's wording exactly."
                            } else {
                                instruction
                            },
                            "text": last.text,
                        }),
                    )
                    .await
                    .map_err(|e| e.message)?;
                response
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string()
            }
        };

        if new_text.trim().is_empty() {
            return Err("Nothing to insert.".into());
        }
        self.swap_last_insertion(last, new_text).await
    }

    /// Select what we inserted and type over it.
    async fn swap_last_insertion(
        self: &Arc<Self>,
        last: LastInsertion,
        new_text: String,
    ) -> Result<(), String> {
        let options = self.settings().inject.clone();
        let chars = last.chars;
        let target = last.hwnd;
        let replacement = new_text.clone();

        let outcome = tauri::async_runtime::spawn_blocking(move || {
            if target != 0 {
                crate::winctx::focus_window(target);
                std::thread::sleep(Duration::from_millis(12));
            }
            inject::send_shift_key_repeat(inject::VK_LEFT_ARROW, chars)?;
            std::thread::sleep(Duration::from_millis(20));
            let result = inject::insert_text(&replacement, &options);
            if result.ok {
                Ok(result)
            } else {
                Err(result.message)
            }
        })
        .await
        .map_err(|e| e.to_string())??;

        *self.last_insertion.lock() = Some(LastInsertion {
            text: new_text,
            method: outcome.method,
            chars: outcome.chars,
            hwnd: target,
            history_id: last.history_id,
        });
        Ok(())
    }

    async fn rewrite_last(
        self: &Arc<Self>,
        instruction: &str,
        last: Option<LastInsertion>,
    ) -> Result<(), String> {
        let Some(last) = last else {
            return Err("There is nothing to rewrite yet.".into());
        };
        let instruction = if instruction.is_empty() {
            "Clean up grammar and punctuation without changing meaning."
        } else {
            instruction
        };

        let response = self
            .backend
            .call(
                "process.command",
                json!({ "instruction": instruction, "text": last.text }),
            )
            .await
            .map_err(|e| e.message)?;
        let new_text = response.get("text").and_then(Value::as_str).unwrap_or("").to_string();
        if new_text.trim().is_empty() {
            return Err("The local model returned nothing.".into());
        }

        let options = self.settings().inject.clone();
        let chars = last.chars;
        let target = last.hwnd;
        let replacement = new_text.clone();
        let outcome = tauri::async_runtime::spawn_blocking(move || {
            if target != 0 {
                crate::winctx::focus_window(target);
                std::thread::sleep(Duration::from_millis(12));
            }
            // Select what we inserted, then type over it.
            inject::send_shift_key_repeat(inject::VK_LEFT_ARROW, chars)?;
            std::thread::sleep(Duration::from_millis(20));
            let result = inject::insert_text(&replacement, &options);
            if result.ok {
                Ok(result)
            } else {
                Err(result.message)
            }
        })
        .await
        .map_err(|e| e.to_string())?;

        let outcome = outcome?;
        *self.last_insertion.lock() = Some(LastInsertion {
            text: new_text,
            method: outcome.method,
            chars: outcome.chars,
            hwnd: target,
            history_id: None,
        });
        Ok(())
    }

    pub fn undo_last(self: &Arc<Self>) {
        let Some(last) = self.last_insertion() else {
            return;
        };
        let this = Arc::clone(self);
        tauri::async_runtime::spawn_blocking(move || {
            if last.hwnd != 0 {
                crate::winctx::focus_window(last.hwnd);
                std::thread::sleep(Duration::from_millis(12));
            }
            // A clipboard paste is one undo unit, so Ctrl+Z is exact. Typed
            // text may not be, so remove precisely what we sent.
            let outcome = if last.method == "clipboard" {
                inject::send_undo()
            } else {
                inject::send_key_repeat(inject::VK_BACKSPACE, last.chars)
            };
            if let Err(message) = outcome {
                log::warn!("Undo failed: {message}");
            }
            *this.last_insertion.lock() = None;
            // An undo means LocalFlow got it wrong; the zero-edit rate has to
            // see that, not just the edits made in History.
            let backend = Arc::clone(&this.backend);
            tauri::async_runtime::spawn(async move {
                let _ = backend.call("session.undo", json!({})).await;
            });
        });
    }

    pub fn cancel(self: &Arc<Self>) {
        let active = self.session.lock().active;
        if !active {
            return;
        }
        let this = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            let _ = this.backend.call("session.cancel", json!({})).await;
            this.session.lock().active = false;
            this.hotkeys.set_session_active(false);
            Hud::update(&this.app, HudPayload::with_message(HudState::Idle, "Cancelled"));
            this.schedule_hide(500);
            tray::set_state(&this.app, TrayState::Active);
        });
    }

    pub fn set_paused(self: &Arc<Self>, paused: bool) {
        self.paused.store(paused, Ordering::Relaxed);
        let enabled = self.settings().hotkeys_enabled && !paused;
        self.hotkeys.set_enabled(enabled);
        if paused {
            self.cancel();
        }
        tray::set_state(&self.app, if paused { TrayState::Paused } else { TrayState::Active });
        let _ = self.app.emit("localflow:paused", json!({ "paused": paused }));
        let this = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            let _ = this.backend.call("system.pause", json!({ "paused": paused })).await;
        });
    }

    /// Switch to hands-free and begin listening immediately.
    ///
    /// Hands-free ends when the backend reports a long enough silence, so the
    /// user can put the keyboard down entirely.
    pub async fn set_hands_free(self: &Arc<Self>) -> Result<(), String> {
        let _ = self
            .backend
            .call(
                "settings.update",
                json!({ "hotkeys": { "mode": "hands_free" } }),
            )
            .await
            .map_err(|e| e.message)?;
        self.refresh_settings().await;
        if !self.session.lock().active {
            self.start();
        }
        Ok(())
    }

    /// Start a dictation from the tray or the UI rather than the hotkey.
    pub fn toggle_from_ui(self: &Arc<Self>) {
        let active = self.session.lock().active;
        if active {
            self.stop(false);
        } else {
            self.start();
        }
    }

    /// The backend decided the speaker stopped talking (hands-free mode).
    pub fn on_auto_stop(self: &Arc<Self>) {
        if self.session.lock().active {
            self.stop(true);
        }
    }

    fn finish_idle(&self) {
        Hud::hide(&self.app);
        Hud::update(&self.app, HudPayload::new(HudState::Idle));
        tray::set_state(&self.app, TrayState::Active);
    }

    fn fail(&self, message: &str) {
        log::warn!("Session failed: {message}");
        {
            let mut session = self.session.lock();
            session.active = false;
            session.latched = false;
        }
        self.hotkeys.set_session_active(false);
        Hud::update(&self.app, HudPayload::with_message(HudState::Error, message));
        let _ = self
            .app
            .emit("localflow:error", json!({ "code": "session_failed", "message": message }));
        self.schedule_hide(2600);
        tray::set_state(&self.app, TrayState::Active);
    }

    fn schedule_hide(&self, delay_ms: u64) {
        let app = self.app.clone();
        tauri::async_runtime::spawn(async move {
            tokio::time::sleep(Duration::from_millis(delay_ms)).await;
            Hud::hide(&app);
            Hud::update(&app, HudPayload::new(HudState::Idle));
        });
    }
}

fn context_to_json(context: &WindowContext) -> Value {
    json!({
        "exe": context.exe,
        "window_title": context.window_title,
        "url": context.url,
        "selected_text": context.selected_text,
        "text_before": context.text_before,
        "text_after": context.text_after,
        "control_type": context.control_type,
        "is_password": context.is_password,
        "has_uia_text": context.has_uia_text,
    })
}

fn str_of(value: &Value, key: &str, fallback: &str) -> String {
    value.get(key).and_then(Value::as_str).unwrap_or(fallback).to_string()
}

fn num_of(value: &Value, key: &str, fallback: f64) -> f64 {
    value.get(key).and_then(Value::as_f64).unwrap_or(fallback)
}

fn bool_of(value: &Value, key: &str, fallback: bool) -> bool {
    value.get(key).and_then(Value::as_bool).unwrap_or(fallback)
}

/// Characters in the final word, for "select the last word".
fn last_word_len(text: &str) -> usize {
    let trimmed = text.trim_end();
    let word: String = trimmed
        .chars()
        .rev()
        .take_while(|c| !c.is_whitespace())
        .collect();
    word.chars().count() + (text.len() - trimmed.len())
}

/// Upper-case the first letter of each sentence and of the whole string.
fn capitalise(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut at_start = true;
    for ch in text.chars() {
        if at_start && ch.is_alphabetic() {
            out.extend(ch.to_uppercase());
            at_start = false;
        } else {
            out.push(ch);
            if matches!(ch, '.' | '!' | '?' | '\n') {
                at_start = true;
            } else if !ch.is_whitespace() {
                at_start = false;
            }
        }
    }
    out
}

/// Characters in the final sentence, for "delete last sentence".
fn last_sentence_len(text: &str) -> usize {
    let trimmed = text.trim_end();
    let chars: Vec<char> = trimmed.chars().collect();
    // Skip the terminator we are about to delete, then walk back to the
    // previous sentence end.
    let mut index = chars.len();
    if index == 0 {
        return 0;
    }
    let mut seen_terminator = false;
    while index > 0 {
        let ch = chars[index - 1];
        if matches!(ch, '.' | '!' | '?') {
            if seen_terminator {
                break;
            }
            seen_terminator = true;
        }
        index -= 1;
    }
    let removed = chars.len() - index;
    // Include trailing whitespace we trimmed so the caret lands correctly.
    removed + (text.len() - trimmed.len())
}

#[cfg(test)]
mod tests {
    use super::last_sentence_len;
    #[allow(unused_imports)]
    use super::{capitalise, last_word_len};

    #[test]
    fn last_word_length_includes_trailing_space() {
        assert_eq!(super::last_word_len("send it Friday"), "Friday".len());
        assert_eq!(super::last_word_len("one "), "one ".len());
    }

    #[test]
    fn capitalise_handles_sentences() {
        assert_eq!(super::capitalise("hello there. how are you"), "Hello there. How are you");
        assert_eq!(super::capitalise("ICEGATE is fine"), "ICEGATE is fine");
    }

    #[test]
    fn last_sentence_length_counts_only_the_final_sentence() {
        // The separating space goes too, so the caret lands after "One."
        assert_eq!(last_sentence_len("One. Two."), " Two.".len());
        assert_eq!(last_sentence_len("Only one sentence."), "Only one sentence.".len());
        assert_eq!(last_sentence_len(""), 0);
    }
}
