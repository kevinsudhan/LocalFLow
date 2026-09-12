//! Foreground-window identification and caret context via UI Automation.
//!
//! Windows exposes no single API that returns "the text around the caret" for
//! every application, so this is a tiered best effort:
//!
//! 1. **UIA TextPattern** - real text before/after the caret and the current
//!    selection. Works in Word, Notepad, WordPad, most Win32 edit controls,
//!    Chromium/Electron surfaces and anything else that implements it properly.
//! 2. **UIA ValuePattern** - the whole field value, enough to know whether the
//!    field is empty, but not where the caret sits.
//! 3. **Nothing** - we report `has_uia_text: false` and downstream treats the
//!    dictation as starting a fresh sentence, which is the safe default.
//!
//! All UIA work happens on one dedicated COM thread that keeps the automation
//! object alive, because creating `IUIAutomation` per call costs ~10 ms.

use std::sync::mpsc::{self, Receiver, Sender};
use std::time::Duration;

use serde::{Deserialize, Serialize};

#[cfg(windows)]
use windows::core::Interface;
#[cfg(windows)]
use windows::Win32::Foundation::{CloseHandle, HWND, MAX_PATH, RECT};
#[cfg(windows)]
use windows::Win32::System::Com::*;
#[cfg(windows)]
use windows::Win32::System::Threading::*;
#[cfg(windows)]
use windows::Win32::UI::Accessibility::*;
#[cfg(windows)]
use windows::Win32::UI::WindowsAndMessaging::*;

pub const CONTEXT_CHARS: i32 = 600;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct WindowContext {
    pub exe: String,
    pub window_title: String,
    #[serde(default)]
    pub url: String,
    pub selected_text: String,
    pub text_before: String,
    pub text_after: String,
    pub control_type: String,
    pub is_password: bool,
    pub has_uia_text: bool,
    #[serde(skip)]
    pub hwnd: isize,
    pub caret: CaretRect,
    pub monitor: MonitorRect,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct CaretRect {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub valid: bool,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct MonitorRect {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub work_bottom: i32,
}

enum Request {
    Capture(Sender<WindowContext>),
    Shutdown,
}

/// Runs UIA queries on a dedicated COM thread.
pub struct ContextCollector {
    tx: Sender<Request>,
}

impl ContextCollector {
    pub fn new() -> Self {
        let (tx, rx) = mpsc::channel::<Request>();
        std::thread::Builder::new()
            .name("localflow-uia".into())
            .spawn(move || worker(rx))
            .expect("failed to start the UI Automation thread");
        Self { tx }
    }

    /// Capture the current context, giving up after `budget`.
    ///
    /// A misbehaving application can block a UIA call indefinitely; dictation
    /// must never hang because of that, so a timeout returns whatever the cheap
    /// Win32 path already knows.
    pub fn capture(&self, budget: Duration) -> WindowContext {
        let (tx, rx) = mpsc::channel();
        if self.tx.send(Request::Capture(tx)).is_err() {
            return basic_context();
        }
        match rx.recv_timeout(budget) {
            Ok(context) => context,
            Err(_) => {
                log::debug!("UIA capture timed out; using window info only");
                basic_context()
            }
        }
    }

    pub fn shutdown(&self) {
        let _ = self.tx.send(Request::Shutdown);
    }
}

#[cfg(windows)]
fn worker(rx: Receiver<Request>) {
    unsafe {
        // MTA: this thread makes blocking cross-process calls and owns no UI.
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    }
    let automation: Option<IUIAutomation> = unsafe {
        match CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER) {
            Ok(instance) => Some(instance),
            Err(err) => {
                log::warn!("UI Automation unavailable: {err}. Falling back to window info.");
                None
            }
        }
    };

    while let Ok(request) = rx.recv() {
        match request {
            Request::Capture(reply) => {
                let mut context = basic_context();
                if let Some(automation) = automation.as_ref() {
                    enrich(automation, &mut context);
                }
                let _ = reply.send(context);
            }
            Request::Shutdown => break,
        }
    }
    unsafe { CoUninitialize() };
}

#[cfg(not(windows))]
fn worker(rx: Receiver<Request>) {
    while let Ok(request) = rx.recv() {
        match request {
            Request::Capture(reply) => {
                let _ = reply.send(WindowContext::default());
            }
            Request::Shutdown => break,
        }
    }
}

/// Cheap, always-available facts: which window is in front and in which app.
#[cfg(windows)]
pub fn basic_context() -> WindowContext {
    let mut context = WindowContext::default();
    unsafe {
        let hwnd = GetForegroundWindow();
        if hwnd.0.is_null() {
            return context;
        }
        context.hwnd = hwnd.0 as isize;
        context.window_title = window_title(hwnd);
        context.exe = process_name(hwnd);
        context.monitor = monitor_rect(hwnd);
    }
    context
}

#[cfg(not(windows))]
pub fn basic_context() -> WindowContext {
    WindowContext::default()
}

#[cfg(windows)]
unsafe fn window_title(hwnd: HWND) -> String {
    let mut buffer = [0u16; 512];
    let len = GetWindowTextW(hwnd, &mut buffer);
    if len <= 0 {
        return String::new();
    }
    String::from_utf16_lossy(&buffer[..len as usize])
}

#[cfg(windows)]
unsafe fn process_name(hwnd: HWND) -> String {
    let mut pid: u32 = 0;
    GetWindowThreadProcessId(hwnd, Some(&mut pid));
    if pid == 0 {
        return String::new();
    }
    let handle = match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
        Ok(handle) => handle,
        Err(_) => return String::new(),
    };
    let mut buffer = [0u16; MAX_PATH as usize];
    let mut size = buffer.len() as u32;
    let ok = QueryFullProcessImageNameW(
        handle,
        PROCESS_NAME_WIN32,
        windows::core::PWSTR(buffer.as_mut_ptr()),
        &mut size,
    );
    let _ = CloseHandle(handle);
    if ok.is_err() {
        return String::new();
    }
    let full = String::from_utf16_lossy(&buffer[..size as usize]);
    full.rsplit('\\').next().unwrap_or(&full).to_ascii_lowercase()
}

