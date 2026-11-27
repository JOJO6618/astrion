//! Read and update conversations in Python's existing on-disk JSON format.

#[path = "legacy/agent.rs"]
mod agent;
#[path = "legacy/media.rs"]
pub(crate) mod media;
#[path = "legacy/tree.rs"]
mod tree;

use std::{
    collections::HashSet,
    fmt,
    fs::File,
    io::{self, BufReader},
    path::Path,
};

use serde::Serialize;
use serde_json::{Map, Value, json};
use time::{
    OffsetDateTime, PrimitiveDateTime,
    format_description::well_known::{Iso8601, Rfc3339},
};
use uuid::Uuid;

use crate::model::public_history_message;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LegacyKind {
    Agent,
    Chat,
}

pub enum LegacyChatTurn<'a> {
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

/// Give each declared but unpaired tool call an explicit interrupted result.
/// The caller must pass results from this assistant batch only.
pub(crate) fn interrupted_chat_tool_results(assistant: &Value, results: &[Value]) -> Vec<Value> {
    let completed: HashSet<&str> = results.iter().filter_map(|tool| tool["tool_call_id"].as_str()).collect();
    assistant["tool_calls"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|call| {
            let id = call["id"].as_str().filter(|id| !completed.contains(id))?;
            Some(json!({
                "role": "tool",
                "tool_call_id": id,
                "name": call["function"]["name"],
                "content": json!({
                    "success": false,
                    "status": "cancelled",
                    "message": "执行已中断，未取得完成结果",
                }).to_string(),
            }))
        })
        .collect()
}

#[derive(Debug, Serialize)]
pub struct LegacyInspection<'a> {
    pub source_id: &'a str,
    pub kind: LegacyKind,
    pub runnable: bool,
    pub reasons: Vec<String>,
}

#[derive(Debug)]
pub struct LegacyError {
    pub status: u16,
    pub message: String,
}

impl LegacyError {
    fn invalid(message: impl Into<String>) -> Self {
        Self {
            status: 400,
            message: message.into(),
        }
    }

    fn conflict(message: impl Into<String>) -> Self {
        Self {
            status: 409,
            message: message.into(),
        }
    }

    fn unsupported(reasons: &[String]) -> Self {
        Self {
            status: 422,
            message: reasons.join(". "),
        }
    }
}

impl fmt::Display for LegacyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for LegacyError {}

impl From<io::Error> for LegacyError {
    fn from(error: io::Error) -> Self {
        let status = match error.kind() {
            io::ErrorKind::NotFound => 404,
            io::ErrorKind::PermissionDenied => 403,
            _ => 500,
        };
        Self {
            status,
            message: format!("Could not read Python conversation: {error}"),
        }
    }
}

impl From<serde_json::Error> for LegacyError {
    fn from(error: serde_json::Error) -> Self {
        if let Some(kind) = error.io_error_kind() {
            return io::Error::new(kind, error).into();
        }
        Self::invalid(format!("Invalid Python conversation JSON: {error}"))
    }
}

/// A detached Python conversation document. Source paths remain inert JSON values.
#[derive(Debug)]
pub struct LegacyDocument {
    raw: Value,
    kind: LegacyKind,
}

fn ensure_local_compact_file(path: &str, workspace: &Path, execution_root: &Path) -> Result<(), LegacyError> {
    use crate::{conversation_transfer::assets, knowledge::storage::open_canonical_dir};

    let normalized = path.replace('\\', "/");
    let valid = normalized.strip_prefix(".astrion/compact_result/")
        .is_some_and(|name| !name.contains('/') && name.ends_with(".md"));
    if valid && [execution_root, workspace].into_iter().any(|root| {
        open_canonical_dir(root).and_then(|directory| assets::open_file(&directory, &normalized))
            .and_then(|file| file.metadata()).is_ok_and(|meta| meta.len() > 0)
    }) {
        return Ok(());
    }
    Err(LegacyError::unsupported(&["The saved compression record is unavailable in this conversation's workspace".into()]))
}

