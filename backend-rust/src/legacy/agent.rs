use std::collections::HashSet;

use serde_json::{Value, json};

pub(super) fn run_blockers(document: &Value) -> Vec<String> {
    let mut reasons = state_blockers(document);
    if let Err(reason) = model_history(document) {
        reasons.push(reason);
    }
    reasons
}

pub(super) fn state_blockers(document: &Value) -> Vec<String> {
    state_blockers_in_context(document, false)
}

pub(super) fn state_blockers_in_context(document: &Value, restored_summary: bool) -> Vec<String> {
    let metadata = &document["metadata"];
    let messages = document["messages"]
        .as_array()
        .expect("validated messages array");
    let mut reasons = Vec::new();
    if field_is_set(metadata, "chat")
        || messages
            .iter()
            .any(|message| field_is_set(&message["metadata"], "chat"))
    {
        reasons.push("A document with Chat tree state cannot run as a linear Agent".into());
    }
    let active_compression = ["compression_in_progress", "compression_resume_payload", "compression_job_id"]
        .iter().any(|key| field_is_set(metadata, key));
    let historical_compression = [
        "compression_count",
        "last_deep_compression_record",
        "deep_compression_records",
    ]
    .iter()
    .any(|key| field_is_set(metadata, key))
        || messages.iter().any(|message| {
            let metadata = &message["metadata"];
            field_is_set(metadata, "deep_compacted")
                || field_is_set(metadata, "auto_shallow_compacted")
                || message["content"]
                    .as_str()
                    .is_some_and(references_legacy_material)
        });
    if active_compression || (historical_compression && !restored_summary && !verified_transfer(document)) {
        reasons.push(
            "Compressed history or handoff artifacts require the original environment".into(),
        );
    }
    if metadata["status"]
        .as_str()
        .is_some_and(|status| status != "active")
        || [
            "active_task_id",
            "active_turn_id",
            "pending_permission_mode",
            "pending_execution_mode",
            "pending_work_mode",
            "pending_network_permission",
            "git_worktree_children_pending",
            "active_workflow_id",
            "active_goal_id",
        ]
        .iter()
        .any(|key| field_is_set(metadata, key) || field_is_set(document, key))
        || messages
            .iter()
            .any(|message| field_is_set(&message["metadata"], "streaming"))
    {
        reasons
            .push("The document contains inactive status or pending Python runtime state".into());
    }
    reasons
}

fn verified_transfer(document: &Value) -> bool {
    use crate::conversation_transfer::document::{text, verify_import};
    let transfer = &document["metadata"]["conversation_transfer"];
    let (Some(origin), Some(hash), Some(operation)) = (
        text(transfer, "source_conversation_id"), text(transfer, "document_sha256"), text(transfer, "operation_id"),
    ) else { return false; };
    transfer["paths"].is_object() && verify_import(document, origin, hash, operation).is_ok()
}

