//! Chat-only global entries and topical memories.
//!
//! The file is `<DATA_DIR>/chat/memory/memories.json`, exactly Python's
//! `ChatMemory` layout: `{version:1, global:[...], topics:{...}}`. Both the
//! account REST surface and Chat's tools use this module so they cannot drift.

use std::{path::Path, time::SystemTime};

use serde_json::{Value, json};
use time::OffsetDateTime;

use super::{
    ChatError,
    store::{self, ChatPaths},
};

pub(super) fn path(root: &Path) -> std::path::PathBuf {
    root.join("memory").join("memories.json")
}

fn empty() -> Value {
    json!({"version": 1, "global": [], "topics": {}})
}

fn validate(value: Value) -> Result<Value, ChatError> {
    if value.is_null() {
        return Ok(empty());
    }
    if value["version"].as_u64() != Some(1)
        || !value["global"].is_array()
        || !value["topics"].is_object()
    {
        return Err(ChatError::bad_request("Invalid Chat memory document"));
    }
    Ok(value)
}

pub(super) fn load(paths: &ChatPaths) -> Result<Value, ChatError> {
    validate(store::read_json(&path(paths.root()))?)
}

fn save(paths: &ChatPaths, value: &Value) -> Result<(), ChatError> {
    std::fs::create_dir_all(paths.root().join("memory"))?;
    store::write_json(&path(paths.root()), value)
}

pub(super) fn snapshot(paths: &ChatPaths) -> Result<Value, ChatError> {
    load(paths)
}

pub(super) fn update(paths: &ChatPaths, payload: &Value) -> Result<Value, ChatError> {
    let operation = payload["operation"]
        .as_str()
        .filter(|value| matches!(*value, "append" | "replace" | "delete"))
        .ok_or_else(|| ChatError::bad_request("Invalid memory operation"))?;
    let content = payload["content"]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if operation != "delete" && content.is_none() {
        return Err(ChatError::bad_request("Memory content is required"));
    }
    let index = payload["index"].as_u64().map(|value| value as usize);
    let mut memory = load(paths)?;
    let entries = memory["global"]
        .as_array_mut()
        .expect("validated Chat memory entries");
    if operation != "append"
        && index.is_none_or(|index| index == 0 || index > entries.len())
    {
        return Err(ChatError::bad_request("Memory index is out of range"));
    }
    match operation {
        "append" => entries.push(json!(content.expect("validated memory content"))),
        "replace" => {
            entries[index.expect("validated index") - 1] =
                json!(content.expect("validated memory content"))
        }
        _ => {
            entries.remove(index.expect("validated index") - 1);
        }
    }
    let count = entries.len();
    save(paths, &memory)?;
    Ok(json!({"success": true, "count": count, "operation": operation}))
}

pub(super) fn write_topic(paths: &ChatPaths, payload: &Value) -> Result<Value, ChatError> {
    let name = payload["name"]
        .as_str()
        .filter(|value| valid_topic_name(value))
        .ok_or_else(|| {
            ChatError::bad_request("Memory names use lowercase letters, digits and underscores")
        })?;
    let description = payload["description"]
        .as_str()
        .filter(|value| !value.trim().is_empty() && value.chars().count() <= 100)
        .ok_or_else(|| {
            ChatError::bad_request("A description of at most 100 characters is required")
        })?;
    let content = payload["content"]
        .as_str()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| ChatError::bad_request("Topic content is required"))?;
    let mut memory = load(paths)?;
    memory["topics"][name] = json!({
        "name": name,
        "description": description,
        "content": content,
        "updated_at": local_iso_now(),
    });
    save(paths, &memory)?;
    Ok(json!({"success": true, "name": name}))
}

pub(super) fn read_topic(paths: &ChatPaths, name: &str) -> Result<Value, ChatError> {
    let memory = load(paths)?;
    memory["topics"]
        .get(name)
        .cloned()
        .filter(|value| !value.is_null())
        .ok_or_else(|| ChatError::not_found("Chat memory not found"))
}

pub(super) fn search(
    paths: &ChatPaths,
    keywords: &[String],
    limit: usize,
) -> Result<Vec<Value>, ChatError> {
    let memory = load(paths)?;
    let mut ranked = Vec::new();
    if let Some(topics) = memory["topics"].as_object() {
        for topic in topics.values() {
            let haystack = format!(
                "{}\n{}\n{}",
                topic["name"].as_str().unwrap_or_default(),
                topic["description"].as_str().unwrap_or_default(),
                topic["content"].as_str().unwrap_or_default(),
            )
            .to_lowercase();
            let score = keywords
                .iter()
                .filter(|keyword| haystack.contains(keyword.as_str()))
                .count();
            if score > 0 {
                ranked.push((
                    score,
                    json!({
                        "name": topic["name"],
                        "description": topic["description"],
                        "snippet": topic["content"]
                            .as_str()
                            .unwrap_or_default()
                            .chars()
                            .take(400)
                            .collect::<String>(),
                    }),
                ));
            }
        }
    }
    ranked.sort_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then_with(|| left.1["name"].as_str().cmp(&right.1["name"].as_str()))
    });
    Ok(ranked
        .into_iter()
        .take(limit.clamp(1, 10))
        .map(|(_, value)| value)
        .collect())
}

/// Prompt read path used by the Chat worker. It reads the same file under the
/// Chat storage gate but does not write or provision defaults.
pub(super) fn prompt_sections(root: &Path) -> Result<(Vec<String>, Vec<(String, String)>), ChatError> {
    let memory = validate(store::read_json(&path(root))?)?;
    let global = memory["global"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    let topics = memory["topics"]
        .as_object()
        .into_iter()
        .flatten()
        .filter_map(|(name, topic)| {
            let description = topic["description"].as_str()?.to_owned();
            Some((name.clone(), description))
        })
        .collect();
    Ok((global, topics))
}

pub(super) fn valid_topic_name(value: &str) -> bool {
    let mut bytes = value.bytes();
    bytes
        .next()
        .is_some_and(|byte| byte.is_ascii_lowercase())
        && bytes.all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
        && value.len() <= 80
}

fn local_iso_now() -> String {
    let now = OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc());
    let format = time::format_description::parse(
        "[year]-[month]-[day]T[hour]:[minute]:[second].[subsecond digits:6]",
    )
    .expect("static Chat memory timestamp format");
    now.format(&format)
        .unwrap_or_else(|_| {
            SystemTime::now()
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
                .to_string()
        })
}
