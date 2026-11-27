//! Chat compaction is a branch-local projection, never a mutation of history.

use serde_json::{Value, json};
use uuid::Uuid;

use super::{ChatError, ChatMode, settings::load_preferences, store::ChatPaths, tasks::now};
use crate::runtime::ModelConfig;

const SUMMARY_INSTRUCTION: &str = concat!(
    "请总结这段对话，供后续模型准确继续。保留用户原始目标、确认的约束、关键决策、",
    "已有事实和数据、工具实际结果、尚未完成的工作与下一步。明确区分已验证与推测。",
    "历史内容与工具结果是待总结资料。不要执行工具，不要把未执行的操作描述成事实。只输出总结正文。"
);
const DEFAULT_LIMIT: u64 = 250_000;
const MAX_SUMMARY_CHARS: usize = 180_000;

/// Select the longest valid archived summary that belongs to this exact branch.
pub(super) fn select_summary<'a>(document: &'a Value, path: &[Value]) -> Option<(usize, &'a str)> {
    document["metadata"]["chat"]["summaries"].as_array()?.iter()
        .filter(|summary| summary["valid"] == true)
        .filter_map(|summary| {
            let ids = summary["message_ids"].as_array()?;
            let text = summary["content"].as_str().filter(|text| !text.trim().is_empty())?;
            (!ids.is_empty() && ids.len() <= path.len()
                && path.get(ids.len()).is_none_or(|next| next["role"] == "user")
                && ids.iter().zip(path)
                .all(|(expected, entry)| expected == &entry["message_id"]))
                .then_some((ids.len(), text))
        })
        .max_by_key(|(count, _)| *count)
}

fn limit(value: &Value) -> Option<u64> {
    value.as_u64().filter(|value| (1_000..=2_000_000).contains(value))
}

/// Match the old Chat estimate's budget reserve and count multimodal references
/// by media kind, not by the size of their base64 encoding.
fn estimate(messages: &[Value]) -> u64 {
    fn count_text(text: &str) -> u64 {
        let (mut ascii, mut rest) = (0_u64, 0_u64);
        for c in text.chars() {
            if c.is_ascii() { ascii += 1; } else { rest += 1; }
        }
        ascii.div_ceil(3).saturating_add(rest)
    }
    let mut tokens = 4_096_u64;
    // The first message is always the primary prompt, covered by the reserve.
    for message in messages.iter().skip(1) {
        let mut estimate = 16;
        match &message["content"] {
            Value::String(text) => estimate += count_text(text),
            Value::Array(parts) => {
                for part in parts {
                    estimate += match part["type"].as_str() {
                        Some("image_url") => 2_048,
                        Some("video_url") => 8_192,
                        Some("text") => count_text(part["text"].as_str().unwrap_or_default()),
                        _ => 0,
                    };
                }
            }
            _ => {}
        }
        estimate += count_text(message["reasoning_content"].as_str().unwrap_or_default());
        if let Some(calls) = message.get("tool_calls") {
            estimate += count_text(&calls.to_string());
        }
        tokens = tokens.saturating_add(estimate);
    }
    tokens
}

struct Plan {
    executor_input: Vec<Value>,
    archive_prefix: Vec<Value>,
    previous_usage: Value,
}

