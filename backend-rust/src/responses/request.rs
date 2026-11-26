use std::collections::{HashMap, HashSet};

use serde_json::{Map, Value, json};

use super::{
    has_type, is_truthy, python_json, python_string, python_trim, string_field, string_or_empty,
    strip_server_id, value_or, values,
};

const UNSUPPORTED_SCHEMA_KEYS: &[&str] = &[
    "propertyNames",
    "patternProperties",
    "unevaluatedProperties",
    "unevaluatedItems",
    "dependentSchemas",
    "dependentRequired",
    "dependencies",
    "if",
    "then",
    "else",
    "contains",
    "minContains",
    "maxContains",
    "contentEncoding",
    "contentMediaType",
    "contentSchema",
    "$schema",
    "$id",
    "$anchor",
    "$comment",
    "$vocabulary",
    "$dynamicRef",
    "$dynamicAnchor",
    "$recursiveRef",
    "$recursiveAnchor",
];
const NAMED_SCHEMA_KEYS: &[&str] = &["properties", "$defs", "definitions"];

/// Inputs to the pure Responses request builder. Optional provider policies can
/// be set by the caller after constructing the request.
#[derive(Debug, Clone, Copy)]
pub struct ResponsesRequest<'a> {
    pub model_id: &'a str,
    pub messages: &'a [Value],
    pub instructions: &'a str,
    pub tools: &'a [Value],
    pub effort: Option<&'a str>,
    pub summary: &'a str,
    pub max_output_tokens: Option<i64>,
    pub verbosity: Option<&'a str>,
    pub session_id: Option<&'a str>,
    pub use_developer_prompt: bool,
}

impl<'a> ResponsesRequest<'a> {
    /// Start with the protocol defaults, including detailed reasoning summaries.
    pub fn new(model_id: &'a str, messages: &'a [Value]) -> Self {
        Self {
            model_id,
            messages,
            instructions: "",
            tools: &[],
            effort: None,
            summary: "detailed",
            max_output_tokens: None,
            verbosity: None,
            session_id: None,
            use_developer_prompt: false,
        }
    }
}

/// Provider details used by the instructions and prompt-cache policies.
#[derive(Debug, Clone, Copy, Default)]
pub struct ResponsesProfile<'a> {
    pub model_id: Option<&'a str>,
    pub base_url: Option<&'a str>,
    pub provider_id: Option<&'a str>,
    pub responses_auth: Option<&'a str>,
}

/// Move OpenAI system instructions to the first developer message for caching.
pub fn should_use_developer_prompt(profile: &ResponsesProfile<'_>) -> bool {
    let model = python_trim(profile.model_id.unwrap_or_default()).to_lowercase();
    let model_bytes = model.as_bytes();
    let o_series = matches!(
        model_bytes,
        [b'o', b'1'..=b'9'] | [b'o', b'1'..=b'9', b'-', ..]
    );
    let openai_model = (model.starts_with("gpt-") && !model.starts_with("gpt-oss"))
        || model.starts_with("chatgpt-")
        || o_series;
    openai_model || should_send_prompt_cache_key(profile)
}

/// Send a cache key for OAuth and first-party OpenAI connections.
pub fn should_send_prompt_cache_key(profile: &ResponsesProfile<'_>) -> bool {
    if profile.responses_auth == Some("codex_oauth")
        || matches!(profile.provider_id, Some("openai-api" | "openai-codex"))
    {
        return true;
    }
    let host = base_host(profile.base_url.unwrap_or_default());
    [
        "openai.com",
        "openai.azure.com",
        "gateway.ai.cloudflare.com",
    ]
    .iter()
    .any(|domain| {
        host == *domain
            || host
                .strip_suffix(domain)
                .is_some_and(|subdomain| subdomain.ends_with('.'))
    })
}

