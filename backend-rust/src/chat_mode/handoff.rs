//! Suggestion records and atomic Chat tree transitions for Chat-to-Agent handoff.

mod materials;
mod service;

pub(super) use service::{Handoffs, get, post};

use std::collections::{HashMap, HashSet};

use serde_json::{Map, Value, json};
use uuid::Uuid;

use super::{ChatError, tasks};
use crate::legacy::LegacyDocument;

const TERMINAL: [&str; 3] = ["completed", "declined", "superseded"];
const ACTIONABLE: [&str; 3] = ["pending", "selecting", "failed"];

fn handoff_id() -> String {
    format!("handoff_{}", Uuid::new_v4().simple())
}

fn pause(state: &mut Map<String, Value>) {
    state.insert("countdown_enabled".into(), json!(false));
    state.insert("deadline".into(), Value::Null);
}

fn terminal(state: &Map<String, Value>) -> bool {
    state
        .get("status")
        .and_then(Value::as_str)
        .is_some_and(|status| TERMINAL.contains(&status))
}

fn actionable(state: &Map<String, Value>) -> bool {
    state
        .get("status")
        .and_then(Value::as_str)
        .is_some_and(|status| ACTIONABLE.contains(&status))
}

fn starts_work(message: &Value) -> bool {
    message["metadata"]["starts_work"] != json!(false)
}

/// Extract `<suggest_agent .../>` tags after masking fenced and inline code.
fn tags(content: &Value) -> Vec<String> {
    let text = match content {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter(|part| part["type"] == "text")
            .map(|part| part["text"].as_str().unwrap_or_default())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => return Vec::new(),
    };
    let bytes = mask_code(text.as_bytes());
    let mut tags = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] != b'<' {
            index += 1;
            continue;
        }
        let prefix = b"<suggest_agent";
        if index + prefix.len() > bytes.len()
            || !bytes[index..index + prefix.len()].eq_ignore_ascii_case(prefix)
        {
            index += 1;
            continue;
        }
        let after = index + prefix.len();
        if bytes
            .get(after)
            .is_some_and(|byte| byte.is_ascii_alphanumeric() || *byte == b'_')
        {
            index += 1;
            continue;
        }
        let Some(relative) = bytes[after..].windows(2).position(|pair| pair == b"/>") else {
            break;
        };
        let close = after + relative;
        if bytes[after..close].contains(&b'>') {
            index += 1;
            continue;
        }
        tags.push(String::from_utf8_lossy(&bytes[index..close + 2]).into_owned());
        index = close + 2;
    }
    tags
}

