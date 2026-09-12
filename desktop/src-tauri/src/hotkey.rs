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
/// Every event the hook is handed, counted so the re-arm timer can tell a
/// quiet keyboard from a dead hook. A count, never content.
static SEEN_EVENTS: AtomicU32 = AtomicU32::new(0);
static REARMS: AtomicU32 = AtomicU32::new(0);
/// Ctrl key events the hook was handed, and events flagged as synthesised.
/// Both are counts; neither records what was typed.
static CTRL_EVENTS: AtomicU32 = AtomicU32::new(0);
static INJECTED_EVENTS: AtomicU32 = AtomicU32::new(0);

const REARM_TIMER_ID: usize = 1;
const REARM_INTERVAL_MS: u32 = 20_000;
/// How often to check that a key the hook still believes is held is in fact
/// still held. Short enough that a lost release costs a moment rather than a
/// whole session, long enough to stay free: one atomic read, and a single
/// `GetAsyncKeyState` only while a key is actually down.
const HOLD_TIMER_ID: usize = 2;
const HOLD_INTERVAL_MS: u32 = 120;
static MATCHES: AtomicU32 = AtomicU32::new(0);
static KEY_WITHOUT_CHORD: AtomicU32 = AtomicU32::new(0);
/// Releases the hook never delivered, recovered by the watchdog below.
static LOST_RELEASES: AtomicU32 = AtomicU32::new(0);

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
    /// Every key event the hook has been handed. Alive-or-dead evidence.
    pub seen_events: u32,
    /// Times the hook had to be put back after going quiet.
    pub rearms: u32,
    /// Ctrl key events the hook has been handed.
    pub ctrl_events: u32,
    /// Events arriving flagged as synthesised rather than typed.
    pub injected_events: u32,
    /// Key releases the hook never delivered, recovered from the live key state.
    pub lost_releases: u32,
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
            seen_events: SEEN_EVENTS.load(Ordering::Relaxed),
            rearms: REARMS.load(Ordering::Relaxed),
            ctrl_events: CTRL_EVENTS.load(Ordering::Relaxed),
            injected_events: INJECTED_EVENTS.load(Ordering::Relaxed),
            lost_releases: LOST_RELEASES.load(Ordering::Relaxed),
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
    ///
    /// The hook is also re-armed periodically. Windows silently removes a
    /// low-level keyboard hook whose callback exceeds `LowLevelHooksTimeout`
    /// (300 ms by default), which a machine under load can cause at any time.
    /// There is no notification and no API that reports whether a hook is still
    /// installed, so the handle stays valid-looking and the shortcut simply
    /// stops working with nothing logged anywhere. Putting the hook back costs
    /// microseconds, so the only sane remedy is to do it on a timer.
    #[cfg(windows)]
    pub fn start(&self, sender: Sender<HotkeyEvent>) -> Result<(), String> {
        STATE.lock().sender = Some(sender);
        let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<u32, String>>();

        std::thread::Builder::new()
            .name("localflow-hotkeys".into())
            .spawn(move || unsafe {
                let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_proc), None, 0);
                let mut hook = match hook {
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

                // A null window handle posts WM_TIMER to this thread's queue.
                // With no window to own them the requested ids are ignored and
                // Windows allocates its own, so keep what it returns: the two
                // timers are told apart by the id in wParam.
                let rearm_timer = SetTimer(None, REARM_TIMER_ID, REARM_INTERVAL_MS, None);
                let hold_timer = SetTimer(None, HOLD_TIMER_ID, HOLD_INTERVAL_MS, None);
                let mut last_seen = SEEN_EVENTS.load(Ordering::Relaxed);

                let mut message = MSG::default();
                while GetMessageW(&mut message, None, 0, 0).as_bool() {
                    if message.message == WM_TIMER && message.wParam.0 == hold_timer {
                        // A held key whose release never arrived. Recovering it
                        // also means the hook is suspect, so put a fresh one in
                        // rather than waiting out the re-arm interval.
                        if recover_lost_release() && !rearm(&mut hook) {
                            break;
                        }
                        continue;
                    }
                    if message.message == WM_TIMER && message.wParam.0 == rearm_timer {
                        let seen = SEEN_EVENTS.load(Ordering::Relaxed);
                        // A heartbeat in the log, because the counters are
                        // otherwise only visible on one onboarding screen -
                        // which is no use when the question is what the hook
                        // is doing on a different screen entirely. Counts
                        // only; nothing about which keys were pressed.
                        log::info!(
                            "hook heartbeat: seen={} injected={} ctrl={} matched={} partial={} rearms={} lost_releases={} enabled={}",
                            seen,
                            INJECTED_EVENTS.load(Ordering::Relaxed),
                            CTRL_EVENTS.load(Ordering::Relaxed),
                            MATCHES.load(Ordering::Relaxed),
                            KEY_WITHOUT_CHORD.load(Ordering::Relaxed),
                            REARMS.load(Ordering::Relaxed),
                            LOST_RELEASES.load(Ordering::Relaxed),
                            STATE.lock().enabled,
                        );
                        // Only re-arm when the hook has gone quiet. If keys are
                        // still arriving it is demonstrably alive, and swapping
                        // it out mid-stream would be pure risk. Never re-arm
                        // while the dictation key is held, or the release would
                        // be delivered to a hook that no longer exists - the
                        // watchdog above is what closes that case out.
                        if seen == last_seen && !STATE.lock().primary_down && !rearm(&mut hook) {
                            break;
                        }
                        last_seen = seen;
                        continue;
                    }
                    let _ = TranslateMessage(&message);
                    DispatchMessageW(&message);
                }
                KillTimer(None, hold_timer).ok();
                KillTimer(None, rearm_timer).ok();
                let _ = UnhookWindowsHookEx(hook);
                HOOK_INSTALLED.store(false, Ordering::Relaxed);
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

/// Whether a hold has to be force-ended: the hook still believes the key is
/// down while the hardware says it is not.
///
/// Trivial on its face, and separate because inverting it would be silent and
/// catastrophic in one direction - every dictation ending the instant it
/// started - and merely useless in the other.
fn release_was_lost(hook_thinks_down: bool, key_physically_down: bool) -> bool {
    hook_thinks_down && !key_physically_down
}

#[cfg(windows)]
/// Take the hook out and put a fresh one in. `false` means it could not be
/// replaced, which leaves the process with no hook at all and is fatal to
/// push-to-talk, so the caller stops the thread.
unsafe fn rearm(hook: &mut HHOOK) -> bool {
    if UnhookWindowsHookEx(*hook).is_err() {
        // Already gone - most likely Windows removed it - so there is nothing
        // to take out and the replacement below is exactly what is needed.
    }
    match SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_proc), None, 0) {
        Ok(fresh) => {
            *hook = fresh;
            REARMS.fetch_add(1, Ordering::Relaxed);
            true
        }
        Err(err) => {
            HOOK_INSTALLED.store(false, Ordering::Relaxed);
            log::error!("could not re-arm the keyboard hook: {err}");
            false
        }
    }
}

