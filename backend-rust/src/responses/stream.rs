use std::collections::{BTreeMap, HashMap, HashSet};
use std::fmt;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use super::timing::OutputTimer;
use super::{
    ToolRequiredMap, has_type, is_truthy, python_json, python_trim, sanitize_tool_arguments,
    string_field, string_or_empty, strip_server_id, value_or, values,
};

/// An error event from the Responses API, or an EOF before completion.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResponsesApiError {
    pub message: String,
    pub code: Option<String>,
}

impl fmt::Display for ResponsesApiError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for ResponsesApiError {}

/// Incrementally translate a single Responses SSE stream into Chat-shaped chunks.
#[derive(Debug)]
pub struct ResponsesSseParser {
    required: ToolRequiredMap,
    item_indexes: HashMap<String, usize>,
    tool_names: Vec<String>,
    arguments: BTreeMap<usize, String>,
    summaries: BTreeMap<(String, i64), String>,
    summary_emitted: bool,
    reasoning: Vec<Value>,
    reasoning_keys: HashSet<String>,
    completed: bool,
    failure: Option<ResponsesApiError>,
    started_at: Instant,
    timer: OutputTimer,
}

impl Default for ResponsesSseParser {
    fn default() -> Self {
        Self::new(ToolRequiredMap::new())
    }
}

impl ResponsesSseParser {
    /// Pass required tool fields from [`super::tool_required_map`].
    pub fn new(required: ToolRequiredMap) -> Self {
        Self {
            required,
            item_indexes: HashMap::new(),
            tool_names: Vec::new(),
            arguments: BTreeMap::new(),
            summaries: BTreeMap::new(),
            summary_emitted: false,
            reasoning: Vec::new(),
            reasoning_keys: HashSet::new(),
            completed: false,
            failure: None,
            started_at: Instant::now(),
            timer: OutputTimer::default(),
        }
    }

    /// Convert a decoded SSE line, which may produce zero or more chunks.
    pub fn push_line(&mut self, line: &str) -> Result<Vec<Value>, ResponsesApiError> {
        self.push_line_at(line, self.started_at.elapsed())
    }

    /// Convert a line with an explicit monotonic timestamp measured from the
    /// start of this stream. Use a single clock for all lines in a stream.
    pub fn push_line_at(
        &mut self,
        line: &str,
        at: Duration,
    ) -> Result<Vec<Value>, ResponsesApiError> {
        if let Some(payload) = line.strip_prefix("data:") {
            return self.push_data_at(payload, at);
        }
        self.failure.clone().map_or_else(|| Ok(Vec::new()), Err)
    }

    /// Convert an SSE data payload from an event decoder. Multiple data lines
    /// may be joined with newlines before passing the payload here.
    pub fn push_data(&mut self, payload: &str) -> Result<Vec<Value>, ResponsesApiError> {
        self.push_data_at(payload, self.started_at.elapsed())
    }

