//! Shared backend decides residency; this module owns native release and restoration.
use super::{manager::Browser, policy, *};
use serde_json::{json, Value};
use std::collections::HashSet;
use windows::core::{Interface, HSTRING};
use windows::Win32::{Foundation::HWND, UI::WindowsAndMessaging::DestroyWindow};

#[derive(Default)]
pub struct Retention {
    pub last_poll: u64,
    pub pending: bool,
    pub visits: std::collections::HashMap<String, u64>,
}

impl Browser {
    pub fn retention_tick(&mut self) {
        if self.retention.pending
            || self.profiles.is_empty()
            || policy::now() - self.retention.last_poll < 2000
        {
            return;
        }
        self.retention.last_poll = policy::now();
        self.retention.pending = true;
        let ticket = self.connect_sequence;
        let mut owners = HashSet::new();
        for profile in self.profiles.values() {
            for owner in profile.data.owners.keys() {
                if let Some((_, scope)) = owner.split_once(':') {
                    owners.insert(scope.to_owned());
                }
            }
        }
        let tabs: Vec<_> = self.tabs.iter().filter(|tab| !tab.retired).map(|tab| {
            let scope = tab.owner.split_once(':').map(|(_, scope)| scope).unwrap_or("");
            owners.insert(scope.to_owned());
            json!({"id":tab.id,"owner":scope,"selected":self.selected.get(&tab.owner)==Some(&tab.id),
                "lastActive":tab.last_active,"lastAi":tab.last_ai,
                "busy":self.ai.calls.values().any(|call| call.owner==tab.owner)})
        }).collect();
        let scopes: Vec<Value> = owners
            .iter()
            .filter_map(|scope| serde_json::from_str(scope).ok())
            .collect();
        let visits: std::collections::HashMap<_, _> = self
            .retention
            .visits
            .iter()
            .filter_map(|(owner, time)| {
                owner
                    .split_once(':')
                    .map(|(_, scope)| (scope.to_owned(), *time))
            })
            .collect();
        let state = json!({"owners":scopes,"tabs":tabs,"visits":visits}).to_string();
        let origin = self.origin.clone();
        let app = self.app.clone();
        let result = unsafe {
            (|| -> windows::core::Result<()> {
                let cookies = self
                    .main
                    .CoreWebView2()?
                    .cast::<ICoreWebView2_2>()?
                    .CookieManager()?;
                cookies.GetCookies(
                    &HSTRING::from(&origin.clone()),
                    &GetCookiesCompletedHandler::create(Box::new(move |status, cookies| {
                        let header = (|| -> windows::core::Result<String> {
                            status?;
                            let cookies = cookies.unwrap();
                            let mut count = 0;
                            cookies.Count(&mut count)?;
                            let mut rows = vec![];
                            for i in 0..count {
                                let cookie = cookies.GetValueAtIndex(i)?;
                                let name = text(|out| cookie.Name(out))?;
                                let value = text(|out| cookie.Value(out))?;
                                if !name.contains([';', '\r', '\n'])
                                    && !value.contains([';', '\r', '\n'])
                                {
                                    rows.push(format!("{name}={value}"));
                                }
                            }
                            Ok(rows.join("; "))
                        })();
                        let app = app.clone();
                        let origin = origin.clone();
                        let state = state.clone();
                        std::thread::spawn(move || {
                            let result = (|| -> Result<Value, String> {
                                let header = header.map_err(|e| e.to_string())?;
                                reqwest::blocking::Client::builder()
                                    .no_proxy()
                                    .timeout(std::time::Duration::from_secs(5))
                                    .build()
                                    .map_err(|e| e.to_string())?
                                    .get(format!("{origin}/api/browser-retention"))
                                    .header("Cookie", header)
                                    .header("X-Astrion-Browser-State", state)
                                    .send()
                                    .map_err(|e| e.to_string())?
                                    .error_for_status()
                                    .map_err(|e| e.to_string())?
                                    .json()
                                    .map_err(|e| e.to_string())
                            })();
                            let handle = app.clone();
                            let _ = app.run_on_main_thread(move || {
                                enqueue(&handle, Event::Retention(ticket, result))
                            });
                        });
                        Ok(())
                    })),
                )
            })()
        };
        if result.is_err() {
            self.retention.pending = false;
        }
    }