#[cfg(windows)]
unsafe fn monitor_rect(hwnd: HWND) -> MonitorRect {
    use windows::Win32::Graphics::Gdi::*;
    let monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
    let mut info = MONITORINFO {
        cbSize: std::mem::size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    if GetMonitorInfoW(monitor, &mut info).as_bool() {
        MonitorRect {
            x: info.rcMonitor.left,
            y: info.rcMonitor.top,
            width: info.rcMonitor.right - info.rcMonitor.left,
            height: info.rcMonitor.bottom - info.rcMonitor.top,
            work_bottom: info.rcWork.bottom,
        }
    } else {
        MonitorRect::default()
    }
}

#[cfg(windows)]
fn enrich(automation: &IUIAutomation, context: &mut WindowContext) {
    unsafe {
        let Ok(element) = automation.GetFocusedElement() else {
            return;
        };

        if let Ok(is_password) = element.CurrentIsPassword() {
            context.is_password = is_password.as_bool();
        }
        if let Ok(control_type) = element.CurrentControlType() {
            context.control_type = control_type_name(control_type);
        }
        if let Ok(RECT { left, top, right, bottom }) = element.CurrentBoundingRectangle() {
            if right > left && bottom > top {
                context.caret = CaretRect {
                    x: left,
                    y: top,
                    width: right - left,
                    height: bottom - top,
                    valid: true,
                };
            }
        }

        // Never read the contents of a password field, only its existence.
        if context.is_password {
            return;
        }

        if read_text_pattern(&element, context) {
            context.has_uia_text = true;
            return;
        }
        read_value_pattern(&element, context);
    }
}

#[cfg(windows)]
unsafe fn read_text_pattern(element: &IUIAutomationElement, context: &mut WindowContext) -> bool {
    let Ok(unknown) = element.GetCurrentPattern(UIA_TextPatternId) else {
        return false;
    };
    let Ok(pattern) = unknown.cast::<IUIAutomationTextPattern>() else {
        return false;
    };
    let Ok(selection) = pattern.GetSelection() else {
        return false;
    };
    let Ok(count) = selection.Length() else {
        return false;
    };
    if count == 0 {
        return false;
    }
    let Ok(range) = selection.GetElement(0) else {
        return false;
    };

    if let Ok(text) = range.GetText(8192) {
        context.selected_text = text.to_string();
    }

    // Text before the caret: collapse a clone onto the selection start, then
    // walk the start endpoint backwards.
    if let Ok(before) = range.Clone() {
        let _ = before.MoveEndpointByRange(
            TextPatternRangeEndpoint_End,
            &range,
            TextPatternRangeEndpoint_Start,
        );
        let _ = before.MoveEndpointByUnit(
            TextPatternRangeEndpoint_Start,
            TextUnit_Character,
            -CONTEXT_CHARS,
        );
        if let Ok(text) = before.GetText(CONTEXT_CHARS) {
            context.text_before = text.to_string();
        }
    }

    if let Ok(after) = range.Clone() {
        let _ = after.MoveEndpointByRange(
            TextPatternRangeEndpoint_Start,
            &range,
            TextPatternRangeEndpoint_End,
        );
        let _ =
            after.MoveEndpointByUnit(TextPatternRangeEndpoint_End, TextUnit_Character, CONTEXT_CHARS);
        if let Ok(text) = after.GetText(CONTEXT_CHARS) {
            context.text_after = text.to_string();
        }
    }

    true
}

#[cfg(windows)]
unsafe fn read_value_pattern(element: &IUIAutomationElement, context: &mut WindowContext) {
    let Ok(unknown) = element.GetCurrentPattern(UIA_ValuePatternId) else {
        return;
    };
    let Ok(pattern) = unknown.cast::<IUIAutomationValuePattern>() else {
        return;
    };
    let Ok(value) = pattern.CurrentValue() else {
        return;
    };
    let text = value.to_string();
    // Without a caret position this only tells us whether the field is empty -
    // which is still enough to decide "this starts a new document".
    if text.trim().is_empty() {
        context.has_uia_text = true;
        context.text_before.clear();
    }
}

#[cfg(windows)]
fn control_type_name(control_type: UIA_CONTROLTYPE_ID) -> String {
    // Compared rather than matched: these are `const` values, not variants.
    for (id, name) in [
        (UIA_EditControlTypeId, "edit"),
        (UIA_DocumentControlTypeId, "document"),
        (UIA_TextControlTypeId, "text"),
        (UIA_ComboBoxControlTypeId, "combobox"),
        (UIA_ButtonControlTypeId, "button"),
        (UIA_PaneControlTypeId, "pane"),
        (UIA_CustomControlTypeId, "custom"),
        (UIA_ListControlTypeId, "list"),
    ] {
        if control_type == id {
            return name.to_string();
        }
    }
    "other".to_string()
}

/// Bring a window back to the foreground after the HUD or settings stole focus.
#[cfg(windows)]
pub fn focus_window(hwnd_value: isize) -> bool {
    if hwnd_value == 0 {
        return false;
    }
    unsafe {
        let hwnd = HWND(hwnd_value as *mut std::ffi::c_void);
        if !IsWindow(hwnd).as_bool() {
            return false;
        }
        if GetForegroundWindow() == hwnd {
            return true;
        }
        // SetForegroundWindow only obeys the thread that owns the foreground
        // window, so attach to it first.
        let target_thread = GetWindowThreadProcessId(hwnd, None);
        let current_thread = GetCurrentThreadId();
        let attached = target_thread != current_thread
            && AttachThreadInput(current_thread, target_thread, true).as_bool();
        let ok = SetForegroundWindow(hwnd).as_bool();
        if attached {
            let _ = AttachThreadInput(current_thread, target_thread, false);
        }
        ok
    }
}

#[cfg(not(windows))]
pub fn focus_window(_hwnd: isize) -> bool {
    false
}
