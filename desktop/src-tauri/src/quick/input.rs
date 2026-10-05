//! Prevent a clean quick-chat Alt tap from entering the main window's menu loop.
//! Physical keyboard events still pass through the global hook unchanged.
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager};
use windows::Win32::UI::WindowsAndMessaging::{SC_KEYMENU, WM_NCDESTROY, WM_SYSCOMMAND};
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};

static ALT_ENABLED: AtomicBool = AtomicBool::new(false);
const SUBCLASS_ID: usize = 0x41535131;

pub fn set_alt_enabled(enabled: bool) {
    ALT_ENABLED.store(enabled, Ordering::Release);
}

pub fn install(app: &AppHandle) -> Result<(), String> {
    let window = app.get_window("main").ok_or("Main window missing")?;
    let hwnd = window.hwnd().map_err(|error| error.to_string())?.0 as HWND;
    if unsafe { SetWindowSubclass(hwnd, Some(main_input), SUBCLASS_ID, 0) } == 0 {
        return Err("Unable to install main-window Alt menu handler".into());
    }
    Ok(())
}

unsafe extern "system" fn main_input(
    hwnd: HWND,
    message: u32,
    w: WPARAM,
    l: LPARAM,
    id: usize,
    _data: usize,
) -> LRESULT {
    // A recent clean Alt release from this HWND distinguishes Alt from F10 and
    // menu mnemonics. Alt+Space/F4/Tab and other applications keep normal behavior.
    if message == WM_SYSCOMMAND && (w & 0xfff0) == SC_KEYMENU as usize && l == 0
        && ALT_ENABLED.load(Ordering::Acquire)
        && super::keyboard::take_bare_alt_menu(hwnd as isize)
    {
        super::diagnostics::record("main-alt-menu-suppressed", serde_json::json!({}));
        return 0;
    }
    if message == WM_NCDESTROY {
        RemoveWindowSubclass(hwnd, Some(main_input), id);
    }
    DefSubclassProc(hwnd, message, w, l)
}