/// End a hold whose release the hook never delivered.
///
/// The release is the only thing that stops a dictation, and it exists in
/// exactly one place: an event the hook is handed. Windows will drop that event
/// for reasons entirely outside this process. It silently removes a low-level
/// hook whose callback overran `LowLevelHooksTimeout`, and it does so without
/// notifying anyone, so the press is seen, the hook dies during the hold, and
/// the matching release is delivered to nothing. A higher-integrity foreground
/// window can swallow it. So can the session's own re-arm, which is why that
/// is skipped while a key is down.
///
/// Whatever the cause, the failure is the same and it is the worst one the app
/// has: the microphone stays open, the HUD sits on screen saying it is
/// listening, and nothing the user does with the keyboard ends it, because the
/// only path out is the event that was lost. It stayed open for seventy seconds
/// on a real machine.
///
/// The live key state is not subject to any of that - it is what the hardware
/// says right now, not an event that has to survive a delivery path - so it is
/// the authority on whether a key is still held. Returns whether a release had
/// to be recovered.
#[cfg(windows)]
unsafe fn recover_lost_release() -> bool {
    let mut state = STATE.lock();
    let Some(spec) = state.primary else {
        return false;
    };
    if !release_was_lost(state.primary_down, modifier_down(VIRTUAL_KEY(spec.vk))) {
        return false;
    }
    state.primary_down = false;
    LOST_RELEASES.fetch_add(1, Ordering::Relaxed);
    log::warn!(
        "{} was released without the hook seeing it; ending the dictation",
        spec.describe()
    );
    if let Some(sender) = state.sender.as_ref() {
        let _ = sender.send(HotkeyEvent::Up);
    }
    true
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
    // Ask the OS for the live modifier state rather than accumulating it
    // from the hook's own events.
    //
    // Tracking looked better on paper - the modifier and the key it
    // qualifies then come from one stream - but the hook discards events
    // Windows flags as synthesised, so a release that arrives that way is
    // never seen and the tracked bit latches on forever. Every subsequent
    // press then fails the exact-match test, including shortcuts with no
    // modifiers at all: F9 was rejected 29 times running. A stale bit is a
    // worse failure than an occasional misread, because it never recovers.
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

    SEEN_EVENTS.fetch_add(1, Ordering::Relaxed);
    let info = &*(lparam.0 as *const KBDLLHOOKSTRUCT);

    let vk = info.vkCode as u16;
    let message = wparam.0 as u32;
    let is_down = message == WM_KEYDOWN || message == WM_SYSKEYDOWN;
    let is_up = message == WM_KEYUP || message == WM_SYSKEYUP;
    let injected = info.flags.0 & LLKHF_INJECTED.0 != 0;
    if injected {
        INJECTED_EVENTS.fetch_add(1, Ordering::Relaxed);
    }
    if matches!(vk, 0x11 | 0xA2 | 0xA3) {
        CTRL_EVENTS.fetch_add(1, Ordering::Relaxed);
    }

    // Anything synthesised is counted and then ignored, so LocalFlow's own
    // SendInput can never feed itself.
    if injected {
        return CallNextHookEx(None, code, wparam, lparam);
    }

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
    fn a_lost_release_is_only_recovered_when_the_key_is_really_up() {
        // The case this exists for: held, then released without the hook ever
        // being handed the release.
        assert!(release_was_lost(true, false));
        // Genuinely still held. Ending here would cut every dictation short.
        assert!(!release_was_lost(true, true));
        // Nothing in flight.
        assert!(!release_was_lost(false, false));
        assert!(!release_was_lost(false, true));
    }

    #[test]
    fn modifier_keys_need_no_chord() {
        assert!(HotkeySpec::parse("RightAlt").unwrap().key_is_modifier());
        assert!(!HotkeySpec::parse("Ctrl+Space").unwrap().key_is_modifier());
    }
}
