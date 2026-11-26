//! Branch and metadata mutations over the authoritative Chat message tree.
//!
//! The tree math lives in `legacy::tree`; this module only adds the service
//! contract around it: stable operation receipts, revision conflicts, network
//! notices, conversation settings and crash recovery.

use serde_json::{Map, Value, json};

use super::{ChatError, store::ChatPaths};
use crate::legacy::{LegacyChatTurn, LegacyDocument};

pub(super) const MAX_OPERATION_ID: usize = 128;

pub(super) enum Selection<'a> {
    Send {
        parent_id: Option<&'a str>,
        message: Value,
    },
    Regenerate {
        turn_id: &'a str,
    },
    EditAndSend {
        message_id: &'a str,
        content: &'a str,
    },
}

impl<'a> Selection<'a> {
    fn into_legacy(self) -> LegacyChatTurn<'a> {
        match self {
            Self::Send { parent_id, message } => LegacyChatTurn::Send { parent_id, message },
            Self::Regenerate { turn_id } => LegacyChatTurn::Regenerate { turn_id },
            Self::EditAndSend { message_id, content } => {
                LegacyChatTurn::EditAndSend { message_id, content }
            }
        }
    }
}

pub(super) fn operation_id_str(value: Option<&str>) -> Result<String, ChatError> {
    let value = value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ChatError::bad_request("A stable operation ID is required"))?;
    if value.chars().count() > MAX_OPERATION_ID {
        return Err(ChatError::bad_request("Operation ID is too long"));
    }
    Ok(value.to_owned())
}

pub(super) fn operation_id(value: Option<&Value>) -> Result<String, ChatError> {
    operation_id_str(value.and_then(Value::as_str))
}

/// Applies one durable Chat command with an idempotency receipt stored in
/// `metadata.chat.commands`, exactly like Python's ChatStorage.command.
fn command(
    paths: &ChatPaths,
    id: &str,
    operation_id: &str,
    payload: &Value,
    create: bool,
    expected_revision: Option<u64>,
    apply: impl FnOnce(&mut LegacyDocument) -> Result<Value, ChatError>,
) -> Result<Value, ChatError> {
    let mut raw = paths.read_raw(id)?;
    let fingerprint = fingerprint(payload);
    if let Some(previous) = raw["metadata"]["chat"]["commands"].get(operation_id) {
        if previous["fingerprint"].as_str() != Some(fingerprint.as_str()) {
            return Err(ChatError::conflict(
                "Operation ID reused for a different Chat command",
            ));
        }
        return Ok(previous["result"].clone());
    }
    let mut document = LegacyDocument::from_value(raw.clone())?;
    document.check_chat_revision(expected_revision)?;
    let value = apply(&mut document)?;
    raw = document.into_raw();
    // Python's ChatStorage.command stores ``{revision, value}``; the receipt is
    // also the REST response body, so both implementations must agree on disk.
    let receipt = json!({
        "revision": raw["metadata"]["chat"]["revision"],
        "value": value,
    });
    raw["metadata"]["chat"]["commands"][operation_id] = json!({
        "fingerprint": fingerprint,
        "result": receipt.clone(),
    });
    paths.write_raw(id, raw, create)?;
    Ok(receipt)
}

#[allow(clippy::too_many_arguments)]
pub(super) fn begin(
    paths: &ChatPaths,
    id: &str,
    operation_id: &str,
    selection: Selection<'_>,
    create: bool,
    revision: Option<u64>,
    presentation_id: Option<&str>,
    configured: &Value,
    updated_at: f64,
) -> Result<Value, ChatError> {
    // Python's payload always carries these five keys (with null for unused
    // selectors). Fingerprints are compared byte-for-byte on canonical JSON, so
    // field names and nulls here are part of the durable compatibility contract.
    let payload = json!({
        "action": "begin",
        "parent_id": selection.parent_id(),
        "message": selection.message(),
        "regenerate_turn_id": selection.regenerate_turn_id(),
        "edit_message_id": selection.edit_message_id(),
    });
    command(paths, id, operation_id, &payload, create, revision, |document| {
        super::handoff::before_send(document.raw(), operation_id)?;
        let previous_leaf = document.raw()["metadata"]["chat"]["latest_leaf_id"].clone();
        let empty = document.raw()["messages"]
            .as_array()
            .is_none_or(Vec::is_empty);
        let mut raw = apply_configured(document.raw().clone(), configured)?;
        if empty
            && let Some(presentation) = presentation_id.filter(|value| !value.is_empty())
        {
            raw["metadata"]["chat"]["handoff_presentation_id"] = json!(presentation);
        }
        *document = LegacyDocument::from_value(raw)?;
        let turn_id = document.begin_chat_turn(selection.into_legacy(), updated_at)?;
        super::handoff::after_send(document, &previous_leaf)?;
        let raw = document.raw();
        let turn = raw["metadata"]["chat"]["turns"]
            .as_array()
            .and_then(|turns| turns.iter().find(|turn| turn["id"] == turn_id))
            .cloned()
            .ok_or_else(|| ChatError::storage("Chat begin receipt is incomplete"))?;
        Ok(turn)
    })
}