fn mask_code(source: &[u8]) -> Vec<u8> {
    let mut bytes = source.to_vec();
    let mut line_start = 0;
    while line_start < bytes.len() {
        let mut cursor = line_start;
        while cursor < bytes.len() && cursor - line_start < 3 && bytes[cursor] == b' ' {
            cursor += 1;
        }
        let marker = bytes
            .get(cursor)
            .copied()
            .filter(|byte| matches!(byte, b'`' | b'~'));
        let mut run = 0;
        if let Some(marker) = marker {
            while bytes.get(cursor + run) == Some(&marker) {
                run += 1;
            }
        }
        if run < 3 {
            line_start = match bytes[line_start..].iter().position(|byte| *byte == b'\n') {
                Some(offset) => line_start + offset + 1,
                None => break,
            };
            continue;
        }
        let marker = bytes[cursor];
        let body_start = bytes[line_start..]
            .iter()
            .position(|byte| *byte == b'\n')
            .map(|offset| line_start + offset + 1)
            .unwrap_or(bytes.len());
        let mut close_end = bytes.len();
        let mut scan = body_start;
        while scan < bytes.len() {
            let mut start = scan;
            let mut spaces = 0;
            while start < bytes.len() && spaces < 3 && bytes[start] == b' ' {
                start += 1;
                spaces += 1;
            }
            let mut count = 0;
            while bytes.get(start + count) == Some(&marker) {
                count += 1;
            }
            if count >= run {
                close_end = bytes[scan..]
                    .iter()
                    .position(|byte| *byte == b'\n')
                    .map(|offset| scan + offset + 1)
                    .unwrap_or(bytes.len());
                break;
            }
            scan = match bytes[scan..].iter().position(|byte| *byte == b'\n') {
                Some(offset) => scan + offset + 1,
                None => break,
            };
        }
        for byte in &mut bytes[line_start..close_end] {
            if *byte != b'\n' {
                *byte = b' ';
            }
        }
        line_start = close_end;
    }
    // Inline code spans: `...`, ``...``, etc. Runs were preserved by the mask
    // above, so scanning the already-mutated bytes is enough.
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] != b'`' {
            index += 1;
            continue;
        }
        let mut run = 1;
        while bytes.get(index + run) == Some(&b'`') {
            run += 1;
        }
        let closing = bytes[index + run..]
            .windows(run)
            .position(|window| window.iter().all(|byte| *byte == b'`'));
        let Some(relative) = closing else {
            index += run;
            continue;
        };
        let close = index + run + relative + run;
        for byte in &mut bytes[index..close] {
            if *byte != b'\n' {
                *byte = b' ';
            }
        }
        index = close;
    }
    bytes
}

fn unescape(value: &str) -> String {
    let mut result = String::new();
    let mut rest = value;
    while let Some(index) = rest.find('&') {
        result.push_str(&rest[..index]);
        let candidate = &rest[index..];
        let Some(end) = candidate.find(';').filter(|end| *end <= 12) else {
            result.push('&');
            rest = &candidate[1..];
            continue;
        };
        let entity = &candidate[1..end];
        let replacement = match entity {
            "quot" => Some('"'),
            "apos" | "amp" => Some(if entity == "amp" { '&' } else { '\'' }),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "nbsp" => Some('\u{a0}'),
            _ => entity
                .strip_prefix("#x")
                .or_else(|| entity.strip_prefix("#X"))
                .and_then(|hex| u32::from_str_radix(hex, 16).ok())
                .filter(|code| char::from_u32(*code).is_some())
                .and_then(char::from_u32)
                .or_else(|| {
                    entity
                        .strip_prefix('#')
                        .and_then(|number| number.parse::<u32>().ok())
                        .filter(|code| char::from_u32(*code).is_some())
                        .and_then(char::from_u32)
                }),
        };
        if let Some(character) = replacement {
            result.push(character);
            rest = &candidate[end + 1..];
        } else {
            result.push('&');
            rest = &candidate[1..];
        }
    }
    result.push_str(rest);
    result
}

fn reason(tag: &str) -> String {
    let bytes = tag.as_bytes();
    let lower = tag.to_ascii_lowercase();
    let mut search = 0;
    while let Some(offset) = lower[search..].find("reason") {
        let start = search + offset;
        let boundary_before =
            start == 0 || !(bytes[start - 1].is_ascii_alphanumeric() || bytes[start - 1] == b'_');
        let mut cursor = start + "reason".len();
        while bytes.get(cursor).is_some_and(u8::is_ascii_whitespace) {
            cursor += 1;
        }
        if boundary_before && bytes.get(cursor) == Some(&b'=') {
            cursor += 1;
            while bytes.get(cursor).is_some_and(u8::is_ascii_whitespace) {
                cursor += 1;
            }
            if let Some(quote @ (b'\'' | b'"')) = bytes.get(cursor).copied() {
                cursor += 1;
                if let Some(end) = bytes[cursor..].iter().position(|byte| *byte == quote) {
                    return unescape(&tag[cursor..cursor + end])
                        .chars()
                        .take(500)
                        .collect();
                }
            }
        }
        search = start + "reason".len();
    }
    String::new()
}

