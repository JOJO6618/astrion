pub mod windows;
pub mod wgc;
pub mod image;
use super::{QuickState,emit};
use serde_json::{json,Value};
use tauri::{AppHandle,Manager,WebviewUrl,WebviewWindowBuilder,PhysicalPosition,PhysicalSize};
use std::sync::atomic::Ordering;
use ::windows::Win32::Foundation::HWND;
use ::windows::Win32::UI::WindowsAndMessaging::{SetWindowDisplayAffinity,WDA_EXCLUDEFROMCAPTURE};
use ::windows::Win32::Graphics::Gdi::{MonitorFromWindow,MONITOR_DEFAULTTONEAREST};

pub fn close(app:&AppHandle){
    for (label,window) in app.webview_windows(){if label.starts_with("quick-overlay-"){let _=window.destroy();}}
    if let Some(state)=app.try_state::<QuickState>(){state.capturing.store(false,Ordering::Release);state.windows.lock().unwrap().clear();}
}
pub fn prepare(app:&AppHandle)->Result<(),String>{
    close(app);
    let quick=app.get_webview_window("quick").ok_or("Quick window missing")?;
    for (index,monitor) in quick.available_monitors().map_err(|e|e.to_string())?.iter().enumerate(){
        let label=format!("quick-overlay-{index}");
        let overlay=WebviewWindowBuilder::new(app,&label,WebviewUrl::External("astrion-quick://app/capture/capture.html".parse().unwrap()))
            .title("Astrion capture overlay").visible(false).decorations(false).transparent(true).shadow(false)
            .always_on_top(true).skip_taskbar(true).resizable(false).focused(false)
            .initialization_script(include_str!("../../../../../static/quick-capture/windows-bridge.js"))
            .on_navigation(|url| matches!(url.host_str(), Some("app") | Some("astrion-quick.app") | Some("astrion-quick.localhost")))
            .build().map_err(|e|e.to_string())?;
        overlay.set_position(PhysicalPosition::new(monitor.position().x,monitor.position().y)).map_err(|e|e.to_string())?;
        overlay.set_size(PhysicalSize::new(monitor.size().width,monitor.size().height)).map_err(|e|e.to_string())?;
        unsafe{SetWindowDisplayAffinity(HWND(overlay.hwnd().map_err(|e|e.to_string())?.0 as _),WDA_EXCLUDEFROMCAPTURE)}.map_err(|e|format!("Overlay capture exclusion: {e}"))?;
        overlay.show().map_err(|e|e.to_string())?;
    }
    let handle=app.clone();let ticket=app.state::<QuickState>().generation.load(Ordering::Acquire);
    std::thread::spawn(move ||{
        loop {
            let state=handle.state::<QuickState>();
            if !state.visible.load(Ordering::Acquire) || state.generation.load(Ordering::Acquire)!=ticket{break;}
            let excluded=handle.webview_windows().into_iter().filter(|(label,_)|label=="quick" || label.starts_with("quick-overlay-"))
                .filter_map(|(_,w)|w.hwnd().ok().map(|h|h.0 as isize)).collect();
            if let Ok(windows)=windows::list(excluded){*state.windows.lock().unwrap()=windows;}
            update(&handle);std::thread::sleep(std::time::Duration::from_millis(600));
        }
    });
    Ok(())
}
pub fn update(app:&AppHandle){
    let Some(state)=app.try_state::<QuickState>() else{return};
    let Some(quick)=app.get_webview_window("quick") else{return};
    let (Ok(q_origin),Ok(q_scale))=(quick.inner_position(),quick.scale_factor())else{return};
    let regions=state.regions.lock().unwrap().clone();let windows=state.windows.lock().unwrap().clone();
    let presentation=state.presentation.lock().unwrap().clone();
    for (label,overlay) in app.webview_windows(){
        if !label.starts_with("quick-overlay-"){continue;}
        let (Ok(origin),Ok(size),Ok(scale))=(overlay.inner_position(),overlay.inner_size(),overlay.scale_factor())else{continue};
        let exclude:Vec<_>=regions.iter().map(|r|json!({
            "x":((q_origin.x-origin.x) as f64+r["x"].as_f64().unwrap_or(0.0)*q_scale)/scale,
            "y":((q_origin.y-origin.y) as f64+r["y"].as_f64().unwrap_or(0.0)*q_scale)/scale,
            "width":r["width"].as_f64().unwrap_or(0.0)*q_scale/scale,"height":r["height"].as_f64().unwrap_or(0.0)*q_scale/scale})).collect();
        let snapshots:Vec<_>=windows.iter().map(|w|json!({"id":w.id,"pid":w.pid,"app":w.app,"candidate":w.candidate,
            "x":w.x as f64/scale,"y":w.y as f64/scale,"width":w.width as f64/scale,"height":w.height as f64/scale})).collect();
        emit(app,&label,"exclude",json!(exclude));
        emit(app,&label,"windows",json!({"windows":snapshots,"exclude":exclude,
            "display":{"x":origin.x as f64/scale,"y":origin.y as f64/scale,"width":size.width as f64/scale,"height":size.height as f64/scale},
            "capturing":state.capturing.load(Ordering::Acquire),"label":presentation["label"],"failure":presentation["failure"],"colors":presentation["colors"]}));
    }
}
pub fn submit(app:&AppHandle,label:&str,op:&str,args:&Value)->Result<(),String>{
    let state=app.state::<QuickState>();
    if !state.visible.load(Ordering::Acquire){return Err("Quick Chat is hidden".into());}
    let overlay=app.get_webview_window(label).ok_or("Stale overlay")?;
    if state.capturing.swap(true,Ordering::AcqRel){return Err("Capture is already running".into());}
    let ticket=state.generation.load(Ordering::Acquire);
    let outcome=(||{
        if op=="window-selection"{
            let id=args["id"].as_str().ok_or("Invalid window id")?;
            if !state.windows.lock().unwrap().iter().any(|w|w.id==id && w.candidate){return Err("Window is no longer a capture candidate".into());}
            return Ok((wgc::Target::Window(windows::validate(id)?),None));
        }
        let rect=&args["rect"];
        let values=["x","y","width","height"].map(|key|rect[key].as_f64().filter(|v|v.is_finite()).ok_or("Invalid capture rectangle"));
        let [x,y,width,height]=values;let (x,y,width,height)=(x?,y?,width?,height?);
        let scale=overlay.scale_factor().map_err(|e|e.to_string())?;let size=overlay.inner_size().map_err(|e|e.to_string())?;
        if x<0.0 || y<0.0 || width<3.0 || height<3.0 || (x+width)*scale>size.width as f64+1.0 || (y+height)*scale>size.height as f64+1.0{return Err("Selection outside monitor".into());}
        let monitor=unsafe{MonitorFromWindow(HWND(overlay.hwnd().map_err(|e|e.to_string())?.0 as _),MONITOR_DEFAULTTONEAREST)};
        Ok((wgc::Target::Monitor(monitor.0 as isize),Some((x*scale,y*scale,width*scale,height*scale))))
    })();
    let (target,rect)=match outcome{Ok(v)=>v,Err(error)=>{state.capturing.store(false,Ordering::Release);return Err(error);}};
    update(app);let handle=app.clone();std::thread::spawn(move ||{
        let result=wgc::capture(target).and_then(|pixels|match rect {Some((x,y,w,h))=>image::crop(pixels,x,y,w,h),None=>Ok(pixels)}).and_then(image::data_url);
        let ui_handle=handle.clone();
        let _=handle.run_on_main_thread(move || {
            let state=ui_handle.state::<QuickState>();
            if state.generation.load(Ordering::Acquire)==ticket && state.visible.load(Ordering::Acquire){
                state.capturing.store(false,Ordering::Release);update(&ui_handle);
                super::diagnostics::record("capture-complete",json!({"success":result.is_ok()}));
                // Restore native content focus before Vue appends the image and focuses its textarea.
                if let Err(error)=super::window::focus(&ui_handle) {
                    emit(&ui_handle,"quick","capture-error",json!(error));
                }
                match result {Ok(url)=>emit(&ui_handle,"quick","capture",json!(url)),Err(error)=>emit(&ui_handle,"quick","capture-error",json!(error))};
            }
        });
    });
    Ok(())
}
