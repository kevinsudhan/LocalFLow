//! Global push-to-talk via a low-level keyboard hook.
//!
//! `RegisterHotKey` cannot express "while held": it only reports a press. Push
//! to talk needs true key-down and key-up, needs to work while any application
//! is focused, and needs to swallow the chord so `Ctrl+Space` does not also
//! reach the editor underneath. `WH_KEYBOARD_LL` is the only Win32 mechanism
//! that provides all three.
//!
//! The hook callback runs on a dedicated thread with its own message pump and
//! does the minimum possible work: compare, swallow, post to a channel.
//! Injected events are ignored so our own `SendInput` cannot feed itself.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc::Sender;

use once_cell::sync::Lazy;
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};

#[cfg(windows)]
use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
#[cfg(windows)]
use windows::Win32::System::Threading::GetCurrentThreadId;
#[cfg(windows)]
use windows::Win32::UI::Input::KeyboardAndMouse::*;
#[cfg(windows)]
use windows::Win32::UI::WindowsAndMessaging::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum HotkeyEvent {
    /// The dictation chord went down.
    Down,
    /// The dictation chord came up.
    Up,
    /// Escape pressed while a dictation is in flight.
    Cancel,
    /// The undo chord fired.
    Undo,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct HotkeySpec {
    pub vk: u16,
    pub ctrl: bool,
    pub shift: bool,
    pub alt: bool,
    pub win: bool,
}

impl HotkeySpec {
    /// Parse "Ctrl+Shift+Space", "F9", "RightAlt", "CapsLock", ...
    pub fn parse(text: &str) -> Option<Self> {
        let mut spec = HotkeySpec::default();
        let mut found_key = false;
        for raw in text.split('+') {
            let part = raw.trim();
            if part.is_empty() {
                continue;
            }
            match part.to_ascii_lowercase().as_str() {
                "ctrl" | "control" | "ctl" => spec.ctrl = true,
                "shift" => spec.shift = true,
                "alt" | "option" => spec.alt = true,
                "win" | "super" | "meta" | "cmd" => spec.win = true,
                other => {
                    spec.vk = vk_from_name(other)?;
                    found_key = true;
                }
            }
        }
        if !found_key {
            return None;
        }
        Some(spec)
    }

    pub fn describe(&self) -> String {
        let mut parts = Vec::new();
        if self.ctrl {
            parts.push("Ctrl".to_string());
        }
        if self.shift {
            parts.push("Shift".to_string());
        }
        if self.alt {
            parts.push("Alt".to_string());
        }
        if self.win {
            parts.push("Win".to_string());
        }
        parts.push(name_from_vk(self.vk));
        parts.join("+")
    }

    /// A modifier used *as* the key (e.g. hold Right Alt) has no chord to check.
    fn key_is_modifier(&self) -> bool {
        matches!(
            self.vk,
            0xA0..=0xA5 | 0x10 | 0x11 | 0x12 | 0x5B | 0x5C // shifts, ctrls, alts, wins
        )
    }
}

fn vk_from_name(name: &str) -> Option<u16> {
    let vk = match name {
        "space" | "spacebar" => 0x20,
        "enter" | "return" => 0x0D,
        "tab" => 0x09,
        "escape" | "esc" => 0x1B,
        "backspace" => 0x08,
        "capslock" | "caps" => 0x14,
        "insert" | "ins" => 0x2D,
        "delete" | "del" => 0x2E,
        "home" => 0x24,
        "end" => 0x23,
        "pageup" => 0x21,
        "pagedown" => 0x22,
        "left" => 0x25,
        "up" => 0x26,
        "right" => 0x27,
        "down" => 0x28,
        "rightalt" | "ralt" | "altgr" => 0xA5,
        "leftalt" | "lalt" => 0xA4,
        "rightctrl" | "rctrl" => 0xA3,
        "leftctrl" | "lctrl" => 0xA2,
        "rightshift" | "rshift" => 0xA1,
        "leftshift" | "lshift" => 0xA0,
        "`" | "backquote" | "grave" => 0xC0,
        "-" | "minus" => 0xBD,
        "=" | "equals" => 0xBB,
        "[" => 0xDB,
        "]" => 0xDD,
        "\\" => 0xDC,
        ";" => 0xBA,
        "'" => 0xDE,
        "," => 0xBC,
        "." => 0xBE,
        "/" => 0xBF,
        _ => {
            if let Some(rest) = name.strip_prefix('f') {
                if let Ok(n) = rest.parse::<u16>() {
                    if (1..=24).contains(&n) {
                        return Some(0x70 + n - 1);
                    }
                }
            }
            let mut chars = name.chars();
            let first = chars.next()?;
            if chars.next().is_some() {
                return None;
            }
            if first.is_ascii_alphanumeric() {
                first.to_ascii_uppercase() as u16
            } else {
                return None;
            }
        }
    };
    Some(vk)
}

fn name_from_vk(vk: u16) -> String {
    match vk {
        0x20 => "Space".into(),
        0x0D => "Enter".into(),
        0x09 => "Tab".into(),
        0x1B => "Escape".into(),
        0x14 => "CapsLock".into(),
        0xA5 => "RightAlt".into(),
        0xA4 => "LeftAlt".into(),
        0xA3 => "RightCtrl".into(),
        0xA2 => "LeftCtrl".into(),
        0xA1 => "RightShift".into(),
        0xA0 => "LeftShift".into(),
        0x70..=0x87 => format!("F{}", vk - 0x70 + 1),
        v if (0x30..=0x5A).contains(&v) => {
            char::from_u32(v as u32).map(|c| c.to_string()).unwrap_or_default()
        }
        v => format!("0x{v:02X}"),
    }
}

struct HookState {
    primary: Option<HotkeySpec>,
    undo: Option<HotkeySpec>,
    cancel_vk: u16,
    enabled: bool,
    primary_down: bool,
    /// True while a dictation is in flight, so Escape is only swallowed then.
    session_active: bool,
    sender: Option<Sender<HotkeyEvent>>,
}

impl HookState {
    const fn new() -> Self {
        Self {
            primary: None,
            undo: None,
            cancel_vk: 0x1B,
            enabled: true,
            primary_down: false,
            session_active: false,
            sender: None,
        }
    }
}

static STATE: Lazy<Mutex<HookState>> = Lazy::new(|| Mutex::new(HookState::new()));

// Counters, not a keystroke log. The hook sees every key on the machine, so
// nothing about what was typed is ever recorded - only how many times the
// configured dictation key matched, and how many times it was pressed without
// its modifiers. That is the difference between "Windows is not giving us the
// key at all" and "the chord is wrong", which is otherwise unanswerable.
static HOOK_INSTALLED: AtomicBool = AtomicBool::new(false);
static MATCHES: AtomicU32 = AtomicU32::new(0);
static KEY_WITHOUT_CHORD: AtomicU32 = AtomicU32::new(0);

/// What the keyboard hook currently believes, for the shortcut tester.
#[derive(Debug, Clone, Serialize)]
pub struct HotkeyProbe {
    pub installed: bool,
    pub enabled: bool,
    pub primary: String,
    /// Times the full chord fired.
    pub matches: u32,
    /// Times the dictation key arrived without the modifiers it needs.
    pub key_without_chord: u32,
}

pub struct HotkeyManager {
    #[cfg(windows)]
    thread_id: Mutex<u32>,
}

impl HotkeyManager {
    pub fn new() -> Self {
        Self {
            #[cfg(windows)]
            thread_id: Mutex::new(0),
        }
    }

    pub fn configure(
        &self,
        primary: &str,
        undo: &str,
        cancel: &str,
        enabled: bool,
    ) -> Result<String, String> {
        let spec = HotkeySpec::parse(primary)
            .ok_or_else(|| format!("'{primary}' is not a shortcut LocalFlow understands."))?;
        let mut state = STATE.lock();
        state.primary = Some(spec);
        state.undo = HotkeySpec::parse(undo);
        state.cancel_vk = HotkeySpec::parse(cancel).map(|s| s.vk).unwrap_or(0x1B);
        state.enabled = enabled;
        state.primary_down = false;
        Ok(spec.describe())
    }

    /// A snapshot for the shortcut tester and diagnostics.
    pub fn probe(&self) -> HotkeyProbe {
        let state = STATE.lock();
        HotkeyProbe {
            installed: HOOK_INSTALLED.load(Ordering::Relaxed),
            enabled: state.enabled,
            primary: state.primary.map(|s| s.describe()).unwrap_or_default(),
            matches: MATCHES.load(Ordering::Relaxed),
            key_without_chord: KEY_WITHOUT_CHORD.load(Ordering::Relaxed),
        }
    }

    pub fn set_session_active(&self, active: bool) {
        STATE.lock().session_active = active;
    }

    pub fn set_enabled(&self, enabled: bool) {
        let mut state = STATE.lock();
        state.enabled = enabled;
        if !enabled {
            state.primary_down = false;
        }
    }

    /// Install the hook on its own thread. Returns once the hook is live.
    #[cfg(windows)]
    pub fn start(&self, sender: Sender<HotkeyEvent>) -> Result<(), String> {
        STATE.lock().sender = Some(sender);
        let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<u32, String>>();

        std::thread::Builder::new()
            .name("localflow-hotkeys".into())
            .spawn(move || unsafe {
                let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_proc), None, 0);
                let hook = match hook {
                    Ok(handle) => handle,
                    Err(err) => {
                        let _ = ready_tx.send(Err(format!(
                            "Windows refused the keyboard hook ({err}). LocalFlow needs it \
                             for push-to-talk."
                        )));
                        return;
                    }
                };
                HOOK_INSTALLED.store(true, Ordering::Relaxed);
                let _ = ready_tx.send(Ok(GetCurrentThreadId()));

                let mut message = MSG::default();
                while GetMessageW(&mut message, None, 0, 0).as_bool() {
                    let _ = TranslateMessage(&message);
                    DispatchMessageW(&message);
                }
                let _ = UnhookWindowsHookEx(hook);
            })
            .map_err(|e| format!("could not start the hotkey thread: {e}"))?;

        match ready_rx.recv_timeout(std::time::Duration::from_secs(5)) {
            Ok(Ok(thread_id)) => {
                *self.thread_id.lock() = thread_id;
                log::info!("Keyboard hook installed on thread {thread_id}");
                Ok(())
            }
            Ok(Err(message)) => Err(message),
            Err(_) => Err("The hotkey thread did not start.".into()),
        }
    }

    #[cfg(not(windows))]
    pub fn start(&self, sender: Sender<HotkeyEvent>) -> Result<(), String> {
        STATE.lock().sender = Some(sender);
        Ok(())
    }

    #[cfg(windows)]
    pub fn stop(&self) {
        let thread_id = *self.thread_id.lock();
        if thread_id != 0 {
            unsafe {
                let _ = PostThreadMessageW(thread_id, WM_QUIT, WPARAM(0), LPARAM(0));
            }
        }
    }

    #[cfg(not(windows))]
    pub fn stop(&self) {}
}

