//! Windows native browser. Untrusted pages get raw WebView2 controllers, never Tauri APIs.
mod actions;
mod ai;
mod ai_capture;
mod ai_http;
mod ai_native;
mod ai_script;
mod clipboard;
mod credential_dom;
mod credentials;
mod features;
mod import_commit;
mod imports;
mod layout;
mod manager;
mod model;
mod owner;
mod page_features;
mod password_adapter;
mod policy;
mod retention;
mod storage;
mod vault;
mod view_hooks;
mod views;
use serde_json::Value;
use std::result::Result;
use std::{cell::RefCell, collections::VecDeque, path::PathBuf};
use tauri::{AppHandle, Manager};
use webview2_com::{Microsoft::Web::WebView2::Win32::*, *};
use windows::core::PWSTR;

pub const BRIDGE_SCRIPT: &str = include_str!("main-bridge.js");
pub use ai_http::start as start_ai_service;
thread_local! {
    static STATE:RefCell<Option<manager::Browser>>=const{RefCell::new(None)};
    static QUEUE:RefCell<VecDeque<Event>>=const{RefCell::new(VecDeque::new())};
    static SCHEDULED:RefCell<bool>=const{RefCell::new(false)};
}
pub enum Event {
    AiCall(Value, bool, std::sync::mpsc::SyncSender<Value>),
    AiRuntime(Result<ICoreWebView2Controller, String>),
    AiMessage(Value),
    AiReply(Value, Result<Value, String>),
    AiProtocol(String, String, Value),
    Request(String, Value),
    Invalidate,
    Tick,
    Retention(u64, Result<Value, String>),
    Page(String, u64, Box<Event>),
    ImportCookies(String, Result<Vec<Value>, String>),
    ImportCookieWritten(String, Result<Value, String>),
    ImportVerified(String, Result<Vec<Value>, String>),
    ImportStep,
    Command {
        request: Value,
        lease: String,
        result: Result<(), String>,
    },
    Identity {
        ticket: u64,
        request: Value,
        result: Result<String, String>,
    },
    Environment(String, Result<ICoreWebView2Environment, String>),
    Storage(String, Result<ICoreWebView2Controller, String>),
    Created(String, Result<ICoreWebView2Controller, String>),
    Update(String),
    Navigate(String, String),
    Loaded(String, bool, i32),
    Popup(
        String,
        ICoreWebView2NewWindowRequestedEventArgs,
        ICoreWebView2Deferral,
    ),
    Permission(
        String,
        ICoreWebView2PermissionRequestedEventArgs,
        ICoreWebView2Deferral,
    ),
    Download(
        String,
        ICoreWebView2DownloadStartingEventArgs,
        ICoreWebView2Deferral,
    ),
    DownloadUpdate(String),
    Shortcut(String, String),
    Context(
        String,
        ICoreWebView2ContextMenuRequestedEventArgs,
        ICoreWebView2Deferral,
    ),
    Cdp(String, String, Value),
    Close(String),
    Worker {
        request: Value,
        lease: String,
        profile: String,
        result: Result<Value, String>,
    },
    Clear {
        request: Value,
        lease: String,
        result: Result<(), String>,
    },
}
pub fn enqueue_page(app: &AppHandle, id: &str, generation: u64, event: Event) {
    enqueue(app, Event::Page(id.to_owned(), generation, Box::new(event)));
}
// COM callbacks are STA-only. Queue COM objects locally; only the wakeup crosses threads.
// This also prevents synchronous WebView2 callbacks from recursively borrowing Browser.
pub fn enqueue(app: &AppHandle, event: Event) {
    QUEUE.with(|queue| queue.borrow_mut().push_back(event));
    let wake = SCHEDULED.with(|flag| {
        let mut flag = flag.borrow_mut();
        if *flag {
            false
        } else {
            *flag = true;
            true
        }
    });
    if wake {
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            let _ = app.run_on_main_thread(drain);
        });
    }
}
fn drain() {
    // Yield to the Windows message loop between bounded batches (imports/downloads).
    for _ in 0..32 {
        let event = QUEUE.with(|queue| queue.borrow_mut().pop_front());
        let Some(event) = event else { break };
        STATE.with(|state| {
            if let Some(browser) = state.borrow_mut().as_mut() {
                browser.event(event);
            }
        });
    }
    SCHEDULED.with(|flag| *flag.borrow_mut() = false);
    if QUEUE.with(|queue| !queue.borrow().is_empty()) {
        STATE.with(|state| {
            if let Some(browser) = state.borrow().as_ref() {
                let app = browser.app.clone();
                tauri::async_runtime::spawn(async move {
                    let _ = app.run_on_main_thread(drain);
                });
            }
        });
        SCHEDULED.with(|flag| *flag.borrow_mut() = true);
    }
}
pub fn initialize(
    app: &AppHandle,
    origin: String,
    root: PathBuf,
    python: PathBuf,
    backend: PathBuf,
) -> Result<(), String> {
    let view = app.get_webview("main").ok_or("browser_main_view_missing")?;
    let app = app.clone();
    let clock = app.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
        let handle = clock.clone();
        if clock
            .run_on_main_thread(move || enqueue(&handle, Event::Tick))
            .is_err()
        {
            break;
        }
    });
    view.with_webview(move |native| unsafe {
        let controller = native.controller();
        match manager::Browser::new(
            app.clone(),
            origin,
            root,
            python,
            backend,
            controller.clone(),
        ) {
            Ok(browser) => STATE.with(|state| *state.borrow_mut() = Some(browser)),
            Err(error) => {
                let _ = std::fs::write(
                    std::env::temp_dir().join("astrion-browser-startup.log"),
                    error,
                );
                return;
            }
        }
        STATE.with(|state| {
            if let Some(browser) = state.borrow().as_ref() {
                if let Err(error) = browser.start_ai() {
                    browser.log(&error);
                }
            }
        });
        let wc = match controller.CoreWebView2() {
            Ok(wc) => wc,
            Err(_) => return,
        };
        let mut token = 0;
        let handle = app.clone();
        let _ = wc.add_WebMessageReceived(
            &WebMessageReceivedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else { return Ok(()) };
                let Ok(message) = text(|out| args.TryGetWebMessageAsString(out)) else {
                    return Ok(());
                };
                if message.len() > 32 * 1024 * 1024 {
                    return Ok(());
                }
                if let Ok(value) = serde_json::from_str::<Value>(&message) {
                    let file_import =
                        value["type"] == "credentials" && value["data"]["action"] == "preview-file";
                    if message.len() > 2 * 1024 * 1024 && !file_import {
                        return Ok(());
                    }
                    if value["bridge"] == "astrion-browser-v1" {
                        enqueue(
                            &handle,
                            Event::Request(text(|out| args.Source(out))?, value),
                        );
                    }
                }
                Ok(())
            })),
            &mut token,
        );
        let handle = app.clone();
        let _ = wc.add_NavigationStarting(
            &NavigationStartingEventHandler::create(Box::new(move |_, _| {
                enqueue(&handle, Event::Invalidate);
                Ok(())
            })),
            &mut token,
        );
    })
    .map_err(|e| e.to_string())
}
pub fn resized(app: &AppHandle) {
    let app = app.clone();
    let _ = app.run_on_main_thread(|| {
        STATE.with(|state| {
            if let Some(browser) = state.borrow_mut().as_mut() {
                browser.send("geometry-request", serde_json::json!({}));
                browser.layout();
            }
        })
    });
}
pub fn dispose() {
    STATE.with(|state| {
        if let Some(mut browser) = state.borrow_mut().take() {
            browser.invalidate();
            for (_, (args, deferral)) in browser.popups.drain() {
                unsafe {
                    let _ = args.SetHandled(true);
                    let _ = deferral.Complete();
                }
            }
            for (_, operation) in browser.downloads.drain() {
                unsafe {
                    let _ = operation.Cancel();
                }
            }
        }
    });
    QUEUE.with(|queue| queue.borrow_mut().clear());
}
pub unsafe fn text(
    get: impl FnOnce(*mut PWSTR) -> windows::core::Result<()>,
) -> windows::core::Result<String> {
    let mut value = PWSTR::null();
    get(&mut value)?;
    Ok(take_pwstr(value))
}
pub fn native_error<T>(value: windows::core::Result<T>) -> Result<T, String> {
    value.map_err(|e| format!("browser_native_error: {e}"))
}
