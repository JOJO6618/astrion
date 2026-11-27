//! Stream-side tool call detection shared by the Agent and Chat loops.
//!
//! The display layer opens one block per tool call as soon as the model names
//! it, instead of waiting until the previous tool has finished executing. Both
//! loops feed every streamed fragment here and publish the returned events.

use std::collections::BTreeMap;

use serde_json::Value;

use crate::chat::ToolCallDelta;

use super::tool_intent::{extract_complete_intent, extract_partial_intent};

/// A tool event published before execution starts.
pub(crate) enum ToolStreamEvent {
    /// A new tool call appeared; open its block in the preparing state.
    Preparing {
        id: String,
        name: String,
        intent: Option<String>,
        intent_complete: bool,
    },
    /// The streamed `intent` argument changed.
    Intent {
        id: String,
        name: String,
        intent: String,
        intent_complete: bool,
    },
}

pub(crate) struct ToolCallStream {
    intent_enabled: bool,
    tools: BTreeMap<u64, StreamTool>,
}

#[derive(Default)]
struct StreamTool {
    id: String,
    name: String,
    intent: Option<String>,
    complete: bool,
    prepared: bool,
}

impl Default for ToolCallStream {
    fn default() -> Self {
        Self::with_intent(true)
    }
}

impl ToolCallStream {
    pub(super) fn with_intent(intent_enabled: bool) -> Self {
        Self { intent_enabled, tools: BTreeMap::new() }
    }

    /// Feed one accumulated tool call fragment and return the events to publish.
    pub(crate) fn push(&mut self, delta: ToolCallDelta<'_>) -> Vec<ToolStreamEvent> {
        let mut events = Vec::new();
        let tool = self.tools.entry(delta.index).or_default();
        if !delta.id.is_empty() {
            tool.id = delta.id.to_owned();
        }
        if !delta.name.is_empty() {
            tool.name = delta.name.to_owned();
        }
        let (intent, complete) = if self.intent_enabled {
            detect_intent(delta.arguments)
        } else {
            (None, false)
        };
        if !tool.prepared {
            if let Some(intent) = &intent {
                tool.intent = Some(intent.clone());
                tool.complete = complete;
            }
            // Without an id the block would have nothing to key on; the next
            // fragment carries it and still opens the block before execution.
            if !tool.id.is_empty() && !tool.name.is_empty() {
                tool.prepared = true;
                events.push(ToolStreamEvent::Preparing {
                    id: tool.id.clone(),
                    name: tool.name.clone(),
                    intent: tool.intent.clone(),
                    intent_complete: tool.complete,
                });
            }
            return events;
        }
        let Some(intent) = intent else {
            return events;
        };
        let changed = tool.intent.as_deref() != Some(intent.as_str());
        if changed || (complete && !tool.complete) {
            tool.intent = Some(intent.clone());
            if complete {
                tool.complete = true;
            }
            events.push(ToolStreamEvent::Intent {
                id: tool.id.clone(),
                name: tool.name.clone(),
                intent,
                intent_complete: complete,
            });
        }
        events
    }
}

/// Complete intents win over the coarse scan, which is only used while streaming.
fn detect_intent(arguments: &str) -> (Option<String>, bool) {
    match extract_complete_intent(arguments) {
        Some(intent) => (Some(intent), true),
        None => (extract_partial_intent(arguments), false),
    }
}

/// Publish one event through the caller's task event channel.
pub(crate) fn publish(
    event: &ToolStreamEvent,
    mut emit: impl FnMut(&str, Value) -> Result<(), String>,
) -> Result<(), String> {
    let (kind, data) = match event {
        ToolStreamEvent::Preparing {
            id,
            name,
            intent,
            intent_complete,
        } => (
            "tool_preparing",
            serde_json::json!({
                "id": id, "name": name, "intent": intent, "intent_complete": intent_complete,
            }),
        ),
        ToolStreamEvent::Intent {
            id,
            name,
            intent,
            intent_complete,
        } => (
            "tool_intent",
            serde_json::json!({
                "id": id, "name": name, "intent": intent, "intent_complete": intent_complete,
            }),
        ),
    };
    emit(kind, data)
}

#[cfg(test)]
#[path = "../../../test/2026-11-27_RustAgent设置生效/tool_intent_stream_contract.rs"]
mod preference_contract;
