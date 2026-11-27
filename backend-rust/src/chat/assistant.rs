use std::collections::{BTreeMap, HashSet};

use serde::Deserialize;
use serde_json::{Value, json};

use super::{Completion, StreamDelta, ToolCallDelta, provider_error};

#[derive(Default)]
pub(crate) struct Assistant {
    content: String,
    thinking: String,
    calls: BTreeMap<u64, ToolCall>,
    finish_reason: Option<FinishReason>,
    usage: Option<Value>,
    /// Some providers leak argument fragments into `content` once tool calls
    /// start streaming; like the previous backend, those bytes are not content.
    streamed_tool_calls: bool,
}

#[derive(Deserialize)]
struct Chunk {
    choices: Option<Vec<Choice>>,
    usage: Option<Value>,
}

#[derive(Deserialize)]
struct Choice {
    #[serde(default)]
    index: u64,
    delta: Option<Delta>,
    finish_reason: Option<String>,
}

#[derive(Deserialize)]
struct Delta {
    role: Option<String>,
    content: Option<String>,
    reasoning_content: Option<String>,
    reasoning_details: Option<Vec<Value>>,
    tool_calls: Option<Vec<ToolDelta>>,
}

#[derive(Deserialize)]
struct ToolDelta {
    index: u64,
    id: Option<String>,
    #[serde(rename = "type")]
    kind: Option<String>,
    function: Option<FunctionDelta>,
}

#[derive(Deserialize)]
struct FunctionDelta {
    name: Option<String>,
    arguments: Option<String>,
}

#[derive(Default)]
struct ToolCall {
    id: String,
    name: String,
    arguments: String,
}

#[derive(PartialEq)]
enum FinishReason {
    Stop,
    ToolCalls,
}