fn base_host(base_url: &str) -> String {
    // The Python policy uses the lowercased netloc up to its first colon.
    let url = base_url
        .trim_start_matches(|character: char| character <= ' ')
        .replace(['\r', '\n', '\t'], "");
    let authority = if let Some(authority) = url.strip_prefix("//") {
        authority
    } else if let Some((scheme, authority)) = url.split_once("://") {
        if !scheme.starts_with(|character: char| character.is_ascii_alphabetic())
            || !scheme
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"+-.".contains(&byte))
        {
            return String::new();
        }
        authority
    } else {
        return String::new();
    };
    let netloc = authority.split(['/', '?', '#']).next().unwrap_or_default();
    if netloc.contains(['[', ']']) {
        return String::new();
    }
    netloc.split(':').next().unwrap_or_default().to_lowercase()
}

/// Assemble the Responses request, including stateless encrypted reasoning.
pub fn build_responses_body(request: ResponsesRequest<'_>) -> Value {
    let (system, history) = split_system_messages(request.messages);
    let instructions = if system.is_empty() {
        request.instructions.to_owned()
    } else if is_frozen_system(request.messages) {
        system
    } else {
        format!("{}\n\n{system}", request.instructions)
    };
    let mut input = chat_request_to_input(&history);
    let mut body = json!({
        "model": request.model_id,
        "input": [],
        "store": false,
        "stream": true,
        "include": ["reasoning.encrypted_content"],
    });
    if request.use_developer_prompt && !instructions.is_empty() {
        input.insert(
            0,
            json!({
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": instructions}],
            }),
        );
    } else {
        body["instructions"] = json!(instructions);
    }
    body["input"] = Value::Array(input);
    let tools = flatten_tools(request.tools);
    if !tools.is_empty() {
        body["tools"] = Value::Array(tools);
        body["tool_choice"] = json!("auto");
    }
    let mut reasoning = Map::new();
    if let Some(effort) = request.effort.filter(|value| !value.is_empty()) {
        reasoning.insert("effort".into(), json!(effort));
    }
    if !request.summary.is_empty() {
        reasoning.insert("summary".into(), json!(request.summary));
    }
    if !reasoning.is_empty() {
        body["reasoning"] = Value::Object(reasoning);
    }
    if let Some(verbosity) = request.verbosity.filter(|value| !value.is_empty()) {
        body["text"] = json!({"verbosity": verbosity});
    }
    if let Some(max_tokens) = request.max_output_tokens.filter(|value| *value > 0) {
        body["max_output_tokens"] = json!(max_tokens);
    }
    if let Some(session_id) = request.session_id.filter(|value| !value.is_empty()) {
        body["prompt_cache_key"] = json!(session_id);
    }
    body
}

fn has_role(value: &Value, role: &str) -> bool {
    string_field(value, "role") == Some(role)
}

fn is_frozen_system(messages: &[Value]) -> bool {
    messages.first().is_some_and(|message| {
        has_role(message, "system")
            && message.get("_frozen_system_prompt") == Some(&Value::Bool(true))
    })
}

/// Merge system prompts and return the remaining chat messages in history order.
pub fn split_system_messages(messages: &[Value]) -> (String, Vec<Value>) {
    let mut system = Vec::new();
    let mut history = Vec::new();
    for message in messages.iter().filter(|message| message.is_object()) {
        if has_role(message, "system") {
            let content = &message["content"];
            let text = content
                .as_str()
                .map_or_else(|| python_json(content), str::to_owned);
            if !text.is_empty() {
                system.push(text);
            }
        } else {
            history.push(message.clone());
        }
    }
    (system.join("\n\n"), history)
}

