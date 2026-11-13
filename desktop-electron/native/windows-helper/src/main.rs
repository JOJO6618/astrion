//! Narrow CLI adapter over the existing Windows native implementation.
//! No Tauri/WebView2 dependency, no screenshot or keyboard data written to disk.
#[cfg(not(windows))]
compile_error!("astrion-windows-helper must be built on Windows");

#[path = "../../../../desktop/src-tauri/src/quick/keyboard.rs"]
mod keyboard;
#[path = "../../../../desktop/src-tauri/src/quick/capture/wgc.rs"]
mod wgc;
#[path = "../../../../desktop/src-tauri/src/quick/capture/windows.rs"]
mod capture_windows;
#[path = "../../../../desktop/src-tauri/src/quick/capture/image.rs"]
mod image;

use base64::Engine;
use std::io::{Read, Write};
use windows::Win32::Foundation::POINT;
use windows::Win32::Graphics::Gdi::{MonitorFromPoint, MONITOR_DEFAULTTONULL};
use windows::Win32::UI::HiDpi::{SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};

fn number(args: &[String], index: usize) -> Result<f64, String> {
    args.get(index).ok_or("Missing capture coordinate")?.parse::<f64>()
        .map_err(|_| "Invalid capture coordinate".into())
        .and_then(|n| if n.is_finite() { Ok(n) } else { Err("Invalid capture coordinate".into()) })
}

fn emit_image(pixels: wgc::Pixels) -> Result<(), String> {
    let encoded = image::data_url(pixels)?;
    let (_, data) = encoded.split_once(',').ok_or("Invalid encoded image")?;
    let bytes = base64::engine::general_purpose::STANDARD.decode(data).map_err(|e| e.to_string())?;
    std::io::stdout().write_all(&bytes).map_err(|e| e.to_string())
}

fn run(args: &[String]) -> Result<(), String> {
    unsafe { let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2); }
    match args.first().map(String::as_str) {
        Some("listen") => {
            let modifier = keyboard::Modifier::parse(args.get(1).ok_or("Missing modifier")?)?;
            let (tx, rx) = std::sync::mpsc::channel();
            let _listener = keyboard::install(modifier, tx)?;
            // The shell keeps stdin open. EOF means normal exit or a shell crash.
            std::thread::spawn(|| {
                let mut byte = [0u8; 1];
                while let Ok(n) = std::io::stdin().read(&mut byte) {
                    if n == 0 { std::process::exit(0); }
                }
                std::process::exit(0);
            });
            for event in rx {
                if matches!(event, keyboard::KeyEvent::Toggle) {
                    println!("toggle");
                    let _ = std::io::stdout().flush();
                }
            }
            Ok(())
        }
        Some("permission") => {
            use windows::Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED};
            unsafe { RoInitialize(RO_INIT_MULTITHREADED) }.map_err(|e| e.to_string())?;
            let supported = windows::Graphics::Capture::GraphicsCaptureSession::IsSupported().map_err(|e| e.to_string());
            unsafe { RoUninitialize() };
            println!("{}", serde_json::json!({"granted": supported?}));
            Ok(())
        }
        Some("windows") => {
            let excluded_pid: u32 = args.get(1).ok_or("Missing shell pid")?.parse().map_err(|_| "Invalid pid")?;
            let list = capture_windows::list(vec![])?;
            let list: Vec<_> = list.into_iter().filter(|row| row.pid != excluded_pid).collect();
            println!("{}", serde_json::to_string(&list).map_err(|e| e.to_string())?);
            Ok(())
        }
        Some("window") => {
            let id = args.get(1).ok_or("Missing window id")?;
            emit_image(wgc::capture(wgc::Target::Window(capture_windows::validate(id)?))?)
        }
        Some("region") => {
            let point = POINT { x: number(args, 1)? as i32, y: number(args, 2)? as i32 };
            let monitor = unsafe { MonitorFromPoint(point, MONITOR_DEFAULTTONULL) };
            if monitor.0.is_null() { return Err("Display is no longer available".into()); }
            let pixels = wgc::capture(wgc::Target::Monitor(monitor.0 as isize))?;
            emit_image(image::crop(pixels, number(args, 3)?, number(args, 4)?, number(args, 5)?, number(args, 6)?)?)
        }
        _ => Err("Unsupported native helper operation".into()),
    }
}

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if let Err(error) = run(&args) {
        eprintln!("[astrion-native] {error}");
        std::process::exit(1);
    }
}
