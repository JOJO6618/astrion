//! Guidance is accepted only while an Agent task owns the original conversation writer.

mod media;

use std::{collections::HashSet, path::Path as FsPath};

use axum::{
    Extension, Json, Router,
    extract::{DefaultBodyLimit, Path, State, rejection::JsonRejection},
    http::StatusCode,
    routing::post,
};
use ring::digest::{Context, SHA1_FOR_LEGACY_USE_ONLY};
use serde_json::{Value, json};
use uuid::{Builder, Uuid};

use crate::{
    approval::ApprovalPrincipal, legacy::LegacyDocument, workspace_catalog::RegisteredWorkspace,
};

use super::{
    Runtime, RuntimeError, Status, Store, Task, append_document, legacy_error, now,
    verify_workspace_binding,
};

const MAX_QUEUED: usize = 5;
pub(super) const ATTACHMENT_PROMPT: &str = "请查看本次附带的附件。";
const QUEUE: &str = "runtime_message_queue";

/// Install after Gateway authentication and workspace selection.
pub fn router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/tasks/{id}/runtime_guidance", post(enqueue))
        .layer(DefaultBodyLimit::max(50 * 1024 * 1024))
        .with_state(runtime)
}

type Reply = (StatusCode, Json<Value>);

async fn enqueue(
    State(runtime): State<Runtime>,
    Extension(principal): Extension<ApprovalPrincipal>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
    body: Result<Json<Value>, JsonRejection>,
) -> Reply {
    let Json(payload) = match body {
        Ok(payload) => payload,
        Err(error) => {
            return (
                error.status(),
                Json(json!({"success":false,"error":error.body_text()})),
            );
        }
    };
    let result = tokio::task::spawn_blocking(move || {
        let item = guidance_item(&payload)?;
        let mut store = runtime.store()?;
        verify_workspace_binding(&store, principal.workspace_id(), Some(&workspace.path))?;
        let task = store
            .tasks
            .get(&id)
            .filter(|task| task.workspace_id == principal.workspace_id())
            .ok_or_else(RuntimeError::task_not_found)?;
        if task.status != Status::Running
            || task.cancel_requested
            || task.guidance_closed
            || payload.get("conversation_id").is_some_and(|value| {
                truthy(value) && value.as_str() != Some(task.conversation_id.as_str())
            })
        {
            return Err(RuntimeError::new(
                409,
                "Task has ended; cannot append guidance",
            ));
        }
        if task.guidance_count >= MAX_QUEUED {
            return Err(RuntimeError::new(409, "Guidance queue is full (max 5)"));
        }
        let mut document = store
            .read(&task.workspace_id, &task.conversation_id)?
            .into_raw();
        let state = &mut document["metadata"][QUEUE];
        if !state.is_object() {
            *state = json!({"pending":[],"guidance":[],"paused":false});
        }
        let queued = ensure_array(&mut state["guidance"]);
        queued.push(item);
        let count = task.guidance_count + 1;
        let document = LegacyDocument::from_value(document).map_err(legacy_error)?;
        store.persist(&task.workspace_id, document, false)?;
        let task = store.tasks.get_mut(&id).expect("task is locked");
        task.guidance_count = count;
        task.updated_at = now();
        Ok(json!({"success":true,"data":{"task_id":id,"queued_count":count}}))
    })
    .await;
    match result {
        Ok(Ok(body)) => (StatusCode::OK, Json(body)),
        Ok(Err(error)) => failed(error),
        Err(_) => failed(RuntimeError::new(500, "Runtime operation interrupted")),
    }
}

fn failed(error: RuntimeError) -> Reply {
    (
        StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        Json(json!({"success":false,"error":error.message})),
    )
}

