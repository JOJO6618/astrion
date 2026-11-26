use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    routing::{delete, get, patch, post},
};
use serde::Deserialize;
use serde_json::{Map as JsonMap, Value, json};

use super::{
    ChatError, ChatMode,
    drafts,
    duplicate as conversation_copy,
    memory,
    settings as chat_settings,
    store::public_document,
    tasks::now,
    tree,
};

type Reply = (StatusCode, Json<Value>);

pub(super) fn router(mode: ChatMode) -> Router {
    Router::new()
        .route("/api/chat/media", post(super::media_http::upload).layer(axum::extract::DefaultBodyLimit::max(super::media_http::MAX_VIDEO + 1024 * 1024)))
        .route("/api/chat/media/{id}", get(super::media_http::download))
        .route("/api/chat/settings", get(account_settings_get).patch(account_settings_patch))
        .route("/api/chat/memory", get(memory_get).post(memory_post))
        .route("/api/chat/conversations", get(list))
        .route("/api/chat/conversations/{id}", get(bootstrap))
        .route("/api/chat/conversations/{id}", delete(remove))
        .route("/api/chat/drafts/{key}", get(draft_get).put(draft_save))
        .route("/api/chat/conversations/{id}/duplicate", post(duplicate))
        .route("/api/chat/conversations/{id}/handoff", get(super::handoff::get).post(super::handoff::post))
        .route("/api/chat/conversations/{id}/presentation", post(presentation))
        .route("/api/chat/conversations/{id}/edit", post(edit))
        .route("/api/chat/conversations/{id}/settings", patch(settings))
        .route("/api/chat/conversations/{id}/network", post(network))
        .route("/api/chat/conversations/{id}/title", patch(title))
        .route("/api/chat/conversations/{id}/compress-limit", post(compress_limit))
        .route("/api/chat/tasks", post(send))
        .route("/api/chat/tasks/{id}", get(poll))
        .route("/api/chat/tasks/{id}/cancel", post(cancel))
        .with_state(mode)
}

pub(super) fn error(error: ChatError) -> Reply {
    (
        StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        Json(error.reply_body()),
    )
}

fn ok(value: Value) -> Reply {
    (StatusCode::OK, Json(value))
}

async fn run(operation: impl FnOnce() -> Result<Value, ChatError> + Send + 'static) -> Reply {
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(value)) => ok(value),
        Ok(Err(err)) => error(err),
        Err(_) => error(ChatError::storage("Chat operation interrupted")),
    }
}

#[derive(Deserialize)]
struct Search {
    q: Option<String>,
}

async fn account_settings_get(State(mode): State<ChatMode>) -> Reply {
    run(move || Ok(json!({"success": true, "data": chat_settings::payload(&mode)?}))).await
}

async fn account_settings_patch(
    State(mode): State<ChatMode>,
    Json(body): Json<Value>,
) -> Reply {
    let Some(patch) = body.as_object().cloned() else {
        return error(ChatError::bad_request("Expected a JSON object"));
    };
    run(move || {
        let data = chat_settings::patch(&mode, &patch)?;
        Ok(json!({"success": true, "data": data}))
    })
    .await
}

async fn memory_get(State(mode): State<ChatMode>) -> Reply {
    run(move || {
        let data = mode.storage(|paths| memory::snapshot(paths))?;
        Ok(json!({"success": true, "data": data}))
    })
    .await
}

async fn draft_get(State(mode): State<ChatMode>, Path(key): Path<String>) -> Reply {
    run(move || {
        let data = mode.storage(|paths| {
            drafts::validate_key(paths, &key)?;
            drafts::read(paths, &key)
        })?;
        Ok(json!({"success": true, "data": data}))
    })
    .await
}

async fn draft_save(
    State(mode): State<ChatMode>,
    Path(key): Path<String>,
    Json(body): Json<Value>,
) -> Reply {
    let Some(payload) = body.as_object().cloned() else {
        return error(ChatError::bad_request("Expected a JSON object"));
    };
    run(move || {
        let data = mode.storage(|paths| {
            drafts::validate_key(paths, &key)?;
            drafts::save(paths, &key, &Value::Object(payload))
        })?;
        Ok(json!({"success": true, "data": data}))
    })
    .await
}

async fn duplicate(
    State(mode): State<ChatMode>,
    Path(id): Path<String>,
    Json(body): Json<Value>,
) -> Reply {
    let Some(payload) = body.as_object().cloned() else {
        return error(ChatError::bad_request("Expected a JSON object"));
    };
    run(move || {
        let conversation_id = mode.storage(|paths| {
            conversation_copy::duplicate(paths, &id, &Value::Object(payload))
        })?;
        Ok(json!({
            "success": true,
            "data": {"conversation_id": conversation_id},
        }))
    })
    .await
}

async fn memory_post(State(mode): State<ChatMode>, Json(body): Json<Value>) -> Reply {
    if !body.is_object() {
        return error(ChatError::bad_request("Expected a JSON object"));
    }
    run(move || {
        let data = mode.storage(|paths| {
            if body["kind"].as_str() == Some("topic") {
                memory::write_topic(paths, &body)?;
            } else {
                memory::update(paths, &body)?;
            }
            memory::snapshot(paths)
        })?;
        Ok(json!({"success": true, "data": data}))
    })
    .await
}

