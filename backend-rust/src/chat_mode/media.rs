//! Resolve Chat draft media IDs through the trusted media index.
//!
//! Python's `modules.chat_mode.media.resolve_refs` never trusts client fields:
//! it looks each ID up in `MediaStore(root).get_media_entry(...)` and returns
//! only the registered kind/mime/size/hash. The Rust counterpart reuses the
//! existing safe blob/index reader from `legacy::media`.

use std::path::Path;

use serde_json::{Value, json};

use super::ChatError;
use crate::legacy::media::BlobStore;

pub(super) fn resolve_refs(root: &Path, values: &Value) -> Result<Vec<Value>, ChatError> {
    resolve_typed_refs(root, values, None)
}

pub(super) fn resolve_typed_refs(
    root: &Path,
    values: &Value,
    kind: Option<&str>,
) -> Result<Vec<Value>, ChatError> {
    let Some(values) = values.as_array() else {
        return Err(ChatError::bad_request("Invalid media list"));
    };
    if values.len() > 20 {
        return Err(ChatError::bad_request("Invalid media list"));
    }
    if values.is_empty() {
        return Ok(Vec::new());
    }
    let store = BlobStore::open(root);
    let mut resolved = Vec::with_capacity(values.len());
    for value in values {
        let media_id = value
            .as_str()
            .or_else(|| value["media_id"].as_str())
            .map(str::trim)
            .filter(|id| !id.is_empty() && id.chars().count() <= 100)
            .ok_or_else(|| ChatError::bad_request("Expected an uploaded media ID"))?;
        let entry = store
            .as_ref()
            .and_then(|store| store.entry(media_id))
            .ok_or_else(|| ChatError::not_found("Chat media unavailable"))?;
        if !matches!(entry["kind"].as_str(), Some("image" | "video")) {
            return Err(ChatError::not_found("Chat media unavailable"));
        }
        if kind.is_some_and(|kind| entry["kind"] != kind) {
            return Err(ChatError::bad_request("Wrong Chat media kind"));
        }
        let mime_type = entry["mime_type"]
            .as_str()
            .filter(|value| !value.is_empty())
            .ok_or_else(|| ChatError::not_found("Chat media unavailable"))?;
        let size = entry["size"]
            .as_u64()
            .ok_or_else(|| ChatError::not_found("Chat media unavailable"))?;
        let sha256 = entry["sha256"]
            .as_str()
            .filter(|value| !value.is_empty())
            .ok_or_else(|| ChatError::not_found("Chat media unavailable"))?;
        resolved.push(json!({
            "media_id": media_id,
            "kind": entry["kind"],
            "mime_type": mime_type,
            "size": size,
            "sha256": sha256,
        }));
    }
    Ok(resolved)
}
