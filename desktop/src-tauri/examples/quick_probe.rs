//! Standalone capability probe; no backend, model requests, or production settings.
#[path = "../src/quick/capture/wgc.rs"]
mod wgc;
use serde_json::json;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use windows::Win32::Foundation::{HWND, POINT};
use windows::Win32::Graphics::Gdi::{MonitorFromWindow, MONITOR_DEFAULTTONEAREST};
use windows::Win32::UI::WindowsAndMessaging::{GetCursorPos, SetCursorPos, SetWindowDisplayAffinity, WDA_EXCLUDEFROMCAPTURE, WDA_NONE};

fn main() {
    let output = std::env::var_os("ASTRION_QUICK_PROBE_OUTPUT").map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from("../../cache/windows-quick-probe"));
    std::fs::create_dir_all(&output).expect("probe output directory");
    let events = Arc::new(Mutex::new(Vec::<serde_json::Value>::new()));
    let protocol_events = events.clone();
    tauri::Builder::default()
        .register_uri_scheme_protocol("quick-probe", move |context, request| {
            let path = request.uri().path();
            if path == "/event" {
                if let Ok(value) = serde_json::from_slice::<serde_json::Value>(request.body()) {
                    protocol_events.lock().unwrap().push(json!({"window": context.webview_label(), "event": value}));
                }
                return tauri::http::Response::builder().header("Content-Type", "application/json")
                    .body(b"{}".to_vec()).unwrap();
            }
            let (mime, bytes) = match path {
                "/app.js" => ("text/javascript", include_bytes!("../../../test/2026-10-05_WindowsQuickProbe/app.js").to_vec()),
                "/style.css" => ("text/css", include_bytes!("../../../test/2026-10-05_WindowsQuickProbe/style.css").to_vec()),
                _ => ("text/html", include_bytes!("../../../test/2026-10-05_WindowsQuickProbe/index.html").to_vec()),
            };
            tauri::http::Response::builder().header("Content-Type", mime).body(bytes).unwrap()
        })
        .setup(move |app| {
            let target = WebviewWindowBuilder::new(app, "probe-target", WebviewUrl::External("quick-probe://app/target".parse()?))
                .title("Astrion Quick native probe")
                .position(140.0, 140.0).inner_size(640.0, 420.0).decorations(false)
                .disable_drag_drop_handler().build()?;
            let quick = WebviewWindowBuilder::new(app, "probe-quick", WebviewUrl::External("quick-probe://app/quick".parse()?))
                .title("Quick transparent probe").position(140.0, 140.0).inner_size(640.0, 420.0)
                .transparent(true).decorations(false).shadow(false).always_on_top(true)
                .disable_drag_drop_handler().build()?;
            let handle = app.handle().clone();
            let target_handle = target.hwnd()?.0 as isize;
            let quick_handle = quick.hwnd()?.0 as isize;
            let scale = quick.scale_factor()?;
            let position = quick.inner_position()?;
            let size = quick.inner_size()?;
            let monitors: Vec<_> = quick.available_monitors()?.iter().map(|m| json!({
                "position": {"x":m.position().x,"y":m.position().y},
                "size":{"width":m.size().width,"height":m.size().height},"scale":m.scale_factor()
            })).collect();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_secs(3));
                let mut report = json!({"scale":scale,"quick_position":{"x":position.x,"y":position.y},
                    "quick_size":{"width":size.width,"height":size.height},"monitors":monitors});
                let hwnd = HWND(quick_handle as _);
                let monitor = unsafe { MonitorFromWindow(HWND(target_handle as _), MONITOR_DEFAULTTONEAREST) }.0 as isize;
                for (name, target) in [
                    ("target-window", wgc::Target::Window(target_handle)),
                    ("monitor-visible", wgc::Target::Monitor(monitor)),
                ] {
                    record_capture(&output, &mut report, name, target);
                }
                let affinity = unsafe { SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE) };
                report["display_affinity"] = json!(affinity.map(|_| "ok".to_owned()).unwrap_or_else(|e|e.to_string()));
                std::thread::sleep(Duration::from_millis(250));
                record_capture(&output, &mut report, "monitor-excluded", wgc::Target::Monitor(monitor));
                report["pointer_test"] = match pointer_test(&handle, &events, position.x, position.y, scale) {
                    Ok(value) => value,
                    Err(error) => json!({"error":error}),
                };
                let _ = unsafe { SetWindowDisplayAffinity(hwnd, WDA_NONE) };
                report["events"] = json!(events.lock().unwrap().clone());
                let _ = std::fs::write(output.join("report.json"), serde_json::to_vec_pretty(&report).unwrap());
                if std::env::var("ASTRION_QUICK_PROBE_KEEP_OPEN").ok().as_deref() != Some("1") {
                    handle.exit(0);
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("run native quick probe");
}

fn record_capture(output: &std::path::Path, report: &mut serde_json::Value, name: &str, target: wgc::Target) {
    report[name] = match wgc::capture(target) {
        Ok(pixels) => {
            let saved = pixels.save_bmp(&output.join(format!("{name}.bmp")));
            json!({"width":pixels.width,"height":pixels.height,"saved":saved.is_ok(),
                "marker_pixels":pixels.bgra.chunks_exact(4).filter(|p| p[2]>220 && p[1]<80 && p[0]>100).count()})
        }
        Err(error) => json!({"error":error}),
    };
}

fn pointer_test(app: &tauri::AppHandle, events: &Arc<Mutex<Vec<serde_json::Value>>>, x: i32, y:i32, scale:f64)
    -> Result<serde_json::Value, String> {
    use windows::Win32::UI::Input::KeyboardAndMouse::*;
    let mut original = POINT::default();
    unsafe { GetCursorPos(&mut original) }.map_err(|e|e.to_string())?;
    let quick = app.get_webview_window("probe-quick").ok_or("quick window missing")?;
    let click = |px:i32, py:i32| -> Result<(),String> {
        unsafe { SetCursorPos(px, py) }.map_err(|e|e.to_string())?;
        std::thread::sleep(Duration::from_millis(100));
        let input = |flags| INPUT { r#type:INPUT_MOUSE, Anonymous:INPUT_0 { mi:MOUSEINPUT {
            dwFlags:flags, ..Default::default() } } };
        if unsafe { SendInput(&[input(MOUSEEVENTF_LEFTDOWN),input(MOUSEEVENTF_LEFTUP)], std::mem::size_of::<INPUT>() as i32) } != 2 {
            return Err("SendInput failed".into());
        }
        std::thread::sleep(Duration::from_millis(250));
        Ok(())
    };
    events.lock().unwrap().clear();
    quick.set_ignore_cursor_events(false).map_err(|e|e.to_string())?;
    click(x+(100.0*scale) as i32,y+(100.0*scale) as i32)?;
    quick.set_ignore_cursor_events(true).map_err(|e|e.to_string())?;
    click(x+(400.0*scale) as i32,y+(250.0*scale) as i32)?;
    let _ = unsafe { SetCursorPos(original.x, original.y) };
    quick.set_ignore_cursor_events(false).map_err(|e|e.to_string())?;
    let logged=events.lock().unwrap();
    Ok(json!({"quick_hit":logged.iter().any(|v|v["window"]=="probe-quick"),
        "target_hit":logged.iter().any(|v|v["window"]=="probe-target"),"events":*logged}))
}
