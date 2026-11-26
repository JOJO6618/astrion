//! Responses requests for a selected ChatGPT subscription connection.

use reqwest::StatusCode;
use serde_json::{Value, json};

use crate::{
    chat::{self, Completion},
    providers::CodexTransport,
    responses::{ResponsesRequest, build_responses_body},
};

/// Trusted properties of the selected model and the current session.
pub struct CodexRequestOptions<'a> {
    pub instructions: &'a str,
    pub reasoning_effort: Option<&'a str>,
    pub session_id: &'a str,
    pub fast: bool,
    pub max_output_tokens: Option<u64>,
    pub model_info: &'a Value,
}

/// Stream one Codex turn, scoping encrypted reasoning to the authorized connection.
pub async fn complete(
    transport: &CodexTransport,
    model: &str,
    messages: &[Value],
    tools: &[Value],
    options: &CodexRequestOptions<'_>,
    mut on_text: impl FnMut(&str),
) -> Result<Completion, String> {
    complete_with_updates(transport, model, messages, tools, options, |delta| {
        if let super::StreamDelta::Text(text) = delta { on_text(text); }
    }).await
}

pub(crate) async fn complete_with_updates(
    transport: &CodexTransport, model: &str, messages: &[Value], tools: &[Value], options: &CodexRequestOptions<'_>,
    on_delta: impl FnMut(super::StreamDelta<'_>),
) -> Result<Completion, String> {
    if options.session_id.trim().is_empty() {
        return Err("Codex Responses requires a session ID".into());
    }
    let authorization = transport.authorize().await?;
    let connection = authorization.fingerprint().to_owned();
    let history = super::responses_history(messages, &connection);
    let body = request_body(model, &history, tools, options);
    let response = transport
        .post_responses(authorization, options.session_id, &body)
        .await?;
    let status = response.status();
    if status != StatusCode::OK {
        let detail = chat::response_detail(response).await;
        if status == StatusCode::TOO_MANY_REQUESTS {
            return Err(format!(
                "Codex Responses HTTP {status} (subscription usage limit): {detail}"
            ));
        }
        return Err(format!("Codex Responses HTTP {status}: {detail}"));
    }
    if chat::response_is_json(&response) {
        return Err(format!(
            "Codex Responses expected an SSE stream (HTTP {status}): {}",
            chat::response_detail(response).await
        ));
    }
    super::collect_responses(response, tools, connection, on_delta).await
}

fn request_body(
    model: &str,
    messages: &[Value],
    tools: &[Value],
    options: &CodexRequestOptions<'_>,
) -> Value {
    let mut request = ResponsesRequest::new(model, messages);
    request.tools = tools;
    request.instructions = options.instructions;
    request.effort = Some(resolve_effort(options));
    request.max_output_tokens = options
        .max_output_tokens
        .and_then(|limit| i64::try_from(limit).ok());
    request.session_id = Some(options.session_id);
    request.use_developer_prompt = true;
    let mut body = build_responses_body(request);
    if options.fast && supports_fast_mode(options.model_info) {
        body["service_tier"] = json!("priority");
    }
    body
}

fn resolve_effort<'a>(options: &CodexRequestOptions<'a>) -> &'a str {
    let mut supported = values(&options.model_info["supported_reasoning_levels"])
        .iter()
        .filter_map(|level| level["effort"].as_str())
        .filter(|level| !level.is_empty())
        .peekable();
    if let Some(requested) = options
        .reasoning_effort
        .map(str::trim)
        .filter(|v| !v.is_empty())
        && (supported.peek().is_none() || supported.any(|level| level == requested))
    {
        return requested;
    }
    options.model_info["default_reasoning_level"]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("medium")
}

/// Check both model discovery fields used to advertise Fast mode.
pub fn supports_fast_mode(info: &Value) -> bool {
    values(&info["service_tiers"])
        .iter()
        .any(|tier| tier["id"] == "priority")
        || values(&info["additional_speed_tiers"])
            .iter()
            .any(|tier| tier == "fast")
}

fn values(value: &Value) -> &[Value] {
    value.as_array().map_or(&[], Vec::as_slice)
}

#[cfg(test)]
#[path = "../../../test/2026-11-25_RustCodexResponses/codex_http.rs"]
mod tests;
