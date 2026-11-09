use super::{
    model::{Profile, SavedOwner, Tab},
    policy, *,
};
use serde_json::{json, Value};
use std::{collections::HashMap, path::PathBuf};
use windows::core::{Interface, HSTRING};

pub struct Browser {
    pub app: AppHandle,
    pub origin: String,
    pub root: PathBuf,
    pub python: PathBuf,
    pub main: ICoreWebView2Controller,
    pub profiles: HashMap<String, Profile>,
    pub tabs: Vec<Tab>,
    pub selected: HashMap<String, String>,
    pub profile: String,
    pub owner: String,
    pub lease: String,
    pub document: String,
    pub revision: u64,
    pub layout_sequence: u64,
    pub connect_sequence: u64,
    pub geometry: Value,
    pub surface: String,
    pub labels: Value,
    pub grants: std::collections::HashSet<String>,
    pub download_published: u64,
    pub permissions: HashMap<String, features::Permission>,
    pub downloads: HashMap<String, ICoreWebView2DownloadOperation>,
    pub popups: HashMap<
        String,
        (
            ICoreWebView2NewWindowRequestedEventArgs,
            ICoreWebView2Deferral,
        ),
    >,
    pub credentials: credentials::Credentials,
    pub imports: imports::Imports,
    pub ai: ai::AI,
}
impl Browser {
    pub fn new(
        app: AppHandle,
        origin: String,
        root: PathBuf,
        python: PathBuf,
        _backend: PathBuf,
        main: ICoreWebView2Controller,
    ) -> Result<Self, String> {
        std::fs::create_dir_all(root.join("browser")).map_err(|e| e.to_string())?;
        Ok(Self {
            app,
            origin,
            root,
            python,
            main,
            profiles: HashMap::new(),
            tabs: vec![],
            selected: HashMap::new(),
            profile: String::new(),
            owner: String::new(),
            lease: String::new(),
            document: String::new(),
            revision: 0,
            layout_sequence: 0,
            connect_sequence: 0,
            geometry: json!({}),
            surface: "#faf9f5".into(),
            labels: json!({}),
            grants: Default::default(),
            download_published: 0,
            permissions: HashMap::new(),
            downloads: HashMap::new(),
            popups: HashMap::new(),
            credentials: credentials::Credentials::default(),
            imports: imports::Imports::default(),
            ai: ai::AI::default(),
        })
    }
    pub fn active_id(&self) -> String {
        self.selected.get(&self.owner).cloned().unwrap_or_default()
    }
    pub fn index(&self, id: &str) -> Result<usize, String> {
        self.tabs
            .iter()
            .position(|tab| {
                tab.id == id
                    && tab.owner == self.owner
                    && tab.profile == self.profile
                    && !tab.retired
            })
            .ok_or("unknown_browser_tab".into())
    }
    pub fn wc(&self, id: &str) -> Result<ICoreWebView2, String> {
        self.tabs[self.index(id)?]
            .webview
            .clone()
            .ok_or("browser_page_not_ready".into())
    }
    pub fn snapshot(&mut self) -> Value {
        self.revision += 1;
        let profile = self.profiles.get(&self.profile);
        json!({"lease":self.lease,"revision":self.revision,"activeId":self.active_id(),
            "tabs":self.tabs.iter().filter(|tab|tab.owner==self.owner && !tab.retired).map(Tab::snapshot).collect::<Vec<_>>(),
            "history":profile.map(|p|p.data.history.clone()).unwrap_or_default(),
            "downloads":profile.map(|p|p.data.downloads.iter().map(|row|{let mut row=row.clone();row.as_object_mut().unwrap().remove("path");row}).collect::<Vec<_>>()).unwrap_or_default(),
            "permissions":self.permissions.iter().filter(|(_,p)|p.owner==self.owner).map(|(id,p)|json!({"id":id,"tabId":p.tab,"permission":p.kind,"origin":p.origin})).collect::<Vec<_>>(),
            "credentials":self.credential_snapshot(),"dialog":self.page_dialog_snapshot()})
    }
    pub fn send(&self, event: &str, value: Value) {
        if self.document.is_empty() {
            return;
        }
        let message = json!({"bridge":"astrion-browser-v1","document":self.document,"event":event,"value":value});
        unsafe {
            if let Ok(wc) = self.main.CoreWebView2() {
                let _ = wc.PostWebMessageAsJson(&HSTRING::from(message.to_string()));
            }
        }
    }
    pub fn reply(&self, request: &Value, result: Result<Value, String>) {
        let mut message = json!({"bridge":"astrion-browser-v1","document":request["document"],"id":request["id"]});
        match result {
            Ok(value) => message["value"] = value,
            Err(error) => message["error"] = json!(error),
        };
        unsafe {
            if let Ok(wc) = self.main.CoreWebView2() {
                let _ = wc.PostWebMessageAsJson(&HSTRING::from(message.to_string()));
            }
        }
    }
    pub fn publish(&mut self) {
        if !self.lease.is_empty() {
            let value = self.snapshot();
            self.send("state", value);
        }
    }
    pub fn message(&self, error: impl ToString) {
        self.send(
            "message",
            json!({"lease":self.lease,"message":error.to_string()}),
        );
    }
    pub fn log(&self, error: &str) {
        use std::io::Write;
        if let Ok(mut log) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.root.join("browser/native.log"))
        {
            let _ = writeln!(log, "{} {error}", policy::now());
        }
    }
    pub fn persist(&mut self, owner: &str) {
        let tabs: Vec<_> = self
            .tabs
            .iter()
            .filter(|tab| tab.owner == owner && !tab.retired)
            .collect();
        let Some(tab) = tabs.first() else { return };
        let key = tab.profile.clone();
        if let Some(profile) = self.profiles.get_mut(&key) {
            profile.data.owners.insert(
                owner.into(),
                SavedOwner {
                    urls: tabs.iter().map(|tab| tab.url.clone()).collect(),
                    active: tabs
                        .iter()
                        .position(|tab| Some(&tab.id) == self.selected.get(owner))
                        .unwrap_or(0),
                    at: policy::now(),
                },
            );
            let mut owners: Vec<_> = profile
                .data
                .owners
                .iter()
                .map(|(key, value)| (key.clone(), value.at))
                .collect();
            owners.sort_by_key(|(_, at)| std::cmp::Reverse(*at));
            for (owner, _) in owners.into_iter().skip(100) {
                profile.data.owners.remove(&owner);
            }
            if let Err(error) = profile.save() {
                self.message(error);
            }
        }
    }
    pub fn invalidate(&mut self) {
        self.connect_sequence += 1;
        self.lease.clear();
        self.document.clear();
        self.geometry = json!({});
        self.cancel_permissions();
        self.credentials.reset();
        self.imports.cancel();
        self.layout();
    }
    pub fn connect(&mut self, request: &Value) -> Result<(), String> {
        policy::scope(&request["data"]["scope"])?;
        self.connect_sequence += 1;
        let ticket = self.connect_sequence;
        self.lease.clear();
        self.geometry = json!({});
        self.layout();
        self.cancel_permissions();
        self.credentials.reset();
        self.imports.cancel();
        self.document = request["document"]
            .as_str()
            .ok_or("invalid_browser_document")?
            .to_owned();
        let request = request.clone();
        let origin = self.origin.clone();
        let app = self.app.clone();
        let manager = unsafe {
            native_error((|| -> windows::core::Result<_> {
                self.main
                    .CoreWebView2()?
                    .cast::<ICoreWebView2_2>()?
                    .CookieManager()
            })())?
        };
        unsafe {
            native_error(manager.GetCookies(
                &HSTRING::from(&origin),
                &GetCookiesCompletedHandler::create(Box::new(move |status, cookies| {
                    let cookie_header = (|| -> windows::core::Result<String> {
                        status?;
                        let cookies = cookies.unwrap();
                        let mut count = 0;
                        cookies.Count(&mut count)?;
                        let mut values = vec![];
                        for i in 0..count {
                            let cookie = cookies.GetValueAtIndex(i)?;
                            let name = text(|out| cookie.Name(out))?;
                            let value = text(|out| cookie.Value(out))?;
                            if !name.contains([';', '\r', '\n'])
                                && !value.contains([';', '\r', '\n'])
                            {
                                values.push(format!("{name}={value}"));
                            }
                        }
                        Ok(values.join("; "))
                    })();
                    let app = app.clone();
                    let request = request.clone();
                    let origin = origin.clone();
                    std::thread::spawn(move || {
                        let result = (|| -> Result<String, String> {
                            let cookies = cookie_header.map_err(|e| e.to_string())?;
                            let client = reqwest::blocking::Client::builder()
                                .no_proxy()
                                .timeout(std::time::Duration::from_secs(8))
                                .build()
                                .map_err(|e| e.to_string())?;
                            let data: Value = client
                                .get(format!("{origin}/api/session-status"))
                                .header("Cookie", cookies)
                                .send()
                                .map_err(|e| e.to_string())?
                                .error_for_status()
                                .map_err(|e| e.to_string())?
                                .json()
                                .map_err(|e| e.to_string())?;
                            data["session"]["username"]
                                .as_str()
                                .filter(|user| {
                                    !user.is_empty()
                                        && data["success"] == true
                                        && data["session"]["logged_in"] == true
                                })
                                .map(|s| s.to_lowercase())
                                .ok_or("browser_desktop_login_required".into())
                        })();
                        let handle = app.clone();
                        let _ = app.run_on_main_thread(move || {
                            enqueue(
                                &handle,
                                Event::Identity {
                                    ticket,
                                    request,
                                    result,
                                },
                            )
                        });
                    });
                    Ok(())
                })),
            ))?;
        }
        Ok(())
    }
    fn connected(
        &mut self,
        ticket: u64,
        request: &Value,
        result: Result<String, String>,
    ) -> Result<Value, String> {
        if ticket != self.connect_sequence || request["document"] != self.document {
            return Err("stale_browser_connect".into());
        }
        let key = policy::profile_key(&result?);
        if !self.profiles.contains_key(&key) {
            let profile = Profile::load(&self.root, &key)?;
            self.profiles.insert(key.clone(), profile);
            self.start_environment(&key)?;
        }
        self.adopt_scope(&key, &request["data"]["scope"])?;
        self.profile = key.clone();
        self.owner = format!("{key}:{}", policy::scope(&request["data"]["scope"])?);
        self.lease = policy::id();
        self.layout_sequence = 0;
        self.labels = request["data"]["labels"].clone();
        if !self.tabs.iter().any(|tab| tab.owner == self.owner) {
            let saved = self.profiles[&key]
                .data
                .owners
                .get(&self.owner)
                .cloned()
                .unwrap_or_default();
            for url in saved.urls.iter().take(12) {
                if self.tabs.len() >= 48 {
                    break;
                }
                if policy::internal(url).is_some() || policy::manual_navigation(url, false).is_ok()
                {
                    self.create_tab(url)?;
                }
            }
            let restored: Vec<_> = self
                .tabs
                .iter()
                .filter(|t| t.owner == self.owner)
                .map(|t| t.id.clone())
                .collect();
            if restored.is_empty() {
                self.create_tab("about:blank")?;
            } else {
                self.selected.insert(
                    self.owner.clone(),
                    restored[saved.active.min(restored.len() - 1)].clone(),
                );
            }
        }
        self.layout();
        Ok(self.snapshot())
    }
    pub fn event(&mut self, event: Event) {
        match event {
            Event::AiCall(payload, cancel, response) => self.ai_call(payload, cancel, response),
            Event::AiRuntime(result) => self.ai_runtime(result),
            Event::AiMessage(value) => self.ai_message(value),
            Event::AiReply(id, result) => self.ai_reply(&id, result),
            Event::AiProtocol(id, method, value) => self.ai_event(&id, &method, value),
            Event::AiDialog(id, args, deferral) => self.ai_dialog(id, args, deferral),
            Event::Request(source, value) => self.request(&source, &value),
            Event::Tick => {
                self.expire();
                self.ai_poll();
                self.expire_import();
            }
            Event::ImportCookies(id, result) => self.import_cookies(id, result),
            Event::ImportCookieWritten(id, result) => self.import_cookie_written(id, result),
            Event::ImportVerified(id, result) => self.import_verified(id, result),
            Event::ImportStep => self.import_step(),
            Event::Command {
                request,
                lease,
                result,
            } => {
                let value = if self.lease != lease {
                    Err("stale_browser_view".into())
                } else {
                    result.map(|_| self.snapshot())
                };
                self.reply(&request, value);
            }
            Event::Invalidate => self.invalidate(),
            Event::Identity {
                ticket,
                request,
                result,
            } => {
                let value = self.connected(ticket, &request, result);
                self.reply(&request, value);
            }
            Event::Environment(key, result) => self.environment_ready(&key, result),
            Event::Storage(key, result) => self.storage_ready(&key, result),
            Event::Created(id, result) => self.controller_ready(&id, result),
            Event::Update(id) => self.update(&id, false),
            Event::Navigate(id, url) => self.navigating(&id, &url),
            Event::Loaded(id, success, error) => {
                self.loaded(&id, success, error);
                self.ai_event(&id, "native.loaded", json!({}));
            }
            Event::Popup(id, args, deferral) => self.popup(&id, args, deferral),
            Event::Permission(id, args, deferral) => self.permission(&id, args, deferral),
            Event::Download(id, args, deferral) => self.download(&id, args, deferral),
            Event::DownloadUpdate(id) => self.download_update(&id),
            Event::Shortcut(id, key) => {
                if id == self.active_id() {
                    self.send("shortcut", json!({"lease":self.lease,"key":key}));
                }
            }
            Event::Context(id, args, deferral) => self.context(&id, args, deferral),
            Event::Cdp(id, method, value) => self.cdp_event(&id, &method, &value),
            Event::Close(id) => {
                if let Err(error) = self.close_tab(&id) {
                    self.log(&error);
                }
            }
            Event::Worker {
                request,
                lease,
                profile,
                result,
            } => self.worker_done(request, lease, profile, result),
            Event::Clear {
                request,
                lease,
                result,
            } => {
                self.clear_finished(&request, &lease, result);
            }
        }
    }
}