impl LegacyDocument {
    /// Reads only the explicitly selected regular file, with no writes or migrations.
    pub fn read(path: impl AsRef<Path>) -> Result<Self, LegacyError> {
        let path = path.as_ref();
        if !path.symlink_metadata()?.is_file() {
            return Err(LegacyError::invalid(
                "Expected a regular Python conversation JSON file",
            ));
        }
        let file = File::open(path)?;
        if !file.metadata()?.is_file() {
            return Err(LegacyError::invalid(
                "Expected a regular Python conversation JSON file",
            ));
        }
        Self::from_value(serde_json::from_reader(BufReader::new(file))?)
    }

    pub fn parse(bytes: &[u8]) -> Result<Self, LegacyError> {
        Self::from_value(serde_json::from_slice(bytes)?)
    }

    pub fn from_value(raw: Value) -> Result<Self, LegacyError> {
        if !raw.is_object() {
            return Err(LegacyError::invalid(
                "Expected a Python conversation JSON object",
            ));
        }
        if raw["id"].as_str().is_none_or(|id| id.trim().is_empty()) {
            return Err(LegacyError::invalid(
                "Python conversation identity is missing",
            ));
        }
        if !raw["metadata"].is_object() || !raw["messages"].is_array() {
            return Err(LegacyError::invalid(
                "Python conversation requires a metadata object and messages array",
            ));
        }
        let kind = match raw["metadata"].get("conversation_kind") {
            None => LegacyKind::Agent,
            Some(value) => match value.as_str() {
                Some("agent") => LegacyKind::Agent,
                Some("chat") => LegacyKind::Chat,
                _ => return Err(LegacyError::invalid("Unsupported Python conversation kind")),
            },
        };
        Ok(Self { raw, kind })
    }

    pub fn raw(&self) -> &Value {
        &self.raw
    }

    pub fn into_raw(self) -> Value {
        self.raw
    }

    pub fn source_id(&self) -> &str {
        self.raw["id"]
            .as_str()
            .expect("validated conversation identity")
    }

    pub fn kind(&self) -> LegacyKind {
        self.kind
    }

    pub fn created_at(&self) -> Option<f64> {
        timestamp(&self.raw["created_at"])
    }

    pub fn updated_at(&self) -> Option<f64> {
        timestamp(&self.raw["updated_at"])
    }

