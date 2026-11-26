//! Agent versioning and the existing Python checkpoint storage format.

mod deep;
mod diff;
mod files;
#[path = "../runtime/versioning.rs"]
mod integration;
mod mutations;
mod shallow;
mod shallow_write;
mod transaction;

pub use integration::mutation_router;
pub(super) use integration::{record_finished, recover_pending, set_enabled_on_create, track_edit};

use std::{io, path::Path as FilePath};

use axum::{
    Extension, Json, Router,
    extract::{Path, State},
    http::StatusCode,
    routing::get,
};
use cap_fs_ext::DirExt;
use cap_std::{ambient_authority, fs::Dir};
use serde_json::{Map, Value, json};

use super::{Runtime, RuntimeError, Store, verify_workspace_binding};
use crate::{legacy::LegacyKind, workspace_catalog::RegisteredWorkspace};

const INPUTS: &str = "inputs.jsonl";
const CONVERSATION_ONLY: &str = "conversation_only";
const WORKSPACE_AND_CONVERSATION: &str = "workspace_and_conversation";

type Reply = (StatusCode, Json<Value>);

/// Mount under browser authentication and workspace scoping.
pub fn browser_router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/conversations/{id}/versioning", get(status))
        .route(
            "/api/conversations/{id}/versioning/checkpoints/{seq}",
            get(detail),
        )
        .with_state(runtime)
}

/// The list also accepts Host Bearer credentials through the Gateway.
pub fn host_token_router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/conversations/{id}/versioning/checkpoints", get(list))
        .with_state(runtime)
}

async fn status(
    State(runtime): State<Runtime>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
) -> Reply {
    read(runtime, workspace, id, Records::status).await
}

pub(super) fn load_status(
    runtime: &Runtime,
    workspace: &RegisteredWorkspace,
    id: &str,
) -> Result<Value, RuntimeError> {
    let store = runtime.store()?;
    Records::open(&store, workspace, id)?.status()
}

async fn list(
    State(runtime): State<Runtime>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
) -> Reply {
    read(runtime, workspace, id, |record| {
        let items = if record.enabled {
            let mut rows = record.checkpoints().map_err(storage)?;
            let shallow = record.shallow_history().map_err(storage)?;
            for row in &mut rows {
                record
                    .decorate(row, shallow.as_ref(), false)
                    .map_err(storage)?;
            }
            rows
        } else {
            Vec::new()
        };
        Ok(json!({
            "enabled": record.enabled,
            "mode": "overwrite",
            "tracking_mode": record.tracking_mode,
            "items": items,
        }))
    })
    .await
}

async fn detail(
    State(runtime): State<Runtime>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Path((id, raw_seq)): Path<(String, String)>,
) -> Reply {
    // Flask's unsigned integer path converter rejects malformed segments with 404.
    let Ok(seq) = raw_seq.parse::<u64>() else {
        return error_reply(checkpoint_missing());
    };
    if raw_seq.starts_with('+') {
        return error_reply(checkpoint_missing());
    }
    read(runtime, workspace, id, move |record| {
        let mut row = record
            .checkpoints()
            .map_err(storage)?
            .into_iter()
            .find(|row| sequence(row) == seq)
            .ok_or_else(checkpoint_missing)?;
        let shallow = record.shallow_history().map_err(storage)?;
        record
            .decorate(&mut row, shallow.as_ref(), true)
            .map_err(storage)?;
        Ok(row)
    })
    .await
}

async fn read(
    runtime: Runtime,
    workspace: RegisteredWorkspace,
    id: String,
    view: impl FnOnce(&Records) -> Result<Value, RuntimeError> + Send + 'static,
) -> Reply {
    match tokio::task::spawn_blocking(move || {
        let store = runtime.store()?;
        view(&Records::open(&store, &workspace, &id)?)
    })
    .await
    {
        Ok(Ok(data)) => (StatusCode::OK, Json(json!({"success":true,"data":data}))),
        Ok(Err(error)) => error_reply(error),
        Err(_) => error_reply(RuntimeError::new(500, "Versioning lookup interrupted")),
    }
}

