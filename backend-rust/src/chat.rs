pub(crate) mod assistant;
pub(crate) mod sse;

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/chat_stream.rs"]
mod tests;

use std::{sync::OnceLock, time::Duration};

use futures_util::StreamExt;
use reqwest::{
    Client, Response,
    header::{self, HeaderMap, HeaderName, HeaderValue},
};
use serde_json::{Map, Value, json};

use assistant::Assistant;
use sse::Decoder;

#[derive(Debug)]
pub struct Completion {
    pub message: Value,
    pub usage: Option<Value>,
    /// Union of raw Responses text/function output intervals. None when no
    /// valid Responses speed sample exists or the protocol is Chat Completions.
    pub responses_duration_ms: Option<u64>,
}

/// Provider-supplied configuration read by the trusted server.
#[derive(Clone, Default)]
pub struct RequestOptions {
    pub headers: Map<String, Value>,
    pub extra_params: Map<String, Value>,
    pub max_output_tokens: Option<u64>,
}

/// Streams one Chat Completions turn and returns the assistant message for the next request.
/// The callback receives text as it arrives, including text from streams that later fail.
/// Completion requires `[DONE]`, or a clean HTTP end after a successful finish reason.
pub async fn complete(
    endpoint: &str,
    api_key: Option<&str>,
    model: &str,
    messages: &[Value],
    tools: &[Value],
    on_text: impl FnMut(&str),
) -> Result<Value, String> {
    complete_with_usage(endpoint, api_key, model, messages, tools, on_text)
        .await
        .map(|completion| completion.message)
}

/// Returns the completed assistant message and any usage reported by the provider.
pub async fn complete_with_usage(
    endpoint: &str,
    api_key: Option<&str>,
    model: &str,
    messages: &[Value],
    tools: &[Value],
    on_text: impl FnMut(&str),
) -> Result<Completion, String> {
    complete_with_options(
        endpoint,
        api_key,
        model,
        messages,
        tools,
        &RequestOptions::default(),
        on_text,
    )
    .await
}

pub(crate) async fn complete_with_options(
    endpoint: &str,
    api_key: Option<&str>,
    model: &str,
    messages: &[Value],
    tools: &[Value],
    options: &RequestOptions,
    mut on_text: impl FnMut(&str),
) -> Result<Completion, String> {
    let mut body = json!({"model": model, "messages": messages, "stream": true});
    for message in body["messages"]
        .as_array_mut()
        .expect("messages are an array")
    {
        if let Some(fields) = message.as_object_mut() {
            fields.shift_remove("_frozen_system_prompt");
        }
    }
    if !tools.is_empty() {
        body["tools"] = json!(tools);
        body["tool_choice"] = json!("auto");
    }
    if let Some(limit) = options.max_output_tokens.filter(|limit| *limit > 0)
        && !options.extra_params.contains_key("max_completion_tokens")
    {
        body["max_tokens"] = json!(limit);
    }
    apply_extra_params(&mut body, &options.extra_params);
    let headers = request_headers(&options.headers)?;
    let response = post_sse(endpoint, api_key, &body, "Chat Completions", headers).await?;
    let mut decoder = Decoder::default();
    let mut assistant = Assistant::default();
    let mut on_event = |event: &str, data: &str| {
        if event == "error" {
            return Err(format!(
                "Chat Completions stream error: {}",
                error_detail(data)
            ));
        }
        if data.trim() == "[DONE]" {
            return Ok(true);
        }
        let value = serde_json::from_str(data)
            .map_err(|error| format!("invalid Chat Completions SSE chunk: {error}"))?;
        assistant.push_value(value, &mut on_text)?;
        Ok(false)
    };
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|error| {
            format!(
                "Chat Completions stream read failed: {}",
                error.without_url()
            )
        })?;
        if decoder.push(&chunk, &mut on_event)? {
            return assistant.finish();
        }
    }
    if decoder.finish(on_event)? || assistant.has_finish_reason() {
        return assistant.finish();
    }
    Err("Chat Completions stream ended before [DONE] or a successful finish reason".into())
}

