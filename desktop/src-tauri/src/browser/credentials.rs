use super::{manager::Browser, policy, *};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
#[derive(Default)]
pub struct Credentials {
    pub forms: HashMap<String, Value>,
    pub retired: HashMap<String, Vec<(i64, Value, u64)>>,
    pub prompts: HashMap<String, Timed>,
    pub fills: HashMap<String, Value>,
    pub autofilled: HashSet<String>,
    pub clipboard: Option<(u32, u64)>,
}
pub struct Timed {
    pub value: Value,
    pub until: u64,
}
impl Drop for Credentials {
    fn drop(&mut self) {
        if let Some((sequence, _)) = self.clipboard.take() {
            super::clipboard::clear_if_unchanged(sequence);
        }
    }
}
impl Credentials {
    pub fn reset(&mut self) {
        self.forms.clear();
        self.retired.clear();
        self.prompts.clear();
        self.fills.clear();
        self.autofilled.clear();
    }
    pub fn remove(&mut self, id: &str) {
        self.navigating(id);
        self.retired.remove(id);
        self.prompts.remove(id);
    }
    pub fn navigating(&mut self, id: &str) {
        if let Some(form) = self.forms.remove(id) {
            if let Some(context) = form["context"].as_i64() {
                let retired = self.retired.entry(id.into()).or_default();
                retired.push((context, form, policy::now() + 10000));
                if retired.len() > 4 {
                    retired.remove(0);
                }
            }
        }
        self.fills.remove(id);
        self.autofilled
            .retain(|s| !s.starts_with(&format!("{id}:")));
    }
    pub fn expire(&mut self) -> bool {
        let previous = self.prompts.len();
        self.prompts.retain(|_, p| p.until > policy::now());
        self.retired.retain(|_, rows| {
            rows.retain(|(_, _, until)| *until > policy::now());
            !rows.is_empty()
        });
        if let Some((sequence, until)) = self.clipboard {
            if until <= policy::now() {
                super::clipboard::clear_if_unchanged(sequence);
                self.clipboard = None;
            }
        }
        previous != self.prompts.len()
    }
}
impl Browser {
    pub fn credential_snapshot(&self) -> Value {
        let tab = self.active_id();
        let prompt=self.credentials.prompts.get(&tab).filter(|p|p.value["owner"]==self.owner).map(|p|json!({"id":p.value["id"],"tabId":tab,"origin":p.value["origin"],"username":p.value["username"],"update":p.value["existing"].is_string()}));
        json!({"prompt":prompt,"fill":self.credentials.fills.get(&tab)})
    }
    pub fn credential_request(&mut self, request: &Value) {
        match request["data"]["action"].as_str().unwrap_or("") {
            "sources" | "preview" | "preview-file" | "import" | "cancel-import" => {
                self.import_request(request);
                return;
            }
            _ => {}
        }
        let result = self.credential_command(&request["data"]);
        self.publish();
        self.reply(request, result);
    }
    fn credential_command(&mut self, input: &Value) -> Result<Value, String> {
        if self.imports.running() {
            return Err("browser_import_busy".into());
        }
        let action = input["action"].as_str().ok_or("invalid_password_action")?;
        match action {
            "fill" => {
                let id = input["tabId"].as_str().ok_or("invalid_browser_tab")?;
                let fill = self
                    .credentials
                    .fills
                    .get(id)
                    .filter(|f| f["id"] == input["fillId"])
                    .ok_or("stale_password_form")?;
                if !fill["accounts"]
                    .as_array()
                    .is_some_and(|a| a.iter().any(|r| r["id"] == input["id"]))
                {
                    return Err("stale_password_form".into());
                }
                let form = self
                    .credentials
                    .forms
                    .get(id)
                    .ok_or("stale_password_form")?;
                let account = self.profiles[&self.profile]
                    .vault
                    .get(input["id"].as_str().ok_or("invalid_password_id")?)?;
                self.fill_form(id, form, &account, true)?;
                self.credentials.fills.remove(id);
                Ok(json!({}))
            }
            "dismiss" => {
                let id = self.active_id();
                self.credentials.prompts.remove(&id);
                self.credentials.fills.remove(&id);
                Ok(json!({}))
            }
            "accept" => {
                let id = self.active_id();
                let prompt = &self
                    .credentials
                    .prompts
                    .get(&id)
                    .filter(|p| {
                        p.value["id"] == input["id"]
                            && p.value["owner"] == self.owner
                            && p.until > policy::now()
                    })
                    .ok_or("stale_password_prompt")?
                    .value;
                let vault = &mut self
                    .profiles
                    .get_mut(&self.profile)
                    .ok_or("browser_profile_missing")?
                    .vault;
                let existing = vault
                    .matches(prompt["origin"].as_str().unwrap_or(""))
                    .into_iter()
                    .find(|r| r["username"] == prompt["username"]);
                if existing.as_ref().map(|r| &r["id"])
                    != prompt.get("existing").filter(|v| !v.is_null())
                    || existing.as_ref().map(|r| &r["updatedAt"])
                        != prompt.get("updated").filter(|v| !v.is_null())
                {
                    return Err("browser_password_vault_changed".into());
                }
                vault.save(prompt, prompt["existing"].as_str())?;
                self.credentials.prompts.remove(&id);
                Ok(json!({}))
            }
            "copy" => {
                let row = self.profiles[&self.profile]
                    .vault
                    .get(input["id"].as_str().ok_or("invalid_password_id")?)?;
                use tauri::Manager;
                let owner = self
                    .app
                    .get_window("main")
                    .ok_or("browser_window_missing")?
                    .hwnd()
                    .map_err(|e| e.to_string())?;
                let sequence = super::clipboard::write(
                    row["password"].as_str().ok_or("invalid_password_value")?,
                    windows::Win32::Foundation::HWND(owner.0),
                )?;
                self.credentials.clipboard = Some((sequence, policy::now() + 30000));
                Ok(json!({}))
            }
            _ => self
                .profiles
                .get_mut(&self.profile)
                .ok_or("browser_profile_missing")?
                .vault
                .command(action, input),
        }
    }
}