/// Match previous tag records to current tags. Python uses SequenceMatcher;
/// equal runs plus one-to-one replacement of the gaps gives the same stable
/// identity for normal Chat suggestion lists, including edited tag text.
fn match_tags(previous: &[String], current: &[String]) -> HashMap<usize, usize> {
    let mut lengths = vec![vec![0usize; current.len() + 1]; previous.len() + 1];
    for left in (0..previous.len()).rev() {
        for right in (0..current.len()).rev() {
            lengths[left][right] = if previous[left] == current[right] {
                lengths[left + 1][right + 1] + 1
            } else {
                lengths[left + 1][right].max(lengths[left][right + 1])
            };
        }
    }
    let mut equal = Vec::new();
    let (mut left, mut right) = (0, 0);
    while left < previous.len() && right < current.len() {
        if previous[left] == current[right] {
            equal.push((left, right));
            left += 1;
            right += 1;
        } else if lengths[left + 1][right] >= lengths[left][right + 1] {
            left += 1;
        } else {
            right += 1;
        }
    }
    let mut mapping = HashMap::new();
    let mut previous_start = 0;
    let mut current_start = 0;
    for (previous_index, current_index) in equal {
        for offset in 0..(current_index - current_start).min(previous_index - previous_start) {
            mapping.insert(current_start + offset, previous_start + offset);
        }
        mapping.insert(current_index, previous_index);
        previous_start = previous_index + 1;
        current_start = current_index + 1;
    }
    for offset in 0..(current.len() - current_start).min(previous.len() - previous_start) {
        mapping.insert(current_start + offset, previous_start + offset);
    }
    mapping
}

fn document_path(document: &Value) -> Result<Vec<Value>, ChatError> {
    let parsed = LegacyDocument::from_value(document.clone())?;
    Ok(parsed.chat_path(None)?)
}