struct Records {
    id: String,
    workspace: String,
    directory: Option<Dir>,
    enabled: bool,
    tracking_mode: &'static str,
}

impl Records {
    fn open(
        store: &Store,
        workspace: &RegisteredWorkspace,
        id: &str,
    ) -> Result<Self, RuntimeError> {
        let id = normalize_id(id);
        verify_workspace_binding(store, &workspace.id, Some(&workspace.path))?;
        recover_pending(store, &workspace.id, &id)?;
        let document = store.read(&workspace.id, &id)?;
        if document.kind() != LegacyKind::Agent {
            return Err(RuntimeError::session_not_found());
        }
        let data = open_data_directory(&store.data_dir).map_err(storage)?;
        let directory = optional_child(&data, "save", &id).map_err(storage)?;
        if directory.is_some() {
            verify_record_owner(store, &data, &workspace.id, &id)?;
        }
        let config = &document.raw()["metadata"]["versioning"];
        Ok(Self {
            id,
            workspace: workspace.path.to_string_lossy().into_owned(),
            directory,
            enabled: python_bool(&config["enabled"]),
            tracking_mode: tracking_mode(&config["tracking_mode"]),
        })
    }

    fn status(&self) -> Result<Value, RuntimeError> {
        let latest = if self.enabled {
            self.checkpoints().map_err(storage)?.into_iter().next()
        } else {
            None
        };
        Ok(json!({
            "conversation_id": self.id, "host_mode": true,
            "supports_workspace_tracking": false, "supports_conversation_only": true,
            "enabled": self.enabled, "mode": "overwrite", "tracking_mode": self.tracking_mode,
            "latest_seq": latest.as_ref().map(sequence),
            "latest_commit": latest.as_ref().and_then(|row| row.get("tree_hash")),
        }))
    }

    fn checkpoints(&self) -> io::Result<Vec<Value>> {
        let Some(directory) = &self.directory else {
            return Ok(Vec::new());
        };
        let mut rows = files::rows::<Map<String, Value>>(directory, INPUTS)?;
        for row in &mut rows {
            row.insert(
                "tracking_mode".into(),
                json!(tracking_mode(
                    row.get("tracking_mode").unwrap_or(&Value::Null)
                )),
            );
        }
        let mut rows: Vec<_> = rows.into_iter().map(Value::Object).collect();
        rows.sort_by_key(|row| std::cmp::Reverse(sequence(row)));
        Ok(rows)
    }

    fn shallow_history(&self) -> io::Result<Option<shallow::History>> {
        let Some(directory) = &self.directory else {
            return Ok(None);
        };
        shallow::History::open(directory)
    }

    fn decorate(
        &self,
        row: &mut Value,
        shallow: Option<&shallow::History>,
        detail: bool,
    ) -> io::Result<()> {
        if let Some(shallow) = shallow {
            let message_id = self.shallow_message_id(row)?;
            if let Some(id) = message_id.as_deref()
                && let Some(files) =
                    shallow.changes(id, row["workspace_path"].as_str(), &self.workspace, detail)?
            {
                row["insertions"] = json!(files.iter().map(|file| file.insertions).sum::<usize>());
                row["deletions"] = json!(files.iter().map(|file| file.deletions).sum::<usize>());
                row["files_changed"] = json!(files.len());
                row["files"] = json!(files);
                return Ok(());
            }
        }
        if detail && let Some(files) = row["files"].as_array_mut() {
            files.retain(Value::is_object);
            for file in files {
                let file = file.as_object_mut().expect("object retained above");
                file.entry("patch_lines").or_insert_with(|| json!([]));
                file.entry("patch_truncated").or_insert(Value::Bool(false));
            }
        }
        Ok(())
    }

