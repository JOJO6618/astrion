use std::{borrow::Cow, fmt::Write};

use futures_util::StreamExt;
use reqwest::{Url, header::HeaderMap};
use ring::digest::{Context, SHA256};
use serde_json::{Value, json};

use crate::chat::{self, Completion, assistant::Assistant, sse::Decoder};
use crate::responses::{
    ResponsesApiError, ResponsesProfile, ResponsesRequest, ResponsesSseParser,
    build_responses_body, should_use_developer_prompt, tool_required_map,
};

mod budget;
pub(crate) mod retry;
pub mod codex;

pub use crate::chat::RequestOptions;
pub(crate) use crate::chat::StreamDelta;
pub(crate) use budget::RoundBudget;

const REASONING_ITEMS: &str = "responses_reasoning_items";
const REASONING_CONNECTION: &str = "_responses_connection";

/// Streams a model turn using the protocol indicated by the administrator's endpoint.
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

/// Stream a model turn using the selected connection's private headers and preferences.
pub async fn complete_with_options(
    endpoint: &str,
    api_key: Option<&str>,
    model: &str,
    messages: &[Value],
    tools: &[Value],
    options: &RequestOptions,
    mut on_text: impl FnMut(&str),
) -> Result<Completion, String> {
    complete_with_updates(endpoint, api_key, model, messages, tools, options, |delta| {
        if let StreamDelta::Text(text) = delta { on_text(text); }
    }).await
}

