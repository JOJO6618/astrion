//! Keep a sequence number, not a plaintext secret, for delayed clipboard cleanup.
use windows::Win32::{
    Foundation::HANDLE,
    System::{DataExchange::*, Memory::*},
};
pub fn write(value: &str, owner: windows::Win32::Foundation::HWND) -> Result<u32, String> {
    let bytes: Vec<u16> = value.encode_utf16().chain(Some(0)).collect();
    unsafe {
        OpenClipboard(Some(owner)).map_err(|e| e.to_string())?;
        let result = (|| -> windows::core::Result<u32> {
            EmptyClipboard()?;
            let memory = GlobalAlloc(GMEM_MOVEABLE, bytes.len() * 2)?;
            let address = GlobalLock(memory);
            if address.is_null() {
                let _ = windows::Win32::Foundation::GlobalFree(Some(memory));
                return Err(windows::core::Error::from_win32());
            }
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), address.cast::<u16>(), bytes.len());
            let _ = GlobalUnlock(memory);
            match SetClipboardData(13, Some(HANDLE(memory.0))) {
                Ok(_) => Ok(GetClipboardSequenceNumber()),
                Err(error) => {
                    let _ = windows::Win32::Foundation::GlobalFree(Some(memory));
                    Err(error)
                }
            }
        })();
        let _ = CloseClipboard();
        result.map_err(|e| e.to_string())
    }
}
pub fn clear_if_unchanged(sequence: u32) {
    unsafe {
        if GetClipboardSequenceNumber() == sequence && OpenClipboard(None).is_ok() {
            if GetClipboardSequenceNumber() == sequence {
                let _ = EmptyClipboard();
            }
            let _ = CloseClipboard();
        }
    }
}