fn guidance_item(payload: &Value) -> Result<Value, RuntimeError> {
    if !payload.is_object() && !payload.is_null() {
        return Err(RuntimeError::bad_request("Expected a guidance object"));
    }
    let text = match payload.get("message").filter(|value| truthy(value)) {
        Some(value) => value
            .as_str()
            .ok_or_else(|| RuntimeError::bad_request("Guidance message must be text"))?
            .trim(),
        None => "",
    };
    let files = entries(&payload["files"])
        .iter()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|file| !file.is_empty() && file.chars().count() <= 500);
    let mut unique = HashSet::new();
    let files: Vec<_> = files.filter(|file| unique.insert(*file)).take(9).collect();
    let (mut images, mut videos) = (Vec::new(), Vec::new());
    for (default_kind, values) in [("image", &payload["images"]), ("video", &payload["videos"])] {
        for item in entries(values) {
            let kind = media::path_kind(media_path(item)).unwrap_or(default_kind);
            let group = if kind == "image" {
                &mut images
            } else {
                &mut videos
            };
            if group.len() < 9 {
                group.push(item.clone());
            }
        }
    }
    if text.is_empty() && images.is_empty() && videos.is_empty() && files.is_empty() {
        return Err(RuntimeError::bad_request(
            "Guidance content cannot be empty",
        ));
    }
    let mut item = json!({
        "id":Uuid::new_v4(),"source":"guidance","created_at":0,
        "text":if text.is_empty() { ATTACHMENT_PROMPT } else { text },
        "media_paths_only":truthy(&payload["media_paths_only"]),
    });
    for (key, values) in [
        ("files", json!(files)),
        ("images", json!(images)),
        ("videos", json!(videos)),
    ] {
        if !entries(&values).is_empty() {
            item[key] = values;
        }
    }
    Ok(item)
}

fn media_path(item: &Value) -> &str {
    item.as_str()
        .or_else(|| item["path"].as_str())
        .unwrap_or_default()
        .trim()
}

pub(super) fn has_attachments(payload: &Value) -> bool {
    ["images", "videos", "files"].iter().any(|field| !entries(&payload[*field]).is_empty())
}

pub(super) fn initial_media(
    payload: &Value, mut user: Value, data: &FsPath, workspace: &FsPath, execution_root: &FsPath,
    document: &LegacyDocument,
) -> Result<(Vec<Value>, Vec<Value>), RuntimeError> {
    let item = guidance_item(payload)?;
    if user["message_id"].is_null() {
        user["message_id"] = json!(format!("msg_{}", Uuid::new_v4().simple()));
    }
    let mut writer = media::Writer::new(data, workspace, execution_root, document.raw());
    let durable = attachment_messages(&mut writer, user, &item)?;
    let model = model_media(&durable, data, workspace, execution_root, document.raw());
    writer.publish_index(document.source_id(), &durable).map_err(RuntimeError::storage)?;
    Ok((durable, model))
}

fn attachment_messages(writer: &mut media::Writer<'_>, mut user: Value, item: &Value) -> Result<Vec<Value>, RuntimeError> {
    let images = entries(&item["images"]);
    let videos = entries(&item["videos"]);
    let files = entries(&item["files"]);
    let paths_only = truthy(&item["media_paths_only"]);
    if !images.is_empty() {
        user["images"] = json!(images);
    }
    if !videos.is_empty() {
        user["videos"] = json!(videos);
    }
    user["metadata"]["files"] = json!(files);
    user["metadata"]["media_paths_only"] = json!(paths_only);
    writer.store(&mut user, images, videos)?;
    let mut durable = vec![user];
    let mut seen = HashSet::new();
    let paths = path_lines(images.iter().chain(videos).map(media_path)
        .filter(|path| !path.is_empty() && seen.insert(*path)));
    if !paths.is_empty() {
        let hint = if paths_only {
            "本次仅提供媒体文件路径。如需查看媒体内容，请使用当前可用的媒体处理工具。"
        } else {
            "这些媒体已随本次消息一并发送，若你已能看到图片/视频内容，就不要再调用 view_image / view_video 重新打开；下面的路径只用于后续裁切、局部放大或引用。"
        };
        durable.push(runtime_message(
            "media_paths", &format!("{hint}\n本次消息附带的媒体文件路径：{paths}"), "hidden",
        ));
    }
    let paths = path_lines(files.iter().filter_map(Value::as_str));
    if !paths.is_empty() {
        durable.push(runtime_message("file_paths", &format!("本次消息附带的文件路径：{paths}"), "hidden"));
    }
    Ok(durable)
}

