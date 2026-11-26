//! Recover idle Agent session markers after acquiring the main-task gate.

use serde_json::{Value, json};

use crate::{approval::ExecutionMode, web_status::recover_pending_host_modes};

use super::{RuntimeError, Store};

const PENDING_MODES: &[&str] = &[
    "pending_permission_mode",
    "pending_execution_mode",
    "pending_network_permission",
];
const OLD_TASK_MARKERS: &[&str] = &[
    "active_task_id",
    "active_turn_id",
    "active_workflow_id",
    "active_goal_id",
];
const COMPRESSION_FIELDS: &[&str] = &[
    "compression_mode",
    "compression_stage",
    "compression_job_id",
    "compression_resume_payload",
    "compression_pid",
];

pub(super) fn recover_idle_agent(store: &Store, document: &mut Value) -> Result<(), RuntimeError> {
    let metadata = &document["metadata"];
    let has_stale_fields = PENDING_MODES
        .iter()
        .chain(OLD_TASK_MARKERS)
        .any(|key| !document[*key].is_null() || !metadata[*key].is_null())
        || !matches!(
            metadata["compression_in_progress"],
            Value::Null | Value::Bool(false)
        )
        || COMPRESSION_FIELDS
            .iter()
            .any(|key| !metadata[*key].is_null());
    if !has_stale_fields {
        return Ok(());
    }
    let mut metadata = document["metadata"].clone();
    for &key in PENDING_MODES {
        if !document[key].is_null() {
            if !metadata[key].is_null() && metadata[key] != document[key] {
                return Err(invalid_state());
            }
            metadata[key] = document
                .as_object_mut()
                .expect("Agent object")
                .shift_remove(key)
                .expect("existing key");
        }
    }
    if PENDING_MODES.iter().any(|key| !metadata[*key].is_null()) {
        metadata = recover_pending_host_modes(
            &store.data_dir,
            &metadata,
            store
                .process_resources
                .available_mode(ExecutionMode::Direct),
            store.process_resources.available(),
        )?;
    }
    for &key in OLD_TASK_MARKERS {
        for container in [&mut metadata, &mut *document] {
            if !container[key].is_null() && container[key] != false {
                if !container[key].as_str().is_some_and(legacy_task_marker) {
                    return Err(invalid_state());
                }
                container[key] = Value::Null;
            }
        }
    }
    if !matches!(
        metadata["compression_in_progress"],
        Value::Null | Value::Bool(false)
    ) || COMPRESSION_FIELDS
        .iter()
        .any(|key| !metadata[*key].is_null())
    {
        validate_compression_marker(&metadata, document["id"].as_str().expect("Agent identity"))?;
        metadata["compression_in_progress"] = json!(false);
        for &key in COMPRESSION_FIELDS {
            if metadata.get(key).is_some() {
                metadata[key] = Value::Null;
            }
        }
    }
    document["metadata"] = metadata;
    Ok(())
}

fn legacy_task_marker(id: &str) -> bool {
    id.len() <= 192
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn valid_compression_mode(mode: &Value) -> bool {
    mode.is_null() || matches!(mode.as_str(), Some("auto" | "manual" | "requested"))
}

fn validate_compression_marker(metadata: &Value, id: &str) -> Result<(), RuntimeError> {
    let payload = &metadata["compression_resume_payload"];
    if !matches!(
        metadata["compression_in_progress"],
        Value::Null | Value::Bool(_)
    ) || !valid_compression_mode(&metadata["compression_mode"])
        || !metadata["compression_stage"].is_null() && !metadata["compression_stage"].is_string()
        || !metadata["compression_pid"].is_null() && metadata["compression_pid"].as_u64().is_none()
        || !metadata["compression_job_id"].is_null()
            && !metadata["compression_job_id"]
                .as_str()
                .is_some_and(legacy_task_marker)
        || !payload.is_null()
            && (!payload.is_object()
                || payload["conversation_id"] != id
                || !valid_compression_mode(&payload["mode"]))
    {
        return Err(invalid_state());
    }
    Ok(())
}

fn invalid_state() -> RuntimeError {
    RuntimeError::new(422, "Invalid previous Agent runtime state")
}