/// Applies conversation-local settings before a turn begins. Validation happens
/// in the route before this helper runs.
fn apply_configured(mut raw: Value, configured: &Value) -> Result<Value, ChatError> {
    if let Some(settings) = configured.as_object()
        && !settings.is_empty()
    {
        let metadata = raw["metadata"]
            .as_object_mut()
            .ok_or_else(|| ChatError::bad_request("Chat metadata must be an object"))?;
        for (key, value) in settings {
            metadata.insert(key.clone(), value.clone());
        }
        if let Some(thinking) = settings.get("thinking_mode").and_then(Value::as_bool) {
            metadata.insert(
                "run_mode".into(),
                json!(if thinking { "thinking" } else { "fast" }),
            );
        }
    }
    Ok(raw)
}

impl Selection<'_> {
    fn parent_id(&self) -> Option<&str> {
        match self {
            Self::Send { parent_id, .. } => *parent_id,
            Self::Regenerate { .. } | Self::EditAndSend { .. } => None,
        }
    }

    fn message(&self) -> Option<Value> {
        match self {
            Self::Send { message, .. } => Some(message.clone()),
            Self::Regenerate { .. } => None,
            Self::EditAndSend { content, .. } => Some(json!({
                "role": "user",
                "content": content,
                "media_refs": [],
                "metadata": {"visibility":"chat","starts_work":true},
            })),
        }
    }

    fn regenerate_turn_id(&self) -> Option<&str> {
        match self {
            Self::Regenerate { turn_id } => Some(turn_id),
            Self::Send { .. } | Self::EditAndSend { .. } => None,
        }
    }

    fn edit_message_id(&self) -> Option<&str> {
        match self {
            Self::EditAndSend { message_id, .. } => Some(message_id),
            Self::Send { .. } | Self::Regenerate { .. } => None,
        }
    }
}

pub(super) fn append(
    paths: &ChatPaths,
    id: &str,
    turn_id: &str,
    message: Value,
    updated_at: f64,
    statistics: Option<&Value>,
) -> Result<Value, ChatError> {
    let message_id = message["message_id"]
        .as_str()
        .filter(|id| !id.is_empty())
        .ok_or_else(|| ChatError::bad_request("Persisted messages require a stable ID"))?
        .to_owned();
    // The message ID is Python's retry key, and timestamps are added by the
    // writer so they must not participate in the operation fingerprint.
    let mut stable_message = message.clone();
    if let Some(object) = stable_message.as_object_mut() {
        object.shift_remove("timestamp");
    }
    let payload = json!({
        "action":"append",
        "turn":turn_id,
        "message":stable_message,
    });
    command(paths, id, &message_id, &payload, false, None, |document| {
        document.append_chat_messages(turn_id, vec![message], updated_at, statistics)?;
        Ok(json!({"message_id":message_id}))
    })
}

pub(super) fn finish(
    paths: &ChatPaths,
    id: &str,
    turn_id: &str,
    status: &str,
    updated_at: f64,
) -> Result<Value, ChatError> {
    let operation = format!("finish:{turn_id}:{status}");
    let payload = json!({"action":"finish","turn":turn_id,"status":status});
    command(paths, id, &operation, &payload, false, None, |document| {
        document.finish_chat_turn(turn_id, status, updated_at)?;
        super::handoff::after_finish(document, turn_id, status == "complete")?;
        Ok(Value::Null)
    })
}

pub(super) fn edit(
    paths: &ChatPaths,
    id: &str,
    operation_id: &str,
    message_id: &str,
    content: &str,
    revision: Option<u64>,
    updated_at: f64,
) -> Result<Value, ChatError> {
    let payload = json!({"action":"edit","message_id":message_id,"content":content});
    command(paths, id, operation_id, &payload, false, revision, |document| {
        document.edit_chat_message(message_id, content, updated_at)?;
        super::handoff::after_edit(document)?;
        Ok(json!({"message_id": message_id}))
    })
}