    fn push_data_at(
        &mut self,
        payload: &str,
        at: Duration,
    ) -> Result<Vec<Value>, ResponsesApiError> {
        if let Some(error) = &self.failure {
            return Err(error.clone());
        }
        let payload = python_trim(payload);
        if payload.is_empty() || payload == "[DONE]" {
            return Ok(Vec::new());
        }
        let Ok(event) = serde_json::from_str::<Value>(payload) else {
            return Ok(Vec::new());
        };
        let Some(kind) = string_field(&event, "type") else {
            return Ok(Vec::new());
        };
        self.timer.observe(kind, &event, at);

        let mut chunks = Vec::new();
        match kind {
            "response.created" => chunks.push(chat_delta(json!({"role": "assistant"}))),
            "response.output_text.delta" => {
                if is_truthy(&event["delta"]) {
                    chunks.push(chat_delta(json!({"content": event["delta"]})));
                }
            }
            "response.reasoning_text.delta" => {
                if is_truthy(&event["delta"]) {
                    chunks.push(chat_delta(json!({"reasoning_content": event["delta"]})));
                }
            }
            "response.reasoning_summary_text.delta" => {
                self.summaries
                    .entry(summary_key(&event))
                    .or_default()
                    .push_str(&string_or_empty(&event["delta"]));
            }
            "response.reasoning_summary_text.done" => {
                let buffered = self
                    .summaries
                    .remove(&summary_key(&event))
                    .unwrap_or_default();
                let explicit = string_or_empty(&event["text"]);
                let text = if explicit.is_empty() {
                    buffered
                } else {
                    explicit
                };
                if let Some(chunk) = self.summary_chunk(&text) {
                    chunks.push(chunk);
                }
            }
            "response.output_item.added" => {
                let item = &event["item"];
                if has_type(item, "function_call") {
                    chunks.push(self.add_call(item));
                }
            }
            "response.function_call_arguments.delta" => {
                if let Some(index) = self.call_index(&event["item_id"]) {
                    self.arguments
                        .entry(index)
                        .or_default()
                        .push_str(&string_or_empty(&event["delta"]));
                }
            }
            "response.output_item.done" => {
                let item = &event["item"];
                if has_type(item, "reasoning") {
                    self.collect_reasoning(item);
                } else if has_type(item, "function_call")
                    && let Some(chunk) = self.finish_call(item)
                {
                    chunks.push(chunk);
                }
            }
            "response.completed" => self.complete(&event["response"], &mut chunks),
            "response.failed" | "error" => {
                let error = event_error(kind, &event);
                self.failure = Some(error.clone());
                return Err(error);
            }
            _ => {}
        }
        Ok(chunks)
    }

    /// Whether a `response.completed` event has been received.
    pub fn is_completed(&self) -> bool {
        self.completed
    }

    /// Check whether EOF followed a successful `response.completed` event.
    pub fn finish(&self) -> Result<(), ResponsesApiError> {
        if let Some(error) = &self.failure {
            return Err(error.clone());
        }
        if self.completed {
            return Ok(());
        }
        Err(ResponsesApiError {
            message: "Responses 响应流中断（未收到 completed 事件）".into(),
            code: Some("stream_incomplete".into()),
        })
    }

    /// Collected encrypted reasoning items in arrival order, with IDs stripped.
    pub fn reasoning_items(&self) -> &[Value] {
        &self.reasoning
    }

    fn summary_chunk(&mut self, raw: &str) -> Option<Value> {
        let cleaned = raw.replace("**", "");
        let cleaned = python_trim(&cleaned);
        if cleaned.is_empty() {
            return None;
        }
        let content = if self.summary_emitted {
            format!("\n\n{cleaned}")
        } else {
            cleaned.to_owned()
        };
        self.summary_emitted = true;
        let mut chunk = chat_delta(json!({"reasoning_content": content}));
        chunk["_reasoning_summary"] = json!(cleaned);
        Some(chunk)
    }

    fn add_call(&mut self, item: &Value) -> Value {
        let index = self.tool_names.len();
        let id = string_or_empty(&item["id"]);
        if !id.is_empty() {
            self.item_indexes.insert(id, index);
        }
        let name = value_or(&item["name"], &Value::Null);
        self.tool_names.push(string_or_empty(name));
        chat_delta(json!({
            "tool_calls": [{
                "index": index,
                "id": item["call_id"],
                "type": "function",
                "function": {"name": value_or(name, &json!("")), "arguments": ""},
            }],
        }))
    }

    fn call_index(&self, item_id: &Value) -> Option<usize> {
        self.item_indexes
            .get(&string_or_empty(item_id))
            .copied()
            .or_else(|| self.tool_names.len().checked_sub(1))
    }

    fn finish_call(&mut self, item: &Value) -> Option<Value> {
        let index = self.call_index(&item["id"])?;
        let buffered = self.arguments.remove(&index).unwrap_or_default();
        let raw = string_field(item, "arguments")
            .filter(|args| !args.is_empty())
            .map_or(buffered, str::to_owned);
        self.arguments_chunk(index, string_field(item, "name"), raw)
    }

    fn arguments_chunk(&self, index: usize, name: Option<&str>, raw: String) -> Option<Value> {
        let cleaned = sanitize_tool_arguments(name, &Value::String(raw), &self.required);
        (!cleaned.is_empty()).then(|| {
            chat_delta(json!({
                "tool_calls": [{"index": index, "function": {"arguments": cleaned}}],
            }))
        })
    }

