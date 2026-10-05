pub mod assets;
pub mod capture;
pub mod config;
mod diagnostics;
pub mod gateway;
pub mod keyboard;
mod tray;
mod window;

use std::path::PathBuf;
use std::sync::{Mutex,atomic::{AtomicBool,AtomicU64,Ordering}};
use serde_json::{json,Value};
use tauri::{AppHandle,Manager};

pub struct QuickState {
    pub config:Mutex<config::Config>,
    pub port:u16,
    pub root:PathBuf,
    pub static_root:PathBuf,
    pub ready:AtomicBool,
    pub visible:AtomicBool,
    pub quitting:AtomicBool,
    pub generation:AtomicU64,
    pub capturing:AtomicBool,
    pub regions:Mutex<Vec<Value>>,
    pub presentation:Mutex<Value>,
    pub listener:Mutex<Option<keyboard::Listener>>,
    pub input_error:Mutex<String>,
    pub config_lock:Mutex<()>,
    pub windows:Mutex<Vec<capture::windows::WindowInfo>>,
}

pub fn register(builder:tauri::Builder<tauri::Wry>)->tauri::Builder<tauri::Wry> {
    builder.register_asynchronous_uri_scheme_protocol("astrion-quick",|context,request,responder|{
        let app=context.app_handle().clone();let label=context.webview_label().to_owned();
        std::thread::spawn(move || {
            let result=(|| {
                let state=app.try_state::<QuickState>().ok_or("Quick Chat is not initialized")?;
                if label!="quick" && !label.starts_with("quick-overlay-"){return Err("Untrusted quick sender".into());}
                if request.uri().path()=="/bridge" {
                    if request.method()!=tauri::http::Method::POST || request.body().len()>40*1024*1024{return Err("Invalid bridge request".into());}
                    let value:Value=serde_json::from_slice(request.body()).map_err(|e|e.to_string())?;
                    let data=dispatch(&app,&label,&value)?;
                    return Ok(("application/json",serde_json::to_vec(&json!({"ok":true,"data":data})).unwrap()));
                }
                if request.method()!=tauri::http::Method::GET{return Err("Invalid resource method".into());}
                assets::serve(&state.static_root,request.uri().path())
            })();
            let (status,mime,bytes)=match result {
                Ok((mime,bytes))=>(200,mime,bytes),
                Err(error)=>(400,"application/json",serde_json::to_vec(&json!({"ok":false,"error":error})).unwrap()),
            };
            responder.respond(tauri::http::Response::builder().status(status).header("Content-Type",mime)
                .header("Cache-Control","no-store").body(bytes).unwrap());
        });
    })
}

