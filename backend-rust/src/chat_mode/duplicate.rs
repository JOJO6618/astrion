//! Copy an idle Chat conversation, preserving its message tree and per-chat
//! settings while giving the copy a fresh identity and empty command receipts.

use ring::digest;
use serde_json::{Value, json};

use super::{
    ChatError,
    handoff,
    store::ChatPaths,
    tasks, tree,
};

/// Idempotency key used as the copied conversation's `creation_operation_id`.
/// Mirrors Python: `copy_` + the full SHA-256 hex digest of `id:operation`.
pub(super) fn operation_key(conversation_id: &str, operation_id: &str) -> String {
    let mut context = digest::Context::new(&digest::SHA256);
    context.update(conversation_id.as_bytes());
    context.update(b":");
    context.update(operation_id.as_bytes());
    let hex = context
        .finish()
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("copy_{hex}")
}

fn conversation_for_operation(
    paths: &ChatPaths,
    key: &str,
) -> Result<Option<String>, ChatError> {
    for id in paths.list_ids()? {
        let raw = match paths.read_raw(&id) {
            Ok(raw) => raw,
            Err(error) if matches!(error.status, 400 | 404) => continue,
            Err(error) => return Err(error),
        };
        if raw["metadata"]["chat"]["creation_operation_id"] == key {
            return Ok(Some(id));
        }
    }
    Ok(None)
}

pub(super) fn duplicate(
    paths: &ChatPaths,
    conversation_id: &str,
    payload: &Value,
) -> Result<String, ChatError> {
    let operation_id = tree::operation_id(payload.get("operation_id"))?;
    let source = paths.read_raw(conversation_id)?;
    if tree::active_turn(&source).is_some() {
        return Err(ChatError::bad_request(
            "Copy the conversation after its current reply finishes",
        ));
    }
    let key = operation_key(conversation_id, &operation_id);
    let new_id = match conversation_for_operation(paths, &key)? {
        Some(existing) => existing,
        None => {
            let metadata = &source["metadata"];
            let now = tasks::now();
            let new_id = paths.next_conversation_id(now)?;
            let initial = paths.initial_document(
                &new_id,
                now,
                &json!({
                    "operation_id": key,
                    "model_key": metadata["model_key"],
                    "thinking_mode": metadata["thinking_mode"],
                    "settings": {
                        "network_enabled": metadata["network_enabled"],
                        "chatgpt_fast_mode": metadata["chatgpt_fast_mode"],
                        "reasoning_effort": metadata["reasoning_effort"],
                    },
                }),
            )?;
            paths.create(&new_id, initial)?;
            new_id
        }
    };

    // Update the copy in place after creation, as Python does. A retry of the
    // same operation must finish a partially populated copy instead of
    // returning it empty.
    let mut document = paths.read_raw(&new_id)?;
    if document["metadata"]["chat"]["copied_from"] == conversation_id {
        return Ok(new_id);
    }
    document["messages"] = source["messages"].clone();
    document["metadata"] = source["metadata"].clone();
    document["metadata"]["chat"]["commands"] = json!({});
    document["metadata"]["chat"]["creation_operation_id"] = json!(key);
    document["metadata"]["chat"]["copied_from"] = json!(conversation_id);
    document["metadata"]["pending_network_notices"] = json!([]);
    let title = source["title"].as_str().unwrap_or("Chat");
    document["title"] = json!(format!("{title} (copy)"));
    // Keep historical suggestion decisions, but strip live ownership and
    // retry jobs so the copy cannot replay the source's Agent handoff.
    handoff::detach(&mut document)?;
    paths.write_raw(&new_id, document, false)?;
    Ok(new_id)
}