fn sync(document: &mut Value, fresh_turn: Option<&str>) -> Result<(), ChatError> {
    let path = document_path(document)?;
    let presentation = document["metadata"]["chat"]["handoff_presentation_id"]
        .as_str()
        .filter(|id| !id.is_empty())
        .map(str::to_owned);
    let path_ids: HashSet<String> = path
        .iter()
        .filter_map(|message| message["message_id"].as_str().map(str::to_owned))
        .collect();
    let positions: HashMap<String, usize> = path
        .iter()
        .enumerate()
        .filter_map(|(index, message)| {
            message["message_id"]
                .as_str()
                .map(|id| (id.to_owned(), index))
        })
        .collect();
    let latest_leaf = document["metadata"]["chat"]["latest_leaf_id"]
        .as_str()
        .map(str::to_owned);
    let active_turn = document["metadata"]["chat"]["active_turn_id"]
        .as_str()
        .map(str::to_owned);
    let messages = document["messages"].as_array().cloned().unwrap_or_default();
    let old_entries = document["metadata"]["chat"]["handoffs"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    let mut count = document["metadata"]["chat"]["handoff_count"]
        .as_u64()
        .unwrap_or(0)
        .max(old_entries.len() as u64);
    let now = tasks::now();
    let mut entries: Map<String, Value> = Map::new();

    for (index, (key, value)) in old_entries.iter().enumerate() {
        let mut state = value.as_object().cloned().unwrap_or_default();
        if !state.contains_key("id") {
            state.insert("id".into(), json!(handoff_id()));
        }
        if !state.contains_key("message_id") {
            state.insert("message_id".into(), json!(key));
        }
        if !state.contains_key("sequence") {
            state.insert("sequence".into(), json!(index + 1));
        }
        if !state.contains_key("tag_index") {
            state.insert("tag_index".into(), json!(0));
        }
        if !state.contains_key("present") {
            state.insert("present".into(), json!(true));
        }
        if !state.contains_key("created_at") {
            state.insert("created_at".into(), json!(now));
        }
        if state.get("presentation_id").is_none_or(Value::is_null) {
            pause(&mut state);
        }
        count = count.max(state["sequence"].as_u64().unwrap_or(index as u64 + 1));
        let id = state["id"].as_str().unwrap_or_default().to_owned();
        entries.insert(id, Value::Object(state));
    }

    for message in &messages {
        if message["role"] != "assistant" {
            continue;
        }
        let message_id = message["message_id"].as_str().unwrap_or_default();
        let message_turn = message["metadata"]["chat"]["turn_id"].as_str();
        if active_turn.is_some() && active_turn.as_deref() == message_turn {
            continue;
        }
        let fresh =
            fresh_turn.is_some() && fresh_turn == message_turn && path_ids.contains(message_id);
        let current_tags = tags(&message["content"]);
        let mut previous_ids: Vec<String> = entries
            .iter()
            .filter(|(_, state)| {
                state["message_id"] == message_id && state["present"] != json!(false)
            })
            .map(|(id, _)| id.clone())
            .collect();
        previous_ids.sort_by_key(|id| entries[id]["tag_index"].as_u64().unwrap_or(0));
        let default_tag = current_tags.first().cloned().unwrap_or_default();
        for id in &previous_ids {
            if let Some(state) = entries.get_mut(id).and_then(Value::as_object_mut) {
                state.entry("tag").or_insert_with(|| json!(default_tag));
            }
        }
        let previous_tags: Vec<String> = previous_ids
            .iter()
            .map(|id| entries[id]["tag"].as_str().unwrap_or_default().to_owned())
            .collect();
        let mapping = match_tags(&previous_tags, &current_tags);
        let kept: HashSet<&String> = mapping
            .values()
            .map(|index| &previous_ids[*index])
            .collect();
        for id in &previous_ids {
            if kept.contains(id) {
                continue;
            }
            if let Some(state) = entries.get_mut(id).and_then(Value::as_object_mut) {
                state.insert("present".into(), json!(false));
                pause(state);
                if !terminal(state) {
                    state.insert("status".into(), json!("superseded"));
                    state.insert("decision_source".into(), json!("edited"));
                    state.insert("decided_at".into(), json!(now));
                }
            }
        }
        for (tag_index, raw) in current_tags.iter().enumerate() {
            let matched = mapping
                .get(&tag_index)
                .map(|index| previous_ids[*index].clone());
            let id = matched
                .or_else(|| {
                    entries.iter().find_map(|(id, state)| {
                        (state["message_id"] == message_id
                            && state["present"] == json!(false)
                            && state["tag"] == json!(raw))
                        .then(|| id.clone())
                    })
                })
                .unwrap_or_else(|| {
                    count += 1;
                    let id = handoff_id();
                    let owner = presentation.as_deref().filter(|_| fresh);
                    entries.insert(
                        id.clone(),
                        json!({
                            "id": id,
                            "message_id": message_id,
                            "sequence": count,
                            "status": "pending",
                            "created_at": now,
                            "leaf_id": if path_ids.contains(message_id) {
                                latest_leaf.clone()
                            } else {
                                Some(message_id.to_owned())
                            },
                            "presentation_id": owner,
                            "runtime_id": service::runtime_id(),
                            "countdown_enabled": owner.is_some(),
                            "deadline": owner.map(|_| now + service::COUNTDOWN.as_secs_f64()),
                        }),
                    );
                    id
                });
            if let Some(state) = entries.get_mut(&id).and_then(Value::as_object_mut) {
                state.insert("tag_index".into(), json!(tag_index));
                state.insert("tag".into(), json!(raw));
                state.insert("present".into(), json!(true));
                state.insert("reason".into(), json!(reason(raw)));
            }
        }
    }

    for state in entries.values_mut().filter_map(Value::as_object_mut) {
        let Some(message_id) = state["message_id"].as_str() else {
            continue;
        };
        let Some(position) = positions.get(message_id).copied() else {
            continue;
        };
        if actionable(state)
            && path[position + 1..]
                .iter()
                .any(|later| later["role"] == "user" && starts_work(later))
        {
            state.insert("status".into(), json!("declined"));
            state.insert("decision_source".into(), json!("historical_message"));
            pause(state);
        }
    }

    document["metadata"]["chat"]["handoffs"] = Value::Object(entries);
    document["metadata"]["chat"]["handoff_count"] = json!(count);
    Ok(())
}

/// Copies preserve decisions and mint independent identities without executable jobs.
pub(super) fn detach(document: &mut Value) -> Result<(), ChatError> {
    sync(document, None)?;
    let entries = document["metadata"]["chat"]["handoffs"].take();
    if let Some(chat) = document["metadata"]["chat"].as_object_mut() {
        chat.remove("handoff_presentation_id");
    }
    let mut detached = Map::new();
    for original in entries.as_object().into_iter().flat_map(Map::values) {
        let mut state = original.as_object().cloned().unwrap_or_default();
        let source_id = state["id"].as_str().unwrap_or_default().to_owned();
        state.insert("source_handoff_id".into(), json!(source_id));
        let id = handoff_id();
        state.insert("id".into(), json!(id.clone()));
        state.remove("presentation_id");
        pause(&mut state);
        if matches!(
            state["status"].as_str(),
            Some("preparing" | "selecting" | "failed")
        ) {
            state.insert("status".into(), json!("pending"));
            state.insert("error".into(), json!(""));
        }
        for key in ["operation_id", "task_id", "agent_task_id", "runtime_id"] {
            state.remove(key);
        }
        if !terminal(&state) {
            for key in [
                "agent_conversation_id",
                "target_workspace_id",
                "workspace_id",
                "agent_url",
                "materials",
                "selection",
            ] {
                state.remove(key);
            }
        }
        detached.insert(id, Value::Object(state));
    }
    document["metadata"]["chat"]["handoffs"] = Value::Object(detached);
    Ok(())
}

fn entries(document: &Value) -> Result<&Map<String, Value>, ChatError> {
    document["metadata"]["chat"]["handoffs"]
        .as_object()
        .ok_or_else(|| ChatError::not_found("Agent suggestion unavailable"))
}

fn resolve(document: &Value, identity: &str) -> Result<String, ChatError> {
    let states = entries(document)?;
    if states.contains_key(identity) {
        return Ok(identity.into());
    }
    let mut matches = states
        .iter()
        .filter(|(_, state)| state["id"] == identity || state["message_id"] == identity);
    let found = matches.next().filter(|_| matches.next().is_none());
    found
        .map(|(id, _)| id.clone())
        .ok_or_else(|| ChatError::not_found("Agent suggestion unavailable or ambiguous"))
}

fn state<'a>(document: &'a Value, id: &str) -> Result<&'a Map<String, Value>, ChatError> {
    entries(document)?
        .get(id)
        .and_then(Value::as_object)
        .ok_or_else(|| ChatError::not_found("Agent suggestion unavailable"))
}

