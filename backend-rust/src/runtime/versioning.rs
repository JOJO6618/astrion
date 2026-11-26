//! Connect versioning mutations to the Runtime's Agent admission and storage lock.

use std::path::{Path as FilePath, PathBuf};

use axum::{
    Extension, Json, Router,
    extract::{Path, State, rejection::JsonRejection},
    http::StatusCode,
    routing::post,
};
use serde_json::{Value, json};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

use super::super::{Status, store::Task};
use super::{
    Reply, Runtime, RuntimeError, Store, error_reply, files, mutations, normalize_id,
    open_data_directory, python_bool, shallow_write, storage, transaction, verify_record_owner,
    verify_workspace_binding,
};
use crate::workspace_catalog::RegisteredWorkspace;

/// Browser-only mutation routes. The caller supplies CSRF and registered workspace scoping.
pub fn mutation_router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/conversations/{id}/versioning", post(toggle))
        .route("/api/conversations/{id}/versioning/restore", post(restore))
        .with_state(runtime)
}

async fn toggle(
    State(runtime): State<Runtime>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
    payload: Result<Json<Value>, JsonRejection>,
) -> Reply {
    mutate(
        runtime,
        workspace,
        id,
        payload,
        |store, workspace, id, body| {
            mutations::configure(
                store,
                workspace,
                id,
                python_bool(&body["enabled"]),
                body["tracking_mode"].as_str(),
            )
        },
    )
    .await
}

async fn restore(
    State(runtime): State<Runtime>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
    payload: Result<Json<Value>, JsonRejection>,
) -> Reply {
    mutate(runtime, workspace, id, payload, mutations::restore).await
}

async fn mutate(
    runtime: Runtime,
    workspace: RegisteredWorkspace,
    id: String,
    payload: Result<Json<Value>, JsonRejection>,
    operation: impl FnOnce(&Store, &str, &str, &Value) -> Result<Value, RuntimeError> + Send + 'static,
) -> Reply {
    let Ok(Json(body)) = payload else {
        return error_reply(RuntimeError::bad_request("Expected a JSON object"));
    };
    if !body.is_object() {
        return error_reply(RuntimeError::bad_request("Expected a JSON object"));
    }
    let result = tokio::task::spawn_blocking(move || {
        let store = runtime.store()?;
        let id = normalize_id(&id);
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        recover_pending(&store, &workspace.id, &id)?;
        require_idle(&store, &workspace.id, Some(&id))?;
        operation(&store, &workspace.id, &id, &body)
    })
    .await;
    match result {
        Ok(Ok(data)) => (StatusCode::OK, Json(json!({"success":true,"data":data}))),
        Ok(Err(error)) => error_reply(error),
        Err(_) => error_reply(RuntimeError::new(500, "Versioning mutation interrupted")),
    }
}

pub(in crate::runtime) fn set_enabled_on_create(
    store: &Store,
    workspace: &str,
    id: &str,
) -> Result<(), RuntimeError> {
    if crate::web_assets::personalization::versioning_default(&store.data_dir).map_err(storage)? {
        mutations::configure(store, workspace, id, true, Some(super::CONVERSATION_ONLY))?;
    }
    Ok(())
}

/// Run before reading a session for Agent admission, while holding the Store lock.
pub(in crate::runtime) fn recover_pending(
    store: &Store,
    workspace: &str,
    id: &str,
) -> Result<(), RuntimeError> {
    store.conversation_path(workspace, id)?;
    let data = open_data_directory(&store.data_dir).map_err(storage)?;
    let Some(records) = files::child(&data, "save", false).map_err(storage)? else {
        return Ok(());
    };
    if let Some(source) = transaction::pending_workspace(&records, workspace)? {
        if !recover_source(store, &data, &records, workspace, &source)? {
            transaction::clear_workspace(&records, workspace)?;
        }
        if source == id {
            return Ok(());
        }
    }
    recover_source(store, &data, &records, workspace, id).map(|_| ())
}

fn recover_source(
    store: &Store,
    data: &cap_std::fs::Dir,
    records: &cap_std::fs::Dir,
    workspace: &str,
    id: &str,
) -> Result<bool, RuntimeError> {
    store.conversation_path(workspace, id)?;
    let Some(marker) = files::child(records, id, false).map_err(storage)? else {
        return Ok(false);
    };
    let Some(journal) = transaction::pending(&marker)? else {
        return Ok(false);
    };
    verify_record_owner(store, data, workspace, id)?;
    let _lock = files::lock(&marker).map_err(storage)?;
    require_idle(
        store,
        workspace,
        if journal.affects_workspace() {
            None
        } else {
            Some(id)
        },
    )?;
    let (header, _) = store.header(workspace, id)?;
    let root = execution_root(store, workspace, &header)?;
    transaction::recover(store, &marker, records, workspace, id, &root, journal)?;
    Ok(true)
}

