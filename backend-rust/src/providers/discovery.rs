//! `GET {base_url}/models` discovery and Python-compatible payload parsing.

use std::time::Duration;

use reqwest::{
    Client,
    header::{HeaderMap, HeaderName, HeaderValue},
};
use serde_json::{Map, Value, json};

const FETCH_TIMEOUT: Duration = Duration::from_secs(20);
const NON_CHAT: [&str; 11] = [
    "embed",
    "embedding",
    "tts",
    "whisper",
    "dall-e",
    "moderation",
    "transcri",
    "speech",
    "rerank",
    "babbage",
    "davinci",
];

pub(super) struct Discovery {
    pub models: Vec<Value>,
    pub error: Option<String>,
}

/// The production client follows the same proxy resolution as every other
/// outbound request: environment variables, then the configured proxy, then the
/// operating system proxy (macOS CFNetwork / Windows Internet settings).
/// Redirects are not followed, matching httpx's default.
pub(super) fn system_client() -> Result<Client, String> {
    crate::net::apply(Client::builder(), crate::net::resolve(None).as_ref())
        .timeout(FETCH_TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("request_error: {error}"))
}

pub(super) async fn fetch_models(
    client: &Client,
    base_url: &str,
    api_key: &str,
    headers: &Map<String, Value>,
) -> Discovery {
    let url = format!("{}/models", base_url.trim_end_matches('/'));
    let mut request_headers = HeaderMap::new();
    request_headers.insert(
        reqwest::header::ACCEPT,
        HeaderValue::from_static("application/json"),
    );
    if !api_key.is_empty() {
        match HeaderValue::from_str(&format!("Bearer {api_key}")) {
            Ok(value) => {
                request_headers.insert(reqwest::header::AUTHORIZATION, value);
            }
            Err(error) => return error_out(format!("request_error: {error}")),
        }
    }
    for (name, value) in headers {
        let name = name.trim();
        if name.is_empty() {
            continue;
        }
        let (Ok(name), Ok(value)) = (
            HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_str(&python_text(value)),
        ) else {
            return error_out("request_error: invalid custom header".into());
        };
        request_headers.insert(name, value);
    }
    let response = match client.get(url).headers(request_headers).send().await {
        Ok(response) => response,
        Err(error) if error.is_timeout() => return error_out("timeout".into()),
        Err(error) if error.is_connect() => {
            return error_out(format!("connect_error: {}", crate::net::describe_error(&error)));
        }
        Err(error) => {
            return error_out(format!("request_error: {}", crate::net::describe_error(&error)));
        }
    };
    let status = response.status();
    if status == reqwest::StatusCode::UNAUTHORIZED || status == reqwest::StatusCode::FORBIDDEN {
        return error_out(format!("auth_failed (http_{})", status.as_u16()));
    }
    if status != reqwest::StatusCode::OK {
        return error_out(format!("http_{}", status.as_u16()));
    }
    let body: Value = match response.bytes().await {
        Ok(bytes) => match serde_json::from_slice(&bytes) {
            Ok(body) => body,
            Err(_) => return error_out("bad_payload".into()),
        },
        Err(_) => return error_out("bad_payload".into()),
    };
    let mut models = parse_models_payload(&body);
    models
        .retain(|model| !is_non_chat(model.get("id").and_then(Value::as_str).unwrap_or_default()));
    if models.is_empty() {
        return error_out("empty_models".into());
    }
    Discovery {
        models,
        error: None,
    }
}

fn error_out(error: String) -> Discovery {
    Discovery {
        models: Vec::new(),
        error: Some(error),
    }
}

fn is_non_chat(model_id: &str) -> bool {
    let id = model_id.to_ascii_lowercase();
    NON_CHAT.iter().any(|fragment| id.contains(fragment))
}

/// Catalog entries may carry a hand-maintained `static_models` list for plans
/// whose endpoint does not expose `/models`. Online discovery always wins.
pub(super) fn static_fallback(entry: Option<&Value>, mut discovery: Discovery) -> Discovery {
    let Some(entry) = entry else {
        return discovery;
    };
    if !discovery.models.is_empty() && discovery.error.is_none() {
        return discovery;
    }
    let fallback = parse_models_payload(&entry["static_models"]);
    if fallback.is_empty() {
        return discovery;
    }
    discovery.models = fallback;
    discovery.error = None;
    discovery
}