/// Translate conversation history to Responses input items.
pub fn chat_request_to_input(messages: &[Value]) -> Vec<Value> {
    let mut input = Vec::new();
    let mut call_ids = Vec::<&Value>::new();
    for message in messages.iter().filter(|message| message.is_object()) {
        let content = &message["content"];
        match message["role"].as_str() {
            Some("user") => input.push(user_message(user_content(content))),
            Some("assistant") => {
                input.extend(
                    values(&message["responses_reasoning_items"])
                        .iter()
                        .filter(|item| has_type(item, "reasoning"))
                        .map(strip_server_id),
                );
                let text = if content.is_array() {
                    json!(flatten_content(content))
                } else {
                    content.clone()
                };
                if is_truthy(&text) {
                    input.push(json!({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }));
                }
                for call in values(&message["tool_calls"])
                    .iter()
                    .filter(|call| call.is_object())
                {
                    let id = &call["id"];
                    let function = &call["function"];
                    let name = &function["name"];
                    if !is_truthy(id) || !is_truthy(name) {
                        continue;
                    }
                    call_ids.push(id);
                    input.push(json!({
                        "type": "function_call",
                        "call_id": id,
                        "name": name,
                        "arguments": value_or(&function["arguments"], &json!("")),
                    }));
                }
            }
            Some("tool") => {
                let id = &message["tool_call_id"];
                let output = flatten_content(content);
                if is_truthy(id) && call_ids.contains(&id) {
                    input.push(
                        json!({"type": "function_call_output", "call_id": id, "output": output}),
                    );
                } else {
                    let name = python_string(value_or(&message["name"], &json!("tool")));
                    input.push(user_message(vec![input_text(format!(
                        "[工具结果（{name}）]\n{output}"
                    ))]));
                }
            }
            _ => {}
        }
    }
    input
}

fn flatten_content(content: &Value) -> String {
    match content {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|part| {
                match part {
                    Value::Object(_) => match part["type"].as_str() {
                        Some("text") => Some(string_or_empty(&part["text"])),
                        Some("image_url") => Some("[图片]".into()),
                        Some("video_url") => Some("[视频]".into()),
                        _ => None,
                    },
                    _ => Some(python_string(part)),
                }
                .filter(|text| !text.is_empty())
            })
            .collect::<Vec<_>>()
            .join("\n"),
        _ => python_json(content),
    }
}

fn user_message(content: Vec<Value>) -> Value {
    json!({"type": "message", "role": "user", "content": content})
}

fn input_text(text: impl Into<String>) -> Value {
    json!({"type": "input_text", "text": text.into()})
}

fn user_content(content: &Value) -> Vec<Value> {
    if let Some(text) = content.as_str() {
        return vec![input_text(text)];
    }
    let mut result = Vec::new();
    for part in values(content) {
        match part {
            Value::Object(_) => match part["type"].as_str() {
                Some("text") => {
                    let text = string_or_empty(&part["text"]);
                    if !text.is_empty() {
                        result.push(input_text(text));
                    }
                }
                Some("image_url") => {
                    let image = &part["image_url"];
                    if is_truthy(image) {
                        let url = if image.is_object() {
                            image["url"].clone()
                        } else {
                            json!(python_string(image))
                        };
                        if is_truthy(&url) {
                            result.push(json!({"type": "input_image", "image_url": url}));
                        }
                    }
                }
                Some("video_url") => {
                    result.push(input_text("[用户提供了视频，当前模型不支持视频输入]"))
                }
                _ => {}
            },
            _ => {
                let text = python_string(part);
                let text = python_trim(&text);
                if !text.is_empty() {
                    result.push(input_text(text));
                }
            }
        }
    }
    if result.is_empty() {
        result.push(input_text(""));
    }
    result
}

