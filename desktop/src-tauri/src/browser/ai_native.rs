use super::{manager::Browser, policy, views, *};
use serde_json::{json, Value};
use windows::core::HSTRING;

pub(super) fn allowed_file(payload: &Value, input: &str) -> bool {
    let path = policy::manual_navigation(input, false)
        .ok()
        .and_then(|value| tauri::Url::parse(&value).ok())
        .filter(|url| url.scheme() == "file")
        .and_then(|url| url.to_file_path().ok());
    let Some(path) = path else {
        return false;
    };
    payload["file_urls"].as_array().is_some_and(|items| {
        items.iter().any(|item| {
            item.as_str()
                .and_then(|value| tauri::Url::parse(value).ok())
                .filter(|url| url.scheme() == "file")
                .and_then(|url| url.to_file_path().ok())
                .is_some_and(|allowed| allowed == path)
        })
    })
}

const EVENTS: &[&str] = &[
    "Page.frameNavigated",
    "Page.navigatedWithinDocument",
    "Page.javascriptDialogOpening",
    "Page.javascriptDialogClosed",
    "Page.fileChooserOpened",
    "Network.requestWillBeSent",
    "Network.responseReceived",
    "Network.loadingFinished",
    "Network.loadingFailed",
    "Runtime.consoleAPICalled",
    "Input.dragIntercepted",
];
impl Browser {
    pub fn ai_reply(&self, id: &Value, result: Result<Value, String>) {
        let mut value = json!({"type":"reply","id":id});
        match result {
            Ok(result) => value["value"] = result,
            Err(error) => {
                value["error"] = json!(error);
                value["code"] = json!("browser_error");
            }
        }
        self.ai_post(value);
    }
    fn ai_subscribe(&mut self, id: &str, wc: &ICoreWebView2) -> Result<(), String> {
        if self.ai.subscribed.contains(id) {
            return Ok(());
        }
        let generation = self
            .tabs
            .iter()
            .find(|tab| tab.id == id)
            .ok_or("unknown_browser_tab")?
            .generation;
        unsafe {
            let mut token = 0;
            for method in EVENTS {
                let receiver =
                    native_error(wc.GetDevToolsProtocolEventReceiver(&HSTRING::from(*method)))?;
                let app = self.app.clone();
                let tab = id.to_owned();
                let method = method.to_string();
                native_error(receiver.add_DevToolsProtocolEventReceived(
                    &DevToolsProtocolEventReceivedEventHandler::create(Box::new(move |_, args| {
                        if let Some(args) = args {
                            let raw = text(|out| args.ParameterObjectAsJson(out))?;
                            if raw.len() <= 4 * 1024 * 1024 {
                                if let Ok(value) = serde_json::from_str(&raw) {
                                    enqueue_page(
                                        &app,
                                        &tab,
                                        generation,
                                        Event::AiProtocol(tab.clone(), method.clone(), value),
                                    );
                                }
                            }
                        }
                        Ok(())
                    })),
                    &mut token,
                ))?;
            }
            // Script dialogs stay WebView2's native UI (same as Electron on macOS). The model
            // sees them through Page.javascriptDialogOpening and answers with
            // Page.handleJavaScriptDialog, which closes the native dialog.
        }
        self.ai.subscribed.insert(id.to_owned());
        if let Some(controller) = self
            .tabs
            .iter()
            .find(|tab| tab.id == id)
            .and_then(|tab| tab.controller.as_ref())
        {
            let mut visible = windows::core::BOOL::default();
            unsafe {
                let _ = controller.IsVisible(&mut visible);
                if !visible.as_bool() {
                    let _ = controller.SetBounds(windows::Win32::Foundation::RECT {
                        left: 0,
                        top: 0,
                        right: 1280,
                        bottom: 800,
                    });
                }
            }
        }
        Ok(())
    }
    pub fn ai_native(&mut self, request: &Value) {
        let result = self.ai_native_checked(request);
        match result {
            Ok(Some(value)) => self.ai_reply(&request["id"], Ok(value)),
            Ok(None) => {}
            Err(error) => self.ai_reply(&request["id"], Err(error)),
        }
    }
    fn ai_native_checked(&mut self, request: &Value) -> Result<Option<Value>, String> {
        let kind = request["type"].as_str().ok_or("invalid_native_operation")?;
        let id = request["tab"].as_str().ok_or("unknown_browser_tab")?;
        let call_id = request["call_id"]
            .as_str()
            .ok_or("cancelled_browser_call")?;
        let call = self
            .ai
            .calls
            .get(call_id)
            .filter(|call| call.until > policy::now())
            .ok_or("cancelled_browser_call")?;
        let index = self
            .tabs
            .iter()
            .position(|tab| {
                tab.id == id
                    && tab.owner == call.owner
                    && tab.profile == call.profile
                    && !tab.retired
                    && tab.section.is_none()
            })
            .ok_or("browser_owner_mismatch")?;
        if request["owner"] != call.owner {
            return Err("browser_owner_mismatch".into());
        }
        if kind == "close" && self.tabs[index].suspended {
            self.close_tab(id)?;
            return Ok(Some(json!({})));
        }
        let wc = self.tabs[index]
            .webview
            .clone()
            .ok_or("browser_page_not_ready")?;
        let data = &request["data"];
        let op = call.payload["op"].as_str().unwrap_or("");
        let current = unsafe { native_error(text(|out| wc.Source(out)))? };
        // Only the trusted backend can grant local files, separately for each call.
        if current.to_ascii_lowercase().starts_with("file:")
            && !allowed_file(&call.payload, &current)
        {
            return Err("browser_path_not_allowed".into());
        }
        let origin = policy::origin(&current).unwrap_or_default();
        let action = [
            "click",
            "type",
            "fill_form",
            "select_option",
            "press_key",
            "drag",
            "drop",
            "file_upload",
            "evaluate",
            "fill_saved_login",
        ]
        .contains(&op)
            || (op == "handle_dialog" && call.payload["args"]["accept"] == true);
        // The shared controller checks the approved site. Native input additionally pins
        // the actual origin seen at dispatch, closing redirects between approval and input.
        if action
            && call
                .origins
                .get(id)
                .is_some_and(|expected| expected != &origin)
        {
            return Err("site_not_approved: 页面在操作中改变了来源，请重新读取页面后操作。".into());
        }
        match kind {
            "cdp" => {
                let method = data["method"].as_str().ok_or("invalid_cdp_method")?;
                let params = data["params"].clone();
                if method == "DOM.setFileInputFiles" || method == "Input.dispatchDragEvent" {
                    let paths = if method == "DOM.setFileInputFiles" {
                        &params["files"]
                    } else {
                        &params["data"]["files"]
                    };
                    let empty = Vec::new();
                    let paths = if method == "Input.dispatchDragEvent" && paths.is_null() {
                        &empty
                    } else {
                        paths.as_array().ok_or("invalid_browser_paths")?
                    };
                    let readable = call.payload["readable_paths"].as_array();
                    for path in paths {
                        let raw = path.as_str().ok_or("invalid_browser_path")?;
                        if !readable.is_some_and(|items| items.contains(path))
                            || !std::path::Path::new(raw).is_file()
                        {
                            return Err("browser_path_not_allowed".into());
                        }
                    }
                }
                self.ai_subscribe(id, &wc)?;
                let app = self.app.clone();
                let reply = request["id"].clone();
                views::cdp(&wc, method, params, move |result| {
                    enqueue(&app, Event::AiReply(reply, result))
                })?;
                Ok(None)
            }
            "navigate" => {
                unsafe {
                    match data["action"].as_str() {
                        Some("reload") => native_error(wc.Reload())?,
                        Some("back") => native_error(wc.GoBack())?,
                        Some("forward") => native_error(wc.GoForward())?,
                        _ => {
                            // The shared controller already turned keywords into a search URL.
                            let url = policy::manual_navigation(
                                data["url"].as_str().ok_or("invalid_browser_url")?,
                                false,
                            )?;
                            if policy::local_file(&url) {
                                if !allowed_file(&call.payload, &url) {
                                    return Err("browser_path_not_allowed".into());
                                }
                                *self.tabs[index].local_target.borrow_mut() = Some(url.clone());
                            }
                            native_error(wc.Navigate(&HSTRING::from(url)))?;
                        }
                    }
                }
                Ok(Some(json!({})))
            }
            "close" => {
                self.close_tab(id)?;
                self.ai_event(id, "native.closed", json!({}));
                Ok(Some(json!({})))
            }
            "saved-login" => {
                if op != "fill_saved_login" {
                    return Err("browser_password_operation_not_allowed".into());
                }
                let vault = &self.profiles[&self.tabs[index].profile].vault;
                vault.ready()?;
                Ok(Some(json!(vault.matches(&origin))))
            }
            "capture" => {
                let controller = self.tabs[index]
                    .controller
                    .as_ref()
                    .ok_or("browser_controller_missing")?;
                super::ai_capture::capture(&self.app, &wc, controller, request["id"].clone())?;
                Ok(None)
            }
            _ => Err("unsupported_native_operation".into()),
        }
    }
}
