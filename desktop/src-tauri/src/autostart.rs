//! "Start LocalFlow with Windows" via the per-user Run key.
//!
//! HKCU is deliberate: it needs no elevation, and uninstalling for one user
//! does not leave a broken entry for another.

#[cfg(windows)]
use windows::core::{w, HSTRING, PCWSTR};
#[cfg(windows)]
use windows::Win32::System::Registry::*;

const VALUE_NAME: &str = "LocalFlow";

#[cfg(windows)]
fn run_key() -> Result<HKEY, String> {
    let mut key = HKEY::default();
    let status = unsafe {
        RegCreateKeyExW(
            HKEY_CURRENT_USER,
            w!("Software\\Microsoft\\Windows\\CurrentVersion\\Run"),
            0,
            None,
            REG_OPTION_NON_VOLATILE,
            KEY_READ | KEY_WRITE,
            None,
            &mut key,
            None,
        )
    };
    if status.is_ok() {
        Ok(key)
    } else {
        Err(format!("could not open the startup registry key ({status:?})"))
    }
}

#[cfg(windows)]
pub fn set_enabled(enabled: bool) -> Result<(), String> {
    let key = run_key()?;
    let name = HSTRING::from(VALUE_NAME);
    let result = if enabled {
        let exe = std::env::current_exe()
            .map_err(|e| format!("could not locate LocalFlow.exe: {e}"))?;
        // --minimized so a boot-time launch goes straight to the tray.
        let command = format!("\"{}\" --minimized", exe.to_string_lossy());
        let wide: Vec<u16> = HSTRING::from(command).as_wide().to_vec();
        let bytes = unsafe {
            std::slice::from_raw_parts(
                wide.as_ptr() as *const u8,
                (wide.len() + 1) * std::mem::size_of::<u16>(),
            )
        };
        unsafe { RegSetValueExW(key, PCWSTR(name.as_ptr()), 0, REG_SZ, Some(bytes)) }
    } else {
        let status = unsafe { RegDeleteValueW(key, PCWSTR(name.as_ptr())) };
        // Deleting something that was never there is success, not failure.
        if status == windows::Win32::Foundation::ERROR_FILE_NOT_FOUND {
            windows::Win32::Foundation::ERROR_SUCCESS
        } else {
            status
        }
    };
    unsafe {
        let _ = RegCloseKey(key);
    }
    if result.is_ok() {
        Ok(())
    } else {
        Err(format!("could not update the startup setting ({result:?})"))
    }
}

#[cfg(windows)]
pub fn is_enabled() -> bool {
    let Ok(key) = run_key() else { return false };
    let name = HSTRING::from(VALUE_NAME);
    let mut size: u32 = 0;
    let status = unsafe {
        RegQueryValueExW(key, PCWSTR(name.as_ptr()), None, None, None, Some(&mut size))
    };
    unsafe {
        let _ = RegCloseKey(key);
    }
    status.is_ok() && size > 0
}

#[cfg(not(windows))]
pub fn set_enabled(_enabled: bool) -> Result<(), String> {
    Err("Startup registration is only implemented on Windows.".into())
}

#[cfg(not(windows))]
pub fn is_enabled() -> bool {
    false
}