/// Port of `modules/provider_manager.py::parse_models_payload`.
///
/// Accepts `{data:[...]}`, a bare array, and `{models:[...]}`. It keeps the
/// legacy `context_length` plus the newer explicit default/max/input/output
/// limits; only positive finite integers are stored. An explicit upstream
/// `context_length` is also recorded as `max_context_window` (the shared
/// ChatRust limit contract), while a bare `context_window` stays legacy-only.
pub(super) fn parse_models_payload(body: &Value) -> Vec<Value> {
    let items = match body {
        Value::Array(items) => items.as_slice(),
        Value::Object(body) => {
            if let Some(items) = body.get("data").and_then(Value::as_array) {
                items.as_slice()
            } else if let Some(items) = body.get("models").and_then(Value::as_array) {
                items.as_slice()
            } else {
                return Vec::new();
            }
        }
        _ => return Vec::new(),
    };
    let mut output = Vec::new();
    for item in items {
        if let Some(text) = item.as_str() {
            let id = text.trim();
            if !id.is_empty() {
                output.push(json!({"id": id}));
            }
            continue;
        }
        if !item.is_object() {
            continue;
        }
        let id = first_text(item, &["id", "name"]);
        if id.is_empty() {
            continue;
        }
        let mut entry = Map::new();
        entry.insert("id".into(), Value::String(id.clone()));
        let display = first_text(item, &["display_name", "displayName", "name"]);
        if !display.is_empty() && display != id {
            entry.insert("name".into(), Value::String(display));
        }
        let mut maximum = positive_limit(
            item.get("max_context_window_tokens")
                .unwrap_or(&Value::Null),
        )
        .or_else(|| positive_limit(item.get("max_context_window").unwrap_or(&Value::Null)))
        .or_else(|| positive_limit(item.get("max_context_length").unwrap_or(&Value::Null)));
        let default = positive_limit(item.get("default_context_window").unwrap_or(&Value::Null))
            .or_else(|| positive_limit(item.get("default_context_length").unwrap_or(&Value::Null)))
            .or_else(|| {
                maximum.and_then(|_| {
                    positive_limit(item.get("context_window").unwrap_or(&Value::Null))
                })
            });
        let explicit_context = positive_limit(item.get("context_length").unwrap_or(&Value::Null));
        let mut context = explicit_context
            .or(maximum)
            .or_else(|| positive_limit(item.get("context_window").unwrap_or(&Value::Null)));
        if default.is_some() && maximum.is_none() && context == default {
            context = None;
        }
        // A provider-reported `context_length` is an explicit ceiling; a bare
        // `context_window` is not (older parsers merged the two fields).
        if maximum.is_none() {
            maximum = explicit_context;
        }
        let max_input = positive_limit(item.get("max_input_tokens").unwrap_or(&Value::Null))
            .or_else(|| positive_limit(item.get("max_prompt_tokens").unwrap_or(&Value::Null)));
        let max_output = positive_limit(item.get("max_output_tokens").unwrap_or(&Value::Null))
            .or_else(|| positive_limit(item.get("max_completion_tokens").unwrap_or(&Value::Null)))
            .or_else(|| positive_limit(item.get("max_tokens").unwrap_or(&Value::Null)));
        for (key, value) in [
            ("context_length", context),
            ("default_context_window", default),
            ("max_context_window", maximum),
            ("max_input_tokens", max_input),
            ("max_output_tokens", max_output),
        ] {
            if let Some(value) = value {
                entry.insert(key.into(), json!(value));
            }
        }
        output.push(Value::Object(entry));
    }
    output
}

fn first_text(item: &Value, keys: &[&str]) -> String {
    for key in keys {
        let value = item.get(*key).unwrap_or(&Value::Null);
        let text = match value {
            Value::String(text) => text.clone(),
            Value::Bool(true) => "True".into(),
            Value::Bool(false) => "False".into(),
            Value::Number(number) => number.to_string(),
            _ => continue,
        };
        let text = text.trim();
        if !text.is_empty() {
            return text.to_owned();
        }
    }
    String::new()
}

/// Python's `positive_token_limit`: reject booleans, non-positive values, and
/// values above `i64::MAX`; strings and integral floats are accepted.
pub(super) fn positive_limit(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(_) | Value::Null => None,
        Value::Number(number) => {
            if let Some(value) = number.as_i64() {
                return (value > 0).then_some(value);
            }
            if let Some(value) = number.as_u64() {
                return (value <= i64::MAX as u64).then_some(value as i64);
            }
            let value = number.as_f64()?;
            (value > 0.0 && value.fract() == 0.0 && value <= i64::MAX as f64)
                .then_some(value as i64)
        }
        Value::String(text) => text.trim().parse::<i64>().ok().filter(|value| *value > 0),
        _ => None,
    }
}

/// Python `str()` for the scalar JSON values custom headers may contain.
pub(super) fn python_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        Value::Number(number) => number.to_string(),
        other => other.to_string(),
    }
}
