use super::{manager::Browser, model::Tab, policy, *};
use serde_json::json;
use tauri::Manager;
use windows::{
    core::{w, Interface, HSTRING, PCWSTR},
    Win32::{Foundation::HWND, UI::WindowsAndMessaging::*},
};

pub fn cdp(
    wc: &ICoreWebView2,
    method: &str,
    params: serde_json::Value,
    callback: impl FnOnce(Result<serde_json::Value, String>) + 'static,
) -> Result<(), String> {
    unsafe {
        native_error(wc.CallDevToolsProtocolMethod(
            &HSTRING::from(method),
            &HSTRING::from(params.to_string()),
            &CallDevToolsProtocolMethodCompletedHandler::create(Box::new(move |status, value| {
                let result = status.map_err(|e| e.to_string()).and_then(|_| {
                    serde_json::from_str(&value.to_string()).map_err(|e| e.to_string())
                });
                callback(result);
                Ok(())
            })),
        ))
    }
}
impl Browser {
    pub fn start_environment(&self, key: &str) -> Result<(), String> {
        let path = self.profiles[key].directory.join("webview2");
        let app = self.app.clone();
        let key = key.to_owned();
        unsafe {
            native_error(CreateCoreWebView2EnvironmentWithOptions(
                PCWSTR::null(),
                &HSTRING::from(path.as_path()),
                None,
                &CreateCoreWebView2EnvironmentCompletedHandler::create(Box::new(
                    move |status, environment| {
                        let result = status
                            .map_err(|e| e.to_string())
                            .and_then(|_| environment.ok_or("browser_environment_missing".into()));
                        enqueue(&app, Event::Environment(key, result));
                        Ok(())
                    },
                )),
            ))
        }
    }
    pub fn environment_ready(
        &mut self,
        key: &str,
        result: Result<ICoreWebView2Environment, String>,
    ) {
        match result {
            Ok(environment) => {
                if let Err(error) = self.create_storage(key, &environment) {
                    self.message(error);
                }
                if let Some(profile) = self.profiles.get_mut(key) {
                    profile.environment = Some(environment);
                }
                let ids: Vec<_> = self
                    .tabs
                    .iter()
                    .filter(|t| t.profile == key && t.section.is_none() && t.controller.is_none())
                    .map(|t| t.id.clone())
                    .collect();
                for id in ids {
                    if let Err(error) = self.start_controller(&id) {
                        self.fail_tab(&id, &error);
                    }
                }
            }
            Err(error) => {
                for tab in &mut self.tabs {
                    if tab.profile == key {
                        tab.error = error.clone();
                        tab.pending = false;
                        tab.loading = false;
                    }
                }
                self.log(&error);
            }
        }
        self.layout();
        self.publish();
    }
    pub fn create_tab(&mut self, url: &str) -> Result<String, String> {
        if self.tabs.len() >= 48 {
            return Err("browser_tab_limit".into());
        }
        let url = if policy::internal(url).is_some() {
            url.to_owned()
        } else {
            policy::manual_navigation(url, true)?
        };
        let tab = Tab::new(&self.owner, &self.profile, &url, self.active_id());
        let id = tab.id.clone();
        let internal = tab.section.is_some();
        self.tabs.push(tab);
        self.selected.insert(self.owner.clone(), id.clone());
        if !internal && self.profiles[&self.profile].environment.is_some() {
            if let Err(error) = self.start_controller(&id) {
                self.fail_tab(&id, &error);
            }
        }
        self.persist(&self.owner.clone());
        Ok(id)
    }
    pub fn start_controller(&mut self, id: &str) -> Result<(), String> {
        let index = self
            .tabs
            .iter()
            .position(|tab| tab.id == id)
            .ok_or("unknown_browser_tab")?;
        if self.tabs[index].controller.is_some() || !self.tabs[index].host.is_invalid() {
            return Ok(());
        }
        let parent = self
            .app
            .get_window("main")
            .ok_or("browser_window_missing")?
            .hwnd()
            .map_err(|e| e.to_string())?;
        let host = unsafe {
            native_error(CreateWindowExW(
                WINDOW_EX_STYLE(0),
                w!("STATIC"),
                PCWSTR::null(),
                WS_CHILD | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
                0,
                0,
                1,
                1,
                Some(HWND(parent.0)),
                None,
                None,
                None,
            ))?
        };
        self.tabs[index].host = host;
        self.tabs[index].pending = self.tabs[index].url != "about:blank";
        let environment = self.profiles[&self.tabs[index].profile]
            .environment
            .clone()
            .ok_or("browser_environment_not_ready")?;
        let app = self.app.clone();
        let id = id.to_owned();
        unsafe {
            native_error(environment.CreateCoreWebView2Controller(
                host,
                &CreateCoreWebView2ControllerCompletedHandler::create(Box::new(
                    move |status, controller| {
                        enqueue(
                            &app,
                            Event::Created(
                                id,
                                status.map_err(|e| e.to_string()).and_then(|_| {
                                    controller.ok_or("browser_controller_missing".into())
                                }),
                            ),
                        );
                        Ok(())
                    },
                )),
            ))
        }
    }
    pub fn controller_ready(&mut self, id: &str, result: Result<ICoreWebView2Controller, String>) {
        let Some(index) = self.tabs.iter().position(|tab| tab.id == id) else {
            if let Ok(controller) = result {
                unsafe {
                    let _ = controller.Close();
                }
            }
            return;
        };
        let result = (|| -> Result<(), String> {
            let controller = result?;
            let wc = unsafe { native_error(controller.CoreWebView2())? };
            unsafe {
                native_error(controller.SetIsVisible(false))?;
                let settings = native_error(wc.Settings())?;
                native_error(settings.SetAreDevToolsEnabled(false))?;
                native_error(settings.SetIsStatusBarEnabled(false))?;
                if let Ok(settings) = settings.cast::<ICoreWebView2Settings4>() {
                    native_error(settings.SetIsPasswordAutosaveEnabled(false))?;
                    native_error(settings.SetIsGeneralAutofillEnabled(false))?;
                }
            }
            self.tabs[index].controller = Some(controller.clone());
            self.tabs[index].webview = Some(wc.clone());
            *self.tabs[index].local_target.borrow_mut() =
                policy::local_file(&self.tabs[index].url).then(|| self.tabs[index].url.clone());
            self.attach_hooks(id, &wc, &controller)?;
            if let Some((args, deferral)) = self.popups.remove(id) {
                unsafe {
                    let result = args.SetNewWindow(&wc);
                    let _ = args.SetHandled(true);
                    let _ = deferral.Complete();
                    native_error(result)?;
                }
            } else {
                let url = self.tabs[index].url.clone();
                if url != "about:blank" {
                    *self.tabs[index].local_target.borrow_mut() =
                        policy::local_file(&url).then(|| url.clone());
                    unsafe {
                        native_error(wc.Navigate(&HSTRING::from(url)))?;
                    }
                }
            }
            Ok(())
        })();
        if let Err(error) = result {
            self.fail_tab(id, &error);
        }
        self.layout();
        self.publish();
    }
    pub fn popup(
        &mut self,
        parent: &str,
        args: ICoreWebView2NewWindowRequestedEventArgs,
        deferral: ICoreWebView2Deferral,
    ) {
        let result = (|| -> Result<(), String> {
            let index = self
                .tabs
                .iter()
                .position(|tab| tab.id == parent && !tab.retired)
                .ok_or("unknown_browser_tab")?;
            let ai_active = self.ai.calls.values().any(|call| {
                call.sent
                    && call.owner == self.tabs[index].owner
                    && call.origins.contains_key(parent)
            });
            if (parent != self.active_id() && !ai_active) || self.tabs.len() >= 48 {
                return Err("browser_popup_inactive".into());
            }
            let url = unsafe { native_error(text(|out| args.Uri(out)))? };
            let current = self.tabs[index]
                .webview
                .as_ref()
                .and_then(|wc| unsafe { text(|out| wc.Source(out)).ok() })
                .unwrap_or_default();
            let url = if policy::local_file(&current) {
                policy::manual_navigation(&url, false)?
            } else {
                policy::navigation(&url, false)?
            };
            let environment = self.profiles[&self.tabs[index].profile]
                .environment
                .clone()
                .ok_or("browser_environment_not_ready")?;
            let previous = (self.owner.clone(), self.profile.clone());
            self.owner = self.tabs[index].owner.clone();
            self.profile = self.tabs[index].profile.clone();
            let result = self.create_tab(&url);
            self.owner = previous.0;
            self.profile = previous.1;
            let id = result?;
            // Creation callbacks are queued. Preserve the original WindowProxy by assigning
            // the un-navigated controller in controller_ready, on the exact opener environment.
            self.popups
                .insert(id.clone(), (args.clone(), deferral.clone()));
            let _ = environment;
            Ok(())
        })();
        if let Err(error) = result {
            unsafe {
                let _ = args.SetHandled(true);
                let _ = deferral.Complete();
            }
            self.message(error);
        }
    }
    pub fn fail_tab(&mut self, id: &str, error: &str) {
        if let Some((args, deferral)) = self.popups.remove(id) {
            unsafe {
                let _ = args.SetHandled(true);
                let _ = deferral.Complete();
            }
        }
        if let Some(tab) = self.tabs.iter_mut().find(|tab| tab.id == id) {
            tab.error = error.into();
            tab.pending = false;
            tab.loading = false;
        }
        self.log(error);
    }
    pub fn close_tab(&mut self, id: &str) -> Result<(), String> {
        let index = self
            .tabs
            .iter()
            .position(|tab| tab.id == id)
            .ok_or("unknown_browser_tab")?;
        let owner = self.tabs[index].owner.clone();
        let profile = self.tabs[index].profile.clone();
        let previous = self.tabs[index].previous.clone();
        self.credentials.remove(id);
        self.deny_tab_permissions(id);
        self.reject_page_dialog(id);
        self.ai.subscribed.remove(id);
        if let Some((args, deferral)) = self.popups.remove(id) {
            unsafe {
                let _ = args.SetHandled(true);
                let _ = deferral.Complete();
            }
        }
        let downloading = self.profiles.get(&profile).is_some_and(|p| {
            p.data.downloads.iter().any(|row| {
                row["tabId"] == id
                    && row["id"]
                        .as_str()
                        .is_some_and(|key| self.downloads.contains_key(key))
            })
        });
        if downloading {
            self.tabs[index].retired = true;
        } else {
            self.tabs.remove(index);
        }
        if self
            .selected
            .get(&owner)
            .is_some_and(|selected| selected == id)
        {
            let next = self
                .tabs
                .iter()
                .find(|tab| tab.id == previous && tab.owner == owner && !tab.retired)
                .or_else(|| {
                    self.tabs
                        .iter()
                        .filter(|tab| tab.owner == owner && !tab.retired)
                        .last()
                })
                .map(|tab| tab.id.clone());
            if let Some(next) = next {
                self.selected.insert(owner.clone(), next);
            } else {
                self.selected.remove(&owner);
            }
        }
        if self
            .tabs
            .iter()
            .any(|tab| tab.owner == owner && !tab.retired)
        {
            self.persist(&owner);
        } else if let Some(profile) = self.profiles.get_mut(&profile) {
            profile.data.owners.remove(&owner);
            profile.save()?;
        }
        self.layout();
        self.publish();
        Ok(())
    }
    pub fn navigating(&mut self, id: &str, url: &str) {
        if let Some(tab) = self.tabs.iter_mut().find(|t| t.id == id) {
            tab.url = url.into();
            tab.pending = true;
            tab.loading = true;
            tab.error.clear();
            tab.contexts.clear();
            tab.frame.clear();
            tab.document.clear();
        }
        self.credentials.navigating(id);
        self.deny_tab_permissions(id);
        self.layout();
        self.publish();
    }
    pub fn loaded(&mut self, id: &str, success: bool, error: i32) {
        if let Some(tab) = self.tabs.iter_mut().find(|t| t.id == id) {
            tab.pending = false;
            tab.loading = false;
            if success {
                tab.committed = true;
            }
            if !success && error != 14 {
                tab.error = format!("WebView2 navigation failed ({error})");
            }
        }
        self.update(id, success);
        self.layout();
        if success {
            self.cdp_event(id, "installAdapter", &json!({}));
        }
    }
    pub fn update(&mut self, id: &str, record: bool) {
        let Some(index) = self.tabs.iter().position(|tab| tab.id == id) else {
            return;
        };
        let tab = &mut self.tabs[index];
        if let Some(wc) = &tab.webview {
            unsafe {
                if let Ok(title) = text(|out| wc.DocumentTitle(out)) {
                    tab.title = title;
                }
                if !tab.pending {
                    if let Ok(url) = text(|out| wc.Source(out)) {
                        tab.url = url;
                    }
                }
                if tab.find.is_some() {
                    if let Ok(find) = wc.cast::<ICoreWebView2_28>().and_then(|wc| wc.Find()) {
                        let (mut matches, mut active) = (0, 0);
                        let _ = find.MatchCount(&mut matches);
                        let _ = find.ActiveMatchIndex(&mut active);
                        tab.find =
                            Some(json!({"matches":matches,"active":if matches>0{active+1}else{0}}));
                    }
                }
            }
        }
        let owner = tab.owner.clone();
        if let Some(profile) = self.profiles.get_mut(&tab.profile) {
            if record {
                profile.record(&tab.url, &tab.title);
            }
            if let Some(row) = profile
                .data
                .history
                .iter_mut()
                .find(|row| row["url"] == tab.url)
            {
                row["title"] = json!(tab.title);
            }
        }
        self.persist(&owner);
        self.publish();
    }
}