    pub fn retention_result(&mut self, ticket: u64, result: Result<Value, String>) {
        self.retention.pending = false;
        if ticket != self.connect_sequence {
            return;
        }
        let Ok(value) = result else {
            return;
        };
        if value["success"] != true {
            return;
        }
        let Some(username) = value["username"].as_str() else {
            return;
        };
        let profile = policy::profile_key(username);
        for scope in value["deleted"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            let owner = format!("{profile}:{scope}");
            let calls: Vec<_> = self
                .ai
                .calls
                .iter()
                .filter(|(_, call)| call.owner == owner)
                .map(|(id, _)| id.clone())
                .collect();
            for id in calls {
                self.ai_post(json!({"type":"cancel","call_id":id}));
                if let Some(call) = self.ai.calls.remove(&id) {
                    let _=call.response.send(json!({"success":false,"code":"conversation_deleted","error":"对话已删除。"}));
                }
            }
            let ids: Vec<_> = self
                .tabs
                .iter()
                .filter(|tab| tab.owner == owner)
                .map(|tab| tab.id.clone())
                .collect();
            for id in ids {
                self.suspend_tab(&id);
                self.tabs.retain(|tab| tab.id != id);
            }
            self.selected.remove(&owner);
            if let Some(data) = self.profiles.get_mut(&profile) {
                data.data.owners.remove(&owner);
                let _ = data.save();
            }
        }
        for id in value["release"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            let Some(tab) = self
                .tabs
                .iter()
                .find(|tab| tab.id == id && tab.profile == profile)
            else {
                continue;
            };
            if self.ai.calls.values().any(|call| call.owner == tab.owner) {
                continue;
            }
            // A command that arrived after the request takes precedence over that response.
            if tab.last_active > self.retention.last_poll || tab.last_ai > self.retention.last_poll
            {
                continue;
            }
            self.suspend_tab(id);
        }
        self.layout();
        self.publish();
    }

    pub fn suspend_tab(&mut self, id: &str) {
        let Some(index) = self.tabs.iter().position(|tab| tab.id == id) else {
            return;
        };
        if self.tabs[index].section.is_some() || self.tabs[index].suspended {
            return;
        }
        self.credentials.remove(id);
        self.deny_tab_permissions(id);
        self.ai.subscribed.remove(id);
        self.ai_event(id, "native.closed", json!({}));
        if let Some((args, deferral)) = self.popups.remove(id) {
            unsafe {
                let _ = args.SetHandled(true);
                let _ = deferral.Complete();
            }
        }
        let tab = &mut self.tabs[index];
        tab.generation += 1;
        tab.suspended = true;
        tab.pending = false;
        tab.loading = false;
        tab.find = None;
        tab.document.clear();
        tab.contexts.clear();
        tab.frame.clear();
        tab.committed = false;
        tab.webview = None;
        unsafe {
            if let Some(controller) = tab.controller.take() {
                let _ = controller.Close();
            }
            if !tab.host.is_invalid() {
                let _ = DestroyWindow(tab.host);
                tab.host = HWND::default();
            }
        }
    }

    pub fn resume_tab(&mut self, id: &str) -> Result<(), String> {
        let Some(index) = self.tabs.iter().position(|tab| tab.id == id) else {
            return Ok(());
        };
        if !self.tabs[index].suspended {
            return Ok(());
        }
        self.tabs[index].suspended = false;
        self.tabs[index].restored = true;
        self.tabs[index].last_active = policy::now();
        if self.profiles[&self.tabs[index].profile]
            .environment
            .is_some()
        {
            self.start_controller(id)?;
        }
        Ok(())
    }
}
