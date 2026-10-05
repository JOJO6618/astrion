//! Windows launches share one shell, backend, tray and keyboard listener.
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager};

#[derive(Default)]
struct LaunchIntent {
    open_main: AtomicBool,
}

pub fn register(builder: tauri::Builder<tauri::Wry>) -> tauri::Builder<tauri::Wry> {
    // Must be the first plugin: a second process exits before setup starts a backend.
    builder
        .manage(LaunchIntent::default())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            app.state::<LaunchIntent>().open_main.store(true, Ordering::Release);
            let handle = app.clone();
            let _ = app.run_on_main_thread(move || main_window_ready(&handle));
        }))
}

pub fn main_window_ready(app: &AppHandle) {
    let Some(window) = app.get_window("main") else { return };
    let Some(state) = app.try_state::<LaunchIntent>() else { return };
    if !state.open_main.swap(false, Ordering::AcqRel) {
        return;
    }
    let _ = window.unminimize();
    let _ = window.show();
    let _ = window.set_focus();
    if let Some(webview) = app.get_webview("main") {
        let _ = webview.set_focus();
    }
}