    fn collect_reasoning(&mut self, item: &Value) {
        let cleaned = strip_server_id(item);
        let mut sorted = cleaned.clone();
        sorted.sort_all_objects();
        let key: String = python_json(&sorted).chars().take(200).collect();
        if self.reasoning_keys.insert(key) {
            self.reasoning.push(cleaned);
        }
    }

    fn complete(&mut self, response: &Value, chunks: &mut Vec<Value>) {
        self.completed = true;
        for text in std::mem::take(&mut self.summaries).into_values() {
            chunks.extend(self.summary_chunk(&text));
        }
        for (index, raw) in std::mem::take(&mut self.arguments) {
            if !raw.is_empty() {
                let name = self.tool_names.get(index).map(String::as_str);
                chunks.extend(self.arguments_chunk(index, name, raw));
            }
        }
        for item in values(&response["output"])
            .iter()
            .filter(|item| has_type(item, "reasoning"))
        {
            self.collect_reasoning(item);
        }
        let reason = if self.tool_names.is_empty() {
            "stop"
        } else {
            "tool_calls"
        };
        let mut chunk = json!({"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]});
        if let Some(usage) = map_usage(&response["usage"]) {
            chunk["usage"] = usage;
        }
        chunk["_response_output_timing"] = self.timer.summary();
        chunks.push(chunk);
    }
}

fn chat_delta(delta: Value) -> Value {
    json!({"choices": [{"index": 0, "delta": delta}]})
}

fn summary_key(event: &Value) -> (String, i64) {
    let index = match &event["summary_index"] {
        Value::Number(number) => number
            .as_i64()
            .unwrap_or_else(|| number.as_f64().unwrap_or_default() as i64),
        Value::String(text) => python_trim(text).parse().unwrap_or_default(),
        Value::Bool(value) => i64::from(*value),
        _ => 0,
    };
    (string_or_empty(&event["item_id"]), index)
}

fn map_usage(usage: &Value) -> Option<Value> {
    let reasoning = &usage["output_tokens_details"]["reasoning_tokens"];
    if ["input_tokens", "output_tokens", "total_tokens"]
        .into_iter()
        .all(|field| usage[field].as_u64().is_none())
        && reasoning.as_u64().is_none()
    {
        return None;
    }
    let zero = json!(0);
    let prompt = value_or(&usage["input_tokens"], &zero);
    let completion = value_or(&usage["output_tokens"], &zero);
    let total = if is_truthy(&usage["total_tokens"]) {
        usage["total_tokens"].clone()
    } else {
        token_total(prompt, completion)
    };
    let mut mapped =
        json!({"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total});
    let cached = &usage["input_tokens_details"]["cached_tokens"];
    if !cached.is_null() {
        mapped["prompt_tokens_details"] = json!({"cached_tokens": cached});
    }
    if !reasoning.is_null() {
        mapped["completion_tokens_details"] = json!({"reasoning_tokens": reasoning});
    }
    Some(mapped)
}

fn token_total(prompt: &Value, completion: &Value) -> Value {
    if let (Some(left), Some(right)) = (prompt.as_u64(), completion.as_u64())
        && let Some(total) = left.checked_add(right)
    {
        return total.into();
    }
    if let (Some(left), Some(right)) = (prompt.as_i64(), completion.as_i64())
        && let Some(total) = left.checked_add(right)
    {
        return total.into();
    }
    if let (Some(left), Some(right)) = (prompt.as_str(), completion.as_str()) {
        return json!(format!("{left}{right}"));
    }
    let number = |value: &Value| match value {
        Value::Bool(value) => f64::from(*value),
        other => other.as_f64().unwrap_or_default(),
    };
    json!(number(prompt) + number(completion))
}

fn event_error(kind: &str, event: &Value) -> ResponsesApiError {
    let (error, message, default) = if kind == "response.failed" {
        let error = &event["response"]["error"];
        (error, &error["message"], "Responses 响应失败")
    } else {
        (
            event,
            value_or(&event["message"], &event["error"]),
            "Responses 流式错误",
        )
    };
    let message = string_or_empty(message);
    ResponsesApiError {
        message: if message.is_empty() {
            default.into()
        } else {
            message
        },
        code: string_field(error, "code").map(str::to_owned),
    }
}