/// Drop unsupported schema keywords without discarding user-defined property names.
pub fn sanitize_json_schema(node: &Value) -> Value {
    match node {
        Value::Array(items) => Value::Array(items.iter().map(sanitize_json_schema).collect()),
        Value::Object(members) => Value::Object(
            members
                .iter()
                .filter(|(key, _)| !UNSUPPORTED_SCHEMA_KEYS.contains(&key.as_str()))
                .map(|(key, value)| {
                    let value = match value {
                        Value::Object(named) if NAMED_SCHEMA_KEYS.contains(&key.as_str()) => {
                            Value::Object(
                                named
                                    .iter()
                                    .map(|(name, schema)| {
                                        (name.clone(), sanitize_json_schema(schema))
                                    })
                                    .collect(),
                            )
                        }
                        _ => sanitize_json_schema(value),
                    };
                    (key.clone(), value)
                })
                .collect(),
        ),
        _ => node.clone(),
    }
}

fn schema_required(params: &Value) -> HashSet<String> {
    match params.get("required") {
        Some(Value::Array(required)) => required
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect(),
        Some(Value::String(required)) => required
            .chars()
            .map(|character| character.to_string())
            .collect(),
        Some(Value::Object(required)) => required.keys().cloned().collect(),
        _ => HashSet::new(),
    }
}

fn optional_params_nullable(mut params: Value) -> Value {
    let required = schema_required(&params);
    let Some(properties) = params.get_mut("properties").and_then(Value::as_object_mut) else {
        return params;
    };
    for (name, spec) in properties {
        if !spec.is_object() || required.contains(name) {
            continue;
        }
        match spec.get_mut("type") {
            Some(Value::String(kind)) if kind != "null" => spec["type"] = json!([kind, "null"]),
            Some(Value::Array(kinds))
                if !kinds.iter().any(|kind| kind.as_str() == Some("null")) =>
            {
                kinds.push(json!("null"));
            }
            _ => {}
        }
        let description = string_or_empty(&spec["description"]);
        if !description.contains("null") {
            spec["description"] = json!(format!(
                "{description}（不需要时填 null，系统会视为未提供）"
            ));
        }
    }
    params
}

fn tool_function(tool: &Value) -> Option<&Value> {
    let function = if has_type(tool, "function") {
        tool.get("function")?
    } else {
        tool
    };
    function
        .get("name")
        .filter(|name| is_truthy(name))
        .map(|_| function)
}

/// Convert wrapped Chat tool definitions to the Responses function shape.
pub fn flatten_tools(tools: &[Value]) -> Vec<Value> {
    tools
        .iter()
        .filter_map(tool_function)
        .map(|function| {
            let fallback = json!({"type": "object", "properties": {}});
            let parameters = optional_params_nullable(sanitize_json_schema(value_or(
                &function["parameters"],
                &fallback,
            )));
            let mut tool = json!({
                "type": "function",
                "name": function["name"],
                "description": value_or(&function["description"], &json!("")),
                "parameters": parameters,
            });
            if !function["strict"].is_null() {
                tool["strict"] = json!(is_truthy(&function["strict"]));
            }
            tool
        })
        .collect()
}

pub type ToolRequiredMap = HashMap<String, HashSet<String>>;

/// Collect the required fields from the original Chat tool definitions.
pub fn tool_required_map(tools: &[Value]) -> ToolRequiredMap {
    tools
        .iter()
        .filter_map(tool_function)
        .map(|function| {
            (
                python_string(&function["name"]),
                schema_required(&function["parameters"]),
            )
        })
        .collect()
}

/// Remove optional top-level null arguments from a known tool's argument string.
pub fn sanitize_tool_arguments(
    name: Option<&str>,
    arguments: &Value,
    required: &ToolRequiredMap,
) -> String {
    let text = arguments.as_str().map_or_else(
        || python_json(value_or(arguments, &json!({}))),
        str::to_owned,
    );
    let Some(required) = name
        .filter(|name| !name.is_empty())
        .and_then(|name| required.get(name))
    else {
        return text;
    };
    let Ok(Value::Object(mut parsed)) = serde_json::from_str::<Value>(&text) else {
        return text;
    };
    parsed.retain(|key, value| !value.is_null() || required.contains(key));
    python_json(&Value::Object(parsed))
}