    pub fn inspect(&self) -> LegacyInspection<'_> {
        let reasons = match self.kind {
            LegacyKind::Agent => agent::run_blockers(&self.raw),
            LegacyKind::Chat => match self.chat_model_history(None) {
                Err(error) => vec![error.message],
                Ok(_) if !self.raw["metadata"]["chat"]["active_turn_id"].is_null() => {
                    vec!["A legacy Chat generation is already active".into()]
                }
                Ok(_) => Vec::new(),
            },
        };
        LegacyInspection {
            source_id: self.source_id(),
            kind: self.kind,
            runnable: reasons.is_empty(),
            reasons,
        }
    }

    /// Projects the full stored history for display, retaining IDs and annotations.
    pub fn public_messages(&self) -> Vec<Value> {
        self.raw["messages"]
            .as_array()
            .expect("validated messages array")
            .iter()
            .map(public_message)
            .collect()
    }

    /// Returns a detached branch projection. `None` selects the saved latest leaf.
    pub fn chat_path(&self, leaf_id: Option<&str>) -> Result<Vec<Value>, LegacyError> {
        self.require_chat()?;
        tree::chat_path(&self.raw, leaf_id)
    }

    pub fn public_chat_path(&self, leaf_id: Option<&str>) -> Result<Vec<Value>, LegacyError> {
        Ok(self
            .chat_path(leaf_id)?
            .iter()
            .map(public_message)
            .collect())
    }

    pub fn chat_siblings(&self, message_id: &str) -> Result<Vec<String>, LegacyError> {
        self.require_chat()?;
        tree::siblings(&self.raw, message_id)
    }

    /// Builds the selected branch's wire history, using a saved frozen prompt when present.
    pub fn chat_model_history(&self, leaf_id: Option<&str>) -> Result<Vec<Value>, LegacyError> {
        self.project_chat_history(self.chat_path(leaf_id)?)
    }

    /// Uses the given turn's parent or tip, independent of the saved latest branch.
    pub fn chat_turn_history(&self, turn_id: &str) -> Result<Vec<Value>, LegacyError> {
        self.require_chat()?;
        self.project_chat_history(tree::chat_turn_path(&self.raw, turn_id)?)
    }

    pub(crate) fn chat_turn_path(&self, turn_id: &str) -> Result<Vec<Value>, LegacyError> {
        self.require_chat()?;
        tree::chat_turn_path(&self.raw, turn_id)
    }

    pub(crate) fn chat_path_history_with_media(
        &self, path: Vec<Value>, account: &Path, include_frozen: bool,
    ) -> Result<Vec<Value>, LegacyError> {
        self.require_chat()?;
        let path = self.complete_interrupted_chat_batches(path);
        let frozen = self.raw["metadata"]["frozen_system_prompt"].as_str()
            .filter(|prompt| include_frozen && !prompt.trim().is_empty());
        let mut history = agent::project_history(&path, frozen, true, true)
            .map_err(|reason| LegacyError::unsupported(&[reason]))?;
        media::attach_chat_blobs(&path, &mut history[usize::from(frozen.is_some())..], account)
            .map_err(|reason| LegacyError::unsupported(&[reason]))?;
        Ok(history)
    }

    pub fn chat_revision(&self) -> Result<u64, LegacyError> {
        self.require_chat()?;
        tree::revision(&self.raw)
    }

    pub fn check_chat_revision(&self, expected: Option<u64>) -> Result<(), LegacyError> {
        let revision = self.chat_revision()?;
        if expected.is_some_and(|expected| expected != revision) {
            return Err(LegacyError::conflict("Legacy Chat revision changed"));
        }
        Ok(())
    }

    pub fn begin_chat_turn(
        &mut self,
        selection: LegacyChatTurn<'_>,
        updated_at: f64,
    ) -> Result<String, LegacyError> {
        self.require_chat()?;
        tree::begin(&mut self.raw, selection, &format_timestamp(updated_at)?)
    }

    pub fn append_chat_messages(
        &mut self,
        turn_id: &str,
        messages: Vec<Value>,
        updated_at: f64,
        token_statistics: Option<&Value>,
    ) -> Result<(), LegacyError> {
        self.require_chat()?;
        let timestamp = format_timestamp(updated_at)?;
        tree::append(&mut self.raw, turn_id, messages, &timestamp)?;
        if let Some(statistics) = token_statistics {
            merge_statistics(&mut self.raw, statistics, &timestamp);
        }
        Ok(())
    }

    pub fn finish_chat_turn(
        &mut self,
        turn_id: &str,
        status: &str,
        updated_at: f64,
    ) -> Result<(), LegacyError> {
        self.require_chat()?;
        tree::finish(
            &mut self.raw,
            turn_id,
            status,
            &format_timestamp(updated_at)?,
        )
    }

    pub fn edit_chat_message(
        &mut self,
        message_id: &str,
        content: &str,
        updated_at: f64,
    ) -> Result<(), LegacyError> {
        self.require_chat()?;
        tree::edit(
            &mut self.raw,
            message_id,
            content,
            &format_timestamp(updated_at)?,
        )
    }

    fn require_chat(&self) -> Result<(), LegacyError> {
        if self.kind != LegacyKind::Chat {
            return Err(LegacyError::invalid(
                "Branch paths require a Python Chat conversation",
            ));
        }
        Ok(())
    }

    fn project_chat_history(&self, messages: Vec<Value>) -> Result<Vec<Value>, LegacyError> {
        let frozen = self.raw["metadata"]["frozen_system_prompt"]
            .as_str()
            .filter(|prompt| !prompt.trim().is_empty());
        let messages = self.complete_interrupted_chat_batches(messages);
        agent::project_history(&messages, frozen, true, false)
            .map_err(|reason| LegacyError::unsupported(&[reason]))
    }

    /// Already-closed turns from early Rust releases can predate durable batch
    /// recovery. Fill their missing results in the model projection only; do
    /// not rewrite a user's saved tree, replay tools, or reorder real results.
    fn complete_interrupted_chat_batches(&self, path: Vec<Value>) -> Vec<Value> {
        let interrupted: HashSet<&str> = self.raw["metadata"]["chat"]["turns"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|turn| matches!(turn["status"].as_str(), Some("stopped" | "failed")))
            .filter_map(|turn| turn["id"].as_str())
            .collect();
        if interrupted.is_empty() {
            return path;
        }
        let mut projected = Vec::with_capacity(path.len());
        let mut rows = path.into_iter().peekable();
        while let Some(message) = rows.next() {
            let track = message["role"] == "assistant"
                && message["tool_calls"].as_array().is_some_and(|calls| !calls.is_empty())
                && message["metadata"]["chat"]["turn_id"]
                    .as_str()
                    .is_some_and(|turn| interrupted.contains(turn));
            let index = projected.len();
            projected.push(message);
            if track {
                while let Some(tool) = rows.next_if(|row| row["role"] == "tool") {
                    projected.push(tool);
                }
                let absent = interrupted_chat_tool_results(&projected[index], &projected[index + 1..]);
                projected.extend(absent);
            }
        }
        projected
    }

    /// Builds the Agent's wire history without changing the source document.
    pub fn agent_history(&self) -> Result<Vec<Value>, LegacyError> {
        self.require_agent()?;
        let reasons = agent::state_blockers(&self.raw);
        if !reasons.is_empty() {
            return Err(LegacyError::unsupported(&reasons));
        }
        agent::model_history(&self.raw).map_err(|reason| LegacyError::unsupported(&[reason]))
    }

    pub(crate) fn agent_history_after_clear(&self, start: usize, data: &Path, workspace: &Path, execution_root: &Path) -> Result<Vec<Value>, LegacyError> {
        if start == 0 { return self.agent_history_with_media(data,workspace,execution_root); }
        self.require_agent()?;
        let rows = self.raw["messages"].as_array().expect("validated message list");
        let tail = &rows[start.min(rows.len())..];
        if tail.iter().any(|row| row["metadata"]["message_source"] == "compression_handoff") {
            return self.agent_history_with_media(data,workspace,execution_root);
        }
        let projected = json!({"messages":tail, "metadata":self.raw["metadata"]});
        let blockers = agent::state_blockers_in_context(&projected,true);
        if !blockers.is_empty() { return Err(LegacyError::unsupported(&blockers)); }
        let mut history = agent::model_history(&projected).map_err(|err| LegacyError::unsupported(&[err]))?;
        media::attach(&projected,&mut history[1..],data,workspace,execution_root);
        Ok(history)
    }

    pub fn agent_history_with_media(&self, data: &Path, workspace: &Path, execution_root: &Path) -> Result<Vec<Value>, LegacyError> {
        self.require_agent()?;
        let rows = self.raw["messages"].as_array().expect("validated message list");
        let deep = |m: &Value| m["metadata"]["deep_compacted"] == true;
        let shallow = |m: &Value| m["role"] == "tool" && m["metadata"]["auto_shallow_compacted"] == true;
        let meta = &self.raw["metadata"];
        let record = meta["last_deep_compression_record"].as_object().map(|_| &meta["last_deep_compression_record"])
            .or_else(|| meta["deep_compression_records"].as_array()?.last());
        let has_deep = record.is_some() || rows.iter().any(deep);
        if !has_deep && !rows.iter().any(shallow) {
            let mut history = self.agent_history()?;
            media::attach(&self.raw, &mut history[1..], data, workspace, execution_root);
            return Ok(history);
        }
        let summary = record.and_then(|record| record["summary"].as_str()).filter(|s| !s.trim().is_empty());
        let mut active: Vec<_> = rows.iter().filter(|m| !deep(m)).cloned().collect();
        let options = crate::web_assets::personalization::shallow_options(data).map_err(|e|LegacyError::unsupported(&[e.to_string()]))?;
        if options.is_some() {
            for message in active.iter_mut().filter(|m|shallow(m)) {
                message["content"] = json!(crate::runtime::shallow::PLACEHOLDER);
            }
        }
        let guide = active.iter().rev().find_map(|message| {
            let body = message["content"].as_str()?;
            (message["role"] == "user" && (message["metadata"]["message_source"] == "compression_handoff"
                || body.contains("压缩记录文件：") && body.contains("本次压缩摘要："))).then_some(body)
        });
        let restored_summary = guide.is_some_and(|body| !body.trim().is_empty()) || summary.is_some();
        let reasons = agent::state_blockers_in_context(&self.raw, restored_summary || !has_deep);
        if !reasons.is_empty() { return Err(LegacyError::unsupported(&reasons)); }
        if has_deep && !agent::verified_transfer(&self.raw) {
            let guide_path = guide.map(|body| body.replace('\\', "/"));
            let reference = record.and_then(|state| state["compact_file"].as_str()).filter(|path| !path.trim().is_empty())
                .or_else(|| guide_path.as_deref().and_then(crate::conversation_transfer::assets::logical_path));
            if let Some(path) = reference {
                ensure_local_compact_file(path, workspace, execution_root)?;
            }
        }
        if guide.is_none() && let Some(summary) = summary { active.insert(0,json!({"role":"user","content":summary})); }
        let projected = json!({"messages":active,"metadata":meta});
        let mut history = agent::model_history(&projected).map_err(|e| LegacyError::unsupported(&[e]))?;
        media::attach(&projected,&mut history[1..],data,workspace,execution_root);
        Ok(history)
    }

    /// Updates the detached document before a Store commit to the existing file.
    /// An empty append only merges supplied statistics. Old messages are never rewritten.
    pub fn append_agent_messages(
        &mut self,
        appended: Vec<Value>,
        updated_at: f64,
        token_statistics: Option<&Value>,
    ) -> Result<(), LegacyError> {
        self.require_agent()?;
        if appended.is_empty() && token_statistics.is_none() {
            return Ok(());
        }
        let updated_at = format_timestamp(updated_at)?;
        if !appended.is_empty() {
            let messages = self.raw["messages"]
                .as_array_mut()
                .expect("validated messages array");
            let mut identities: HashSet<String> = messages
                .iter()
                .filter_map(|message| message["message_id"].as_str().map(str::to_owned))
                .collect();
            let stored = appended
                .into_iter()
                .map(|message| storage_message(message, &mut identities, &updated_at))
                .collect::<Result<Vec<_>, _>>()?;
            messages.extend(stored);
            self.raw["updated_at"] = Value::String(updated_at.clone());
            update_message_counts(&mut self.raw);
        }
        if let Some(statistics) = token_statistics {
            merge_statistics(&mut self.raw, statistics, &updated_at);
        }
        Ok(())
    }

    fn require_agent(&self) -> Result<(), LegacyError> {
        if self.kind != LegacyKind::Agent {
            return Err(LegacyError::unsupported(&[
                "A Chat tree requires its branch and turn interface".into(),
            ]));
        }
        Ok(())
    }
}