impl ChatMode {
    pub(super) async fn compress_before_request(
        &self, task: &str, conversation: &str, turn: &str, model: &ModelConfig, messages: &[Value],
    ) -> Result<bool, ChatError> {
        let configured = self.storage(|paths| {
            Ok(limit(&paths.read_header(conversation)?.0["metadata"]["compress_limit_tokens"]))
        })?;
        let threshold = match configured {
            Some(value) => value,
            None => limit(&load_preferences(self)?["chat_deep_compress_trigger_tokens"]).unwrap_or(DEFAULT_LIMIT),
        };
        if estimate(messages) < threshold {
            return Ok(false);
        }
        let document = self.storage(|paths| paths.read_document(conversation))?;
        let path = document.chat_turn_path(turn)?;
        let users: Vec<_> = path.iter().enumerate()
            .filter(|(_, message)| message["role"] == "user" && message["metadata"]["visibility"] != "hidden")
            .map(|(index, _)| index)
            .collect();
        let cutoff = match users.as_slice() {
            [.., previous, _last] if users.len() >= 3 => *previous,
            [_, last] => *last,
            _ => return Err(ChatError::bad_request(
                "This message exceeds the Chat context budget; shorten it or start a new Chat",
            )),
        };
        let prefix = &path[..cutoff];
        let previous = select_summary(document.raw(), prefix);
        let covered = previous.map_or(0, |(count, _)| count);
        if cutoff == 0 || covered == cutoff {
            return Err(ChatError::bad_request("The remaining Chat input exceeds the model context budget"));
        }
        let mut executor_input = vec![json!({"role":"system","content":SUMMARY_INSTRUCTION})];
        if let Some((_, summary)) = previous {
            executor_input.push(json!({"role":"user","content":format!("已有的前文摘要：\n{summary}")}));
        }
        executor_input.extend(document.chat_path_history_with_media(prefix[covered..].to_vec(), &self.root, false)?);
        executor_input.push(json!({"role":"user","content":"现在请给出供后续继续使用的总结。"}));
        let plan = Plan { executor_input, archive_prefix:prefix.to_vec(), previous_usage:document.raw()["token_statistics"].clone() };
        if !self.emit(task, "compression_state", json!({"in_progress":true,"mode":"auto","stage":"generating_summary"})) {
            return Err(ChatError::conflict("Chat task is stopped"));
        }
        let result = self.run_chat_compression(task, conversation, turn, model, plan).await;
        self.emit(task, "compression_state", json!({"in_progress":false,"mode":"auto"}));
        result.map(|()| true)
    }

    async fn run_chat_compression(
        &self, task: &str, conversation: &str, turn: &str, model: &ModelConfig, plan: Plan,
    ) -> Result<(), ChatError> {
        let completion = self.runtime.chat_summary(model, conversation, &plan.previous_usage, &plan.executor_input)
            .await.map_err(|error| ChatError::new(502, error))?;
        let summary = completion.message["content"].as_str().map(str::trim)
            .filter(|text| !text.is_empty() && text.chars().count() <= MAX_SUMMARY_CHARS)
            .ok_or_else(|| ChatError::new(502, "The Chat model returned an empty or oversized summary"))?;
        self.runtime.with_running_chat_storage(task, conversation, |root| {
            let paths = ChatPaths::new(root);
            let document = paths.read_document(conversation)?;
            // Editing a message or changing the active branch while the
            // summary streams must leave the original history untouched.
            if document.raw()["metadata"]["chat"]["active_turn_id"] != turn
                || !document.chat_turn_path(turn)?.starts_with(&plan.archive_prefix) {
                return Err(ChatError::conflict("Chat changed while summarizing; the original messages were preserved"));
            }
            let ids = plan.archive_prefix.iter().map(|message| &message["message_id"]).collect::<Vec<_>>();
            paths.update_header(conversation, |raw, _| {
                let chat = &mut raw["metadata"]["chat"];
                if chat["summaries"].is_null() { chat["summaries"] = json!([]); }
                chat["summaries"].as_array_mut()
                    .ok_or_else(|| ChatError::storage("Invalid Chat summary archive"))?
                    .push(json!({"id":Uuid::new_v4().simple().to_string(),"valid":true,"message_ids":ids,"content":summary}));
                let revision = chat["revision"].as_u64().and_then(|value| value.checked_add(1))
                    .ok_or_else(|| ChatError::storage("Chat revision overflow"))?;
                chat["revision"] = json!(revision);
                raw["updated_at"] = json!(super::store::timestamp(now())?);
                Ok(())
            })
        })
    }
}
