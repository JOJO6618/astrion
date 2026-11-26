use std::collections::{HashMap, HashSet};
use std::time::Duration;

use serde_json::{Value, json};

use super::{has_type, is_truthy, python_string, string_or_empty, value_or};

#[derive(Debug, PartialEq, Eq, Hash)]
enum OutputPart {
    Text(String),
    Function,
}

/// Measure the union of intervals during which at least one output is active.
#[derive(Debug, Default)]
pub(super) struct OutputTimer {
    item_keys: HashMap<String, String>,
    active: HashSet<(String, OutputPart)>,
    started_at: Option<Duration>,
    elapsed: Duration,
    completed: bool,
}

impl OutputTimer {
    pub(super) fn observe(&mut self, kind: &str, event: &Value, at: Duration) {
        let item = &event["item"];
        let key = self.item_key(event, item);
        if kind.starts_with("response.reasoning") || kind == "response.completed" {
            self.active.clear();
            self.close_interval(at);
            self.completed |= kind == "response.completed";
            return;
        }
        match kind {
            "response.output_item.added" => {
                if has_type(item, "function_call") && is_truthy(&item["name"]) {
                    self.start((key, OutputPart::Function), at);
                } else if has_type(item, "reasoning") {
                    self.active.clear();
                    self.close_interval(at);
                }
            }
            "response.output_text.delta" | "response.refusal.delta" => {
                if is_truthy(&event["delta"]) {
                    self.start((key, text_part(event)), at);
                }
            }
            "response.function_call_arguments.delta" => {
                if is_truthy(&event["delta"]) {
                    self.start((key, OutputPart::Function), at);
                }
            }
            "response.output_text.done" | "response.refusal.done" => {
                self.active.remove(&(key, text_part(event)));
                self.close_interval(at);
            }
            "response.function_call_arguments.done" => {
                self.active.remove(&(key, OutputPart::Function));
                self.close_interval(at);
            }
            "response.output_item.done" => {
                self.active.retain(|(active_key, _)| active_key != &key);
                self.close_interval(at);
            }
            _ => {}
        }
    }

    pub(super) fn summary(&self) -> Value {
        let whole_ms = u64::try_from(self.elapsed.as_millis()).unwrap_or(u64::MAX);
        let fractional_ns = self.elapsed.subsec_nanos() % 1_000_000;
        let round_up = fractional_ns > 500_000 || (fractional_ns == 500_000 && whole_ms % 2 == 1);
        let duration_ms = whole_ms.saturating_add(u64::from(round_up));
        if self.completed && duration_ms > 0 {
            json!({"duration_ms": duration_ms})
        } else {
            json!({})
        }
    }

    fn item_key(&mut self, event: &Value, item: &Value) -> String {
        let item_id = string_or_empty(value_or(&event["item_id"], &item["id"]));
        if let Some(key) = self.item_keys.get(&item_id) {
            return key.clone();
        }
        let index = &event["output_index"];
        let key = if index.is_null() {
            format!("id:{item_id}")
        } else {
            format!("index:{}", python_string(index))
        };
        if !item_id.is_empty() {
            self.item_keys.insert(item_id, key.clone());
        }
        key
    }

    fn start(&mut self, part: (String, OutputPart), at: Duration) {
        if self.active.is_empty() {
            self.started_at = Some(at);
        }
        self.active.insert(part);
    }

    fn close_interval(&mut self, at: Duration) {
        if self.active.is_empty()
            && let Some(start) = self.started_at.take()
        {
            self.elapsed = self.elapsed.saturating_add(at.saturating_sub(start));
        }
    }
}

fn text_part(event: &Value) -> OutputPart {
    OutputPart::Text(
        event
            .get("content_index")
            .map_or_else(|| "0".into(), python_string),
    )
}