fn public_message(message: &Value) -> Value {
    let mut projected = public_history_message(message);
    if let Some(metadata) = projected.get_mut("metadata").and_then(Value::as_object_mut) {
        metadata.shift_remove("responses_reasoning_items");
        metadata.shift_remove("_responses_connection");
    }
    projected
}

fn update_message_counts(document: &mut Value) {
    let messages = document["messages"]
        .as_array()
        .expect("validated messages array");
    let total_messages = messages.len();
    let total_tools: usize = messages
        .iter()
        .filter(|message| message["role"] == "assistant")
        .filter_map(|message| message["tool_calls"].as_array())
        .map(Vec::len)
        .sum();
    document["metadata"]["total_messages"] = total_messages.into();
    document["metadata"]["total_tools"] = total_tools.into();
}

fn storage_message(
    mut message: Value,
    identities: &mut HashSet<String>,
    timestamp: &str,
) -> Result<Value, LegacyError> {
    let fields = message
        .as_object_mut()
        .ok_or_else(|| LegacyError::invalid("Expected a message object"))?;
    match fields.get("role").and_then(Value::as_str) {
        Some("assistant") => {
            fields
                .entry("reasoning_content")
                .or_insert_with(|| Value::String(String::new()));
            if fields.get("content").is_none_or(Value::is_null) {
                fields.insert("content".into(), Value::String(String::new()));
            }
            // Python stores encrypted reasoning alongside the response's metadata.
            // The optional Rust fingerprint supplies proof for Rust's connection check.
            move_responses_state(fields)?;
        }
        Some("system" | "user" | "tool") => {}
        _ => return Err(LegacyError::invalid("Unsupported Agent message role")),
    }
    fields.shift_remove("_frozen_system_prompt");
    if !fields
        .get("message_id")
        .and_then(Value::as_str)
        .is_some_and(|id| !id.is_empty() && identities.insert(id.to_owned()))
    {
        let id = loop {
            let id = format!("msg_{}", Uuid::new_v4().simple());
            if identities.insert(id.clone()) {
                break id;
            }
        };
        fields.insert("message_id".into(), id.into());
    }
    fields
        .entry("timestamp")
        .or_insert_with(|| Value::String(timestamp.into()));
    Ok(message)
}

