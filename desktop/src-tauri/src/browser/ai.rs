//! Private scripting host for the exact shared BrowserAI/TabDriver implementation.
use super::{manager::Browser, model::Profile, policy, *};
use serde_json::{json, Value};
use std::{
    collections::{HashMap, HashSet},
    sync::mpsc::SyncSender,
};
use windows::{
    core::{Interface, HSTRING},
    Win32::Foundation::RECT,
};
pub struct Pending {
    pub payload: Value,
    pub owner: String,
    pub profile: String,
    pub response: SyncSender<Value>,
    pub until: u64,
    pub reserved: Option<String>,
    pub prepared: bool,
    pub sent: bool,
    pub origins: HashMap<String, String>,
}
#[derive(Default)]
pub struct AI {
    pub controller: Option<ICoreWebView2Controller>,
    pub ready: bool,
    pub calls: HashMap<String, Pending>,
    pub subscribed: HashSet<String>,
    pub dialogs: HashMap<
        String,
        (
            ICoreWebView2ScriptDialogOpeningEventArgs,
            ICoreWebView2Deferral,
        ),
    >,
    pub dialog_ids: HashMap<String, String>,
    pub download_dirs: HashMap<String, (std::path::PathBuf, u64)>,
}
impl Drop for AI {
    fn drop(&mut self) {
        for (_, call) in self.calls.drain() {
            let _ = call
                .response
                .send(json!({"success":false,"code":"browser_closed","error":"浏览器已关闭。"}));
        }
        for (_, (_, deferral)) in self.dialogs.drain() {
            unsafe {
                let _ = deferral.Complete();
            }
        }
        if let Some(controller) = self.controller.take() {
            unsafe {
                let _ = controller.Close();
            }
        }
    }
}
impl Browser {
    pub fn start_ai(&self) -> Result<(), String> {
        let environment = unsafe {
            native_error((|| -> windows::core::Result<_> {
                self.main
                    .CoreWebView2()?
                    .cast::<ICoreWebView2_2>()?
                    .Environment()
            })())?
        };
        let parent = self
            .app
            .get_window("main")
            .ok_or("browser_window_missing")?
            .hwnd()
            .map_err(|e| e.to_string())?;
        let app = self.app.clone();
        unsafe {
            native_error(environment.CreateCoreWebView2Controller(
                windows::Win32::Foundation::HWND(parent.0),
                &CreateCoreWebView2ControllerCompletedHandler::create(Box::new(
                    move |status, controller| {
                        enqueue(
                            &app,
                            Event::AiRuntime(status.map_err(|e| e.to_string()).and_then(|_| {
                                controller.ok_or("browser_ai_controller_missing".into())
                            })),
                        );
                        Ok(())
                    },
                )),
            ))
        }
    }
    pub fn ai_runtime(&mut self, result: Result<ICoreWebView2Controller, String>) {
        let result = (|| -> Result<(), String> {
            let controller = result?;
            unsafe {
                native_error(controller.SetIsVisible(false))?;
                native_error(controller.SetBounds(RECT {
                    left: 0,
                    top: 0,
                    right: 1280,
                    bottom: 800,
                }))?;
                let wc = native_error(controller.CoreWebView2())?;
                let settings = native_error(wc.Settings())?;
                let _ = settings.SetAreDevToolsEnabled(false);
                let _ = settings.SetAreDefaultContextMenusEnabled(false);
                let mut token = 0;
                let app = self.app.clone();
                native_error(wc.add_WebMessageReceived(
                    &WebMessageReceivedEventHandler::create(Box::new(move |_, args| {
                        if let Some(args) = args {
                            if let Ok(raw) = text(|out| args.TryGetWebMessageAsString(out)) {
                                if raw.len() <= 16 * 1024 * 1024 {
                                    if let Ok(value) = serde_json::from_str(&raw) {
                                        enqueue(&app, Event::AiMessage(value));
                                    }
                                }
                            }
                        }
                        Ok(())
                    })),
                    &mut token,
                ))?;
                native_error(wc.add_NavigationStarting(
                    &NavigationStartingEventHandler::create(Box::new(move |_, args| {
                        if let Some(args) = args {
                            if text(|out| args.Uri(out))? != "about:blank" {
                                args.SetCancel(true)?;
                            }
                        }
                        Ok(())
                    })),
                    &mut token,
                ))?;
                let script = super::ai_script::script();
                native_error(wc.ExecuteScript(
                    &HSTRING::from(script),
                    &ExecuteScriptCompletedHandler::create(Box::new(|_, _| Ok(()))),
                ))?;
            }
            self.ai.controller = Some(controller);
            Ok(())
        })();
        if let Err(error) = result {
            self.log(&format!("browser_ai_startup: {error}"));
        }
    }
    pub fn ai_post(&self, value: Value) {
        if let Some(controller) = &self.ai.controller {
            unsafe {
                if let Ok(wc) = controller.CoreWebView2() {
                    let _ = wc.PostWebMessageAsJson(&HSTRING::from(value.to_string()));
                }
            }
        }
    }
    pub fn ai_call(&mut self, payload: Value, cancel: bool, response: SyncSender<Value>) {
        let id = payload["call_id"].as_str().unwrap_or("").to_owned();
        if cancel {
            let removed = self.ai.calls.remove(&id);
            if let Some(call) = &removed {
                let _ = call
                    .response
                    .send(json!({"success":false,"code":"cancelled","error":"已取消。"}));
            }
            self.ai_post(json!({"type":"cancel","call_id":id}));
            let _ = response.send(json!({"success":true,"cancelled":removed.is_some()}));
            return;
        }
        let result = (|| -> Result<(String, String), String> {
            if id.is_empty()
                || id.len() > 160
                || self.ai.calls.contains_key(&id)
                || self.ai.calls.len() >= 64
            {
                return Err("invalid_browser_call".into());
            }
            let identity = &payload["identity"];
            for key in ["username", "workspace_id", "conversation_id"] {
                let value = identity[key].as_str().ok_or("invalid_browser_identity")?;
                if value.is_empty()
                    || value.chars().count() > 256
                    || value.chars().any(char::is_control)
                {
                    return Err("invalid_browser_identity".into());
                }
            }
            let profile = policy::profile_key(identity["username"].as_str().unwrap());
            let owner = format!(
                "{profile}:{}",
                json!([
                    "agent",
                    identity["workspace_id"],
                    identity["conversation_id"]
                ])
            );
            Ok((owner, profile))
        })();
        let (owner, profile) = match result {
            Ok(value) => value,
            Err(error) => {
                let _ =
                    response.send(json!({"success":false,"code":"invalid_argument","error":error}));
                return;
            }
        };
        self.ai.calls.insert(
            id,
            Pending {
                payload,
                owner,
                profile,
                response,
                until: policy::now() + 140000,
                reserved: None,
                prepared: false,
                sent: false,
                origins: HashMap::new(),
            },
        );
        self.ai_poll();
    }
    pub fn ai_poll(&mut self) {
        let ids: Vec<_> = self.ai.calls.keys().cloned().collect();
        for id in ids {
            let Some(mut call) = self.ai.calls.remove(&id) else {
                continue;
            };
            if call.until <= policy::now() {
                self.ai_post(json!({"type":"cancel","call_id":id}));
                let _ = call.response.send(
                    json!({"success":false,"code":"browser_timeout","error":"浏览器调用超时。"}),
                );
                continue;
            }
            if !call.sent
                && self
                    .ai
                    .calls
                    .values()
                    .any(|other| other.owner == call.owner && other.sent)
            {
                self.ai.calls.insert(id, call);
                continue;
            }
            if !call.prepared {
                let result = (|| -> Result<(), String> {
                    if !self.profiles.contains_key(&call.profile) {
                        self.profiles.insert(
                            call.profile.clone(),
                            Profile::load(&self.root, &call.profile)?,
                        );
                        self.start_environment(&call.profile)?;
                    }
                    let args = &call.payload["args"];
                    let existing = self.tabs.iter().any(|tab| {
                        tab.owner == call.owner && tab.section.is_none() && !tab.retired
                    });
                    let create = (call.payload["op"] == "tabs" && args["action"] == "new")
                        || (call.payload["op"] == "navigate"
                            && args["tab_id"].is_null()
                            && !existing);
                    if create {
                        let previous = (self.owner.clone(), self.profile.clone());
                        self.owner = call.owner.clone();
                        self.profile = call.profile.clone();
                        let result = self.create_tab("about:blank");
                        self.owner = previous.0;
                        self.profile = previous.1;
                        call.reserved = Some(result?);
                    }
                    call.prepared = true;
                    Ok(())
                })();
                if let Err(error) = result {
                    let _ = call
                        .response
                        .send(json!({"success":false,"code":"browser_error","error":error}));
                    continue;
                }
            }
            let ready = self.ai.ready
                && self
                    .tabs
                    .iter()
                    .filter(|tab| tab.owner == call.owner && tab.section.is_none() && !tab.retired)
                    .all(|tab| tab.webview.is_some());
            if ready && !call.sent {
                let rows = self.ai_rows(&call.owner);
                for row in rows.as_array().unwrap() {
                    let tab = row["id"].as_str().unwrap().to_owned();
                    call.origins.insert(
                        tab.clone(),
                        policy::origin(row["url"].as_str().unwrap_or("")).unwrap_or_default(),
                    );
                    if let Some(dir) = call.payload["download_dir"].as_str() {
                        self.ai
                            .download_dirs
                            .insert(tab, (dir.into(), call.until + 5000));
                    }
                }
                call.sent = true;
                let message = json!({"type":"call","payload":call.payload,"owner":call.owner,"profile":call.profile,"rows":rows,"reserved":call.reserved});
                self.ai.calls.insert(id.clone(), call);
                self.ai_post(message);
            } else {
                self.ai.calls.insert(id, call);
            }
        }
    }
    pub fn ai_row(&self, id: &str) -> Value {
        let Some(tab) = self.tabs.iter().find(|tab| tab.id == id) else {
            return Value::Null;
        };
        let snapshot = tab.snapshot();
        let mut visible = windows::core::BOOL::default();
        if let Some(controller) = &tab.controller {
            unsafe {
                let _ = controller.IsVisible(&mut visible);
            }
        }
        json!({"id":tab.id,"owner":tab.owner,"profile":tab.profile,"url":tab.url,"title":tab.title,"loading":tab.loading,"section":tab.section,"zoom":tab.zoom,"visible":visible.as_bool(),"back":snapshot["canGoBack"],"forward":snapshot["canGoForward"]})
    }
    fn ai_rows(&self, owner: &str) -> Value {
        json!(self
            .tabs
            .iter()
            .filter(|tab| tab.owner == owner && !tab.retired && tab.section.is_none())
            .map(|tab| self.ai_row(&tab.id))
            .collect::<Vec<_>>())
    }
    pub fn ai_event(&self, id: &str, method: &str, params: Value) {
        self.ai_post(
            json!({"type":"event","tab":id,"method":method,"params":params,"row":self.ai_row(id)}),
        );
    }
    pub fn ai_message(&mut self, value: Value) {
        match value["type"].as_str().unwrap_or("") {
            "ready" => {
                self.ai.ready = true;
                self.ai_poll();
            }
            "result" => {
                if let Some(call) = self
                    .ai
                    .calls
                    .remove(value["call_id"].as_str().unwrap_or(""))
                {
                    for tab in call.origins.keys() {
                        if let Some((_, until)) = self.ai.download_dirs.get_mut(tab) {
                            *until = policy::now() + 5000;
                        }
                    }
                    let _ = call.response.send(value["value"].clone());
                }
            }
            "activity" => self.send("ai-activity", value["data"].clone()),
            _ => self.ai_native(&value),
        }
    }
}