pub(super) fn recover(
    paths: &ChatPaths,
    id: &str,
    turn_id: &str,
    status: &str,
    updated_at: f64,
) -> Result<Value, ChatError> {
    // Python's recovery mutates the document directly instead of writing a
    // command receipt; keep the same on-disk shape and rely on turn state for
    // idempotence.
    let mut document = paths.read_document(id)?;
    let raw = document.raw().clone();
    let Some(turn) = raw["metadata"]["chat"]["turns"]
        .as_array()
        .and_then(|turns| turns.iter().find(|turn| turn["id"] == turn_id))
        .cloned()
    else {
        return Ok(json!({"turn_id": turn_id, "status": status, "recovered": false}));
    };
    if turn["status"] != "running" {
        return Ok(json!({"turn_id": turn_id, "status": turn["status"], "recovered": false}));
    }
    {
        let tip = turn["tip_id"]
            .as_str()
            .or_else(|| turn["parent_id"].as_str())
            .ok_or_else(|| ChatError::storage("Chat turn has no path root"))?;
        let path = document.chat_path(Some(tip))?;
        let mut pending: Map<String, Value> = Map::new();
        for message in &path {
            if message["role"] == "assistant"
                && let Some(calls) = message["tool_calls"].as_array()
            {
                for call in calls {
                    if let Some(call_id) = call["id"].as_str() {
                        pending.insert(
                            call_id.to_owned(),
                            json!(call["function"]["name"].as_str().unwrap_or_default()),
                        );
                    }
                }
            } else if message["role"] == "tool"
                && let Some(call_id) = message["tool_call_id"].as_str()
            {
                pending.shift_remove(call_id);
            }
        }
        for (call_id, name) in pending {
            document.append_chat_messages(
                turn_id,
                vec![json!({
                    "message_id": format!("msg_{}", uuid::Uuid::new_v4().simple()),
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json!({
                        "success": false,
                        "status": "cancelled",
                        "message": "执行已中断，未取得完成结果",
                    }).to_string(),
                })],
                updated_at,
                None,
            )?;
        }
        document.finish_chat_turn(turn_id, status, updated_at)?;
        super::handoff::after_finish(&mut document, turn_id, false)?;
    }
    let raw = document.into_raw();
    let revision = raw["metadata"]["chat"]["revision"].clone();
    paths.write_raw(id, raw, false)?;
    Ok(json!({
        "turn_id": turn_id,
        "status": status,
        "recovered": true,
        "revision": revision,
    }))
}

pub(super) fn set_network(
    paths: &ChatPaths,
    id: &str,
    operation_id: &str,
    enabled: bool,
    updated_at: f64,
) -> Result<Value, ChatError> {
    let payload = json!({"action":"network","enabled":enabled});
    command(paths, id, operation_id, &payload, false, None, |document| {
        let mut raw = document.raw().clone();
        {
            let metadata = raw["metadata"].as_object_mut().expect("validated metadata");
            if metadata["network_enabled"].as_bool() != Some(enabled) {
                let revision = metadata["network_revision"].as_u64().unwrap_or(0) + 1;
                metadata.insert("network_enabled".into(), json!(enabled));
                metadata.insert("network_revision".into(), json!(revision));
                metadata["pending_network_notices"]
                    .as_array_mut()
                    .ok_or_else(|| ChatError::storage("Invalid Chat network notice queue"))?
                    .push(json!({"revision":revision,"enabled":enabled}));
                let chat_revision = metadata["chat"]["revision"].as_u64().unwrap_or(0) + 1;
                metadata["chat"]["revision"] = json!(chat_revision);
                raw["updated_at"] = json!(crate::chat_mode::store::timestamp(updated_at)?);
            }
        }
        *document = LegacyDocument::from_value(raw)?;
        let raw = document.raw();
        Ok(json!({
            "enabled": raw["metadata"]["network_enabled"],
            "revision": raw["metadata"]["network_revision"],
        }))
    })
}