async fn list(State(mode): State<ChatMode>, Query(query): Query<Search>) -> Reply {
    run(move || {
        let data = mode.storage(|paths| {
            if let Some(needle) = query.q.as_deref().map(str::trim).filter(|q| !q.is_empty()) {
                paths.search(needle, 100)
            } else {
                paths.list_rows()
            }
        })?;
        Ok(json!({"success": true, "data": data}))
    })
    .await
}

async fn bootstrap(State(mode): State<ChatMode>, Path(id): Path<String>) -> Reply {
    run(move || {
        let document = mode.storage(|paths| {
            let document = paths.read_document(&id)?;
            public_document(document)
        })?;
        Ok(json!({
            "success": true,
            "data": {"document": document, "display": Value::Null},
        }))
    })
    .await
}

#[derive(Deserialize)]
struct Presentation {
    presentation_id: Option<String>,
    release: Option<bool>,
}

async fn presentation(
    State(mode): State<ChatMode>,
    Path(id): Path<String>,
    Json(body): Json<Presentation>,
) -> Reply {
    run(move || {
        let release = body.release == Some(true);
        mode.handoff_presentation(&id, body.presentation_id.as_deref(), release)?;
        if release { return Ok(json!({"success": true})); }
        let document = mode.storage(|paths| public_document(paths.read_document(&id)?))?;
        Ok(json!({"success":true,"data":{"document":document,"display":null}}))
    })
    .await
}

#[derive(Deserialize)]
struct Edit {
    operation_id: Option<String>,
    message_id: String,
    content: String,
    revision: Option<u64>,
}

async fn edit(
    State(mode): State<ChatMode>,
    Path(id): Path<String>,
    Json(body): Json<Edit>,
) -> Reply {
    let timestamp_now = now();
    run(move || {
        let operation = tree::operation_id_str(body.operation_id.as_deref())?;
        let result = mode.storage(|paths| {
            tree::edit(
                paths,
                &id,
                &operation,
                &body.message_id,
                &body.content,
                body.revision,
                timestamp_now,
            )
        })?;
        Ok(json!({"success": true, "data": result}))
    })
    .await
}

async fn settings(
    State(mode): State<ChatMode>,
    Path(id): Path<String>,
    Json(body): Json<Value>,
) -> Reply {
    let settings = match body.as_object() {
        Some(settings) => settings.clone(),
        None => return error(ChatError::bad_request("Expected a JSON object")),
    };
    if let Err(err) = validate_settings(&settings) {
        return error(err);
    }
    let timestamp_now = now();
    run(move || {
        let result = mode.storage(|paths| tree::configure(paths, &id, &settings, timestamp_now))?;
        Ok(json!({"success": true, "data": result}))
    })
    .await
}

pub(super) fn validate_settings(settings: &JsonMap<String, Value>) -> Result<(), ChatError> {
    if settings.keys().any(|key| {
        !matches!(
            key.as_str(),
            "model_key" | "thinking_mode" | "reasoning_effort" | "chatgpt_fast_mode"
        )
    }) {
        return Err(ChatError::bad_request(
            "Unsupported Chat conversation setting",
        ));
    }
    if let Some(thinking) = settings.get("thinking_mode")
        && !thinking.is_boolean()
    {
        return Err(ChatError::bad_request("Thinking mode must be a boolean"));
    }
    if let Some(effort) = settings.get("reasoning_effort")
        && !effort.is_null()
        && effort
            .as_str()
            .is_none_or(|value| !matches!(value, "low" | "medium" | "high" | "xhigh" | "max"))
    {
        return Err(ChatError::bad_request("Invalid reasoning effort"));
    }
    if let Some(model) = settings.get("model_key")
        && model.as_str().is_none_or(|value| value.trim().is_empty())
    {
        return Err(ChatError::bad_request("Invalid model identity"));
    }
    if settings
        .get("chatgpt_fast_mode")
        .is_some_and(|value| !value.is_boolean())
    {
        return Err(ChatError::bad_request(
            "chatgpt_fast_mode must be a boolean",
        ));
    }
    Ok(())
}

#[derive(Deserialize)]
struct NetworkBody {
    operation_id: Option<String>,
    enabled: Option<bool>,
}

async fn network(
    State(mode): State<ChatMode>,
    Path(id): Path<String>,
    Json(body): Json<NetworkBody>,
) -> Reply {
    let enabled = match body.enabled {
        Some(enabled) => enabled,
        None => return error(ChatError::bad_request("Network state must be a boolean")),
    };
    let timestamp_now = now();
    run(move || {
        let operation = tree::operation_id_str(body.operation_id.as_deref())?;
        let result =
            mode.storage(|paths| tree::set_network(paths, &id, &operation, enabled, timestamp_now))?;
        Ok(json!({"success": true, "data": result}))
    })
    .await
}