fn move_responses_state(message: &mut Map<String, Value>) -> Result<(), LegacyError> {
    let Some(items) = message.shift_remove("responses_reasoning_items") else {
        message.shift_remove("_responses_connection");
        return Ok(());
    };
    let connection = message.shift_remove("_responses_connection");
    let metadata = message
        .entry("metadata")
        .or_insert_with(|| Value::Object(Map::new()));
    let metadata = metadata.as_object_mut().ok_or_else(|| {
        LegacyError::invalid("Expected assistant message metadata to be an object")
    })?;
    metadata.insert("responses_reasoning_items".into(), items);
    if let Some(connection) = connection {
        metadata.insert("_responses_connection".into(), connection);
    }
    Ok(())
}

fn merge_statistics(document: &mut Value, statistics: &Value, updated_at: &str) {
    const FIELDS: [&str; 5] = [
        "total_input_tokens",
        "total_output_tokens",
        "total_cached_input_tokens",
        "cache_exempt_input_tokens",
        "current_context_tokens",
    ];
    let Some(current) = statistics.as_object() else {
        return;
    };
    if !FIELDS
        .iter()
        .any(|key| current.get(*key).and_then(Value::as_u64).is_some())
    {
        return;
    }
    let stats = &mut document["token_statistics"];
    if stats.is_null() {
        *stats = Value::Object(Map::new());
    }
    let Some(original) = stats.as_object_mut() else {
        return;
    };
    let mut changed = false;
    for key in FIELDS {
        if let Some(value) = current.get(key).and_then(Value::as_u64) {
            let value = Value::from(value);
            changed |= original.get(key) != Some(&value);
            original.insert(key.into(), value);
        }
    }
    if let Some(pending) = current.get("cache_cold_start_pending").and_then(Value::as_bool) {
        let value = Value::Bool(pending);
        changed |= original.get("cache_cold_start_pending") != Some(&value);
        original.insert("cache_cold_start_pending".into(), value);
    }
    if changed {
        if let Some(total) = original
            .get("total_input_tokens")
            .and_then(Value::as_u64)
            .zip(original.get("total_output_tokens").and_then(Value::as_u64))
            .and_then(|(input, output)| input.checked_add(output))
        {
            original.insert("total_tokens".into(), total.into());
        }
        original.insert("updated_at".into(), updated_at.into());
    }
}

