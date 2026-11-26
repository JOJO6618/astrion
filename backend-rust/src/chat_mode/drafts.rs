//! Chat composer drafts, stored beside conversations as `<chat>/drafts/<key>.json`.
//!
//! The JSON fields and merge rules mirror `modules/composer_drafts.py`: a
//! per-writer monotonic sequence watermark, an ordered last-32 writer list, and
//! a revision CAS checked only for a non-stale writer. Directory locking is
//! provided by `ChatMode::storage` (the Host Store mutex).

use std::path::PathBuf;

use serde_json::{Value, json};

use super::{
    ChatError,
    media,
    store::{self, ChatPaths},
};

pub(super) fn path(paths: &ChatPaths, key: &str) -> PathBuf {
    paths.root().join("drafts").join(format!("{key}.json"))
}

fn ensure_directory(paths: &ChatPaths) -> Result<(), ChatError> {
    std::fs::create_dir_all(paths.root().join("drafts"))
        .map_err(ChatError::from)
}

pub(super) fn read(paths: &ChatPaths, key: &str) -> Result<Value, ChatError> {
    match store::read_json(&path(paths, key))? {
        Value::Null => Ok(json!({})),
        Value::Object(document) => Ok(Value::Object(document)),
        _ => Err(ChatError::bad_request("Invalid composer draft")),
    }
}

/// Save one ordered draft update and return the latest document, including the
/// stale-writer case where the watermark is ignored but the latest data is read.
pub(super) fn save(paths: &ChatPaths, key: &str, payload: &Value) -> Result<Value, ChatError> {
    let text = match payload.get("text") {
        None => "",
        Some(Value::String(text)) => text.as_str(),
        Some(_) => return Err(ChatError::bad_request("Invalid Chat draft")),
    };
    if text.chars().count() > 1_000_000 {
        return Err(ChatError::bad_request("Invalid Chat draft"));
    }
    let media = media::resolve_refs(paths.root(), &payload.get("media").cloned().unwrap_or_else(|| json!([])))?;
    let client = payload["client_id"]
        .as_str()
        .filter(|value| !value.is_empty() && value.chars().count() <= 128)
        .ok_or_else(|| ChatError::bad_request("Invalid draft writer"))?;
    let sequence = payload["sequence"]
        .as_u64()
        .filter(|value| *value >= 1)
        .ok_or_else(|| ChatError::bad_request("Invalid draft sequence"))?;
    let base_revision = payload["base_revision"]
        .as_u64()
        .ok_or_else(|| ChatError::bad_request("Invalid draft revision"))?;

    ensure_directory(paths)?;
    let previous = read(paths, key)?;
    let mut versions = previous["writer_sequences"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    let current = versions.get(client).and_then(Value::as_u64).unwrap_or(0);
    if sequence > current {
        versions.shift_remove(client);
        versions.insert(client.to_owned(), json!(sequence));
        while versions.len() > 32 {
            let Some(first) = versions.keys().next().cloned() else {
                break;
            };
            versions.shift_remove(&first);
        }
    } else {
        // A stale writer is acknowledged with the latest snapshot; it must not
        // clear text another window already saved.
        return read(paths, key);
    }
    let revision = previous["revision"].as_u64().unwrap_or(0);
    if base_revision != revision {
        return Err(ChatError::bad_request(
            "Draft changed in another window; local text has been retained",
        ));
    }
    let document = json!({
        "text": text,
        "media": media,
        "writer_sequences": versions,
        "revision": revision + 1,
    });
    store::write_json(&path(paths, key), &document)?;
    read(paths, key)
}

/// Python's route reads the conversation before touching the draft for any key
/// other than `new`; this both validates ownership and blocks path injection.
pub(super) fn validate_key(paths: &ChatPaths, key: &str) -> Result<(), ChatError> {
    if key == "new" {
        return Ok(());
    }
    if !store::valid_conversation_id(key) {
        return Err(ChatError::bad_request("Invalid Chat conversation ID"));
    }
    paths.read_raw(key)?;
    Ok(())
}