pub(crate) async fn post_sse(
    endpoint: &str,
    api_key: Option<&str>,
    body: &Value,
    protocol: &str,
    headers: HeaderMap,
) -> Result<Response, String> {
    let mut request = client()?
        .post(endpoint)
        .headers(headers)
        .header(header::ACCEPT, "text/event-stream")
        .json(body);
    if let Some(key) = api_key.filter(|key| !key.is_empty()) {
        request = request.bearer_auth(key);
    }
    let response = request
        .send()
        .await
        .map_err(|error| format!("{protocol} request failed: {}", error.without_url()))?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!(
            "{protocol} HTTP {status}: {}",
            response_detail(response).await
        ));
    }
    if response_is_json(&response) {
        return Err(format!(
            "{protocol} expected an SSE stream (HTTP {status}): {}",
            response_detail(response).await
        ));
    }
    Ok(response)
}

pub(crate) fn request_headers(values: &Map<String, Value>) -> Result<HeaderMap, String> {
    let mut headers = HeaderMap::new();
    for (name, value) in values {
        let name = HeaderName::from_bytes(name.as_bytes())
            .map_err(|_| "Invalid configured model header name")?;
        let key = name.as_str();
        if key.starts_with("x-forwarded-")
            || key.starts_with("x-astrion-")
            || matches!(
                key,
                "accept"
                    | "accept-encoding"
                    | "authorization"
                    | "connection"
                    | "content-length"
                    | "content-type"
                    | "cookie"
                    | "expect"
                    | "forwarded"
                    | "host"
                    | "keep-alive"
                    | "proxy-authorization"
                    | "proxy-connection"
                    | "te"
                    | "trailer"
                    | "transfer-encoding"
                    | "upgrade"
            )
        {
            continue;
        }
        let value = value
            .as_str()
            .ok_or("Invalid configured model header value")?;
        let mut value =
            HeaderValue::from_str(value).map_err(|_| "Invalid configured model header value")?;
        value.set_sensitive(true);
        headers.insert(name, value);
    }
    Ok(headers)
}

pub(crate) fn apply_extra_params(body: &mut Value, values: &Map<String, Value>) {
    const PROTECTED: &[&str] = &[
        "api_key",
        "audio",
        "background",
        "base_url",
        "conversation",
        "endpoint",
        "headers",
        "include",
        "input",
        "instructions",
        "messages",
        "modalities",
        "model",
        "n",
        "previous_response_id",
        "store",
        "stream",
        "tool_choice",
        "tools",
        "url",
    ];
    let object = body
        .as_object_mut()
        .expect("model request body is an object");
    for (key, value) in values {
        if !PROTECTED
            .iter()
            .any(|protected| key.eq_ignore_ascii_case(protected))
        {
            object.insert(key.clone(), value.clone());
        }
    }
}

fn client() -> Result<&'static Client, String> {
    static CLIENT: OnceLock<Result<Client, String>> = OnceLock::new();
    CLIENT
        .get_or_init(|| {
            Client::builder()
                .connect_timeout(Duration::from_secs(300))
                .read_timeout(Duration::from_secs(300))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .map_err(|error| format!("model HTTP client initialization failed: {error}"))
        })
        .as_ref()
        .map_err(Clone::clone)
}

pub(crate) fn response_is_json(response: &Response) -> bool {
    response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(';').next())
        .is_some_and(|mime| {
            let mime = mime.trim().to_ascii_lowercase();
            mime == "application/json" || mime.ends_with("+json")
        })
}

pub(crate) async fn response_detail(mut response: Response) -> String {
    const MAX_ERROR_BYTES: usize = 4096;
    let mut body = Vec::new();
    while body.len() < MAX_ERROR_BYTES {
        match response.chunk().await {
            Ok(Some(chunk)) => {
                let remaining = MAX_ERROR_BYTES - body.len();
                body.extend_from_slice(&chunk[..chunk.len().min(remaining)]);
            }
            Ok(None) => break,
            Err(error) => return format!("could not read error body: {}", error.without_url()),
        }
    }
    error_detail(&String::from_utf8_lossy(&body))
}

pub(crate) fn error_detail(data: &str) -> String {
    match serde_json::from_str::<Value>(data) {
        Ok(value) => provider_error(&value),
        Err(_) if data.trim().is_empty() => "empty error body".into(),
        Err(_) => data.trim().to_owned(),
    }
}

fn provider_error(value: &Value) -> String {
    let error = value
        .get("error")
        .filter(|error| !error.is_null())
        .unwrap_or(value);
    error
        .get("message")
        .and_then(Value::as_str)
        .or_else(|| error.as_str())
        .map(str::to_owned)
        .unwrap_or_else(|| error.to_string())
}
