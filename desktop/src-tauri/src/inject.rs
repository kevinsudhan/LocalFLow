//! Inserting text into whatever application owns the caret.
//!
//! Two mechanisms, because neither is universally best:
//!
//! * **Unicode `SendInput`** types the text as synthetic key events. It never
//!   touches the clipboard and works in consoles, but it is slow for long text
//!   and some Electron apps drop fast synthetic input.
//! * **Clipboard paste** is one `Ctrl+V` and is reliable and fast everywhere
//!   that supports pasting, and it gives the host application a single undo
//!   unit - which is what makes `Ctrl+Z` behave naturally afterwards.
//!
//! The clipboard is only used when its current contents can be restored
//! faithfully. If the user has an image or a file on the clipboard, LocalFlow
//! types instead of destroying it.

use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

#[cfg(windows)]
use windows::Win32::Foundation::{GlobalFree, HANDLE, HGLOBAL, HWND};
#[cfg(windows)]
use windows::Win32::System::DataExchange::*;
#[cfg(windows)]
use windows::Win32::System::Memory::*;
#[cfg(windows)]
use windows::Win32::System::Ole::CF_UNICODETEXT;
#[cfg(windows)]
use windows::Win32::UI::Input::KeyboardAndMouse::*;

/// Formats Windows synthesises from `CF_UNICODETEXT`; their presence does not
/// mean the clipboard holds anything we would lose.
#[cfg(windows)]
const TEXT_FAMILY: [u32; 5] = [1, 7, 13, 16, 0x0081];

