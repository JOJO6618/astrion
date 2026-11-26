use std::{collections::BTreeMap, fmt::Write, io::ErrorKind, path::Path};

use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use ring::digest::{SHA256, digest};
use serde_json::{Value, json};
use uuid::Uuid;

use super::{ChatError, document_path, tags};
use crate::{
    knowledge::storage::{ensure_dir, open_canonical_dir, publish, read_bytes},
    legacy::media::BlobStore,
};

const MAX_SNAPSHOT: u64 = 128 * 1024 * 1024;
const MAX_SUMMARY: u64 = 4 * 1024 * 1024;

pub(super) fn valid_id(id: &str) -> bool {
    id.starts_with("handoff_")
        && id.len() <= 100
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

pub(super) fn freeze(
    root: &Path,
    document: &Value,
    handoff: &str,
    leaf: &Value,
) -> Result<Value, ChatError> {
    if !valid_id(handoff) {
        return Err(ChatError::bad_request("Invalid Agent handoff identity"));
    }
    let root = open_canonical_dir(root)?;
    let jobs = ensure_dir(&root, "handoffs")?;
    let job = ensure_dir(&jobs, handoff)?;
    match read_bytes(&job, "snapshot.json", MAX_SNAPSHOT) {
        Ok(bytes) => {
            let snapshot: Value = serde_json::from_slice(&bytes)?;
            if snapshot["id"] != document["id"]
                || snapshot["metadata"]["chat"]["latest_leaf_id"] != *leaf
                || !snapshot["messages"].is_array()
            {
                return Err(ChatError::conflict(
                    "Handoff snapshot belongs to a different Chat branch",
                ));
            }
            return Ok(snapshot);
        }
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }
    let mut snapshot = document.clone();
    snapshot["messages"] = json!(document_path(document)?);
    let bytes = serde_json::to_vec(&snapshot)?;
    if bytes.len() as u64 > MAX_SNAPSHOT {
        return Err(ChatError::bad_request("Chat handoff snapshot is too large"));
    }
    publish(&job, "snapshot.json", &bytes, false)?;
    Ok(snapshot)
}

pub(super) fn relative_path(id: &str) -> String {
    format!(".astrion/handoff/{id}")
}

fn storage(workspace: &Path) -> Result<Dir, ChatError> {
    let root = open_canonical_dir(workspace)?;
    let astrion = ensure_dir(&root, ".astrion")?;
    Ok(ensure_dir(&astrion, "handoff")?)
}

pub(super) fn existing_summary(
    workspace: &Path,
    id: &str,
    snapshot: &Value,
) -> Result<Option<String>, ChatError> {
    let destination = storage(workspace)?;
    let current = match destination.open_dir_nofollow(id) {
        Ok(dir) => dir,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let leaf = &snapshot["metadata"]["chat"]["latest_leaf_id"];
    let recorded: Value = serde_json::from_slice(&read_bytes(&current, "manifest.json", 4096)?)?;
    if recorded["conversation_id"] != snapshot["id"]
        || recorded["leaf_id"] != *leaf
        || recorded["sha256"] != sha256(&python_json(&snapshot["messages"])?)
    {
        return Err(ChatError::conflict(
            "Existing handoff materials belong to another snapshot",
        ));
    }
    // Check both named deliverables and every referenced blob before returning a retry receipt.
    read_bytes(&current, "conversation.md", MAX_SNAPSHOT)?;
    for message in visible_messages(snapshot) {
        for reference in message["media_refs"].as_array().into_iter().flatten() {
            let media = current.open_dir_nofollow("media")?;
            let name = media_filename(reference)?;
            let contents = read_bytes(&media, &name, 100 * 1024 * 1024)?;
            if !reference["media_id"].as_str().is_some_and(|id| {
                id.strip_prefix("sha256:")
                    .is_some_and(|hash| hash == sha256(&contents))
            }) {
                return Err(ChatError::conflict(
                    "Handoff media content does not match its identity",
                ));
            }
        }
    }
    let summary = String::from_utf8(read_bytes(&current, "summary.md", MAX_SUMMARY)?)
        .map_err(ChatError::storage)?;
    if summary.trim().is_empty() {
        return Err(ChatError::conflict("Existing handoff summary is empty"));
    }
    Ok(Some(summary))
}

pub(super) fn export(
    root: &Path,
    workspace: &Path,
    id: &str,
    snapshot: &Value,
    summary: &str,
) -> Result<(), ChatError> {
    let destination = storage(workspace)?;
    let temp_name = format!(".{id}-{}.tmp", Uuid::new_v4().simple());
    destination.create_dir(&temp_name)?;
    let result = (|| {
        let stage = destination.open_dir_nofollow(&temp_name)?;
        let report = report(root, snapshot, &stage)?;
        publish(&stage, "conversation.md", report.as_bytes(), false)?;
        publish(
            &stage,
            "summary.md",
            format!("{}\n", summary.trim()).as_bytes(),
            false,
        )?;
        let manifest = json!({
            "conversation_id":snapshot["id"],
            "leaf_id":snapshot["metadata"]["chat"]["latest_leaf_id"],
            "sha256":sha256(&python_json(&snapshot["messages"])?),
        });
        publish(
            &stage,
            "manifest.json",
            &serde_json::to_vec(&manifest)?,
            false,
        )?;
        destination.rename(&temp_name, &destination, id)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = destination.remove_dir_all(&temp_name);
    }
    result
}

fn visible_messages(snapshot: &Value) -> impl Iterator<Item = &Value> {
    snapshot["messages"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|message| {
            matches!(message["role"].as_str(), Some("user" | "assistant"))
                && message["metadata"]["visibility"] != "hidden"
        })
}

fn report(root: &Path, snapshot: &Value, stage: &Dir) -> Result<String, ChatError> {
    let mut report = format!("# {}\n\n", snapshot["title"].as_str().unwrap_or("Chat"));
    let mut media_store = None;
    let mut copied = std::collections::HashSet::new();
    for message in visible_messages(snapshot) {
        let role = if message["role"] == "user" {
            "用户"
        } else {
            "助手"
        };
        writeln!(
            &mut report,
            "## {role}\n\n{}\n",
            clean_content(&message["content"])
        )
        .unwrap();
        for reference in message["media_refs"].as_array().into_iter().flatten() {
            let id = reference["media_id"]
                .as_str()
                .ok_or_else(|| ChatError::bad_request("Invalid Chat media identity"))?;
            let kind = reference["kind"].as_str().unwrap_or_default();
            let name = media_filename(reference)?;
            if copied.insert(name.clone()) {
                let store = match media_store.as_ref() {
                    Some(store) => store,
                    None => media_store.insert(
                        BlobStore::open(root)
                            .ok_or_else(|| ChatError::not_found("Chat media unavailable"))?,
                    ),
                };
                let entry = store
                    .entry(id)
                    .filter(|entry| {
                        entry["kind"] == kind && entry["mime_type"] == reference["mime_type"]
                    })
                    .ok_or_else(|| {
                        ChatError::not_found("A referenced Chat image or video is unavailable")
                    })?;
                let bytes = store
                    .registered_bytes(id)
                    .filter(|bytes| entry["size"].as_u64() == Some(bytes.len() as u64))
                    .ok_or_else(|| {
                        ChatError::not_found("A referenced Chat image or video is unavailable")
                    })?;
                if id.strip_prefix("sha256:") != Some(sha256(&bytes).as_str()) {
                    return Err(ChatError::conflict(
                        "Chat media content does not match its identity",
                    ));
                }
                publish(&ensure_dir(stage, "media")?, &name, &bytes, false)?;
            }
            writeln!(&mut report, "[{kind}](media/{name})\n").unwrap();
        }
    }
    Ok(report)
}

pub(super) fn clean_content(content: &Value) -> String {
    let mut text = super::super::store::content_text(content);
    for tag in tags(content) {
        text = text.replace(&tag, "");
    }
    text.trim().to_owned()
}

fn media_filename(reference: &Value) -> Result<String, ChatError> {
    let id = reference["media_id"]
        .as_str()
        .and_then(|id| id.strip_prefix("sha256:"))
        .filter(|id| {
            id.len() == 64
                && id
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
        .ok_or_else(|| ChatError::bad_request("Invalid Chat media identity"))?;
    let extension = match reference["mime_type"].as_str() {
        Some("image/png") => "png",
        Some("image/jpeg") => "jpg",
        Some("image/webp") => "webp",
        Some("image/gif") => "gif",
        Some("image/bmp") => "bmp",
        Some("video/mp4") => "mp4",
        Some("video/webm") => "webm",
        Some("video/quicktime") => "mov",
        _ => "bin",
    };
    Ok(format!("sha256-{id}.{extension}"))
}

fn sha256(bytes: &[u8]) -> String {
    digest(&SHA256, bytes)
        .as_ref()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

// Match Python's ensure_ascii=False, sort_keys=True manifest representation.
fn python_json(value: &Value) -> Result<Vec<u8>, ChatError> {
    fn write_json(value: &Value, output: &mut String) -> Result<(), serde_json::Error> {
        match value {
            Value::Object(fields) => {
                output.push('{');
                let sorted: BTreeMap<_, _> = fields.iter().collect();
                for (index, (key, value)) in sorted.into_iter().enumerate() {
                    if index != 0 {
                        output.push_str(", ");
                    }
                    output.push_str(&serde_json::to_string(key)?);
                    output.push_str(": ");
                    write_json(value, output)?;
                }
                output.push('}');
            }
            Value::Array(values) => {
                output.push('[');
                for (index, value) in values.iter().enumerate() {
                    if index != 0 {
                        output.push_str(", ");
                    }
                    write_json(value, output)?;
                }
                output.push(']');
            }
            _ => output.push_str(&serde_json::to_string(value)?),
        }
        Ok(())
    }
    let mut output = String::new();
    write_json(value, &mut output)?;
    Ok(output.into_bytes())
}
