use std::collections::{BTreeMap, HashSet};

use serde::Deserialize;
use serde_json::{Value, json};

use super::{Completion, provider_error};

#[derive(Default)]
pub(crate) struct Assistant {
    content: String,
    calls: BTreeMap<u64, ToolCall>,
    finish_reason: Option<FinishReason>,
    usage: Option<Value>,
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
        on_text: &mut impl FnMut(&str),
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
                self.push_delta(delta, on_text)?;
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

    fn push_delta(&mut self, delta: Delta, on_text: &mut impl FnMut(&str)) -> Result<(), String> {
        if let Some(role) = delta.role.as_deref().filter(|role| *role != "assistant") {
            return Err(format!("unexpected Chat Completions delta role {role:?}"));
        }
        if let Some(calls) = delta.tool_calls {
            for fragment in calls {
                if let Some(kind) = fragment.kind.as_deref().filter(|kind| *kind != "function") {
                    return Err(format!(
                        "unsupported Chat Completions tool call type {kind:?}"
                    ));
                }
                self.calls
                    .entry(fragment.index)
                    .or_default()
                    .append(fragment);
            }
        }
        if let Some(text) = delta.content.filter(|text| !text.is_empty()) {
            self.content.push_str(&text);
            on_text(&text);
        }
        Ok(())
    }

    pub(crate) fn has_finish_reason(&self) -> bool {
        self.finish_reason.is_some()
    }

    pub(crate) fn finish(mut self) -> Result<Completion, String> {
        let usage = self.usage.take();
        Ok(Completion {
            message: self.finish_message()?,
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