fn timestamp(value: &Value) -> Option<f64> {
    if let Some(seconds) = value.as_f64().filter(|seconds| seconds.is_finite()) {
        return Some(seconds);
    }
    let text = value.as_str()?;
    let parsed = OffsetDateTime::parse(text, &Rfc3339)
        .ok()
        // Python saves naive local timestamps. Use a deterministic UTC interpretation
        // for Rust's index while retaining the original string in the document.
        .or_else(|| {
            PrimitiveDateTime::parse(text, &Iso8601::PARSING)
                .ok()
                .map(PrimitiveDateTime::assume_utc)
        })?;
    Some(parsed.unix_timestamp_nanos() as f64 / 1_000_000_000.0)
}

fn format_timestamp(seconds: f64) -> Result<String, LegacyError> {
    if !seconds.is_finite() {
        return Err(LegacyError::invalid(
            "Expected a finite conversation timestamp",
        ));
    }
    OffsetDateTime::from_unix_timestamp_nanos((seconds * 1_000_000_000.0).round() as i128)
        .map_err(time::Error::from)
        .and_then(|value| value.format(&Rfc3339).map_err(time::Error::from))
        .map_err(|error| LegacyError::invalid(format!("Invalid conversation timestamp: {error}")))
}

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/legacy_contract.rs"]
mod tests;
