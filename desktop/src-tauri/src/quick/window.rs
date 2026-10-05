use super::{QuickState,emit};
use serde_json::json;
use std::sync::atomic::Ordering;
use std::time::Duration;
use tauri::{AppHandle,Manager,WebviewUrl,WebviewWindowBuilder,PhysicalPosition,PhysicalSize};
use windows::Win32::Foundation::POINT;
use windows::Win32::UI::WindowsAndMessaging::{GetCursorPos,SetWindowDisplayAffinity,WDA_EXCLUDEFROMCAPTURE};

pub fn create(app:&AppHandle)->Result<(),String> {
    let window=WebviewWindowBuilder::new(app,"quick",WebviewUrl::External("astrion-quick://app/quick".parse().unwrap()))
        .title("Astrion Quick Chat").visible(false).decorations(false).transparent(true)
        .shadow(false).always_on_top(true).skip_taskbar(true).resizable(false)
        .disable_drag_drop_handler().initialization_script(include_str!("../../../../static/quick-capture/windows-bridge.js"))
        .on_navigation(|url| matches!(url.host_str(), Some("app") | Some("astrion-quick.app") | Some("astrion-quick.localhost")))
        .build().map_err(|e|e.to_string())?;
    unsafe{SetWindowDisplayAffinity(windows::Win32::Foundation::HWND(window.hwnd().map_err(|e|e.to_string())?.0 as _),WDA_EXCLUDEFROMCAPTURE)}.map_err(|e|format!("Quick window capture exclusion: {e}"))?;
    Ok(())
}
pub fn show(app:&AppHandle)->Result<(),String> {
    let state=app.state::<QuickState>();
    let window=app.get_webview_window("quick").ok_or("Quick window missing")?;
    if !state.visible.load(Ordering::Acquire){
        let mut cursor=POINT::default();unsafe{GetCursorPos(&mut cursor)}.map_err(|e|e.to_string())?;
        let monitors=window.available_monitors().map_err(|e|e.to_string())?;
        let monitor=monitors.iter().find(|m|cursor.x>=m.position().x && cursor.x<m.position().x+m.size().width as i32 && cursor.y>=m.position().y && cursor.y<m.position().y+m.size().height as i32)
            .or_else(||monitors.first()).ok_or("No monitor available")?;
        let area=monitor.work_area();let scale=monitor.scale_factor();let inset=(12.0*scale).round() as i32;
        let width=(960.0*scale).round().min((area.size.width as i32-2*inset) as f64) as u32;
        let height=(area.size.height as i32-2*inset).max(100) as u32;
        window.set_position(PhysicalPosition::new(area.position.x+(area.size.width-width) as i32/2,area.position.y+inset)).map_err(|e|e.to_string())?;
        window.set_size(PhysicalSize::new(width,height)).map_err(|e|e.to_string())?;
        state.generation.fetch_add(1,Ordering::AcqRel);
        state.visible.store(true,Ordering::Release);
        super::capture::prepare(app)?;
    }
    window.show().map_err(|e|e.to_string())?;
    focus(app)?;
    emit(app,"quick","show",serde_json::Value::Null);
    Ok(())
}
pub(super) fn focus(app:&AppHandle)->Result<(),String> {
    app.get_webview_window("quick").ok_or("Quick window missing")?.set_focus().map_err(|e|e.to_string())?;
    // Transfer focus into the WebView2 content, not only its top-level HWND.
    app.get_webview("quick").ok_or("Quick webview missing")?.set_focus().map_err(|e|e.to_string())
}
pub fn start_pointer_loop(app:&AppHandle) {
    let handle=app.clone();std::thread::spawn(move || {
        let mut previous=None;
        loop {
            std::thread::sleep(Duration::from_millis(24));
            let Some(state)=handle.try_state::<QuickState>() else{return};
            if state.quitting.load(Ordering::Acquire){return;}
            if !state.visible.load(Ordering::Acquire){previous=None;continue;}
            let Some(window)=handle.get_webview_window("quick") else{continue};
            let mut point=POINT::default();if unsafe{GetCursorPos(&mut point)}.is_err(){continue;}
            let (Ok(origin),Ok(scale))=(window.inner_position(),window.scale_factor())else{continue};
            let x=(point.x-origin.x) as f64/scale;let y=(point.y-origin.y) as f64/scale;
            let inside=state.regions.lock().unwrap().iter().any(|r|{
                let rx=r["x"].as_f64().unwrap_or(0.0);let ry=r["y"].as_f64().unwrap_or(0.0);
                x>=rx && y>=ry && x<=rx+r["width"].as_f64().unwrap_or(0.0) && y<=ry+r["height"].as_f64().unwrap_or(0.0)
            });
            if previous!=Some(inside){let _=window.set_ignore_cursor_events(!inside);previous=Some(inside);}
            emit(&handle,"quick","pointer-position",json!({"x":x,"y":y}));
        }
    });
}