/// Registered formats that carry metadata rather than user content.
///
/// Windows attaches these to ordinary copied text - Information Protection
/// tags, clipboard-history hints. Counting them as content made a plainly
/// restorable clipboard look unrestorable, so every paste fell back to
/// typing, which is exactly the path that corrupts text.
const IGNORABLE_FORMATS: [&str; 7] = [
    "EnterpriseDataProtectionId",
    "CanIncludeInClipboardHistoryAndRoaming",
    "CanUploadToCloudClipboard",
    "ExcludeClipboardContentFromMonitorProcessing",
    "Clipboard Viewer Ignore",
    "Preferred DropEffect",
    "msSourceUrl",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum InjectMethod {
    Auto,
    Unicode,
    Clipboard,
}

impl InjectMethod {
    pub fn parse(value: &str) -> Self {
        match value.to_ascii_lowercase().as_str() {
            "unicode" | "keystrokes" | "type" => Self::Unicode,
            "clipboard" | "paste" => Self::Clipboard,
            _ => Self::Auto,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct InjectionResult {
    pub ok: bool,
    pub method: String,
    pub chars: usize,
    pub clipboard_preserved: bool,
    pub message: String,
    pub elapsed_ms: u64,
}

#[derive(Debug, Clone)]
pub struct InjectOptions {
    pub method: InjectMethod,
    pub clipboard_threshold: usize,
    pub restore_clipboard: bool,
    pub restore_delay_ms: u64,
    pub key_delay_ms: u64,
    pub focus_settle_ms: u64,
}

impl Default for InjectOptions {
    fn default() -> Self {
        Self {
            method: InjectMethod::Auto,
            // 0 = always paste. Synthesising dozens of keystrokes races the
            // target's input queue and loses characters; a paste is one
            // atomic operation. Applications that cannot take Ctrl+V pin
            // themselves to `unicode` in their profile, so this is safe.
            clipboard_threshold: 0,
            restore_clipboard: true,
            restore_delay_ms: 300,
            key_delay_ms: 1,
            focus_settle_ms: 25,
        }
    }
}

/// Insert `text` at the caret of the focused window.
#[cfg(windows)]
pub fn insert_text(text: &str, options: &InjectOptions) -> InjectionResult {
    let started = Instant::now();
    if text.is_empty() {
        return InjectionResult {
            ok: false,
            method: "none".into(),
            chars: 0,
            clipboard_preserved: true,
            message: "Nothing to insert.".into(),
            elapsed_ms: 0,
        };
    }

    // The user may still be holding Ctrl from the hotkey. Typing Unicode while
    // Ctrl is down turns every character into a control chord, so settle first.
    // Generous, because this is the difference between text and chaos: if
    // Ctrl is still physically down when typing starts, the characters are
    // read as shortcuts - Ctrl+A selects the document and the next keystroke
    // replaces it. People routinely hold the modifier a beat longer than the
    // key, and 300 ms was not enough for that.
    wait_for_modifier_release(Duration::from_millis(1500));
    release_stuck_modifiers();
    if options.focus_settle_ms > 0 {
        std::thread::sleep(Duration::from_millis(options.focus_settle_ms));
    }

    // Multi-line text must be pasted, never typed, whatever its length.
    // Synthesising a newline means pressing Return, and in a chat box, a
    // search field, or any single-line form, Return is the submit button:
    // the first line is sent and the rest of the text is destroyed. A
    // dictated list is short enough to sit well under the length threshold,
    // so length alone is not a sufficient test.
    let multiline = text.contains('\n');
    let use_clipboard = match options.method {
        InjectMethod::Unicode => false,
        InjectMethod::Clipboard => true,
        InjectMethod::Auto => multiline || text.chars().count() >= options.clipboard_threshold,
    };

    if use_clipboard {
        match paste_via_clipboard(text, options) {
            Ok(preserved) => {
                return InjectionResult {
                    ok: true,
                    method: "clipboard".into(),
                    chars: text.chars().count(),
                    clipboard_preserved: preserved,
                    message: String::new(),
                    elapsed_ms: started.elapsed().as_millis() as u64,
                }
            }
            Err(message) => {
                log::warn!("Clipboard insertion failed ({message}); typing instead");
            }
        }
    }

    match type_unicode(text, options.key_delay_ms) {
        Ok(chars) => InjectionResult {
            ok: true,
            method: "unicode".into(),
            chars,
            clipboard_preserved: true,
            message: String::new(),
            elapsed_ms: started.elapsed().as_millis() as u64,
        },
        Err(message) => InjectionResult {
            ok: false,
            method: "unicode".into(),
            chars: 0,
            clipboard_preserved: true,
            message,
            elapsed_ms: started.elapsed().as_millis() as u64,
        },
    }
}

#[cfg(not(windows))]
pub fn insert_text(_text: &str, _options: &InjectOptions) -> InjectionResult {
    InjectionResult {
        ok: false,
        method: "unsupported".into(),
        chars: 0,
        clipboard_preserved: true,
        message: "Text insertion is only implemented on Windows.".into(),
        elapsed_ms: 0,
    }
}

// -- Unicode typing -------------------------------------------------------
#[cfg(windows)]
fn type_unicode(text: &str, key_delay_ms: u64) -> Result<usize, String> {
    let mut inputs: Vec<INPUT> = Vec::with_capacity(text.len() * 2);
    // How many events each character contributed, so a batch can be cut
    // between characters rather than through one.
    let mut widths: Vec<usize> = Vec::with_capacity(text.len());
    let mut chars = 0usize;

    for ch in text.chars() {
        chars += 1;
        let before = inputs.len();
        if ch == '\n' {
            // Shift+Return, not Return. Every chat client treats a bare
            // Return as "send"; Shift+Return inserts a line break there and
            // is an ordinary newline everywhere else. This path is only
            // reached when the clipboard was unavailable or typed input was
            // forced, but losing the text to a premature send is not an
            // acceptable failure mode there either.
            inputs.push(key_input(VK_SHIFT, false));
            inputs.push(key_input(VK_RETURN, false));
            inputs.push(key_input(VK_RETURN, true));
            inputs.push(key_input(VK_SHIFT, true));
            widths.push(inputs.len() - before);
            continue;
        }
        if ch == '\r' {
            continue;
        }
        if ch == '\t' {
            inputs.push(key_input(VK_TAB, false));
            inputs.push(key_input(VK_TAB, true));
            widths.push(inputs.len() - before);
            continue;
        }
        // Characters outside the BMP need both halves of the surrogate pair.
        let mut buffer = [0u16; 2];
        for unit in ch.encode_utf16(&mut buffer) {
            inputs.push(unicode_input(*unit, false));
            inputs.push(unicode_input(*unit, true));
        }
        widths.push(inputs.len() - before);
    }

    if inputs.is_empty() {
        return Ok(0);
    }

    // Send in small batches, cut on a character boundary and paced.
    //
    // The boundary must never fall inside a character: a key-down whose
    // key-up lands in the next call, milliseconds later, is a held key,
    // and Windows auto-repeats it - that is how "carrot" arrives as
    // "carrrrrot". Delivering eighty events at once also overruns the
    // input queue of a modern packaged application such as Notepad,
    // which then drops characters outright, so the batches are kept
    // small and separated by a real pause.
    const BATCH: usize = 8;
    for batch in batches(&inputs, &widths, BATCH) {
        let sent = unsafe { SendInput(batch, std::mem::size_of::<INPUT>() as i32) };
        if sent as usize != batch.len() {
            return Err("Windows blocked synthetic keyboard input. If the focused app \
                        runs as administrator, run LocalFlow as administrator too."
                .into());
        }
        std::thread::sleep(Duration::from_millis(key_delay_ms.max(2)));
    }
    Ok(chars)
}

#[cfg(windows)]
fn unicode_input(unit: u16, key_up: bool) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(0),
                wScan: unit,
                dwFlags: if key_up {
                    KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
                } else {
                    KEYEVENTF_UNICODE
                },
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

/// Split `inputs` into batches of at most `max` events, cutting only on a
/// character boundary. `widths` gives the event count of each character.
#[cfg(windows)]
fn batches<'a>(inputs: &'a [INPUT], widths: &[usize], max: usize) -> Vec<&'a [INPUT]> {
    let mut out = Vec::new();
    let (mut start, mut end) = (0usize, 0usize);
    for width in widths {
        if end > start && end - start + width > max {
            out.push(&inputs[start..end]);
            start = end;
        }
        end += width;
    }
    if end > start {
        out.push(&inputs[start..end]);
    }
    out
}

#[cfg(windows)]
fn key_input(vk: VIRTUAL_KEY, key_up: bool) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk,
                wScan: 0,
                dwFlags: if key_up { KEYEVENTF_KEYUP } else { KEYBD_EVENT_FLAGS(0) },
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

#[cfg(windows)]
fn send_chord(modifier: VIRTUAL_KEY, key: VIRTUAL_KEY) -> Result<(), String> {
    let inputs = [
        key_input(modifier, false),
        key_input(key, false),
        key_input(key, true),
        key_input(modifier, true),
    ];
    let sent = unsafe { SendInput(&inputs, std::mem::size_of::<INPUT>() as i32) };
    if sent as usize == inputs.len() {
        Ok(())
    } else {
        Err("Windows blocked the paste shortcut.".into())
    }
}

#[cfg(windows)]
pub fn send_key_repeat(vk: u16, times: usize) -> Result<(), String> {
    if times == 0 {
        return Ok(());
    }
    let key = VIRTUAL_KEY(vk);
    let mut inputs = Vec::with_capacity(times * 2);
    for _ in 0..times.min(4000) {
        inputs.push(key_input(key, false));
        inputs.push(key_input(key, true));
    }
    for batch in inputs.chunks(80) {
        let sent = unsafe { SendInput(batch, std::mem::size_of::<INPUT>() as i32) };
        if sent as usize != batch.len() {
            return Err("Windows blocked synthetic keyboard input.".into());
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    Ok(())
}

#[cfg(windows)]
pub fn send_shift_key_repeat(vk: u16, times: usize) -> Result<(), String> {
    if times == 0 {
        return Ok(());
    }
    let key = VIRTUAL_KEY(vk);
    let mut inputs = Vec::with_capacity(times * 2 + 2);
    inputs.push(key_input(VK_SHIFT, false));
    for _ in 0..times.min(4000) {
        inputs.push(key_input(key, false));
        inputs.push(key_input(key, true));
    }
    inputs.push(key_input(VK_SHIFT, true));
    for batch in inputs.chunks(80) {
        let sent = unsafe { SendInput(batch, std::mem::size_of::<INPUT>() as i32) };
        if sent as usize != batch.len() {
            return Err("Windows blocked synthetic keyboard input.".into());
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    Ok(())
}

/// Ask the focused application to undo - a clipboard paste is a single undo
/// unit, so this removes exactly the inserted text.
#[cfg(windows)]
pub fn send_undo() -> Result<(), String> {
    wait_for_modifier_release(Duration::from_millis(200));
    release_stuck_modifiers();
    send_chord(VK_CONTROL, VK_Z)
}

#[cfg(not(windows))]
pub fn send_undo() -> Result<(), String> {
    Err("Not supported on this platform.".into())
}

#[cfg(not(windows))]
pub fn send_key_repeat(_vk: u16, _times: usize) -> Result<(), String> {
    Err("Not supported on this platform.".into())
}

#[cfg(not(windows))]
pub fn send_shift_key_repeat(_vk: u16, _times: usize) -> Result<(), String> {
    Err("Not supported on this platform.".into())
}

// -- modifier hygiene -----------------------------------------------------
#[cfg(windows)]
fn modifier_is_down(vk: VIRTUAL_KEY) -> bool {
    unsafe { (GetAsyncKeyState(vk.0 as i32) as u16 & 0x8000) != 0 }
}

#[cfg(windows)]
fn wait_for_modifier_release(timeout: Duration) {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        let held = modifier_is_down(VK_CONTROL)
            || modifier_is_down(VK_MENU)
            || modifier_is_down(VK_SHIFT)
            || modifier_is_down(VK_LWIN)
            || modifier_is_down(VK_RWIN);
        if !held {
            return;
        }
        std::thread::sleep(Duration::from_millis(8));
    }
}

/// Last resort: synthesise key-up for modifiers the user is still holding, so
/// the text we type is not interpreted as shortcuts.
#[cfg(windows)]
fn release_stuck_modifiers() {
    let mut inputs = Vec::new();
    for vk in [VK_CONTROL, VK_LCONTROL, VK_RCONTROL, VK_MENU, VK_LMENU, VK_RMENU, VK_SHIFT] {
        if modifier_is_down(vk) {
            inputs.push(key_input(vk, true));
        }
    }
    if !inputs.is_empty() {
        log::debug!("Releasing {} held modifier(s) before insertion", inputs.len());
        unsafe { SendInput(&inputs, std::mem::size_of::<INPUT>() as i32) };
        std::thread::sleep(Duration::from_millis(12));
    }
}

// -- clipboard ------------------------------------------------------------
#[cfg(windows)]
struct ClipboardGuard;

#[cfg(windows)]
impl ClipboardGuard {
    /// Another process may hold the clipboard; retry briefly rather than fail.
    fn open() -> Result<Self, String> {
        for attempt in 0..12 {
            let ok = unsafe { OpenClipboard(HWND::default()) };
            if ok.is_ok() {
                return Ok(Self);
            }
            std::thread::sleep(Duration::from_millis(10 + attempt * 5));
        }
        Err("Another application is holding the clipboard.".into())
    }
}

#[cfg(windows)]
impl Drop for ClipboardGuard {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseClipboard();
        }
    }
}

#[cfg(windows)]
fn clipboard_formats() -> Vec<u32> {
    let mut formats = Vec::new();
    unsafe {
        let mut format = EnumClipboardFormats(0);
        while format != 0 {
            formats.push(format);
            format = EnumClipboardFormats(format);
        }
    }
    formats
}

/// Whether a clipboard format is Windows metadata rather than user content.
#[cfg(windows)]
fn is_ignorable_format(format: u32) -> bool {
    // Only registered formats have names; the predefined ones do not.
    if format < 0xC000 {
        return false;
    }
    let mut buffer = [0u16; 128];
    let length = unsafe { GetClipboardFormatNameW(format, &mut buffer) };
    if length <= 0 {
        return false;
    }
    let name = String::from_utf16_lossy(&buffer[..length as usize]);
    IGNORABLE_FORMATS.iter().any(|known| known.eq_ignore_ascii_case(&name))
}
#[cfg(windows)]
fn read_clipboard_text() -> Option<String> {
    unsafe {
        let handle = GetClipboardData(CF_UNICODETEXT.0 as u32).ok()?;
        if handle.0.is_null() {
            return None;
        }
        let global = HGLOBAL(handle.0);
        let pointer = GlobalLock(global) as *const u16;
        if pointer.is_null() {
            return None;
        }
        let mut length = 0usize;
        while *pointer.add(length) != 0 && length < 8 * 1024 * 1024 {
            length += 1;
        }
        let slice = std::slice::from_raw_parts(pointer, length);
        let text = String::from_utf16_lossy(slice);
        let _ = GlobalUnlock(global);
        Some(text)
    }
}

#[cfg(windows)]
fn write_clipboard_text(text: &str) -> Result<(), String> {
    unsafe {
        let mut utf16: Vec<u16> = text.encode_utf16().collect();
        utf16.push(0);
        let bytes = utf16.len() * std::mem::size_of::<u16>();
        let global = GlobalAlloc(GMEM_MOVEABLE, bytes)
            .map_err(|e| format!("could not allocate clipboard memory: {e}"))?;
        let pointer = GlobalLock(global) as *mut u16;
        if pointer.is_null() {
            let _ = GlobalFree(global);
            return Err("could not lock clipboard memory".into());
        }
        std::ptr::copy_nonoverlapping(utf16.as_ptr(), pointer, utf16.len());
        let _ = GlobalUnlock(global);

        EmptyClipboard().map_err(|e| format!("could not clear the clipboard: {e}"))?;
        // Ownership of `global` transfers to the system on success.
        if SetClipboardData(CF_UNICODETEXT.0 as u32, HANDLE(global.0)).is_err() {
            let _ = GlobalFree(global);
            return Err("could not write to the clipboard".into());
        }
        Ok(())
    }
}

#[cfg(windows)]
fn paste_via_clipboard(text: &str, options: &InjectOptions) -> Result<bool, String> {
    // Decide whether we can put the clipboard back exactly as we found it.
    let (saved, restorable) = {
        let _guard = ClipboardGuard::open()?;
        let formats = clipboard_formats();
        let only_text = formats
            .iter()
            .all(|f| TEXT_FAMILY.contains(f) || is_ignorable_format(*f));
        let saved = if only_text { read_clipboard_text() } else { None };
        (saved, only_text || formats.is_empty())
    };

    // A clipboard holding an image or a file list cannot be put back exactly
    // as it was. That used to abort the paste and fall through to typing -
    // which meant a preference about the *clipboard* silently corrupted the
    // user's *text*. Dictated text is the thing they just asked for, so the
    // paste goes ahead and the caller reports that the clipboard was
    // replaced; a lost clipboard is re-copyable, mangled text is not.
    let preserved = restorable;

    {
        let _guard = ClipboardGuard::open()?;
        write_clipboard_text(text)?;
    }

    send_chord(VK_CONTROL, VK_V)?;

    if options.restore_clipboard && preserved {
        let delay = options.restore_delay_ms.max(60);
        let previous = saved.clone();
        std::thread::spawn(move || {
            // Give the target application time to actually read the clipboard
            // before we put the old contents back.
            std::thread::sleep(Duration::from_millis(delay));
            if let Ok(_guard) = ClipboardGuard::open() {
                match previous {
                    Some(previous) => {
                        let _ = write_clipboard_text(&previous);
                    }
                    None => unsafe {
                        let _ = EmptyClipboard();
                    },
                }
            }
        });
    }
    Ok(preserved)
}

/// Virtual key codes used by the command handlers.
pub const VK_BACKSPACE: u16 = 0x08;
pub const VK_LEFT_ARROW: u16 = 0x25;