fn state_mut<'a>(
    document: &'a mut Value,
    id: &str,
) -> Result<&'a mut Map<String, Value>, ChatError> {
    document["metadata"]["chat"]["handoffs"][id]
        .as_object_mut()
        .ok_or_else(|| ChatError::not_found("Agent suggestion unavailable"))
}

fn interrupt(state: &mut Map<String, Value>) {
    state.insert("status".into(), json!("failed"));
    state.insert(
        "error".into(),
        json!("Handoff was interrupted; retry to continue in the default workspace"),
    );
    pause(state);
}

fn current_runtime(state: &Map<String, Value>) -> bool {
    state.get("runtime_id").and_then(Value::as_str) == Some(service::runtime_id())
}

fn public_state(state: &Map<String, Value>) -> Value {
    const FIELDS: &[&str] = &[
        "id",
        "message_id",
        "status",
        "reason",
        "deadline",
        "error",
        "sequence",
        "tag_index",
        "present",
        "created_at",
        "decided_at",
        "decision_source",
        "countdown_enabled",
        "presentation_id",
        "leaf_id",
        "agent_conversation_id",
        "workspace_id",
        "agent_url",
        "target_workspace_id",
        "selection",
    ];
    let mut result = state.clone();
    if !current_runtime(state) {
        pause(&mut result);
        if state.get("status").and_then(Value::as_str) == Some("preparing") {
            interrupt(&mut result);
        }
    }
    result.retain(|key, _| FIELDS.contains(&key.as_str()));
    // Host and test fixtures share the same safe projection, including legacy selections.
    if let Some(selection) = result.get_mut("selection").and_then(Value::as_object_mut) {
        selection.retain(|key, _| matches!(key.as_str(), "workspace_id" | "label"));
    }
    Value::Object(result)
}

