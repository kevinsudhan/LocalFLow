//! The floating dictation HUD window.
//!
//! The HUD must never steal focus: the whole point is that the user keeps
//! typing into whatever app they were in. It is created non-focusable and every
//! show path uses `show()` without `set_focus()`.

use serde::Serialize;
use tauri::{AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, WebviewWindow};

use crate::winctx::WindowContext;

pub const HUD_LABEL: &str = "hud";
const HUD_WIDTH: f64 = 520.0;
const HUD_HEIGHT: f64 = 92.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum HudState {
    Idle,
    Listening,
    Processing,
    Inserting,
    Success,
    Error,
}

#[derive(Debug, Clone, Serialize)]
pub struct HudPayload {
    pub state: HudState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub app_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub elapsed_ms: Option<u64>,
}

impl HudPayload {
    pub fn new(state: HudState) -> Self {
        Self { state, text: None, message: None, app_name: None, elapsed_ms: None }
    }

    pub fn with_message(state: HudState, message: impl Into<String>) -> Self {
        Self { message: Some(message.into()), ..Self::new(state) }
    }
}

pub struct Hud;

impl Hud {
    fn window(app: &AppHandle) -> Option<WebviewWindow> {
        app.get_webview_window(HUD_LABEL)
    }

    /// Show the HUD positioned for the window the user is dictating into.
    pub fn show(app: &AppHandle, context: &WindowContext, position: &str, offset: i32) {
        let Some(window) = Self::window(app) else { return };
        let _ = window.set_size(LogicalSize::new(HUD_WIDTH, HUD_HEIGHT));

        if let Some(point) = compute_position(app, context, position, offset) {
            let _ = window.set_position(point);
        }
        let _ = window.set_always_on_top(true);
        // The HUD has no controls, so it must never eat a click: the window is
        // a transparent rectangle over the user's real work.
        let _ = window.set_ignore_cursor_events(true);
        // Deliberately no set_focus(): stealing focus would break insertion.
        let _ = window.show();
    }

    pub fn hide(app: &AppHandle) {
        if let Some(window) = Self::window(app) {
            let _ = window.hide();
        }
    }

    pub fn update(app: &AppHandle, payload: HudPayload) {
        let _ = app.emit("hud:state", payload);
    }

    pub fn update_partial(app: &AppHandle, text: &str) {
        let _ = app.emit(
            "hud:partial",
            HudPayload { text: Some(text.to_string()), ..HudPayload::new(HudState::Listening) },
        );
    }

    pub fn update_level(app: &AppHandle, level: f64, peak: f64) {
        let _ = app.emit("hud:level", serde_json::json!({ "level": level, "peak": peak }));
    }
}

/// Work out where the HUD belongs on the monitor that owns the focused window.
fn compute_position(
    app: &AppHandle,
    context: &WindowContext,
    position: &str,
    offset: i32,
) -> Option<PhysicalPosition<i32>> {
    let scale = Hud::window(app)
        .and_then(|w| w.scale_factor().ok())
        .unwrap_or(1.0);
    let width = (HUD_WIDTH * scale) as i32;
    let height = (HUD_HEIGHT * scale) as i32;

    let monitor = context.monitor;
    let (mx, my, mw, mh, work_bottom) = if monitor.width > 0 {
        (monitor.x, monitor.y, monitor.width, monitor.height, monitor.work_bottom)
    } else {
        let primary = app.primary_monitor().ok().flatten()?;
        let size = primary.size();
        let pos = primary.position();
        (pos.x, pos.y, size.width as i32, size.height as i32, pos.y + size.height as i32)
    };

    let scaled_offset = (offset as f64 * scale) as i32;
    let (mut x, mut y) = match position {
        "top_center" => (mx + (mw - width) / 2, my + scaled_offset),
        "near_cursor" if context.caret.valid => {
            // Below the field, nudged left so it does not cover the caret.
            (context.caret.x - 24, context.caret.y + context.caret.height + 14)
        }
        _ => (mx + (mw - width) / 2, work_bottom - height - scaled_offset),
    };

    // Keep it fully on screen whatever the caret reported.
    x = x.clamp(mx + 8, mx + mw - width - 8);
    y = y.clamp(my + 8, my + mh - height - 8);
    Some(PhysicalPosition::new(x, y))
}
