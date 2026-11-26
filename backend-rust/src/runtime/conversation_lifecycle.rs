//! Ordinary Agent conversation selection, load and deletion.

use std::{
    collections::HashMap,
    fs,
    path::PathBuf,
    sync::{Mutex, MutexGuard},
};

use serde_json::{Value, json};


use super::{Runtime, RuntimeError, Status, Store, verify_workspace_binding};
use crate::{
    legacy::{LegacyDocument, LegacyKind},
    web_assets::personalization::english,
    workspace_catalog::RegisteredWorkspace,
};

/// Workspace service focus is process-local. The desktop tab store owns tab focus.
#[derive(Default)]
pub struct ConversationSelection {
    workspaces: Mutex<HashMap<String, SelectedConversation>>,
}

struct SelectedConversation {
    path: PathBuf,
    id: String,
    model_key: Value,
    run_mode: Value,
}

impl ConversationSelection {
    fn lock(&self) -> Result<MutexGuard<'_, HashMap<String, SelectedConversation>>, RuntimeError> {
        self.workspaces.lock().map_err(RuntimeError::storage)
    }

    pub(super) fn invalidate(&self, workspace: &RegisteredWorkspace, id: &str) -> Result<(), RuntimeError> {
        let mut selected = self.lock()?;
        if selected.get(&workspace.id).is_some_and(|entry| entry.path == workspace.path && entry.id == id) {
            selected.remove(&workspace.id);
        }
        Ok(())
    }
}

impl Runtime {
    pub fn current_web_conversation(
        &self,
        workspace: &RegisteredWorkspace,
        selection: &ConversationSelection,
        requested: Option<&str>,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        let selected = selection.lock()?;
        let requested = requested
            .map(str::trim)
            .filter(|id| !id.is_empty())
            .map(canonical_id);
        let id = requested.as_deref().or_else(|| {
            selected
                .get(&workspace.id)
                .filter(|entry| entry.path == workspace.path)
                .map(|entry| entry.id.as_str())
        });
        let en = english(&store.data_dir);
        let data = if let Some(id) = id {
            let document = read_agent(&store, workspace, id)?;
            let raw = document.raw();
            json!({
                "id": id, "title": title(raw, en), "messages_count": message_count(raw),
                "is_temporary": false, "title_locked": truthy(&raw["metadata"]["title_locked"]),
            })
        } else {
            json!({
                "id": null, "title": if en { "New Chat" } else { "新对话" },
                "messages_count": 0, "is_temporary": true, "title_locked": false,
            })
        };
        Ok(json!({"success":true,"data":data}))
    }

    pub fn load_web_conversation(
        &self,
        workspace: &RegisteredWorkspace,
        selection: &ConversationSelection,
        requested: &str,
        select_workspace_conversation: bool,
        default_model: Option<&str>,
    ) -> Result<Value, RuntimeError> {
        let id = canonical_id(requested);
        let mut result = {
            let store = self.store()?;
            verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
            let document = read_agent(&store, workspace, &id)?;
            let safe = store
                .tasks
                .values()
                .any(|task| task.workspace_id == workspace.id && task.status == Status::Running);
            let raw = document.raw();
            let meta = &raw["metadata"];
            let mut selected = selection.lock()?;
            let previous = selected
                .get(&workspace.id)
                .filter(|entry| select_workspace_conversation && entry.path == workspace.path);
            let model = saved_model(raw).map_or_else(
                || previous.map_or_else(|| json!(default_model), |entry| entry.model_key.clone()),
                Value::from,
            );
            let (run_mode, thinking) = loaded_run_mode(meta, previous, safe);
            let en = english(&store.data_dir);
            let message = if en {
                format!("Conversation loaded: {id}")
            } else {
                format!("对话已加载: {id}")
            };
            let mut result = json!({
                "success": true, "conversation_id": id, "title": title(raw, en),
                "messages_count": message_count(raw), "run_mode": run_mode,
                "thinking_mode": thinking, "model_key": model, "message": message,
            });
            if safe {
                result["safe_navigation"] = json!(true);
                result["reasoning_effort"] = meta["reasoning_effort"].clone();
            } else if select_workspace_conversation {
                selected.insert(
                    workspace.id.clone(),
                    SelectedConversation {
                        path: workspace.path.clone(),
                        id: id.clone(),
                        model_key: model,
                        run_mode,
                    },
                );
            }
            result
        };
        result["versioning"] = match super::versioning::load_status(self, workspace, &id) {
            Ok(Value::Object(mut versioning)) => {
                for key in [
                    "conversation_id",
                    "supports_workspace_tracking",
                    "supports_conversation_only",
                ] {
                    versioning.remove(key);
                }
                Value::Object(versioning)
            }
            Ok(versioning) => versioning,
            Err(error) => json!({
                "host_mode": true, "enabled": false, "mode": "overwrite",
                "tracking_mode": "workspace_and_conversation", "error": error.message,
            }),
        };
        Ok(result)
    }