pub(super) fn require_idle(
    store: &Store,
    workspace: &str,
    conversation: Option<&str>,
) -> Result<(), RuntimeError> {
    if store.tasks.values().any(|task| {
        task.workspace_id == workspace
            && task.status == Status::Running
            && conversation.is_none_or(|id| id == task.conversation_id)
    }) {
        return Err(RuntimeError::new(
            409,
            "A task is already running in this versioning scope",
        ));
    }
    Ok(())
}

pub(super) fn execution_root(
    store: &Store,
    workspace: &str,
    document: &Value,
) -> Result<PathBuf, RuntimeError> {
    verify_workspace_binding(store, workspace, None)?;
    let bound = &store.workspaces[workspace];
    super::super::goals::verify_conversation_root(bound, document)?;
    Ok(
        Runtime::worktree_execution_root(store, workspace, &document["metadata"])?
            .unwrap_or_else(|| bound.clone()),
    )
}

/// Take the pre-edit backup only for an authorized write in an enabled shallow Agent task.
pub(in crate::runtime) fn track_edit(
    store: &Store,
    task_id: &str,
    raw_path: &str,
) -> Result<(), RuntimeError> {
    let task = store
        .tasks
        .get(task_id)
        .ok_or_else(RuntimeError::task_not_found)?;
    if task.status != Status::Running || !eligible(task) || is_runtime_path(raw_path) {
        return Ok(());
    }
    let path = store.conversation_path(&task.workspace_id, &task.conversation_id)?;
    let db = crate::conversation_db::Database::open(path.parent().unwrap()).map_err(RuntimeError::storage)?;
    let config = db.field(&task.conversation_id, "metadata", "versioning").map_err(RuntimeError::storage)?.unwrap_or_default();
    if !python_bool(&config["enabled"]) || mutations::is_full(&config) {
        return Ok(());
    }
    let message_id = task.version_message_id.as_deref().unwrap_or(&task.start.id);
    let records = mutations::Directories::open(store, &task.workspace_id, &task.conversation_id)?;
    let _lock = files::lock(&records.session).map_err(storage)?;
    let root = task_root(store, task)?;
    shallow_write::track(
        &records.session,
        root,
        &store.data_dir,
        raw_path,
        message_id,
        &timestamp(),
    )
    .map_err(storage)
}

/// Record the durable turn and its tracked files before releasing the task's Store lock.
pub(in crate::runtime) fn record_finished(
    store: &Store,
    task_id: &str,
) -> Result<(), RuntimeError> {
    let task = store
        .tasks
        .get(task_id)
        .ok_or_else(RuntimeError::task_not_found)?;
    let run_status = match task.status {
        Status::Succeeded => "completed",
        Status::Failed => "failed",
        Status::Stopped => "stopped",
        Status::Running => {
            return Err(RuntimeError::new(
                409,
                "Versioning requires a finished task",
            ));
        }
    };
    if !eligible(task) {
        return Ok(());
    }
    let (header, _) = store.header(&task.workspace_id, &task.conversation_id)?;
    if !python_bool(&header["metadata"]["versioning"]["enabled"]) {
        return Ok(());
    }
    if header["metadata"]["conversation_kind"] == "chat" {
        return Ok(());
    }
    let Some((index, message_id)) = task_message(store, task)? else {
        return Ok(());
    };
    let root = task_root(store, task)?;
    mutations::checkpoint(
        store,
        task,
        &header,
        root,
        index,
        &message_id,
        run_status,
    )
}

fn task_root<'a>(store: &'a Store, task: &'a Task) -> Result<&'a FilePath, RuntimeError> {
    verify_workspace_binding(store, &task.workspace_id, None)?;
    Ok(task
        .execution_root
        .as_deref()
        .unwrap_or(&store.workspaces[&task.workspace_id]))
}

fn task_message(store: &Store, task: &Task) -> Result<Option<(usize, String)>, RuntimeError> {
    let time = OffsetDateTime::from_unix_timestamp_nanos(
        (task.start.created_at * 1_000_000_000.0).round() as i128,
    )
    .ok().and_then(|stamp| stamp.format(&Rfc3339).ok());
    let Some(time) = time else { return Ok(None); };
    let anchor = mutations::database(store, &task.workspace_id, &task.conversation_id)?
        .checkpoint_anchor(&task.conversation_id, task.version_message_id.as_deref(), &time, &task.start.message)
        .map_err(RuntimeError::storage)?;
    Ok(anchor.map(|(index, id)| (index, id.unwrap_or_else(|| task.start.id.clone()))))
}

fn eligible(task: &Task) -> bool {
    let text = task.start.message.trim();
    !text.is_empty()
        && !text.starts_with("这是一句系统自动发送的user消息")
        && !matches!(
            task.start.message_source.as_deref(),
            Some("git_review" | "workflow" | "goal_recovery" | "goal_review" | "goal_prompt")
        )
}

fn is_runtime_path(path: &str) -> bool {
    path.split(['/', '\\'])
        .any(|part| part.eq_ignore_ascii_case(".astrion") || part.eq_ignore_ascii_case(".git"))
}

pub(super) fn timestamp() -> String {
    OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .expect("current UTC timestamp is representable")
}
