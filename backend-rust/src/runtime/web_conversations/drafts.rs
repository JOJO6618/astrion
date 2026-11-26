use super::super::store::{read_json, require_directory_if_present, write_atomically};
use super::super::{Runtime, RuntimeError, Store, iso_time, legacy_error, now};
use crate::{legacy::LegacyDocument, workspace_catalog::RegisteredWorkspace};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
};

fn digest(value: &str) -> String {
    ring::digest::digest(&ring::digest::SHA256, value.as_bytes())
        .as_ref()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub(crate) fn host_draft_owner() -> String {
    digest("host")[..24].to_owned()
}

struct DraftPath {
    root: PathBuf,
    path: PathBuf,
    scope: String,
}

impl DraftPath {
    fn resolve(
        store: &Store,
        workspace: &RegisteredWorkspace,
        cid: &str,
    ) -> Result<Self, RuntimeError> {
        if store.workspaces.get(&workspace.id) != Some(&workspace.path) {
            return Err(RuntimeError::session_not_found());
        }
        if cid != "new" {
            if !cid.strip_prefix("conv_").is_some_and(|s| {
                !s.is_empty()
                    && s.len() <= 160
                    && s.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
            }) {
                return Err(RuntimeError::bad_request("Invalid conversation_id"));
            }
            if store.session_index(&workspace.id, cid)?.is_none() {
                return Err(RuntimeError::session_not_found());
            }
        }
        let scope = format!("{}:{}:{}", host_draft_owner(), workspace.id, cid);
        let root = store.data_dir.join("composer_drafts");
        let dir = root.join("v2");
        require_directory_if_present(&root)?;
        require_directory_if_present(&dir)?;
        let path = dir.join(format!("{}.json", digest(&scope)));
        Ok(Self { root, path, scope })
    }
    fn read(&self) -> Result<Value, RuntimeError> {
        read_object(&self.path)
    }
    fn save(&self, value: &Value) -> Result<(), RuntimeError> {
        let dir = self.root.join("v2");
        fs::create_dir_all(&dir).map_err(RuntimeError::storage)?;
        require_directory_if_present(&self.root)?;
        require_directory_if_present(&dir)?;
        write_atomically(&self.path, value)
    }
}

fn read_object(path: &Path) -> Result<Value, RuntimeError> {
    let raw = read_json(path)?.unwrap_or_else(|| json!({}));
    if !raw.is_object() {
        return Err(RuntimeError::bad_request("Invalid composer draft"));
    }
    Ok(raw)
}

fn conflict(message: impl Into<String>) -> RuntimeError {
    RuntimeError::new(409, message)
}

fn payload(body: &Value) -> Result<Value, RuntimeError> {
    if !body.is_object() {
        return Err(conflict("Invalid draft payload"));
    }
    let content = body
        .get("content")
        .map_or(Some(""), Value::as_str)
        .filter(|s| s.chars().count() <= 40000)
        .ok_or_else(|| conflict("Draft content must be text of at most 40000 characters"))?;
    let mut result = json!({"content":content,"editor_json":body["editor_json"].as_object(),"updated_at":iso_time(now()),"skill_refs":[]});
    for (key, limit) in [
        ("files", 9),
        ("images", 9),
        ("videos", 1),
        ("recovered_ids", 1000),
    ] {
        let items = list(&body[key]).ok_or_else(|| conflict(format!("Invalid draft {key}")))?;
        if !items.iter().all(Value::is_string) {
            return Err(conflict(format!("Invalid draft {key}")));
        }
        if items.len() > limit {
            return Err(conflict(format!("Too many draft {key}")));
        }
        result[key] = json!(items);
    }
    let refs = list(&body["skill_refs"]).ok_or_else(|| conflict("Invalid skill references"))?;
    let mut skills = Vec::new();
    for skill in refs.iter().take(100) {
        let text = |field: &str, limit| {
            skill[field]
                .as_str()
                .unwrap_or_default()
                .chars()
                .take(limit)
                .collect::<String>()
        };
        let name = text("name", 200);
        let path = text("path", 4000);
        if !name.is_empty() && !path.is_empty() {
            skills.push(json!({"name":name,"description":text("description",1000),"path":path}));
        }
    }
    result["skill_refs"] = json!(skills);
    Ok(result)
}

fn list(value: &Value) -> Option<&[Value]> {
    if value.is_null() {
        Some(&[])
    } else {
        value.as_array().map(Vec::as_slice)
    }
}
fn integer(value: &Value) -> Result<i64, RuntimeError> {
    value
        .as_i64()
        .or_else(|| value.as_str()?.parse().ok())
        .ok_or_else(|| conflict("Invalid draft revision or sequence"))
}

impl Runtime {
    pub fn read_composer_draft(
        &self,
        workspace: &RegisteredWorkspace,
        cid: &str,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        let target = DraftPath::resolve(&store, workspace, cid)?;
        let mut draft = target.read()?;
        if cid == "new" && draft.as_object().is_some_and(|m| m.is_empty()) {
            let dir = target.root.join("host");
            require_directory_if_present(&dir)?;
            let legacy = workspace.id.trim_matches(['.', '_', '-']);
            let name = if legacy.is_empty() { "default" } else { legacy };
            let prior = read_object(&dir.join(format!("{name}.json")))?;
            if prior["content"].as_str().is_some_and(|s| !s.is_empty()) {
                draft = payload(&prior)?;
                draft["revision"] = 1.into();
                draft["writer_sequences"] = json!({});
                target.save(&draft)?;
            }
        }
        draft
            .as_object_mut()
            .unwrap()
            .shift_remove("writer_sequences");
        draft["scope"] = target.scope.into();
        Ok(json!({"success":true,"data":draft}))
    }

    pub fn save_composer_draft(
        &self,
        workspace: &RegisteredWorkspace,
        cid: &str,
        body: &Value,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        let target = DraftPath::resolve(&store, workspace, cid)?;
        let mut next = payload(body)?;
        let prior = target.read()?;
        // A previous writer may have stopped after saving the draft and before acknowledging the queue.
        acknowledge_recovered(&store, workspace, cid, &prior)?;
        let revision = prior["revision"].as_i64().unwrap_or_default();
        let mut writers = prior["writer_sequences"]
            .as_object()
            .cloned()
            .unwrap_or_default();
        let client: String = body["client_id"]
            .as_str()
            .unwrap_or_default()
            .chars()
            .take(120)
            .collect();
        let sequence = if body["sequence"].is_null() {
            0
        } else {
            integer(&body["sequence"])?
        };
        if !client.is_empty() && sequence > 0 {
            if writers
                .get(&client)
                .and_then(Value::as_i64)
                .unwrap_or_default()
                >= sequence
            {
                return Ok(json!({"success":true,"data":{"saved":false,"revision":revision}}));
            }
            writers.shift_remove(&client);
            writers.insert(client, sequence.into());
            while writers.len() > 32 {
                let key = writers.keys().next().unwrap().clone();
                writers.shift_remove(&key);
            }
        }
        if !body["base_revision"].is_null() && integer(&body["base_revision"])? != revision {
            return Err(conflict(
                "Draft changed in another window; local text has been retained",
            ));
        }
        let revision = revision
            .checked_add(1)
            .ok_or_else(|| conflict("Draft revision overflow"))?;
        next["revision"] = revision.into();
        next["writer_sequences"] = Value::Object(writers);
        next["scope"] = target.scope.clone().into();
        target.save(&next)?;
        acknowledge_recovered(&store, workspace, cid, &next)?;
        Ok(json!({"success":true,"data":{"saved":true,"revision":revision}}))
    }
}

fn acknowledge_recovered(
    store: &Store,
    workspace: &RegisteredWorkspace,
    cid: &str,
    draft: &Value,
) -> Result<(), RuntimeError> {
    let recovered: HashSet<_> = draft["recovered_ids"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    if cid != "new" && !recovered.is_empty() {
        let mut doc = store.read(&workspace.id, cid)?.into_raw();
        if let Some(items) = doc
            .pointer_mut("/metadata/runtime_message_queue/pending")
            .and_then(Value::as_array_mut)
        {
            let count = items.len();
            items.retain(|item| !item["id"].as_str().is_some_and(|id| recovered.contains(id)));
            if count != items.len() {
                store.persist(
                    &workspace.id,
                    LegacyDocument::from_value(doc).map_err(legacy_error)?,
                    false,
                )?;
            }
        }
    }
    Ok(())
}