#[derive(Deserialize)]
struct TitleBody {
    title: Option<String>,
}

async fn title(
    State(mode): State<ChatMode>,
    Path(id): Path<String>,
    Json(body): Json<TitleBody>,
) -> Reply {
    let title = body
        .title
        .as_deref()
        .map(str::trim)
        .filter(|title| !title.is_empty() && title.chars().count() <= 200)
        .map(str::to_owned);
    let Some(title) = title else {
        return error(ChatError::bad_request("Invalid Chat title"));
    };
    run(move || {
        mode.storage(|paths| {
            paths.read_raw(&id)?;
            paths.set_title(&id, &title)
        })?;
        Ok(json!({"success": true}))
    })
    .await
}

async fn remove(State(mode): State<ChatMode>, Path(id): Path<String>) -> Reply {
    run(move || {
        let _handoff_guard = mode.cancel_handoffs(&id)?;
        mode.storage(|paths| {
            let raw = paths.read_raw(&id)?;
            if tree::active_turn(&raw).is_some() {
                return Err(ChatError::conflict(
                    "Chat is generating; retry deletion after it stops",
                ));
            }
            paths.delete(&id)?;
            let root = crate::knowledge::storage::open_canonical_dir(paths.root())?;
            use cap_fs_ext::DirExt;
            let removed = root.open_dir_nofollow("drafts")
                .and_then(|dir| dir.remove_file(format!("{id}.json")));
            if let Err(err) = removed && err.kind() != std::io::ErrorKind::NotFound {
                return Err(err.into());
            }
            super::media_gc::collect_after_write(paths);
            Ok(json!({"success": true}))
        })
    })
    .await
}

#[derive(Deserialize)]
struct CompressLimit {
    tokens: Option<u64>,
}

async fn compress_limit(
    State(mode): State<ChatMode>,
    Path(id): Path<String>,
    Json(body): Json<CompressLimit>,
) -> Reply {
    let timestamp_now = now();
    run(move || {
        let (tokens, source) = match body.tokens {
            Some(tokens) if (1_000..=2_000_000).contains(&tokens) => (Some(tokens), "custom"),
            Some(_) => {
                return Err(ChatError::bad_request(
                    "compress limit must be an integer from 1000 to 2000000",
                ));
            }
            None => {
                let preferences = super::store::read_json(
                    &mode.preferences_dir.join("personalization.json"),
                )?;
                let configured = preferences["chat_deep_compress_trigger_tokens"]
                    .as_u64()
                    .filter(|tokens| (1_000..=2_000_000).contains(tokens))
                    .unwrap_or(250_000);
                (Some(configured), "snapshot")
            }
        };
        let result =
            mode.storage(|paths| tree::compress_limit(paths, &id, tokens, source, timestamp_now))?;
        Ok(json!({"success": true, "data": result}))
    })
    .await
}

async fn send(State(mode): State<ChatMode>, Json(body): Json<Value>) -> Reply {
    match mode.create_task(&body) {
        Ok(task) => ok(json!({"success": true, "data": task})),
        Err(err) => error(err),
    }
}

#[derive(Deserialize)]
struct Cursor {
    from: Option<u64>,
}

async fn poll(
    State(mode): State<ChatMode>,
    Path(id): Path<String>,
    Query(query): Query<Cursor>,
) -> Reply {
    let from = query.from.unwrap_or(0);
    let runtime = mode.runtime.clone();
    let task = tokio::task::spawn_blocking(move || runtime.poll_task("", &id, from)).await;
    let task = match task {
        Ok(Ok(task)) => task,
        Ok(Err(err)) => return error(err.into()),
        Err(_) => return error(ChatError::storage("Chat task poll interrupted")),
    };
    let data = super::tasks::public_task(task);
    let conversation_id = data["conversation_id"].as_str().unwrap_or_default().to_owned();
    let view = mode
        .storage(|paths| {
            let document = paths.read_document(&conversation_id)?;
            Ok(json!({
                "document": public_document(document)?,
                "display": Value::Null,
            }))
        })
        .ok()
        .unwrap_or(Value::Null);
    ok(json!({
        "success": true,
        "data": {
            "view": view,
            "task_id": data["task_id"],
            "workspace_id": Value::Null,
            "conversation_kind": "chat",
            "workspace_label": "Chat",
            "status": data["status"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
            "message": data["message"],
            "conversation_id": data["conversation_id"],
            "error": data["error"],
            "message_source": data["message_source"],
            "goal_mode": false,
            "goal_progress": Value::Null,
            "task_type": "chat",
            "events": data["events"],
            "next_offset": data["next_offset"],
            "window_start": data["window_start"],
        }
    }))
}

async fn cancel(State(mode): State<ChatMode>, Path(id): Path<String>) -> Reply {
    let runtime = mode.runtime.clone();
    let result = tokio::task::spawn_blocking(move || runtime.cancel_task("", &id)).await;
    match result {
        Ok(Ok(value)) => ok(value),
        Ok(Err(err)) => error(err.into()),
        Err(_) => error(ChatError::storage("Chat task cancel interrupted")),
    }
}