    pub fn delete_web_conversation(
        &self,
        workspace: &RegisteredWorkspace,
        selection: &ConversationSelection,
        requested: &str,
    ) -> Result<(String, Value), RuntimeError> {
        let id = canonical_id(requested);
        let mut store = self.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        read_agent(&store, workspace, &id)?;
        if store.tasks.values().any(|task| {
            task.workspace_id == workspace.id
                && task.conversation_id == id
                && task.status == Status::Running
        }) {
            return Err(RuntimeError::new(409, "Conversation has an active task"));
        }
        let mut selected = selection.lock()?;
        let path = store.conversation_path(&workspace.id, &id)?;
        store.delete_conversation(&workspace.id,&id)?;
        Self::close_process_scope(&mut store, &workspace.id, Some(&id));
        Self::release_mcp_scope(&mut store, &workspace.id, Some(&id));
        store.search_sentences.remove(&path);
        if selected
            .get(&workspace.id)
            .is_some_and(|entry| entry.path == workspace.path && entry.id == id)
        {
            selected.remove(&workspace.id);
        }
        let message = if english(&store.data_dir) {
            format!("Conversation deleted: {id}")
        } else {
            format!("对话已删除: {id}")
        };
        Ok((id, json!({"success":true,"message":message})))
    }
}

fn canonical_id(input: &str) -> String {
    let input = input.trim();
    if input.starts_with("conv_") {
        input.to_owned()
    } else {
        format!("conv_{input}")
    }
}

fn read_agent(
    store: &Store,
    workspace: &RegisteredWorkspace,
    id: &str,
) -> Result<LegacyDocument, RuntimeError> {
    store
        .conversation_path(&workspace.id, id)
        .map_err(|error| {
            if error.status == 400 {
                RuntimeError::session_not_found()
            } else {
                error
            }
        })?;
    let document = store.read(&workspace.id, id)?;
    if document.kind() == LegacyKind::Agent {
        Ok(document)
    } else {
        Err(RuntimeError::session_not_found())
    }
}

fn loaded_run_mode(
    meta: &Value,
    previous: Option<&SelectedConversation>,
    safe: bool,
) -> (Value, bool) {
    // Python's legacy reader supplies a mode from thinking_mode when the key is absent.
    let thinking = truthy(&meta["thinking_mode"]);
    let legacy = if thinking { "thinking" } else { "fast" };
    let saved = meta
        .get("run_mode")
        .cloned()
        .unwrap_or_else(|| json!(legacy));
    if safe {
        let fallback = || previous.map_or_else(|| json!("fast"), |entry| entry.run_mode.clone());
        let mode = if saved == "deep" {
            json!("thinking")
        } else if truthy(&saved) {
            saved
        } else {
            fallback()
        };
        let thinking = meta.get("thinking_mode").map_or(mode != "fast", truthy);
        return (mode, thinking);
    }
    let normalized = saved
        .as_str()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    let mode = match normalized.as_str() {
        "deep" | "thinking" => "thinking",
        "fast" => "fast",
        _ if meta.get("thinking_mode").is_none()
            && previous.is_some_and(|entry| entry.run_mode == "thinking") =>
        {
            "thinking"
        }
        _ => legacy,
    };
    (json!(mode), mode != "fast")
}

fn saved_model(document: &Value) -> Option<&str> {
    let saved = &document["metadata"]["model_key"];
    if !saved.is_null() {
        return saved.as_str().filter(|key| !key.is_empty());
    }
    document["messages"]
        .as_array()?
        .iter()
        .rev()
        .filter(|message| message["role"] == "assistant")
        .find_map(|message| {
            message["metadata"]["model_key"]
                .as_str()
                .filter(|key| !key.is_empty())
        })
}

fn title(document: &Value, en: bool) -> Value {
    document.get("title").cloned().unwrap_or_else(|| {
        json!(if en {
            "Unknown conversation"
        } else {
            "未知对话"
        })
    })
}

fn message_count(document: &Value) -> usize {
    document["messages"].as_array().map_or(0, Vec::len)
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}
