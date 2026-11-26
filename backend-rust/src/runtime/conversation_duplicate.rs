//! Browser Agent conversation duplication using the existing conversation writer.

use std::{path::Path, sync::Arc};

use axum::{
    Extension, Json, Router,
    extract::{Path as RoutePath, Query, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::post,
};
use serde::Deserialize;
use serde_json::{Map, Value, json};

use crate::{
    catalog::CatalogSource,
    legacy::LegacyDocument,
    web_assets::personalization::{Personalization, english},
    web_auth::BrowserSession,
    workspace_catalog::RegisteredWorkspace,
};

use super::{
    Runtime, RuntimeError,
    conversation_token_statistics::{TOKEN_COUNTERS, integer_value},
    goals::verify_conversation_root,
    legacy_error, new_document, now,
    store::read_json,
    verify_workspace_binding,
};

type WorkspaceResolver = dyn Fn(
        &HeaderMap,
        Option<&str>,
        &Value,
        &BrowserSession,
    ) -> Result<Option<RegisteredWorkspace>, RuntimeError>
    + Send
    + Sync;

#[derive(Clone)]
struct Duplicate {
    runtime: Runtime,
    catalog: CatalogSource,
    preferences: Personalization,
    workspace: Arc<WorkspaceResolver>,
}

/// Mount inside the browser guard. The resolver applies the shared mutation identity policy.
pub fn browser_router(
    runtime: Runtime,
    catalog: CatalogSource,
    preferences: Personalization,
    workspace: impl Fn(
        &HeaderMap,
        Option<&str>,
        &Value,
        &BrowserSession,
    ) -> Result<Option<RegisteredWorkspace>, RuntimeError>
    + Send
    + Sync
    + 'static,
) -> Router {
    Router::new()
        .route("/api/conversations/{id}/duplicate", post(duplicate))
        .with_state(Duplicate {
            runtime,
            catalog,
            preferences,
            workspace: Arc::new(workspace),
        })
}

#[derive(Deserialize)]
struct WorkspaceQuery {
    workspace_id: Option<String>,
}

async fn duplicate(
    State(app): State<Duplicate>,
    Extension(browser): Extension<BrowserSession>,
    RoutePath(id): RoutePath<String>,
    Query(scope): Query<WorkspaceQuery>,
    headers: HeaderMap,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    let body = body.ok().map(|Json(body)| body).unwrap_or_default();
    let result = tokio::task::spawn_blocking(move || {
        let en = english(app.catalog.host_data_dir());
        let scope = (app.workspace)(&headers, scope.workspace_id.as_deref(), &body, &browser);
        let result = match scope {
            Ok(Some(workspace)) => app.copy(&workspace, &id, en),
            Ok(None) => return reply(StatusCode::OK, json!({
                "success": false,
                "code": "no_workspace",
                "error": if en {
                    "No workspace has been created yet. Please create one manually in the workspace management UI."
                } else {
                    "尚未创建任何工作区，请先在工作区管理界面中手动创建工作区"
                },
            })),
            Err(error) => Err(error),
        };
        match result {
            Ok(document) => reply(StatusCode::OK, document),
            Err(error) => failure(error, en),
        }
    })
    .await;
    result.unwrap_or_else(|_| {
        failure(
            RuntimeError::new(500, "Conversation duplicate interrupted"),
            false,
        )
    })
}

impl Duplicate {
    fn copy(
        &self,
        workspace: &RegisteredWorkspace,
        id: &str,
        en: bool,
    ) -> Result<Value, RuntimeError> {
        let creation = self
            .preferences
            .agent_creation_preferences()
            .map_err(RuntimeError::storage)?;
        let catalog = self.catalog.load();
        let store = self.runtime.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        store
            .conversation_path(&workspace.id, id)
            .map_err(|error| {
                if error.status == 400 {
                    RuntimeError::new(
                        404,
                        if en {
                            "Invalid conversation ID"
                        } else {
                            "对话 ID 不合法"
                        },
                    )
                } else {
                    error
                }
            })?;
        let missing = || RuntimeError::new(404, "Conversation not found");
        let raw=store.read(&workspace.id,id)?.into_raw();
        let Value::Object(mut source)=raw else {return Err(missing())};
        if source.get("id").is_some_and(|value| value != id) {
            return Err(missing());
        }
        let messages = match source.shift_remove("messages") {
            None | Some(Value::Null) => Vec::new(),
            Some(Value::Array(messages)) => messages,
            _ => return Err(missing()),
        };
        let source = Value::Object(source);
        let metadata = &source["metadata"];
        if source
            .get("metadata")
            .is_some_and(|value| !value.is_object())
            || metadata
                .get("conversation_kind")
                .is_some_and(|kind| kind != "agent")
        {
            return Err(missing());
        }
        verify_conversation_root(&workspace.path, &source).map_err(|_| missing())?;
        if metadata["project_relative_path"]
            .as_str()
            .is_some_and(|value| {
                let path = Path::new(value);
                path.is_absolute() && path.canonicalize().is_ok_and(|root| root != workspace.path)
            })
        {
            return Err(missing());
        }

        let mut raw = new_document(&store, &workspace.id, &json!({}), now())?.into_raw();
        let timestamp = raw["updated_at"].clone();
        let title = match source["title"].as_str().filter(|title| !title.is_empty()) {
            Some(title) => {
                raw["metadata"]["title_locked"] = json!(true);
                if en {
                    format!("Copy of {title}")
                } else {
                    format!("{title} 的副本")
                }
            }
            None => initial_title(&messages, en),
        };
        raw["token_statistics"] =
            copied_statistics(source.get("token_statistics"), timestamp).ok_or_else(missing)?;
        let copy_metadata = raw["metadata"]
            .as_object_mut()
            .expect("new document metadata");
        fill_metadata(copy_metadata, metadata, &messages, &creation);
        let id = raw["id"].as_str().expect("new document ID").to_owned();
        let mode = if matches!(
            raw["metadata"]["run_mode"].as_str(),
            Some("deep" | "thinking")
        ) {
            "thinking"
        } else {
            "fast"
        };
        let displayed_model = raw["metadata"]["model_key"]
            .as_str()
            .filter(|key| !key.is_empty())
            .or_else(|| catalog.default_key());
        let message = if en {
            format!("Conversation loaded: {id}")
        } else {
            format!("对话已加载: {id}")
        };
        let result = json!({"success": true, "duplicate_conversation_id": id, "load_result": {
            "success": true, "conversation_id": id, "title": title, "messages_count": messages.len(),
            "run_mode": mode, "thinking_mode": mode == "thinking", "model_key": displayed_model,
            "message": message,
        }});
        raw["title"] = json!(title);
        raw["messages"] = Value::Array(messages);
        let document = LegacyDocument::from_value(raw).map_err(legacy_error)?;
        store.persist(&workspace.id, document, true)?;
        Ok(result)
    }
}

fn fill_metadata(
    metadata: &mut Map<String, Value>,
    source: &Value,
    messages: &[Value],
    creation: &Value,
) {
    // Python forks conversation history and selected preferences into fresh runtime state.
    metadata.shift_remove("quick_entry");
    let defaults = json!({
        "project_file_tree": null, "project_statistics": null, "project_snapshot_at": null,
        "is_long_conversation": false, "is_ultra_long_conversation": false, "tool_call_count": 0,
        "last_shallow_compress_tool_count": 0, "compression_in_progress": false,
        "compression_mode": null, "compression_stage": null, "compression_job_id": null,
        "compression_error": null, "compression_resume_payload": null,
        "last_deep_compression_record": null, "context_baseline_pending": true,
        "skip_auto_title_generation": false,
        "git_worktree_enabled": creation["git_worktree_enabled"] == true,
    });
    if let Value::Object(defaults) = defaults {
        metadata.extend(defaults);
    }
    for key in ["compress_limit_tokens", "compress_limit_source"] {
        if let Some(value) = creation.get(key) {
            metadata.insert(key.into(), value.clone());
        }
    }
    for (key, default) in [
        ("permission_mode", json!("unrestricted")),
        ("thinking_mode", json!(false)),
        ("has_images", json!(false)),
        ("status", json!("active")),
        ("total_messages", json!(messages.len())),
    ] {
        metadata.insert(key.into(), source.get(key).cloned().unwrap_or(default));
    }
    let mode = match source["run_mode"].as_str() {
        Some(mode @ ("fast" | "thinking" | "deep")) => mode,
        _ if truthy(&metadata["thinking_mode"]) => "thinking",
        _ => "fast",
    };
    metadata.insert("run_mode".into(), json!(mode));
    let model = (!source["model_key"].is_null())
        .then_some(&source["model_key"])
        .or_else(|| {
            messages.iter().rev().find_map(|message| {
                let key = &message["metadata"]["model_key"];
                (message["role"] == "assistant" && truthy(key)).then_some(key)
            })
        });
    metadata.insert("model_key".into(), model.cloned().unwrap_or_default());
    if source["tool_loading"].is_object() {
        metadata.insert("tool_loading".into(), source["tool_loading"].clone());
    }
    let total_tools: usize = messages
        .iter()
        .filter(|emission| emission["role"] == "assistant")
        .filter_map(|emission| emission["tool_calls"].as_array())
        .map(Vec::len)
        .sum();
    metadata.insert("total_tools".into(), json!(total_tools));
}

fn copied_statistics(source: Option<&Value>, timestamp: Value) -> Option<Value> {
    let mut stats = match source {
        Some(Value::Object(stats)) => stats.clone(),
        None => Map::new(),
        _ => return None,
    };
    let counters = TOKEN_COUNTERS.map(|key| match stats.get(key) {
        Some(value) => integer_value(value),
        None => Some(json!(0)),
    });
    let valid = counters.iter().all(Option::is_some);
    if !valid {
        stats.clear();
    }
    for (key, value) in TOKEN_COUNTERS.into_iter().zip(counters) {
        stats.insert(
            key.into(),
            if valid {
                value.unwrap_or_default()
            } else {
                json!(0)
            },
        );
    }
    let pending = stats.get("cache_cold_start_pending").is_some_and(truthy);
    stats.insert("cache_cold_start_pending".into(), json!(pending));
    stats.entry("updated_at").or_insert(timestamp);
    Some(Value::Object(stats))
}

fn initial_title(messages: &[Value], en: bool) -> String {
    for message in messages.iter().filter(|message| message["role"] == "user") {
        let meta = &message["metadata"];
        let source = meta["source"]
            .as_str()
            .filter(|source| !source.is_empty())
            .or_else(|| meta["message_source"].as_str())
            .unwrap_or_default();
        if meta["hidden"] == true || source.trim().eq_ignore_ascii_case("skill") {
            continue;
        }
        if let Some(text) = message["content"]
            .as_str()
            .map(str::trim)
            .filter(|text| !text.is_empty())
        {
            let mut chars = text.chars();
            let mut title: String = chars.by_ref().take(50).collect();
            if chars.next().is_some() {
                title.push_str("...");
            }
            return title;
        }
    }
    if en { "New Chat" } else { "新对话" }.to_owned()
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn failure(error: RuntimeError, en: bool) -> Response {
    let status = StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let mut document = json!({"success": false, "error": error.message});
    if status.is_server_error() {
        document["message"] = json!(if en {
            "An error occurred while duplicating the conversation"
        } else {
            "复制对话时发生异常"
        });
    }
    reply(status, document)
}

fn reply(status: StatusCode, value: Value) -> Response {
    (status, [(header::CACHE_CONTROL, "no-store")], Json(value)).into_response()
}
