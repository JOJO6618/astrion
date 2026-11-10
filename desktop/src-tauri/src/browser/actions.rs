//! Only the trusted main document may issue browser commands.
use super::{manager::Browser, policy, views, *};
use serde_json::{json, Value};
use windows::core::{Interface, HSTRING};

impl Browser {
    pub fn request(&mut self, source: &str, request: &Value) {
        let checked = (|| -> Result<(), String> {
            let current =
                unsafe { native_error(text(|out| self.main.CoreWebView2()?.Source(out)))? };
            if !policy::trusted(source, &current, &self.origin) {
                return Err("untrusted_browser_sender".into());
            }
            let document = request["document"]
                .as_str()
                .filter(|id| id.len() <= 80 && !id.is_empty())
                .ok_or("invalid_browser_document")?;
            if request["type"] != "connect"
                && request["type"] != "hello"
                && (document != self.document
                    || self.lease.is_empty()
                    || request["data"]["lease"] != self.lease)
            {
                // Theme is document scoped and never reads profile data.
                if request["type"] != "theme" || document != self.document {
                    return Err("stale_browser_view".into());
                }
            }
            Ok(())
        })();
        if let Err(error) = checked {
            self.reply(request, Err(error));
            return;
        }
        let result = match request["type"].as_str().unwrap_or("") {
            "hello" => {
                let document = request["document"].as_str().unwrap();
                if self.document != document {
                    self.invalidate();
                    self.document = document.to_owned();
                }
                Ok(json!({}))
            }
            "connect" => {
                if let Err(error) = self.connect(request) {
                    self.reply(request, Err(error));
                }
                return;
            }
            "layout" => self.apply_layout(&request["data"]).map(|_| json!({})),
            "theme" => {
                self.apply_theme(&request["data"]);
                self.layout();
                Ok(json!({}))
            }
            "input" => Ok(json!({})), // Region mode receives real native input directly.
            "credentials" => {
                self.credential_request(request);
                return;
            }
            "command" => match self.command(request) {
                Ok(true) => return,
                Ok(false) => Ok(self.snapshot()),
                Err(error) => Err(error),
            },
            _ => Err("unsupported_browser_request".into()),
        };
        self.reply(request, result);
    }
    fn command(&mut self, request: &Value) -> Result<bool, String> {
        let input = &request["data"];
        let action = input["action"].as_str().ok_or("invalid_browser_action")?;
        let id = input["id"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| self.active_id());
        let value = &input["value"];
        let previous = self.active_id();
        for tab in &mut self.tabs {
            if tab.id == previous || tab.id == id {
                tab.last_active = policy::now();
            }
        }
        if !["close", "reorder", "duplicate"].contains(&action) {
            self.resume_tab(&id)?;
        }
        match action {
            "new" => {
                self.create_tab(value.as_str().unwrap_or("about:blank"))?;
            }
            "internal" => {
                let url = format!(
                    "astrion://browser/{}",
                    value.as_str().ok_or("invalid_browser_section")?
                );
                if policy::internal(&url).is_none() {
                    return Err("invalid_browser_section".into());
                }
                if let Some(tab) = self
                    .tabs
                    .iter()
                    .find(|tab| tab.owner == self.owner && tab.url == url && !tab.retired)
                {
                    self.selected.insert(self.owner.clone(), tab.id.clone());
                } else {
                    self.create_tab(&url)?;
                }
            }
            "select" => {
                self.index(&id)?;
                self.selected.insert(self.owner.clone(), id.clone());
                self.persist(&self.owner.clone());
            }
            "close" => {
                self.index(&id)?;
                self.close_tab(&id)?;
            }
            "duplicate" => {
                let url = self.tabs[self.index(&id)?].url.clone();
                self.create_tab(&url)?;
            }
            "navigate" => {
                let target = value.as_str().ok_or("invalid_browser_url")?;
                if policy::internal(target).is_some() {
                    self.create_tab(target)?;
                } else {
                    let url = policy::manual_navigation_with(target, Some(&self.search_engine()))?;
                    if id.is_empty() {
                        self.create_tab(&url)?;
                    } else {
                        let index = self.index(&id)?;
                        if let Some(wc) = self.tabs[index].webview.clone() {
                            *self.tabs[index].local_target.borrow_mut() =
                                policy::local_file(&url).then(|| url.clone());
                            unsafe {
                                native_error(wc.Navigate(&HSTRING::from(url)))?;
                            }
                        } else {
                            self.tabs[index].section = None;
                            self.tabs[index].url = url;
                            self.tabs[index].pending = true;
                            if self.profiles[&self.profile].environment.is_some() {
                                self.start_controller(&id)?;
                            }
                        }
                    }
                }
            }
            "reorder" => {
                let order = value.as_array().ok_or("invalid_browser_tab_order")?;
                let expected: Vec<_> = self
                    .tabs
                    .iter()
                    .filter(|t| t.owner == self.owner && !t.retired)
                    .map(|t| t.id.clone())
                    .collect();
                let ids: Vec<_> = order.iter().filter_map(Value::as_str).collect();
                if ids.len() != expected.len()
                    || expected
                        .iter()
                        .any(|id| ids.iter().filter(|x| **x == id).count() != 1)
                {
                    return Err("invalid_browser_tab_order".into());
                }
                self.tabs.sort_by_key(|tab| {
                    ids.iter()
                        .position(|id| *id == tab.id)
                        .unwrap_or(usize::MAX)
                });
                self.persist(&self.owner.clone());
            }
            "permission" => self.resolve_permission(&id, value == true)?,
            "download-cancel" | "download-reveal" | "download-remove" => {
                self.download_command(action, &id)?
            }
            "search-engine" => {
                let engine = value.as_str().ok_or("invalid_browser_search_engine")?;
                if !policy::SEARCH_ENGINES
                    .iter()
                    .any(|(name, _, _)| *name == engine)
                {
                    return Err("invalid_browser_search_engine".into());
                }
                let p = self
                    .profiles
                    .get_mut(&self.profile)
                    .ok_or("browser_profile_missing")?;
                p.data.settings.search_engine = engine.to_owned();
                p.save()?;
            }
            "shortcuts" => {
                let shortcuts = super::model::Shortcuts::normalize(value, true)?;
                let p = self
                    .profiles
                    .get_mut(&self.profile)
                    .ok_or("browser_profile_missing")?;
                p.data.shortcuts = shortcuts;
                p.save()?;
            }
            "history-remove" => {
                let p = self
                    .profiles
                    .get_mut(&self.profile)
                    .ok_or("browser_profile_missing")?;
                p.data.history.retain(|r| r["id"] != id);
                p.save()?;
            }
            "clear" => {
                self.clear_data(request, value)?;
                return Ok(true);
            }
            _ => {
                let index = self.index(&id)?;
                let wc = self.wc(&id)?;
                match action {
                    "back" => unsafe {
                        native_error(wc.GoBack())?;
                    },
                    "forward" => unsafe {
                        native_error(wc.GoForward())?;
                    },
                    "reload" => {
                        self.tabs[index].error.clear();
                        unsafe {
                            native_error(wc.Reload())?;
                        }
                    }
                    "stop" => {
                        unsafe {
                            native_error(wc.Stop())?;
                        }
                        self.tabs[index].pending = false;
                        self.tabs[index].loading = false;
                    }
                    "zoom" => {
                        let zoom = value
                            .as_f64()
                            .filter(|n| n.is_finite())
                            .ok_or("invalid_browser_zoom")?
                            .clamp(50., 200.)
                            / 100.;
                        unsafe {
                            native_error(
                                self.tabs[index]
                                    .controller
                                    .as_ref()
                                    .ok_or("browser_page_not_ready")?
                                    .SetZoomFactor(zoom),
                            )?;
                        }
                        self.tabs[index].zoom = zoom;
                    }
                    "find" => self.find(&id, value)?,
                    "print" => unsafe {
                        native_error(wc.cast::<ICoreWebView2_16>().and_then(|view| {
                            view.ShowPrintUI(COREWEBVIEW2_PRINT_DIALOG_KIND_BROWSER)
                        }))?;
                    },
                    "capture" => {
                        self.capture(request, &wc)?;
                        return Ok(true);
                    }
                    "device" => {
                        let enabled = !value.is_null();
                        let params = if enabled {
                            let width = value["width"]
                                .as_i64()
                                .ok_or("invalid_browser_device")?
                                .clamp(240, 2560);
                            let height = value["height"]
                                .as_i64()
                                .ok_or("invalid_browser_device")?
                                .clamp(240, 2560);
                            json!({"width":width,"height":height,"deviceScaleFactor":0,"mobile":true})
                        } else {
                            json!({})
                        };
                        self.command_cdp(
                            request,
                            &wc,
                            if enabled {
                                "Emulation.setDeviceMetricsOverride"
                            } else {
                                "Emulation.clearDeviceMetricsOverride"
                            },
                            params,
                        )?;
                        return Ok(true);
                    }
                    "copy" | "paste" | "selectAll" => {
                        self.command_cdp(request,&wc,"Runtime.evaluate",json!({"expression":format!("document.execCommand({})",json!(action)),"returnByValue":true}))?;
                        return Ok(true);
                    }
                    _ => return Err("unsupported_browser_action".into()),
                }
            }
        }
        self.layout();
        self.publish();
        Ok(false)
    }
    pub fn command_cdp(
        &self,
        request: &Value,
        wc: &ICoreWebView2,
        method: &str,
        value: Value,
    ) -> Result<(), String> {
        let app = self.app.clone();
        let request = request.clone();
        let lease = self.lease.clone();
        views::cdp(wc, method, value, move |result| {
            enqueue(
                &app,
                Event::Command {
                    request,
                    lease,
                    result: result.and_then(|v| {
                        if v.get("exceptionDetails").is_some() {
                            Err("browser_page_command_failed".into())
                        } else {
                            Ok(())
                        }
                    }),
                },
            )
        })
    }
    fn clear_data(&mut self, request: &Value, value: &Value) -> Result<(), String> {
        if !["history", "cookies", "cache"]
            .iter()
            .all(|key| value[key].is_boolean())
        {
            return Err("invalid_browser_clear_options".into());
        }
        if self.imports.running() {
            return Err("browser_import_busy".into());
        }
        let mut kinds = COREWEBVIEW2_BROWSING_DATA_KINDS(0);
        if value["cookies"] == true {
            kinds = COREWEBVIEW2_BROWSING_DATA_KINDS(
                kinds.0
                    | COREWEBVIEW2_BROWSING_DATA_KINDS_COOKIES.0
                    | COREWEBVIEW2_BROWSING_DATA_KINDS_ALL_DOM_STORAGE.0,
            );
        }
        if value["cache"] == true {
            kinds = COREWEBVIEW2_BROWSING_DATA_KINDS(
                kinds.0 | COREWEBVIEW2_BROWSING_DATA_KINDS_DISK_CACHE.0,
            );
        }
        if kinds.0 == 0 {
            self.clear_finished(request, &self.lease.clone(), Ok(()));
            return Ok(());
        }
        let wc = self.storage_view()?;
        let profile = unsafe {
            native_error(
                wc.cast::<ICoreWebView2_13>()
                    .and_then(|wc| wc.Profile())
                    .and_then(|p| p.cast::<ICoreWebView2Profile2>()),
            )?
        };
        let app = self.app.clone();
        let request = request.clone();
        let lease = self.lease.clone();
        unsafe {
            native_error(profile.ClearBrowsingData(
                kinds,
                &ClearBrowsingDataCompletedHandler::create(Box::new(move |status| {
                    enqueue(
                        &app,
                        Event::Clear {
                            request,
                            lease,
                            result: status.map_err(|e| e.to_string()),
                        },
                    );
                    Ok(())
                })),
            ))
        }
    }
    pub fn clear_finished(&mut self, request: &Value, lease: &str, result: Result<(), String>) {
        let result = (|| {
            result?;
            if self.lease != lease {
                return Err("stale_browser_view".into());
            }
            let options = &request["data"]["value"];
            if options["history"] == true {
                let profile = self
                    .profiles
                    .get_mut(&self.profile)
                    .ok_or("browser_profile_missing")?;
                let previous = std::mem::take(&mut profile.data.history);
                if let Err(error) = profile.save() {
                    profile.data.history = previous;
                    return Err(error);
                }
            }
            if options["cookies"] == true {
                self.credentials.reset();
                self.imports.cancel();
                self.cancel_permissions();
                self.grants
                    .retain(|key| !key.starts_with(&format!("{}:", self.profile)));
            }
            Ok(self.snapshot())
        })();
        self.reply(request, result);
        self.publish();
    }
}