fn field_is_set(value: &Value, key: &str) -> bool {
    match &value[key] {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_none_or(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

pub(super) fn contains_media(message: &Value) -> bool {
    [
        "images",
        "videos",
        "image_files",
        "video_files",
        "media_refs",
        "media_paths_only",
    ]
    .iter()
    .any(|key| field_is_set(message, key) || field_is_set(&message["metadata"], key))
        || message["content"].as_array().is_some_and(|parts| {
            parts.iter().any(|part| {
                matches!(
                    part["type"].as_str(),
                    Some("image_url" | "video_url" | "input_image" | "input_video")
                )
            })
        })
}

fn references_legacy_material(content: &str) -> bool {
    [
        ".astrion/compact_result/",
        ".astrion/handoff/",
        ".astrion\\compact_result\\",
        ".astrion\\handoff\\",
    ]
    .iter()
    .any(|prefix| content.contains(prefix))
}

pub(super) fn model_history(document: &Value) -> Result<Vec<Value>, String> {
    let messages = document["messages"]
        .as_array()
        .expect("validated messages array");
    let metadata = &document["metadata"];
    let frozen = &metadata["frozen_system_prompt"];
    let prompt = frozen
        .as_str()
        .filter(|prompt| !prompt.trim().is_empty())
        .ok_or("A saved complete frozen system prompt is required to continue this Agent")?;
    project_history(messages, Some(prompt), false)
}

pub(super) fn project_history(
    messages: &[Value],
    prompt: Option<&str>,
    chat: bool,
) -> Result<Vec<Value>, String> {
    let mut history = Vec::with_capacity(messages.len() + usize::from(prompt.is_some()));
    if let Some(prompt) = prompt {
        history.push(json!({"role":"system", "content":prompt, "_frozen_system_prompt":true}));
    }
    let mut seen_calls = HashSet::new();
    let mut pending_calls = HashSet::new();
    for (index, message) in messages.iter().enumerate() {
        if chat && contains_media(message) {
            return Err(format!(
                "Message {}: Media references require resolution in the original Chat account",
                index + 1
            ));
        }
        let projected = project_message(message, &mut seen_calls, &mut pending_calls, chat)
            .map_err(|reason| format!("Message {}: {reason}", index + 1))?;
        history.push(projected);
    }
    if !pending_calls.is_empty() {
        return Err("The history ends with an incomplete tool-call batch".into());
    }
    Ok(history)
}

fn project_message<'a>(
    message: &'a Value,
    seen_calls: &mut HashSet<&'a str>,
    pending_calls: &mut HashSet<&'a str>,
    chat: bool,
) -> Result<Value, &'static str> {
    let role = message["role"].as_str().ok_or("missing message role")?;
    if role != "tool" && !pending_calls.is_empty() {
        return Err("an earlier assistant tool call has no matching result");
    }
    let content = &message["content"];
    let rendered_content = if content.is_string()
        || (role == "assistant" && content.is_null() && field_is_set(message, "tool_calls"))
    {
        content.clone()
    } else if !chat
        && matches!(role, "user" | "assistant" | "system")
        && content.as_array().is_some_and(|parts| {
            parts.iter().all(|part| match part["type"].as_str() {
                Some("text") => part["text"].is_string(),
                Some(kind @ ("image_url" | "video_url")) => part[kind]["url"].is_string(),
                _ => false,
            })
        })
    {
        content.clone()
    } else if chat
        && matches!(role, "user" | "assistant")
        && content.as_array().is_some_and(|parts| {
            parts
                .iter()
                .all(|part| part["type"] == "text" && part["text"].is_string())
        })
    {
        Value::Array(
            content
                .as_array()
                .expect("checked Chat text parts")
                .iter()
                .map(|part| json!({"type":"text", "text":part["text"]}))
                .collect(),
        )
    } else {
        return Err("only text and tool-call assistant messages are supported");
    };
    let mut projected = json!({"role":role, "content":rendered_content});
    match role {
        "assistant" => {
            if field_is_set(message, "tool_calls") {
                let calls = message["tool_calls"]
                    .as_array()
                    .ok_or("invalid tool calls array")?;
                let mut projected_calls = Vec::with_capacity(calls.len());
                for call in calls {
                    projected_calls.push(project_call(call, seen_calls, pending_calls)?);
                }
                projected["tool_calls"] = Value::Array(projected_calls);
            }
            // Python provider keys cannot identify the configured Rust connection.
            // Only a stored Rust fingerprint can make a reasoning block eligible for replay.
            let metadata = &message["metadata"];
            if let Some(connection) =
                metadata["_responses_connection"]
                    .as_str()
                    .filter(|connection| {
                        connection.len() == 64
                            && connection.bytes().all(|byte| byte.is_ascii_hexdigit())
                    })
                && metadata["responses_reasoning_items"]
                    .as_array()
                    .is_some_and(|items| !items.is_empty())
            {
                projected["_responses_connection"] = connection.into();
                projected["responses_reasoning_items"] =
                    metadata["responses_reasoning_items"].clone();
            }
        }
        "tool" => {
            let id = message["tool_call_id"]
                .as_str()
                .ok_or("missing tool result call ID")?;
            if !pending_calls.remove(id) {
                return Err("tool result has no matching current call");
            }
            projected["tool_call_id"] = id.into();
        }
        "system" if !chat => {}
        "user" => {}
        _ => return Err("unsupported message role"),
    }
    Ok(projected)
}

fn project_call<'a>(
    call: &'a Value,
    seen_calls: &mut HashSet<&'a str>,
    pending_calls: &mut HashSet<&'a str>,
) -> Result<Value, &'static str> {
    let id = call["id"]
        .as_str()
        .filter(|id| !id.trim().is_empty())
        .ok_or("missing tool call ID")?;
    if call["type"].as_str() != Some("function") {
        return Err("unsupported tool call type");
    }
    let function = &call["function"];
    let name = function["name"]
        .as_str()
        .filter(|name| valid_tool_name(name))
        .ok_or("invalid function name")?;
    let arguments = function["arguments"]
        .as_str()
        .ok_or("missing JSON tool arguments")?;
    if !matches!(
        serde_json::from_str::<Value>(arguments),
        Ok(Value::Object(_))
    ) {
        return Err("tool arguments must be a JSON object");
    }
    if !seen_calls.insert(id) {
        return Err("repeated tool call ID");
    }
    pending_calls.insert(id);
    Ok(json!({"id":id, "type":"function", "function":{"name":name, "arguments":arguments}}))
}

fn valid_tool_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}