pub(super) fn public_document(raw: &mut Value) {
    if let Some(states) = raw["metadata"]["chat"]["handoffs"].as_object_mut() {
        for value in states.values_mut() {
            if let Some(state) = value.as_object() {
                *value = public_state(state);
            }
        }
    }
}

pub(super) fn before_send(raw: &Value, operation_id: &str) -> Result<(), ChatError> {
    if entries(raw).is_ok_and(|states| {
        states.values().any(|value| {
            value.as_object().is_some_and(|state| {
                state.get("status").and_then(Value::as_str) == Some("preparing")
                    && current_runtime(state)
            })
        })
    }) {
        return Err(ChatError::conflict(
            "Wait for the current Agent handoff to finish",
        ));
    }
    if let Some(id) = operation_id
        .strip_suffix(":decline")
        .filter(|id| materials::valid_id(id))
    {
        service::check_decline(raw, id)?;
    }
    Ok(())
}

pub(super) fn after_send(
    document: &mut LegacyDocument,
    previous_leaf: &Value,
) -> Result<(), ChatError> {
    let path: HashSet<String> = document
        .chat_path(None)?
        .iter()
        .filter_map(|message| message["message_id"].as_str().map(str::to_owned))
        .collect();
    let mut raw = document.raw().clone();
    if let Some(states) = raw["metadata"]["chat"]["handoffs"].as_object_mut() {
        for state in states.values_mut().filter_map(Value::as_object_mut) {
            if actionable(state)
                || (state.get("status").and_then(Value::as_str) == Some("preparing")
                    && !current_runtime(state))
            {
                let continued = state.get("leaf_id") == Some(previous_leaf)
                    && state
                        .get("message_id")
                        .and_then(Value::as_str)
                        .is_some_and(|id| path.contains(id));
                decide(
                    state,
                    if continued { "declined" } else { "superseded" },
                    if continued { "message" } else { "branch" },
                );
            }
        }
    }
    sync(&mut raw, None)?;
    *document = LegacyDocument::from_value(raw)?;
    Ok(())
}

pub(super) fn after_finish(
    document: &mut LegacyDocument,
    turn_id: &str,
    complete: bool,
) -> Result<(), ChatError> {
    let mut raw = document.raw().clone();
    sync(&mut raw, complete.then_some(turn_id))?;
    *document = LegacyDocument::from_value(raw)?;
    Ok(())
}

pub(super) fn after_edit(document: &mut LegacyDocument) -> Result<(), ChatError> {
    let mut raw = document.raw().clone();
    sync(&mut raw, None)?;
    *document = LegacyDocument::from_value(raw)?;
    Ok(())
}

fn decide(state: &mut Map<String, Value>, status: &str, source: &str) {
    state.insert("status".into(), json!(status));
    state.insert("decided_at".into(), json!(tasks::now()));
    state.insert("decision_source".into(), json!(source));
    pause(state);
}