fn model_media(durable: &[Value], data: &FsPath, workspace: &FsPath, execution_root: &FsPath, document: &Value) -> Vec<Value> {
    let mut model: Vec<_> = durable.iter()
        .map(|message| json!({"role":"user","content":message["content"]})).collect();
    let media_document = json!({"messages":durable,"metadata":{"conversation_transfer":document["metadata"]["conversation_transfer"]}});
    crate::legacy::media::attach(&media_document, &mut model, data, workspace, execution_root);
    model
}

fn entries(value: &Value) -> &[Value] {
    value.as_array().map(Vec::as_slice).unwrap_or_default()
}

fn ensure_array(value: &mut Value) -> &mut Vec<Value> {
    if !value.is_array() {
        *value = json!([]);
    }
    value.as_array_mut().expect("array")
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_none_or(|n| n != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

/// Python carries older text guidance entries without IDs. Keep their draft recovery IDs stable.
pub fn queue_snapshot(state: &Value, include_guidance: bool) -> Value {
    let mut messages = entries(&state["pending"]).to_vec();
    if include_guidance {
        for (index, raw) in entries(&state["guidance"]).iter().enumerate() {
            let mut item = match raw {
                Value::Object(_) => raw.clone(),
                Value::String(text) => json!({"text":text}),
                _ => continue,
            };
            let Some(text) = item["text"].as_str().filter(|text| !text.trim().is_empty()) else {
                continue;
            };
            let mut digest = Context::new(&SHA1_FOR_LEGACY_USE_ONLY);
            digest.update(Uuid::NAMESPACE_URL.as_bytes());
            digest.update(format!("guidance:{index}:{text}").as_bytes());
            let id = Builder::from_sha1_bytes(
                digest.finish().as_ref()[..16]
                    .try_into()
                    .expect("SHA-1 has 20 bytes"),
            )
            .into_uuid();
            let item_fields = item.as_object_mut().expect("guidance object");
            item_fields.entry("id").or_insert_with(|| json!(id));
            item_fields.entry("created_at").or_insert(json!(0));
            item_fields.entry("source").or_insert(json!("guidance"));
            messages.push(item);
        }
    }
    json!({"messages":messages,"paused":truthy(&state["paused"])})
}

/// Keep previous tasks' guidance recoverable while the current writer injects its own items.
pub(super) fn admit(document: &Value, task: &mut Task) {
    let state = &document["metadata"][QUEUE];
    task.guidance_inherited = entries(&state["guidance"]).len();
    task.runtime_queue = queue_snapshot(state, false);
}

pub(super) fn finish(
    store: &Store,
    task: &Task,
    paused: bool,
    messages: Vec<Value>,
    ts: f64,
) -> Result<Value, RuntimeError> {
    if task.guidance_count == 0
        && task.guidance_inherited == 0
        && entries(&task.runtime_queue["messages"]).is_empty()
        && !truthy(&task.runtime_queue["paused"])
    {
        if !messages.is_empty() {
            store.append_messages(
                &task.workspace_id,
                &task.conversation_id,
                messages,
                ts,
                None,
                None,
            )?;
        }
        return Ok(task.runtime_queue.clone());
    }
    let document = store.read(&task.workspace_id, &task.conversation_id)?;
    let mut queue = queue_snapshot(&document.raw()["metadata"][QUEUE], true);
    queue["paused"] = json!(paused);
    let mut document = append_document(document, messages, ts, None, None)?.into_raw();
    document["metadata"][QUEUE] =
        json!({"pending":queue["messages"],"guidance":[],"paused":paused});
    store.persist(
        &task.workspace_id,
        LegacyDocument::from_value(document).map_err(legacy_error)?,
        false,
    )?;
    Ok(queue)
}

#[derive(Default)]
pub(super) struct Consumed {
    durable: Vec<Value>,
    model: Vec<Value>,
}

impl Consumed {
    pub(super) fn publish(self, task: &mut Task) -> Vec<Value> {
        task.guidance_count = 0;
        for message in self.durable {
            let metadata = &message["metadata"];
            let mut event = metadata.as_object().expect("guidance metadata").clone();
            event.insert("metadata".into(), metadata.clone());
            for key in ["message_id", "content"] {
                event.insert(key.into(), message[key].clone());
            }
            for key in ["images", "videos"] {
                event.insert(key.into(), json!(entries(&message[key])));
            }
            event.insert("message".into(), message["content"].clone());
            task.emit("user_message", Value::Object(event));
        }
        self.model
    }
}

/// Commit the complete model boundary, injected messages and queue removal in one document.
pub(super) fn consume(
    store: &Store,
    task: &Task,
    preceding: &[Value],
    ts: f64,
) -> Result<Consumed, RuntimeError> {
    let document = store.read(&task.workspace_id, &task.conversation_id)?;
    let state = &document.raw()["metadata"][QUEUE];
    let mut durable = Vec::new();
    let workspace = &store.workspaces[&task.workspace_id];
    let execution_root = task.execution_root.as_deref().unwrap_or(workspace);
    let mut media = media::Writer::new(&store.data_dir, workspace, execution_root, document.raw());
    let (inherited, accepted) = entries(&state["guidance"])
        .split_at_checked(task.guidance_inherited)
        .filter(|(_, accepted)| accepted.len() == task.guidance_count)
        .ok_or_else(|| {
            RuntimeError::storage("Guidance queue changed while the task was running")
        })?;
    for item in accepted {
        let text = item
            .as_str()
            .or_else(|| item["text"].as_str())
            .unwrap_or_default()
            .trim();
        if text.is_empty() {
            continue;
        }
        let user = runtime_message("guidance", text, "chat");
        durable.extend(attachment_messages(&mut media, user, item)?);
    }
    let model = model_media(&durable, &store.data_dir, workspace, execution_root, document.raw());
    let messages = preceding.iter().chain(&durable).cloned().collect();
    let inherited = json!(inherited);
    let mut document = append_document(document, messages, ts, None, None)?.into_raw();
    document["metadata"][QUEUE]["guidance"] = inherited;
    store.persist(
        &task.workspace_id,
        LegacyDocument::from_value(document).map_err(legacy_error)?,
        false,
    )?;
    // Blob paths in the committed document resolve even if the derived index is unavailable.
    if let Err(error) = media.publish_index(&task.conversation_id, &durable) {
        eprintln!("Could not update guidance media index: {error}");
    }
    Ok(Consumed { durable, model })
}

fn runtime_message(source: &str, text: &str, visibility: &str) -> Value {
    json!({
        "message_id":Uuid::new_v4(),"role":"user","content":format!("[系统通知|{source}]\n{text}"),
        "metadata":{
            "runtime_injected":true,"source":source,"inline":true,"is_auto_generated":true,
            "auto_message_type":format!("runtime_{source}"),"message_source":source,
            "runtime_guidance":true,"runtime_guidance_original":text,"visibility":visibility,
            "starts_work":false,
        },
    })
}

fn path_lines<'a>(paths: impl IntoIterator<Item = &'a str>) -> String {
    paths
        .into_iter()
        .map(|path| format!("\n- `{path}`"))
        .collect()
}

impl Runtime {
    pub(super) async fn guidance_after_response(
        &self,
        id: &str,
        preceding: Option<Value>,
    ) -> Result<Vec<Value>, String> {
        let runtime = self.clone();
        let id = id.to_owned();
        tokio::task::spawn_blocking(move || {
            let mut store = runtime.store().map_err(|error| error.message)?;
            let task = super::writable_task(&mut store, &id)?;
            if task.guidance_count == 0 {
                task.guidance_closed = true;
                return Ok(Vec::new());
            }
            let preceding: Vec<_> = preceding.into_iter().collect();
            let consumed = consume(&store, &store.tasks[&id], &preceding, now())
                .map_err(|error| error.message)?;
            let task = store.tasks.get_mut(&id).expect("task is locked");
            let model = consumed.publish(task);
            task.checkpoint_events();
            Ok(model)
        })
        .await
        .map_err(|_| "Guidance writer exited unexpectedly".to_owned())?
    }
}