impl Assistant {
    pub(crate) fn push_value(
        &mut self,
        value: Value,
        on_delta: &mut impl FnMut(StreamDelta<'_>),
    ) -> Result<(), String> {
        if !value.is_object() {
            return Err("invalid Chat Completions SSE chunk: expected a JSON object".into());
        }
        if value.get("error").is_some_and(|error| !error.is_null())
            || value.get("type").and_then(Value::as_str) == Some("error")
        {
            return Err(format!(
                "Chat Completions stream error: {}",
                provider_error(&value)
            ));
        }
        let chunk: Chunk = serde_json::from_value(value)
            .map_err(|error| format!("invalid Chat Completions SSE chunk: {error}"))?;
        if let Some(usage) = chunk.usage {
            on_delta(StreamDelta::Usage(&usage));
            self.usage = Some(usage);
        }
        for choice in chunk.choices.unwrap_or_default() {
            if choice.index != 0 {
                return Err(format!(
                    "unexpected Chat Completions choice index {}",
                    choice.index
                ));
            }
            if self.finish_reason.is_some() {
                return Err("Chat Completions sent another choice after its finish reason".into());
            }
            if let Some(delta) = choice.delta {
                self.push_delta(delta, on_delta)?;
            }
            self.finish_reason = match choice.finish_reason.as_deref() {
                None => None,
                Some("stop") => Some(FinishReason::Stop),
                Some("tool_calls") => Some(FinishReason::ToolCalls),
                Some(reason) => {
                    return Err(format!(
                        "Chat Completions ended with finish_reason {reason:?}"
                    ));
                }
            };
        }
        Ok(())
    }

    fn push_delta(&mut self, delta: Delta, on_delta: &mut impl FnMut(StreamDelta<'_>)) -> Result<(), String> {
        if let Some(role) = delta.role.as_deref().filter(|role| *role != "assistant") {
            return Err(format!("unexpected Chat Completions delta role {role:?}"));
        }
        let thinking = delta.reasoning_content.unwrap_or_else(|| delta.reasoning_details.unwrap_or_default().iter()
            .filter(|item| matches!(item["type"].as_str(), None | Some("reasoning.text" | "reasoning.summary" | "text")))
            .filter_map(|item| item["text"].as_str()).collect());
        if !thinking.is_empty() {
            self.thinking.push_str(&thinking);
            on_delta(StreamDelta::Thinking(&thinking));
        }
        if let Some(calls) = delta.tool_calls {
            self.streamed_tool_calls = true;
            for fragment in calls {
                if let Some(kind) = fragment.kind.as_deref().filter(|kind| *kind != "function") {
                    return Err(format!(
                        "unsupported Chat Completions tool call type {kind:?}"
                    ));
                }
                let index = fragment.index;
                self.calls.entry(index).or_default().append(fragment);
                // Publish the accumulated call so the caller can open its display
                // block before the model finishes the remaining arguments.
                let call = self.calls.get(&index).expect("tool call was just appended");
                on_delta(StreamDelta::ToolCall(ToolCallDelta {
                    index,
                    id: &call.id,
                    name: &call.name,
                    arguments: &call.arguments,
                }));
            }
        }
        if let Some(text) = delta.content.filter(|text| !text.is_empty())
            && !self.streamed_tool_calls
        {
            self.content.push_str(&text);
            on_delta(StreamDelta::Text(&text));
        }
        Ok(())
    }

    pub(crate) fn has_finish_reason(&self) -> bool {
        self.finish_reason.is_some()
    }

    pub(crate) fn finish(mut self) -> Result<Completion, String> {
        let usage = self.usage.take();
        let thinking = std::mem::take(&mut self.thinking);
        let mut message = self.finish_message()?;
        if !thinking.is_empty() { message["reasoning_content"] = Value::String(thinking); }
        Ok(Completion {
            message,
            usage,
            responses_duration_ms: None,
        })
    }

    fn finish_message(self) -> Result<Value, String> {
        if self.calls.is_empty() {
            if self.finish_reason == Some(FinishReason::ToolCalls) {
                return Err(
                    "Chat Completions finished with tool_calls but sent no tool calls".into(),
                );
            }
            if self.finish_reason.is_none() && self.content.is_empty() {
                return Err(
                    "Chat Completions ended with no assistant output or finish reason".into(),
                );
            }
            return Ok(json!({"role": "assistant", "content": self.content}));
        }

        let mut ids = HashSet::new();
        let mut calls = Vec::with_capacity(self.calls.len());
        for (expected, (index, call)) in self.calls.into_iter().enumerate() {
            if index != expected as u64 {
                return Err(format!(
                    "Chat Completions skipped tool call index {expected}"
                ));
            }
            if call.id.trim().is_empty() || call.name.trim().is_empty() {
                return Err(format!(
                    "Chat Completions tool call {index} is missing an id or function name"
                ));
            }
            if !ids.insert(call.id.clone()) {
                return Err(format!(
                    "Chat Completions tool call {index} has a duplicate id"
                ));
            }
            match serde_json::from_str::<Value>(&call.arguments) {
                Ok(Value::Object(_)) => {}
                Ok(_) => {
                    return Err(format!(
                        "Chat Completions tool call {index} arguments must be a JSON object"
                    ));
                }
                Err(error) => {
                    return Err(format!(
                        "Chat Completions tool call {index} has invalid JSON arguments: {error}"
                    ));
                }
            }
            calls.push(json!({
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments}
            }));
        }
        let content = (!self.content.is_empty()).then_some(self.content);
        Ok(json!({"role": "assistant", "content": content, "tool_calls": calls}))
    }
}

impl ToolCall {
    fn append(&mut self, fragment: ToolDelta) {
        if let Some(id) = fragment.id {
            self.id.push_str(&id);
        }
        if let Some(function) = fragment.function {
            if let Some(name) = function.name {
                self.name.push_str(&name);
            }
            if let Some(arguments) = function.arguments {
                self.arguments.push_str(&arguments);
            }
        }
    }
}
