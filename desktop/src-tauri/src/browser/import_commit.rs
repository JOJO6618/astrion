use super::{imports::Preview, manager::Browser, policy, *};
use serde_json::{json, Value};
use std::collections::HashSet;
pub struct Commit {
    pub request: Value,
    pub preview: Preview,
    pub report: Value,
    pub cookies: Vec<Value>,
    pub offset: usize,
    pub existing: HashSet<String>,
    pub replace: bool,
    pub waiting: bool,
    pub until: u64,
    pub written: Vec<Value>,
    pub verified: bool,
}
fn identity(cookie: &Value) -> String {
    json!([
        cookie["domain"],
        cookie["name"],
        cookie["path"],
        cookie["partitionKey"]["topLevelSite"],
        cookie["partitionKey"]["hasCrossSiteAncestor"]
    ])
    .to_string()
}
fn parameters(cookie: &Value) -> Value {
    let domain = cookie["domain"].as_str().unwrap_or("");
    let scheme = if cookie["secure"] == true {
        "https"
    } else {
        "http"
    };
    let mut params = json!({"url":format!("{scheme}://{}/",domain.trim_start_matches('.')),
        "name":cookie["name"],"value":cookie["value"],"path":cookie["path"],
        "secure":cookie["secure"],"httpOnly":cookie["httpOnly"]});
    // Omitting domain is required to preserve a host-only Cookie.
    if domain.starts_with('.') {
        params["domain"] = cookie["domain"].clone();
    }
    if let Some(expires) = cookie["expires"].as_f64().filter(|v| *v > 0.) {
        params["expires"] = json!(expires);
    }
    if let Some(same_site) = match cookie["sameSite"].as_i64() {
        Some(0) => Some("None"),
        Some(1) => Some("Lax"),
        Some(2) => Some("Strict"),
        _ => None,
    } {
        params["sameSite"] = json!(same_site);
    }
    if cookie["partitionKey"].is_object() {
        params["partitionKey"] = cookie["partitionKey"].clone();
    }
    params
}
pub fn issue(stat: &mut Value, reason: &str) {
    stat["failed"] = json!(stat["failed"].as_u64().unwrap_or(0) + 1);
    let n = stat["reasons"][reason].as_u64().unwrap_or(0);
    stat["reasons"][reason] = json!(n + 1);
}
impl Browser {
    pub fn begin_import(&mut self, request: &Value) -> Result<(), String> {
        let input = &request["data"]["value"];
        let preview = self
            .imports
            .preview
            .as_ref()
            .filter(|p| {
                p.id == input["id"]
                    && p.lease == self.lease
                    && p.profile == self.profile
                    && p.until > policy::now()
            })
            .ok_or("stale_browser_import")?;
        if preview.vault != self.profiles[&self.profile].vault.list() {
            return Err("browser_password_vault_changed".into());
        }
        if !input["choices"].is_object() || !input["replaceCookies"].is_boolean() {
            return Err("invalid_import_choices".into());
        }
        let groups = preview.value["passwords"]
            .as_array()
            .ok_or("invalid_import_data")?;
        let mut chosen = vec![];
        for group in groups {
            let candidates = group["candidates"]
                .as_array()
                .ok_or("invalid_import_data")?;
            let choice = &input["choices"][group["id"].as_str().unwrap_or("")];
            if choice == "keep" && group["existingId"].is_string() {
                continue;
            }
            let selected =
                if candidates.len() == 1 && !group["existingId"].is_string() && choice.is_null() {
                    candidates.first()
                } else {
                    candidates.iter().find(|c| c["id"] == *choice)
                }
                .ok_or("invalid_import_password_choice")?;
            let mut selected = selected.clone();
            selected["existingId"] = group["existingId"].clone();
            chosen.push(selected);
        }
        let cookies = preview.value["cookies"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let wc = self.storage_view().ok();
        if !cookies.is_empty() && wc.is_none() {
            self.create_tab("about:blank")?;
            return Err("browser_cookie_store_initializing_retry".into());
        }
        let preview = self.imports.preview.take().unwrap();
        let mut report = preview.value["stats"].clone();
        for kind in ["passwords", "cookies", "history"] {
            report[kind]["imported"] = json!(0);
        }
        let vault = &mut self.profiles.get_mut(&self.profile).unwrap().vault;
        // One encrypted atomic write for the entire chosen password set.
        let previous = vault.data.clone();
        let mut next = previous.clone();
        for row in &chosen {
            let rows = next["records"]
                .as_array_mut()
                .ok_or("invalid_password_vault")?;
            let record = json!({"id":row["existingId"].as_str().map(str::to_owned).unwrap_or_else(policy::id),"origin":row["origin"],"username":row["username"],"password":row["password"],"updatedAt":policy::now(),"usedAt":0});
            if let Some(old) = rows.iter_mut().find(|r| r["id"] == record["id"]) {
                *old = record;
            } else {
                rows.push(record);
            }
        }
        if !chosen.is_empty() {
            if vault.persist(next).is_ok() {
                report["passwords"]["imported"] = json!(chosen.len());
            } else {
                for _ in &chosen {
                    issue(&mut report["passwords"], "vault_unavailable");
                }
            }
        }
        report["passwords"]["skipped"] = json!(
            report["passwords"]["skipped"].as_u64().unwrap_or(0)
                + preview.value["stats"]["passwords"]["ready"]
                    .as_u64()
                    .unwrap_or(0)
                    .saturating_sub(chosen.len() as u64)
        );
        let p = self.profiles.get_mut(&self.profile).unwrap();
        let previous = p.data.history.clone();
        let mut known: HashSet<_> = previous
            .iter()
            .map(|r| json!([r["url"], r["at"]]).to_string())
            .collect();
        let rows = preview.value["history"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let mut added = 0;
        for row in &rows {
            if known.insert(json!([row["url"], row["at"]]).to_string()) {
                p.data.history.push(row.clone());
                added += 1;
            }
        }
        p.data
            .history
            .sort_by_key(|r| std::cmp::Reverse(r["at"].as_u64().unwrap_or(0)));
        p.data.history.truncate(20000);
        if p.save().is_ok() {
            report["history"]["imported"] = json!(added);
            report["history"]["skipped"] = json!(
                report["history"]["skipped"].as_u64().unwrap_or(0) + rows.len() as u64 - added
            );
        } else {
            p.data.history = previous;
            for _ in 0..added {
                issue(&mut report["history"], "history_write_failed");
            }
        }
        self.imports.commit = Some(Commit {
            request: request.clone(),
            preview,
            report,
            cookies,
            offset: 0,
            existing: HashSet::new(),
            replace: input["replaceCookies"] == true,
            waiting: false,
            until: policy::now() + 150000,
            written: vec![],
            verified: false,
        });
        let id = self.imports.commit.as_ref().unwrap().preview.id.clone();
        if let Some(wc) = wc {
            let app = self.app.clone();
            let callback_id = id.clone();
            let started =
                super::views::cdp(&wc, "Network.getAllCookies", json!({}), move |result| {
                    let result = result.and_then(|v| {
                        v["cookies"]
                            .as_array()
                            .cloned()
                            .ok_or("invalid_cookie_store".into())
                    });
                    enqueue(&app, Event::ImportCookies(callback_id, result));
                });
            if let Err(error) = started {
                enqueue(&self.app, Event::ImportCookies(id, Err(error)));
            }
        } else {
            enqueue(&self.app, Event::ImportCookies(id, Ok(vec![])));
        }
        Ok(())
    }
    pub fn import_cookies(&mut self, id: String, result: Result<Vec<Value>, String>) {
        let Some(commit) = self.imports.commit.as_mut() else {
            return;
        };
        if commit.preview.id != id
            || commit.preview.lease != self.lease
            || commit.preview.profile != self.profile
        {
            return;
        }
        match result {
            Ok(rows) => commit.existing = rows.iter().map(identity).collect(),
            Err(_) => {
                let remain = commit.cookies.len();
                for _ in 0..remain {
                    issue(&mut commit.report["cookies"], "cookie_store_unavailable");
                }
                commit.offset = remain;
            }
        }
        enqueue(&self.app, Event::ImportStep);
    }
    pub fn import_step(&mut self) {
        let Some(mut commit) = self.imports.commit.take() else {
            return;
        };
        if commit.preview.lease != self.lease || commit.preview.profile != self.profile {
            self.reply(&commit.request, Err("stale_browser_import".into()));
            return;
        }
        if commit.waiting {
            self.imports.commit = Some(commit);
            return;
        }
        // Never collapse a partitioned Cookie into the unpartitioned jar.
        while let Some(cookie) = commit.cookies.get(commit.offset) {
            if commit.existing.contains(&identity(cookie)) && !commit.replace {
                skipped(&mut commit.report["cookies"], "duplicate");
                commit.offset += 1;
            } else if cookie["expires"]
                .as_f64()
                .is_some_and(|t| t >= 0. && t * 1000. <= policy::now() as f64)
            {
                skipped(&mut commit.report["cookies"], "expired");
                commit.offset += 1;
            } else {
                break;
            }
        }
        if let Some(cookie) = commit.cookies.get(commit.offset) {
            let params = parameters(cookie);
            let id = commit.preview.id.clone();
            let app = self.app.clone();
            let started = self.storage_view().and_then(|wc| {
                let callback_id = id.clone();
                super::views::cdp(&wc, "Network.setCookie", params, move |result| {
                    enqueue(&app, Event::ImportCookieWritten(callback_id, result));
                })
            });
            commit.waiting = true;
            self.imports.commit = Some(commit);
            if let Err(error) = started {
                enqueue(&self.app, Event::ImportCookieWritten(id, Err(error)));
            }
        } else if !commit.verified && !commit.written.is_empty() {
            let id = commit.preview.id.clone();
            let callback_id = id.clone();
            let app = self.app.clone();
            let started = self.storage_view().and_then(|wc| {
                super::views::cdp(&wc, "Network.getAllCookies", json!({}), move |result| {
                    let rows = result.and_then(|v| {
                        v["cookies"]
                            .as_array()
                            .cloned()
                            .ok_or("invalid_cookie_store".into())
                    });
                    enqueue(&app, Event::ImportVerified(callback_id, rows));
                })
            });
            commit.waiting = true;
            self.imports.commit = Some(commit);
            if let Err(error) = started {
                enqueue(&self.app, Event::ImportVerified(id, Err(error)));
            }
        } else {
            self.reply(
                &commit.request,
                Ok(json!({"report":{"stats":commit.report}})),
            );
            self.publish();
        }
    }
    pub fn import_cookie_written(&mut self, id: String, result: Result<Value, String>) {
        let Some(commit) = self.imports.commit.as_mut() else {
            return;
        };
        if commit.preview.id != id
            || commit.preview.lease != self.lease
            || commit.preview.profile != self.profile
            || !commit.waiting
        {
            return;
        }
        let cookie = &commit.cookies[commit.offset];
        if result.as_ref().is_ok_and(|v| v["success"] == true) {
            commit.existing.insert(identity(cookie));
            commit.written.push(cookie.clone());
            let stat = &mut commit.report["cookies"];
            stat["imported"] = json!(stat["imported"].as_u64().unwrap_or(0) + 1);
        } else {
            issue(
                &mut commit.report["cookies"],
                if cookie["partitionKey"].is_object() {
                    "cookie_partition_write_failed"
                } else {
                    "cookie_rejected"
                },
            );
        }
        commit.offset += 1;
        commit.waiting = false;
        enqueue(&self.app, Event::ImportStep);
    }
    pub fn import_verified(&mut self, id: String, result: Result<Vec<Value>, String>) {
        let Some(commit) = self.imports.commit.as_mut() else {
            return;
        };
        if commit.preview.id != id
            || commit.preview.lease != self.lease
            || commit.preview.profile != self.profile
        {
            return;
        }
        for expected in &commit.written {
            let matched = result.as_ref().is_ok_and(|rows| {
                rows.iter().any(|row| {
                    identity(row) == identity(expected)
                        && row["value"] == expected["value"]
                        && row["secure"] == expected["secure"]
                        && row["httpOnly"] == expected["httpOnly"]
                        && (expected["expires"].as_f64().unwrap_or(-1.) < 0.)
                            == (row["session"] == true)
                        && match expected["sameSite"].as_i64() {
                            Some(0) => row["sameSite"] == "None",
                            Some(1) => row["sameSite"] == "Lax",
                            Some(2) => row["sameSite"] == "Strict",
                            _ => row["sameSite"].is_null(),
                        }
                })
            });
            if !matched {
                let stat = &mut commit.report["cookies"];
                stat["imported"] = json!(stat["imported"].as_u64().unwrap_or(0).saturating_sub(1));
                issue(stat, "cookie_verify_failed");
            }
        }
        commit.verified = true;
        commit.waiting = false;
        enqueue(&self.app, Event::ImportStep);
    }
    pub fn expire_import(&mut self) {
        if self
            .imports
            .commit
            .as_ref()
            .is_some_and(|c| c.until <= policy::now())
        {
            if let Some(commit) = self.imports.commit.take() {
                self.reply(
                    &commit.request,
                    Err("browser_import_timed_out_partial".into()),
                );
            }
        }
    }
}
fn skipped(stat: &mut Value, reason: &str) {
    stat["skipped"] = json!(stat["skipped"].as_u64().unwrap_or(0) + 1);
    stat["reasons"][reason] = json!(stat["reasons"][reason].as_u64().unwrap_or(0) + 1);
}
