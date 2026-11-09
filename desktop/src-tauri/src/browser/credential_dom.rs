use super::{credentials::Timed, manager::Browser, password_adapter as adapter, policy, views, *};
use serde_json::{json, Value};
impl Browser {
    fn adapter_call(
        &self,
        id: &str,
        document: &str,
        method: &str,
        params: Value,
        next: &str,
    ) -> Result<(), String> {
        let wc = self.wc(id)?;
        let app = self.app.clone();
        let id = id.to_owned();
        let document = document.to_owned();
        let next = next.to_owned();
        views::cdp(&wc, method, params, move |result| {
            let value = match result {
                Ok(value) => json!({"document":document,"result":value}),
                Err(error) => json!({"document":document,"error":error}),
            };
            enqueue(&app, Event::Cdp(id, next, value))
        })
    }
    pub fn cdp_event(&mut self, id: &str, method: &str, value: &Value) {
        if let Err(error) = self.cdp_checked(id, method, value) {
            self.log(&error);
        }
    }
    fn cdp_checked(&mut self, id: &str, method: &str, value: &Value) -> Result<(), String> {
        let index = self.index(id)?;
        if method == "installAdapter" {
            if self.tabs[index].retired {
                return Ok(());
            }
            let document = policy::id();
            self.tabs[index].document = document.clone();
            self.tabs[index].contexts.clear();
            return self.adapter_call(id, &document, "Runtime.enable", json!({}), "runtimeReady");
        }
        if method == "Runtime.bindingCalled" {
            return self.form_signal(id, value);
        }
        let document = self.tabs[index].document.clone();
        if document.is_empty() || value["document"] != document {
            return Ok(());
        }
        let result = &value["result"];
        match method {
            "runtimeReady" => {
                if result.is_null() {
                    return Err("browser_password_runtime_failed".into());
                }
                self.adapter_call(id, &document, "Page.getFrameTree", json!({}), "frameReady")?;
            }
            "bindingReady" => {
                if result.is_null() {
                    return Err("browser_password_binding_failed".into());
                }
                let context = *self.tabs[index]
                    .contexts
                    .iter()
                    .find(|(_, doc)| **doc == document)
                    .ok_or("browser_password_context_missing")?
                    .0;
                self.adapter_call(id,&document,"Runtime.evaluate",json!({"contextId":context,"expression":adapter::script()?,"returnByValue":true}),"adapterReady")?;
            }
            "frameReady" => {
                let frame = result["frameTree"]["frame"]["id"]
                    .as_str()
                    .ok_or("browser_password_top_frame_missing")?;
                self.tabs[index].frame = frame.into();
                self.adapter_call(
                    id,
                    &document,
                    "Page.createIsolatedWorld",
                    json!({"frameId":frame,"worldName":adapter::WORLD,"grantUniveralAccess":false}),
                    "worldReady",
                )?;
            }
            "worldReady" => {
                let context = result["executionContextId"]
                    .as_i64()
                    .ok_or("browser_password_context_missing")?;
                self.tabs[index].contexts.insert(context, document.clone());
                self.adapter_call(
                    id,
                    &document,
                    "Runtime.addBinding",
                    json!({"name":adapter::BINDING,"executionContextId":context}),
                    "bindingReady",
                )?;
            }
            "adapterReady" => {
                if result.is_null() || result.get("exceptionDetails").is_some() {
                    let detail = result["exceptionDetails"]["exception"]["description"]
                        .as_str()
                        .or_else(|| value["error"].as_str())
                        .unwrap_or("unknown");
                    return Err(format!(
                        "browser_password_adapter_failed: {}",
                        detail.chars().take(400).collect::<String>()
                    ));
                }
            }
            _ => {}
        }
        Ok(())
    }
    fn form_signal(&mut self, id: &str, event: &Value) -> Result<(), String> {
        if self.lease.is_empty() || id != self.active_id() || event["name"] != adapter::BINDING {
            return Ok(());
        }
        let index = self.index(id)?;
        let tab = &self.tabs[index];
        let context = event["executionContextId"]
            .as_i64()
            .ok_or("invalid_password_context")?;
        let live_context =
            tab.contexts.get(&context) == Some(&tab.document) && !tab.document.is_empty();
        let input: Value =
            serde_json::from_str(event["payload"].as_str().ok_or("invalid_password_signal")?)
                .map_err(|_| "invalid_password_signal")?;
        let token = |key: &str| {
            input[key]
                .as_str()
                .is_some_and(|s| s.len() == 32 && s.chars().all(|c| c.is_ascii_hexdigit()))
        };
        if !token("documentToken") || !token("formToken") {
            return Ok(());
        }
        let url = input["url"]
            .as_str()
            .filter(|s| s.len() <= 8192)
            .ok_or("invalid_password_url")?;
        let current = unsafe {
            native_error(text(|out| {
                self.wc(id)
                    .map_err(|_| windows::core::Error::from_win32())?
                    .Source(out)
            }))?
        };
        let live =
            live_context && !tab.pending && !tab.retired && tab.error.is_empty() && url == current;
        let retired = input["kind"] == "submitted"
            && self.credentials.retired.get(id).is_some_and(|rows| {
                rows.iter().any(|(ctx, form, until)| {
                    *ctx == context
                        && *until > policy::now()
                        && form["owner"] == self.owner
                        && form["url"] == url
                        && form["documentToken"] == input["documentToken"]
                        && form["formToken"] == input["formToken"]
                })
            });
        if !live && !retired {
            return Ok(());
        }
        let origin = policy::origin(url)?;
        let allow = input["allowFill"] == true
            && input["action"]
                .as_str()
                .and_then(|u| policy::origin(u).ok())
                .as_deref()
                == Some(&origin);
        let mut form = input.clone();
        form["context"] = json!(context);
        form["nativeDocument"] = json!(tab.document);
        form["origin"] = json!(origin);
        form["owner"] = json!(self.owner);
        form["allowFill"] = json!(allow);
        let vault = &self.profiles[&self.profile].vault;
        if input["kind"] == "submitted" {
            if vault.data["settings"]["save"] != true {
                return Ok(());
            }
            let username = input["username"]
                .as_str()
                .filter(|s| s.len() <= 16384)
                .ok_or("invalid_password_username")?;
            let secret = input["password"]
                .as_str()
                .filter(|s| !s.is_empty() && s.len() <= 65536)
                .ok_or("invalid_password_value")?;
            let existing = vault
                .matches(&origin)
                .into_iter()
                .find(|r| r["username"] == username);
            if existing.as_ref().is_some_and(|r| r["password"] == secret) {
                return Ok(());
            }
            let prompt = json!({"id":policy::id(),"tabId":id,"owner":self.owner,"origin":origin,"username":username,"password":secret,"existing":existing.as_ref().map(|r|r["id"].clone()),"updated":existing.as_ref().map(|r|r["updatedAt"].clone())});
            self.credentials.prompts.insert(
                id.into(),
                Timed {
                    value: prompt,
                    until: policy::now() + 120000,
                },
            );
            self.credentials.fills.remove(id);
        } else if input["kind"] == "form" {
            self.credentials.forms.insert(id.into(), form.clone());
            let matches = vault.matches(&origin);
            let identity = format!("{id}:{}:{}", input["documentToken"], input["formToken"]);
            if allow
                && matches.len() == 1
                && vault.data["settings"]["autofill"] == true
                && input["hasPassword"] == false
                && (input["username"] == "" || input["username"] == matches[0]["username"])
                && self.credentials.autofilled.insert(identity.clone())
            {
                self.fill_form(id, &form, &matches[0], false)?;
                self.credentials.fills.remove(id);
            } else if allow
                && !matches.is_empty()
                && input["reason"] == "focused"
                && (matches.len() > 1 || vault.data["settings"]["autofill"] != true)
            {
                self.credentials.fills.insert(id.into(),json!({"id":identity,"tabId":id,"origin":origin,"accounts":matches.iter().map(super::vault::Vault::metadata).collect::<Vec<_>>()}));
            } else {
                self.credentials.fills.remove(id);
            }
        }
        self.publish();
        Ok(())
    }
    pub fn fill_form(
        &self,
        id: &str,
        form: &Value,
        account: &Value,
        replace: bool,
    ) -> Result<(), String> {
        let index = self.index(id)?;
        let tab = &self.tabs[index];
        let context = form["context"].as_i64().ok_or("stale_password_form")?;
        let wc = self.wc(id)?;
        let current = unsafe { native_error(text(|out| wc.Source(out)))? };
        if id != self.active_id()
            || form["owner"] != self.owner
            || form["allowFill"] != true
            || tab.pending
            || form["url"] != current
            || form["origin"] != account["origin"]
            || tab.contexts.get(&context) != Some(&tab.document)
            || form["nativeDocument"] != tab.document
        {
            return Err("stale_password_form".into());
        }
        let mut fill = form.clone();
        fill["username"] = account["username"].clone();
        fill["password"] = account["password"].clone();
        fill["replace"] = json!(replace);
        views::cdp(
            &wc,
            "Runtime.evaluate",
            json!({"contextId":context,"expression":format!("globalThis.__astrionFill?.({fill})"),"returnByValue":true}),
            |_| {},
        )
    }
}
