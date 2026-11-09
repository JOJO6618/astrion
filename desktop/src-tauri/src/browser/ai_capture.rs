//! WebView2's native preview captures hidden controllers without CDP compositor waits.
use super::*;
use base64::Engine;
use serde_json::{json, Value};
use windows::Win32::{
    System::Com::{STATFLAG_NONAME, STATSTG, STREAM_SEEK_SET},
    UI::Shell::SHCreateMemStream,
};
pub fn capture(
    app: &AppHandle,
    wc: &ICoreWebView2,
    controller: &ICoreWebView2Controller,
    reply: Value,
) -> Result<(), String> {
    let app = app.clone();
    let controller = controller.clone();
    unsafe {
        let mut visible = windows::core::BOOL::default();
        native_error(controller.IsVisible(&mut visible))?;
        if !visible.as_bool() {
            native_error(controller.SetIsVisible(true))?;
        }
        let stream = SHCreateMemStream(None).ok_or("browser_capture_stream_failed")?;
        let output = stream.clone();
        native_error(wc.CapturePreview(
            COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_PNG,
            &stream,
            &CapturePreviewCompletedHandler::create(Box::new(move |status| {
                let result = (|| -> Result<Value, String> {
                    native_error(status)?;
                    let mut stat = STATSTG::default();
                    native_error(output.Stat(&mut stat, STATFLAG_NONAME))?;
                    if stat.cbSize < 32 || stat.cbSize > 64 * 1024 * 1024 {
                        return Err("browser_capture_invalid_size".into());
                    }
                    native_error(output.Seek(0, STREAM_SEEK_SET, None))?;
                    let mut bytes = vec![0u8; stat.cbSize as usize];
                    let mut read = 0u32;
                    native_error(
                        output
                            .Read(
                                bytes.as_mut_ptr().cast(),
                                bytes.len() as u32,
                                Some(&mut read),
                            )
                            .ok(),
                    )?;
                    if read as usize != bytes.len() {
                        return Err("browser_capture_incomplete".into());
                    }
                    Ok(json!({"data":base64::engine::general_purpose::STANDARD.encode(bytes)}))
                })();
                if !visible.as_bool() {
                    let _ = controller.SetIsVisible(false);
                }
                enqueue(&app, Event::AiReply(reply, result));
                Ok(())
            })),
        ))
    }
}
