//! Standalone native experiments, with synthetic identity and isolated profiles.
mod fixture;
#[path = "../../../../desktop/src-tauri/src/quick/capture/wgc.rs"]
mod wgc;
use serde_json::{json, Value};
use std::{cell::RefCell, path::PathBuf, sync::{Arc, Mutex}, time::Duration};
use tauri::{Manager, WebviewBuilder, WebviewUrl, WebviewWindowBuilder};
use webview2_com::{Microsoft::Web::WebView2::Win32::*, *};
use windows::{core::{Interface, HSTRING, PWSTR}, Win32::Foundation::RECT};

thread_local! {
    static POPUPS: RefCell<Vec<ICoreWebView2Controller>> = const { RefCell::new(Vec::new()) };
}
type Report = Arc<Mutex<Value>>;
fn record(report: &Report, key: &str, value: Value) {
    report.lock().unwrap()[key] = value;
}
unsafe fn text(get: impl FnOnce(*mut PWSTR) -> windows::core::Result<()>) -> windows::core::Result<String> {
    let mut pointer = PWSTR::null();
    get(&mut pointer)?;
    Ok(take_pwstr(pointer))
}
fn observe(wc: &ICoreWebView2, role: String, report: Report) -> windows::core::Result<()> {
    let mut token = 0;
    unsafe { wc.add_WebMessageReceived(&WebMessageReceivedEventHandler::create(Box::new(move |_, args| {
        let args = args.unwrap();
        let message = text(|out| args.TryGetWebMessageAsString(out))?;
        let source = text(|out| args.Source(out))?;
        let value: Value = serde_json::from_str(&message).unwrap_or(json!(message));
        let key = format!("{}:{}", role, value["kind"].as_str().unwrap_or("message"));
        record(&report, &key, json!({"source": source, "value": value}));
        Ok(())
    })), &mut token)?; }
    Ok(())
}
fn main() {
    let root = std::env::var_os("ASTRION_BROWSER_PROBE_ROOT").map(PathBuf::from).expect("explicit isolated root required");
    std::fs::create_dir_all(&root).unwrap();
    let write = std::env::args().any(|arg| arg == "--write");
    let port = if write {0} else {
        let previous: Value = serde_json::from_slice(&std::fs::read(root.join("write.json")).unwrap()).unwrap();
        previous["origin"].as_str().unwrap().rsplit(':').next().unwrap().parse().unwrap()
    };
    let (origin, stopping) = fixture::start(write, port);
    let report = Arc::new(Mutex::new(json!({"phase": if write {"write"} else {"read"}, "origin":origin, "root":root})));
    let setup_report = report.clone();
    let output = root.join(if write {"write.json"} else {"read.json"});
    tauri::Builder::default().setup(move |app| {
        let main = WebviewWindowBuilder::new(app, "probe", WebviewUrl::External(format!("{origin}/main").parse()?))
            .data_directory(root.join("main"))
            .title("Astrion isolated WebView2 experiment").inner_size(1000.0, 700.0).build()?;
        let host = app.get_window("probe").unwrap();
        let main_wv = app.get_webview("probe").unwrap();
        main_wv.set_auto_resize(false)?;
        main_wv.set_bounds(tauri::Rect {position:tauri::LogicalPosition::new(0.,0.).into(),size:tauri::LogicalSize::new(1000.,700.).into()})?;
        let parent = main.hwnd()?.0 as isize;
        let main_report = setup_report.clone();
        main_wv.with_webview(move |native| unsafe {
            let wc = native.controller().CoreWebView2().unwrap();
            observe(&wc, "main".into(), main_report.clone()).unwrap();
            let mut version = PWSTR::null();
            native.environment().BrowserVersionString(&mut version).unwrap();
            record(&main_report, "runtime", json!(take_pwstr(version)));
        })?;
        for (role, x, y, width, height) in [("page-a",500.,46.,500.,350.),("page-b",500.,400.,500.,250.)] {
            let child = host.add_child(WebviewBuilder::new(role, WebviewUrl::External("about:blank".parse()?))
                .data_directory(root.join(role)).disable_drag_drop_handler(), tauri::LogicalPosition::new(x,y),tauri::LogicalSize::new(width,height))?;
            child.set_auto_resize(false)?;
            let page_report = setup_report.clone();
            let page_url = format!("{origin}/{role}");
            let role = role.to_string();
            child.with_webview(move |native| unsafe {
                let controller = native.controller();
                let wc = controller.CoreWebView2().unwrap();
                observe(&wc, role.clone(), page_report.clone()).unwrap();
                record(&page_report, &format!("{role}:interfaces"), json!({
                    "find":wc.cast::<ICoreWebView2_28>().is_ok(),
                    "profile":wc.cast::<ICoreWebView2_13>().is_ok(),
                    "print":wc.cast::<ICoreWebView2_16>().is_ok(),
                    "contextMenu":wc.cast::<ICoreWebView2_11>().is_ok(),
                    "download":wc.cast::<ICoreWebView2_4>().is_ok(),
                    "findOptions":native.environment().cast::<ICoreWebView2Environment15>().is_ok()
                }));
                controller.SetZoomFactor(1.25).unwrap();
                let mut zoom = 0.; controller.ZoomFactor(&mut zoom).unwrap();
                let mut bounds = RECT::default(); controller.Bounds(&mut bounds).unwrap();
                record(&page_report, &format!("{role}:geometry"), json!({"zoom":zoom,"bounds":[bounds.left,bounds.top,bounds.right,bounds.bottom]}));
                if role == "page-a" {
                    use windows::Win32::Graphics::Gdi::*;
                    use windows::Win32::UI::WindowsAndMessaging::*;
                    let mut wrapper = windows::Win32::Foundation::HWND::default();
                    controller.ParentWindow(&mut wrapper).unwrap();
                    let region = CreateRectRgn(0,0,bounds.right,bounds.bottom);
                    let cut = CreateRectRgn(50,60,250,180);
                    CombineRgn(Some(region),Some(region),Some(cut),RGN_DIFF);
                    let clipped = SetWindowRgn(wrapper,Some(region),true);
                    let _=DeleteObject(cut.into());
                    let _=SetWindowPos(wrapper,Some(HWND_TOP),0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE);
                    record(&page_report,"region:clipped",json!(clipped != 0));
                }
                let environment = native.environment();
                let popup_report = page_report.clone();
                let mut token = 0;
                wc.add_NewWindowRequested(&NewWindowRequestedEventHandler::create(Box::new(move |_, args| {
                    let args = args.unwrap();
                    let deferred = args.GetDeferral()?;
                    let args = args.clone();
                    let report = popup_report.clone();
                    environment.CreateCoreWebView2Controller(windows::Win32::Foundation::HWND(parent as _),
                        &CreateCoreWebView2ControllerCompletedHandler::create(Box::new(move |result, controller| {
                            result?;
                            let controller = controller.unwrap();
                            let popup = controller.CoreWebView2()?;
                            observe(&popup, "popup".into(), report.clone())?;
                            controller.SetBounds(RECT {left:10,top:450,right:480,bottom:650})?;
                            controller.SetIsVisible(true)?;
                            let assigned = args.SetNewWindow(&popup);
                            record(&report,"popup:adopted",json!(assigned.is_ok()));
                            assigned?; args.SetHandled(true)?; deferred.Complete()?;
                            POPUPS.with(|all| all.borrow_mut().push(controller));
                            Ok(())
                        })))?;
                    Ok(())
                })), &mut token).unwrap();
                wc.Navigate(&HSTRING::from(page_url)).unwrap();
            })?;
        }
        let handle = app.handle().clone();
        let final_report = setup_report.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(12));
            match wgc::capture(wgc::Target::Window(parent)) {
                Ok(pixels) => {
                    let cyan = pixels.bgra.chunks_exact(4).filter(|p| p[0]>175 && p[1]>175 && p[2]<60).count();
                    let red = pixels.bgra.chunks_exact(4).filter(|p| p[2]>190 && p[1]<80 && p[0]<90).count();
                    record(&final_report,"region:pixels",json!({"mainVisible":cyan,"pageVisible":red,"width":pixels.width,"height":pixels.height}));
                }
                Err(error) => record(&final_report,"region:error",json!(error))
            }
            let _ = std::fs::write(&output, serde_json::to_vec_pretty(&*final_report.lock().unwrap()).unwrap());
            stopping.store(true, std::sync::atomic::Ordering::Relaxed);
            let exit = handle.clone();
            let _ = handle.run_on_main_thread(move || {
                POPUPS.with(|all| {for controller in all.borrow_mut().drain(..) {let _=unsafe{controller.Close()};}});
                for label in ["page-a","page-b","probe"] { if let Some(view)=exit.get_webview(label) {let _=view.close();} }
                exit.exit(0);
            });
        });
        Ok(())
    }).run(tauri::generate_context!()).expect("native probe");
}