pub fn start(app:&AppHandle,port:u16,root:PathBuf)->Result<(),String> {
    if app.try_state::<QuickState>().is_some(){return Ok(());}
    let config=config::load()?;
    app.manage(QuickState {config:Mutex::new(config),port,root,static_root:assets::static_root(app)?,
        ready:AtomicBool::new(false),visible:AtomicBool::new(false),quitting:AtomicBool::new(false),generation:AtomicU64::new(0),capturing:AtomicBool::new(false),
        regions:Mutex::new(Vec::new()),presentation:Mutex::new(json!({})),listener:Mutex::new(None),input_error:Mutex::new(String::new()),config_lock:Mutex::new(()),windows:Mutex::new(Vec::new())});
    window::create(app)?;
    tray::create(app)?;
    restart_listener(app);
    window::start_pointer_loop(app);
    Ok(())
}
pub fn enabled(app:&AppHandle)->bool {
    app.try_state::<QuickState>().is_some_and(|s| s.config.lock().unwrap().enabled && !s.quitting.load(Ordering::Acquire))
}
pub fn stop(app:&AppHandle) {
    if let Some(state)=app.try_state::<QuickState>() {
        state.quitting.store(true,Ordering::Release);state.generation.fetch_add(1,Ordering::AcqRel);
        state.listener.lock().unwrap().take();
    }
}
pub fn emit(app:&AppHandle,label:&str,event:&str,data:Value) {
    if let Some(webview)=app.get_webview(label){let _=webview.eval(&format!("window.__astrionQuickEmit?.({},{});",json!(event),data));}
}
pub fn show(app:&AppHandle)->Result<(),String> {
    let state=app.try_state::<QuickState>().ok_or("Quick Chat is not initialized")?;
    if !enabled(app){return Err("Quick Chat is disabled".into());}
    if !state.ready.load(Ordering::Acquire){return Err("Quick Chat page is still loading".into());}
    let handle=app.clone();app.run_on_main_thread(move || {
        if let Err(error)=window::show(&handle){emit(&handle,"quick","capture-error",json!(error));}
    }).map_err(|e|e.to_string())
}
pub fn hide(app:&AppHandle) {
    let Some(state)=app.try_state::<QuickState>() else{return};
    diagnostics::record("hide",json!({"visible":state.visible.load(Ordering::Acquire)}));
    if !state.visible.swap(false,Ordering::AcqRel){return;}
    let ticket=state.generation.fetch_add(1,Ordering::AcqRel)+1;
    capture::close(app);emit(app,"quick","will-hide",Value::Null);
    let handle=app.clone();std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(300));
        if let Some(s)=handle.try_state::<QuickState>(){if s.generation.load(Ordering::Acquire)==ticket && !s.visible.load(Ordering::Acquire){if let Some(w)=handle.get_webview_window("quick"){let _=w.hide();}}}
    });
}
fn restart_listener(app:&AppHandle) {
    let state=app.state::<QuickState>();state.listener.lock().unwrap().take();
    state.input_error.lock().unwrap().clear();
    let config=state.config.lock().unwrap().clone();if !config.enabled{return;}
    let (tx,rx)=std::sync::mpsc::channel();
    match keyboard::install(keyboard::Modifier::parse(&config.modifier).unwrap(),tx){
        Ok(listener)=>{*state.listener.lock().unwrap()=Some(listener);let handle=app.clone();std::thread::spawn(move ||{
            while let Ok(event)=rx.recv() {
                let visible=handle.state::<QuickState>().visible.load(Ordering::Acquire);
                match event {
                    keyboard::KeyEvent::Toggle=>{if visible{hide(&handle);}else{let _=show(&handle);}},
                    keyboard::KeyEvent::Escape=>{
                        diagnostics::record("native-escape",json!({"visible":visible}));
                        // A focused HWND does not imply keyboard focus inside WebView2.
                        // Let the renderer inspect its actual DOM focus before handling Esc.
                        if visible {emit(&handle,"quick","native-escape",Value::Null);}
                    },
                }
            }
        });},
        Err(error)=>*state.input_error.lock().unwrap()=error,
    }
}
pub fn settings(app:&AppHandle,op:&str,patch:Option<&Value>)->Result<Value,String> {
    let state=app.try_state::<QuickState>().ok_or("Quick Chat is not initialized")?;
    match op {
        "info"=>Ok(serde_json::to_value(state.config.lock().unwrap().clone()).unwrap()),
        "configure"=>{
            let _guard=state.config_lock.lock().unwrap();
            let next=config::patched(&state.config.lock().unwrap(),patch.ok_or("Missing configuration")?)?;
            config::save(&next)?;*state.config.lock().unwrap()=next.clone();restart_listener(app);
            if !next.enabled{hide(app);}Ok(serde_json::to_value(next).unwrap())
        },
        "permissions"=>{
            let screen=std::thread::spawn(|| {unsafe{let _=windows::Win32::System::WinRT::RoInitialize(windows::Win32::System::WinRT::RO_INIT_MULTITHREADED);}let result=windows::Graphics::Capture::GraphicsCaptureSession::IsSupported();unsafe{windows::Win32::System::WinRT::RoUninitialize();}result.unwrap_or(false)}).join().unwrap_or(false);
            let error=state.input_error.lock().unwrap().clone();
            Ok(json!({"screenPermission":if screen{"granted"}else{"denied"},"inputPermission":if error.is_empty(){"granted"}else{"denied"},"error":error,"platform":"windows"}))
        },
        "capture-permission"=>Ok(json!("granted")),
        "input-permission"=>{restart_listener(app);Ok(json!(if state.input_error.lock().unwrap().is_empty(){"granted"}else{"denied"}))},
        "open"=>{show(app)?;Ok(Value::Null)},_=>Err("Unsupported quick settings operation".into()),
    }
}
fn dispatch(app:&AppHandle,label:&str,args:&Value)->Result<Value,String> {
    let op=args["op"].as_str().ok_or("Missing bridge operation")?;
    let state=app.state::<QuickState>();
    if label!="quick" {
        return match op {
            "hide"=>{hide(app);Ok(Value::Null)},
            "selection"|"window-selection"=>{capture::submit(app,label,op,args)?;Ok(Value::Null)},
            _=>Err("Overlay operation is not allowed".into()),
        };
    }
    match op {
        "request"=>gateway::request(state.port,&state.root,args["route"].as_str().ok_or("Invalid route")?,args["method"].as_str().unwrap_or("GET"),args.get("body").filter(|b|!b.is_null()),args["workspace"].as_str().unwrap_or("")),
        "info"|"configure"|"capture-permission"=>settings(app,op,args.get("patch")),
        "ready"=>{state.ready.store(true,Ordering::Release);Ok(Value::Null)},
        "diagnostic"=>{diagnostics::record("renderer",args["data"].clone());Ok(Value::Null)},
        "hide"=>{hide(app);Ok(Value::Null)},
        "hidden"=>{if !state.visible.load(Ordering::Acquire){if let Some(w)=app.get_webview_window("quick"){let _=w.hide();}}Ok(Value::Null)},
        "layout"=>{
            let regions=args["regions"].as_array().ok_or("Invalid layout")?.iter().take(64).filter(|r|["x","y","width","height"].iter().all(|k|r[*k].as_f64().is_some_and(f64::is_finite))).cloned().collect();
            *state.regions.lock().unwrap()=regions;
            if let Some(p)=args.get("presentation"){*state.presentation.lock().unwrap()=p.clone();}
            capture::update(app);Ok(Value::Null)
        },_=>Err("Unsupported quick operation".into()),
    }
}