#[cfg(windows)]
unsafe fn modifier_down(vk: VIRTUAL_KEY) -> bool {
    (GetAsyncKeyState(vk.0 as i32) as u16 & 0x8000) != 0
}

#[cfg(windows)]
unsafe fn chord_satisfied(spec: &HotkeySpec) -> bool {
    if spec.key_is_modifier() {
        return true;
    }
    let ctrl = modifier_down(VK_CONTROL);
    let shift = modifier_down(VK_SHIFT);
    let alt = modifier_down(VK_MENU);
    let win = modifier_down(VK_LWIN) || modifier_down(VK_RWIN);
    // Exact match: Ctrl+Space must not fire for Ctrl+Shift+Space, which
    // belongs to the focused application.
    ctrl == spec.ctrl && shift == spec.shift && alt == spec.alt && win == spec.win
}

#[cfg(windows)]
unsafe extern "system" fn keyboard_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code != HC_ACTION as i32 {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    let info = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
    // Ignore anything we (or another automation tool) synthesised.
    if info.flags.0 & LLKHF_INJECTED.0 != 0 {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    let vk = info.vkCode as u16;
    let message = wparam.0 as u32;
    let is_down = message == WM_KEYDOWN || message == WM_SYSKEYDOWN;
    let is_up = message == WM_KEYUP || message == WM_SYSKEYUP;

    let mut swallow = false;
    let mut event: Option<HotkeyEvent> = None;
    {
        let mut state = STATE.lock();
        if state.enabled {
            if let Some(spec) = state.primary {
                if vk == spec.vk {
                    if is_down && chord_satisfied(&spec) {
                        MATCHES.fetch_add(1, Ordering::Relaxed);
                        swallow = true;
                        if !state.primary_down {
                            state.primary_down = true;
                            event = Some(HotkeyEvent::Down);
                        }
                        // Auto-repeat while held is swallowed but not re-sent.
                    } else if is_down {
                        // Right key, wrong modifiers: let it through untouched,
                        // but count it so the tester can say which half is
                        // missing instead of just "nothing happened".
                        KEY_WITHOUT_CHORD.fetch_add(1, Ordering::Relaxed);
                    } else if is_up && state.primary_down {
                        state.primary_down = false;
                        swallow = true;
                        event = Some(HotkeyEvent::Up);
                    }
                }
            }

            if event.is_none() && is_down {
                if let Some(undo) = state.undo {
                    if vk == undo.vk && chord_satisfied(&undo) {
                        swallow = true;
                        event = Some(HotkeyEvent::Undo);
                    }
                }
                if event.is_none() && vk == state.cancel_vk && state.session_active {
                    swallow = true;
                    event = Some(HotkeyEvent::Cancel);
                }
            }
        }

        if let (Some(event), Some(sender)) = (event, state.sender.as_ref()) {
            let _ = sender.send(event);
        }
    }

    if swallow {
        return LRESULT(1);
    }
    CallNextHookEx(None, code, wparam, lparam)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_common_shortcuts() {
        let spec = HotkeySpec::parse("Ctrl+Space").unwrap();
        assert!(spec.ctrl && !spec.shift && !spec.alt);
        assert_eq!(spec.vk, 0x20);
        assert_eq!(spec.describe(), "Ctrl+Space");

        assert_eq!(HotkeySpec::parse("F9").unwrap().vk, 0x78);
        assert_eq!(HotkeySpec::parse("RightAlt").unwrap().vk, 0xA5);
        let combo = HotkeySpec::parse("Ctrl+Shift+D").unwrap();
        assert!(combo.ctrl && combo.shift);
        assert_eq!(combo.vk, 'D' as u16);
    }

    #[test]
    fn rejects_modifier_only_and_nonsense() {
        assert!(HotkeySpec::parse("Ctrl").is_none());
        assert!(HotkeySpec::parse("").is_none());
        assert!(HotkeySpec::parse("Ctrl+NotAKey").is_none());
    }

    #[test]
    fn modifier_keys_need_no_chord() {
        assert!(HotkeySpec::parse("RightAlt").unwrap().key_is_modifier());
        assert!(!HotkeySpec::parse("Ctrl+Space").unwrap().key_is_modifier());
    }
}