pub(crate) async fn complete_with_updates(
    endpoint: &str, api_key: Option<&str>, model: &str, messages: &[Value], tools: &[Value],
    options: &RequestOptions, on_delta: impl FnMut(StreamDelta<'_>),
) -> Result<Completion, String> {
    if let Ok(mut url) = Url::parse(endpoint)
        && url.path().trim_end_matches('/').rsplit('/').next() == Some("responses")
    {
        url.set_fragment(None);
        return complete_responses(&url, api_key, model, messages, tools, options, on_delta).await;
    }
    let history = chat_history(messages);
    chat::complete_with_updates(endpoint, api_key, model, &history, tools, options, on_delta).await
}

async fn complete_responses(
    endpoint: &Url,
    api_key: Option<&str>,
    model: &str,
    messages: &[Value],
    tools: &[Value],
    options: &RequestOptions,
    on_delta: impl FnMut(StreamDelta<'_>),
) -> Result<Completion, String> {
    let headers = chat::request_headers(&options.headers)?;
    let connection = responses_connection(endpoint, api_key, &headers);
    let history = responses_history(messages, &connection);
    let mut request = ResponsesRequest::new(model, &history);
    request.tools = tools;
    request.max_output_tokens = options
        .max_output_tokens
        .and_then(|limit| i64::try_from(limit).ok());
    request.use_developer_prompt = should_use_developer_prompt(&ResponsesProfile {
        model_id: Some(model),
        base_url: Some(endpoint.as_str()),
        ..Default::default()
    });
    let mut body = build_responses_body(request);
    chat::apply_extra_params(&mut body, &options.extra_params);
    let response = chat::post_sse(endpoint.as_str(), api_key, &body, "Responses", headers).await?;
    collect_responses(response, tools, connection, on_delta).await
}

async fn collect_responses(
    response: reqwest::Response,
    tools: &[Value],
    connection: String,
    mut on_delta: impl FnMut(StreamDelta<'_>),
) -> Result<Completion, String> {
    let mut stream = response.bytes_stream();
    let mut decoder = Decoder::default();
    let mut parser = ResponsesSseParser::new(tool_required_map(tools));
    let mut assistant = Assistant::default();
    let mut responses_duration_ms = None;
    let mut on_event = |event: &str, data: &str| {
        let chunks = parser.push_data(data).map_err(responses_error)?;
        if event == "error" {
            return Err(format!(
                "Responses stream error: {}",
                chat::error_detail(data)
            ));
        }
        for chunk in chunks {
            if let Some(duration) = chunk["_response_output_timing"]["duration_ms"].as_u64() {
                responses_duration_ms = Some(duration);
            }
            let summary = chunk["_reasoning_summary"].as_str().map(str::to_owned);
            assistant.push_value(chunk, &mut on_delta)?;
            if let Some(summary) = summary { on_delta(StreamDelta::NativeSummary(&summary)); }
        }
        Ok(parser.is_completed())
    };

    loop {
        let Some(chunk) = stream.next().await else {
            decoder.finish(&mut on_event)?;
            break;
        };
        let chunk = chunk
            .map_err(|error| format!("Responses stream read failed: {}", error.without_url()))?;
        if decoder.push(&chunk, &mut on_event)? {
            break;
        }
    }
    parser.finish().map_err(responses_error)?;

    let mut completion = assistant.finish()?;
    completion.responses_duration_ms = responses_duration_ms;
    if !parser.reasoning_items().is_empty() || completion.message["reasoning_content"].is_string() {
        completion.message[REASONING_CONNECTION] = Value::String(connection);
    }
    if !parser.reasoning_items().is_empty() {
        completion.message[REASONING_ITEMS] = json!(parser.reasoning_items());
    }
    Ok(completion)
}

fn responses_error(error: ResponsesApiError) -> String {
    match &error.code {
        Some(code) => format!("Responses stream error ({code}): {error}"),
        None => format!("Responses stream error: {error}"),
    }
}

fn responses_connection(url: &Url, api_key: Option<&str>, headers: &HeaderMap) -> String {
    // Configured headers may identify a tenant or carry a provider credential.
    // Model names can change on the same connection.
    let mut context = Context::new(&SHA256);
    context.update(b"astrion-responses-connection-v1\0");
    context.update(url.as_str().as_bytes());
    context.update(b"\0");
    context.update(api_key.unwrap_or_default().as_bytes());
    if !headers.is_empty() {
        context.update(b"\0provider-headers\0");
        let mut sorted: Vec<_> = headers.iter().collect();
        sorted.sort_unstable_by_key(|(name, _)| name.as_str());
        for (name, value) in sorted {
            let name = name.as_str();
            let value = value.as_bytes();
            context.update(&(name.len() as u64).to_le_bytes());
            context.update(name.as_bytes());
            context.update(&(value.len() as u64).to_le_bytes());
            context.update(value);
        }
    }
    let digest = context.finish();
    let mut fingerprint = String::with_capacity(2 * digest.as_ref().len());
    for byte in digest.as_ref() {
        write!(&mut fingerprint, "{byte:02x}").expect("writing to a String cannot fail");
    }
    fingerprint
}

fn responses_history<'a>(messages: &'a [Value], connection: &str) -> Cow<'a, [Value]> {
    let mismatched = |message: &Value| {
        message.get(REASONING_ITEMS).is_some()
            && message[REASONING_CONNECTION].as_str() != Some(connection)
    };
    if !messages.iter().any(mismatched) {
        return Cow::Borrowed(messages);
    }
    let mut history = messages.to_vec();
    for message in history.iter_mut().filter(|message| mismatched(message)) {
        if let Some(fields) = message.as_object_mut() {
            fields.shift_remove(REASONING_ITEMS);
        }
    }
    Cow::Owned(history)
}

fn chat_history(messages: &[Value]) -> Cow<'_, [Value]> {
    let has_responses_reasoning = |message: &Value| {
        message.get(REASONING_CONNECTION).is_some() || message.get(REASONING_ITEMS).is_some()
    };
    if !messages.iter().any(has_responses_reasoning) {
        return Cow::Borrowed(messages);
    }
    let mut history = messages.to_vec();
    for message in history
        .iter_mut()
        .filter(|message| has_responses_reasoning(message))
    {
        if let Some(fields) = message.as_object_mut() {
            fields.shift_remove("reasoning_content");
        }
        strip_responses_metadata(message);
    }
    Cow::Owned(history)
}

/// Projects an internal conversation message for display without opaque provider state.
pub fn public_history_message(raw: &Value) -> Value {
    let mut message = raw.clone();
    strip_responses_metadata(&mut message);
    if let Some(fields) = message.as_object_mut() {
        fields.shift_remove("_frozen_system_prompt");
    }
    message
}

fn strip_responses_metadata(message: &mut Value) {
    if let Some(fields) = message.as_object_mut() {
        fields.shift_remove(REASONING_CONNECTION);
        fields.shift_remove(REASONING_ITEMS);
    }
}

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/model_http.rs"]
mod tests;
