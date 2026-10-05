#[path = "../src/quick/mod.rs"]
mod quick;
use std::sync::atomic::Ordering;
use std::time::{Duration,Instant};
use serde_json::json;
use tauri::Manager;

fn main(){
    let output=std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../cache/windows-quick-smoke");
    std::fs::create_dir_all(&output).unwrap();
    std::env::set_var("ASTRION_QUICK_DIAGNOSTICS", output.join("escape.jsonl"));
    let root=output.join("data");std::fs::create_dir_all(root.join("host/data")).unwrap();
    std::fs::write(root.join("host/data/host_api_token"),"quick-smoke-token").unwrap();
    let settings=output.join("quick-settings.json");
    let mut config=quick::config::Config::default();
    config.enabled=true;
    std::fs::write(&settings,serde_json::to_vec(&config).unwrap()).unwrap();
    std::env::set_var("ASTRION_QUICK_SETTINGS_FILE",settings);
    quick::register(tauri::Builder::default())
        .setup(move |app|{
            quick::start(app.handle(),8321,root.clone()).map_err(std::io::Error::other)?;
            let handle=app.handle().clone();
            std::thread::spawn(move ||{
                let deadline=Instant::now()+Duration::from_secs(15);
                while !handle.state::<quick::QuickState>().ready.load(Ordering::Acquire) && Instant::now()<deadline {std::thread::sleep(Duration::from_millis(100));}
                let result=quick::show(&handle);std::thread::sleep(Duration::from_secs(3));
                let state=handle.state::<quick::QuickState>();
                let report=json!({"ready":state.ready.load(Ordering::Acquire),"show":result,
                    "visible":state.visible.load(Ordering::Acquire),"regions":*state.regions.lock().unwrap(),
                    "input_error":*state.input_error.lock().unwrap(),"window_count":state.windows.lock().unwrap().len(),
                    "overlays":handle.webview_windows().keys().filter(|k|k.starts_with("quick-overlay-")).count(),
                    "url":handle.get_webview("quick").and_then(|w|w.url().ok()).map(|u|u.to_string())});
                std::fs::write(output.join("report.json"),serde_json::to_vec_pretty(&report).unwrap()).unwrap();
                if std::env::args().any(|arg| arg == "--escape-probe") {
                    quick::emit(&handle,"quick","native-escape",serde_json::Value::Null);
                    std::thread::sleep(Duration::from_millis(800));
                    std::fs::write(output.join("escape-probe.json"),serde_json::to_vec_pretty(&json!({"hidden":!state.visible.load(Ordering::Acquire)})).unwrap()).unwrap();
                }
                if std::env::args().any(|arg| arg == "--capture-probe") {
                    let overlay=handle.webview_windows().into_iter().find(|(label,_)|label.starts_with("quick-overlay-")).unwrap();
                    overlay.1.set_focus().unwrap();
                    handle.get_webview(&overlay.0).unwrap().set_focus().unwrap();
                    std::thread::sleep(Duration::from_millis(200));
                    quick::capture::submit(&handle,&overlay.0,"selection",&json!({"rect":{"x":0,"y":0,"width":32,"height":32}})).unwrap();
                }
                if std::env::args().any(|arg| arg == "--interactive") {
                    // Keep the isolated window available for physical keyboard and IME checks.
                    return;
                }
                quick::hide(&handle);std::thread::sleep(Duration::from_millis(450));
                quick::stop(&handle);handle.exit(0);
            });Ok(())
        }).run(tauri::generate_context!()).expect("quick smoke runtime");
}