    fn shallow_message_id(&self, row: &Value) -> io::Result<Option<String>> {
        if let Some(id) = message_id(row) {
            return Ok(Some(id.to_owned()));
        }
        let Some(path) = row["snapshot_file"].as_str() else {
            return Ok(None);
        };
        let name = format!("{}.json", sequence(row));
        // Legacy Python records use the native separator of the machine that wrote them.
        if path
            .strip_prefix("snapshots/")
            .or_else(|| path.strip_prefix("snapshots\\"))
            != Some(name.as_str())
        {
            return Ok(None);
        }
        let Some(directory) = &self.directory else {
            return Ok(None);
        };
        let Some(snapshots) = files::child(directory, "snapshots", false)? else {
            return Ok(None);
        };
        Ok(files::read_json::<Value>(&snapshots, &name)?
            .as_ref()
            .and_then(message_id)
            .map(str::to_owned))
    }
}

fn normalize_id(id: &str) -> String {
    let id = id.trim();
    if id.is_empty() || id.starts_with("conv_") {
        id.to_owned()
    } else {
        format!("conv_{id}")
    }
}

fn open_data_directory(path: &FilePath) -> io::Result<Dir> {
    if !path.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Versioning requires an absolute directory",
        ));
    }
    let anchor = path.ancestors().last().expect("absolute runtime data path");
    let mut directory = Dir::open_ambient_dir(anchor, ambient_authority())?;
    for part in path
        .strip_prefix(anchor)
        .expect("path anchored at filesystem root")
        .components()
    {
        directory = directory.open_dir_nofollow(part.as_os_str())?;
    }
    Ok(directory)
}

fn optional_child(data: &Dir, folder: &str, id: &str) -> io::Result<Option<Dir>> {
    match files::child(data, folder, false)? {
        Some(folder) => files::child(&folder, id, false),
        None => Ok(None),
    }
}

fn verify_record_owner(store: &Store, data: &Dir, workspace_id: &str, id: &str) -> Result<(), RuntimeError> {
    // Legacy checkpoint directories are keyed only by conversation ID. An imported
    // duplicate in another workspace has no unambiguous owner for those records.
    let Some(conversations) = files::child(data, "conversations", false).map_err(storage)? else {
        return Ok(());
    };
    for entry in conversations.entries().map_err(storage)? {
        let entry = entry.map_err(storage)?;
        if entry.file_name() == workspace_id || !entry.file_type().map_err(storage)?.is_dir() {
            continue;
        }
        let name = entry.file_name();
        if let Some(other) = name.to_str() && super::validate_workspace(other).is_ok()
            && store.has_conversation(other, id)? {
            return Err(RuntimeError::new(409, "Versioning records have an ambiguous workspace owner"));
        }
    }
    Ok(())
}

fn message_id(row: &Value) -> Option<&str> {
    row["shallow_message_id"]
        .as_str()
        .filter(|id| !id.is_empty())
}

fn sequence(row: &Value) -> u64 {
    row["seq"]
        .as_u64()
        .or_else(|| row["seq"].as_str()?.parse().ok())
        .unwrap_or_default()
}

fn tracking_mode(value: &Value) -> &'static str {
    if value
        .as_str()
        .is_some_and(|value| value.trim().eq_ignore_ascii_case(CONVERSATION_ONLY))
    {
        CONVERSATION_ONLY
    } else {
        WORKSPACE_AND_CONVERSATION
    }
}

fn python_bool(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn storage(error: io::Error) -> RuntimeError {
    RuntimeError::new(500, format!("Versioning records unavailable: {error}"))
}

fn checkpoint_missing() -> RuntimeError {
    RuntimeError::new(404, "Checkpoint not found")
}

fn error_reply(error: RuntimeError) -> Reply {
    let status = StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    (status, Json(json!({"success":false,"error":error.message})))
}

#[cfg(test)]
#[path = "../../../test/2026-11-26_Rust版本恢复/versioning_contract.rs"]
mod tests;