/// Turns pending network notices into hidden user messages in the current turn.
pub(super) fn consume_network_notices(
    paths: &ChatPaths,
    id: &str,
    turn_id: &str,
    updated_at: f64,
) -> Result<(), ChatError> {
    let raw = paths.read_raw(id)?;
    let notices = raw["metadata"]["pending_network_notices"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if notices.is_empty() {
        return Ok(());
    }
    let messages: Vec<_> = notices
        .iter()
        .map(|notice| {
            let revision = notice["revision"].as_u64().unwrap_or_default();
            let enabled = notice["enabled"].as_bool().unwrap_or(true);
            json!({
                "message_id": format!("msg_net_{revision}"),
                "role": "user",
                "content": format!(
                    "[Chat 联网设置更新] 联网已{}。请按当前联网状态继续。",
                    if enabled { "开启" } else { "关闭" }
                ),
                "metadata": {
                    "visibility": "hidden",
                    "hidden": true,
                    "starts_work": false,
                    "message_source": "network",
                    "network_revision": revision,
                },
            })
        })
        .collect();
    for message in messages {
        append(paths, id, turn_id, message, updated_at, None)?;
    }
    let mut raw = paths.read_raw(id)?;
    raw["metadata"]["pending_network_notices"] = json!([]);
    paths.write_raw(id, raw, false)?;
    Ok(())
}

pub(super) fn configure(
    paths: &ChatPaths,
    id: &str,
    settings: &Map<String, Value>,
    updated_at: f64,
) -> Result<Value, ChatError> {
    let mut raw = paths.read_raw(id)?;
    if raw["metadata"]["chat"]["active_turn_id"]
        .as_str()
        .is_some()
    {
        return Err(ChatError::conflict(
            "Model settings can change after the current generation",
        ));
    }
    let metadata = raw["metadata"]
        .as_object_mut()
        .ok_or_else(|| ChatError::bad_request("Chat metadata must be an object"))?;
    for (key, value) in settings {
        metadata.insert(key.clone(), value.clone());
    }
    if let Some(thinking) = settings.get("thinking_mode").and_then(Value::as_bool) {
        metadata.insert(
            "run_mode".into(),
            json!(if thinking { "thinking" } else { "fast" }),
        );
    }
    raw["updated_at"] = json!(crate::chat_mode::store::timestamp(updated_at)?);
    paths.write_raw(id, raw, false)?;
    Ok(json!(settings))
}

pub(super) fn compress_limit(
    paths: &ChatPaths,
    id: &str,
    tokens: Option<u64>,
    source: &str,
    updated_at: f64,
) -> Result<Value, ChatError> {
    let mut raw = paths.read_raw(id)?;
    let metadata = raw["metadata"]
        .as_object_mut()
        .ok_or_else(|| ChatError::bad_request("Chat metadata must be an object"))?;
    metadata.insert("compress_limit_tokens".into(), json!(tokens));
    metadata.insert("compress_limit_source".into(), json!(source));
    raw["updated_at"] = json!(crate::chat_mode::store::timestamp(updated_at)?);
    paths.write_raw(id, raw, false)?;
    Ok(json!({"compress_limit_tokens":tokens,"compress_limit_source":source}))
}

pub(super) fn register_presentation(
    paths: &ChatPaths,
    id: &str,
    presentation_id: Option<&str>,
    release: bool,
) -> Result<Value, ChatError> {
    if release {
        return Ok(json!({"success": true}));
    }
    let Some(presentation_id) = presentation_id.filter(|value| !value.is_empty()) else {
        return Err(ChatError::bad_request("Missing presentation identity"));
    };
    if presentation_id.chars().count() > 200 {
        return Err(ChatError::bad_request("Invalid presentation identity"));
    }
    let mut raw = paths.read_raw(id)?;
    let empty = raw["messages"].as_array().is_none_or(Vec::is_empty);
    if empty {
        raw["metadata"]["chat"]["handoff_presentation_id"] = json!(presentation_id);
        paths.write_raw(id, raw, false)?;
    }
    Ok(json!({"success": true}))
}

pub(super) fn active_turn(raw: &Value) -> Option<&str> {
    raw["metadata"]["chat"]["active_turn_id"]
        .as_str()
        .filter(|id| !id.is_empty())
}


fn fingerprint(payload: &Value) -> String {
    let mut canonical = String::new();
    write_canonical(payload, &mut canonical);
    let digest = ring::digest::digest(&ring::digest::SHA256, canonical.as_bytes());
    digest
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn write_canonical(value: &Value, output: &mut String) {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => output.push_str(&serde_json::to_string(value).expect("string")),
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                write_canonical(value, output);
            }
            output.push(']');
        }
        Value::Object(values) => {
            let mut keys: Vec<&String> = values.keys().collect();
            keys.sort();
            output.push('{');
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(&serde_json::to_string(key).expect("key"));
                output.push(':');
                write_canonical(&values[key], output);
            }
            output.push('}');
        }
    }
}

#[cfg(test)]
mod tests {
    use super::fingerprint;
    use serde_json::json;

    #[test]
    fn canonical_fingerprint_ignores_object_order() {
        assert_eq!(
            fingerprint(&json!({"b":1,"a":[true,{"y":2,"x":1}]})),
            fingerprint(&json!({"a":[true,{"x":1,"y":2}],"b":1}))
        );
        assert_ne!(fingerprint(&json!({"a":1})), fingerprint(&json!({"a":2})));
    }
}
