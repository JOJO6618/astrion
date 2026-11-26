//! Host browser settings for child agents and the shared auxiliary model picker.

use std::{io, sync::Arc};

use axum::{
    Extension, Json, Router,
    extract::{RawQuery, State, rejection::JsonRejection},
    http::{HeaderMap, Method, StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use serde::Serialize;
use serde_json::{Value, json};

use crate::{
    catalog::CatalogSource, web_assets::personalization::Personalization, web_auth::BrowserSession,
    workspace_catalog::HostCatalog,
};

const THRESHOLD: &str = "sub_agent_compress_threshold_tokens";
const CAPACITY: &str = "sub_agent_max_instances";
const DEFAULT_THRESHOLD: i64 = 250_000;
const MIN_THRESHOLD: i64 = 10_000;
const MAX_THRESHOLD: i64 = 10_000_000;
const DEFAULT_CAPACITY: i64 = 20;
const MAX_CAPACITY: i64 = 9_007_199_254_740_991;
const WORKSPACE_HEADER: &str = "x-astrion-workspace-id";

type LoadWorkspaces = dyn Fn() -> io::Result<HostCatalog> + Send + Sync;

#[derive(Clone)]
struct SettingsState {
    preferences: Personalization,
    workspaces: Arc<LoadWorkspaces>,
}

/// Mount inside the browser session guard. The model picker is account-wide.
pub fn models_router(catalog: CatalogSource) -> Router {
    Router::new()
        .route("/api/sub_agents/models", get(models))
        .with_state(catalog)
}

/// Mount inside the browser session guard, which supplies the verified workspace selection.
pub fn settings_router(
    preferences: Personalization,
    workspaces: impl Fn() -> io::Result<HostCatalog> + Send + Sync + 'static,
) -> Router {
    Router::new()
        .route("/api/sub_agents/settings", get(settings).put(settings))
        .with_state(SettingsState {
            preferences,
            workspaces: Arc::new(workspaces),
        })
}

async fn models(State(source): State<CatalogSource>) -> Response {
    match tokio::task::spawn_blocking(move || {
        let catalog = source.load().models_response();
        let models: Vec<_> = catalog["items"]
            .as_array()
            .into_iter()
            .flatten()
            .map(aux_model)
            .collect();
        reply(StatusCode::OK, json!({"success": true, "models": models}))
    })
    .await
    {
        Ok(response) => response,
        Err(error) => unavailable(error),
    }
}

fn aux_model(model: &Value) -> Value {
    let provider_type = model["provider_type"].as_str().unwrap_or_default();
    // The common catalog supplies a default protocol for legacy custom models.
    // The auxiliary picker exposes their optional provider metadata as stored.
    let api_protocol = if provider_type.is_empty() {
        &Value::Null
    } else {
        &model["api_protocol"]
    };
    json!({
        "key": model["model_key"],
        "name": model["name"],
        "description": model["description"],
        "provider_type": provider_type,
        "provider_id": model["provider_id"],
        "provider_name": model["provider_name"],
        "api_protocol": api_protocol,
        "supports_thinking": model["supports_thinking"],
        "fast_only": model["fast_only"],
        "thinking_only": model["thinking_only"],
        "multimodal": model["multimodal"],
        "context_window": model["context_window"],
        "max_output": model["max_output_tokens"],
        "supports_reasoning_effort": model["supports_reasoning_effort"],
        "supported_reasoning_levels": model["supported_reasoning_levels"].as_array().map(Vec::as_slice).unwrap_or_default(),
        "default_reasoning_level": model["default_reasoning_level"],
    })
}

async fn settings(
    State(state): State<SettingsState>,
    Extension(browser): Extension<BrowserSession>,
    RawQuery(query): RawQuery,
    headers: HeaderMap,
    method: Method,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    let body = body.ok().map(|Json(body)| body).unwrap_or_default();
    let requested = requested_workspace(query.as_deref(), &headers, &body);
    match tokio::task::spawn_blocking(move || {
        settings_response(&state, &browser, requested.as_deref(), method, body)
    })
    .await
    {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => unavailable(error),
        Err(error) => unavailable(error),
    }
}

fn requested_workspace(query: Option<&str>, headers: &HeaderMap, body: &Value) -> Option<String> {
    let query = query
        .and_then(|query| serde_urlencoded::from_str::<Vec<(String, String)>>(query).ok())
        .and_then(|pairs| pairs.into_iter().find(|(key, _)| key == "workspace_id"))
        .map(|(_, value)| value)
        .filter(|value| !value.is_empty());
    let header = headers
        .get(WORKSPACE_HEADER)
        .and_then(|value| std::str::from_utf8(value.as_bytes()).ok())
        .filter(|value| !value.is_empty());
    let body_id = &body["workspace_id"];
    query
        .or_else(|| header.map(str::to_owned))
        .or_else(|| match body_id {
            Value::String(value) => Some(value.clone()),
            Value::Bool(true) => Some("True".into()),
            Value::Number(value) if value.as_f64() != Some(0.0) => Some(value.to_string()),
            Value::Array(values) if !values.is_empty() => Some(body_id.to_string()),
            Value::Object(values) if !values.is_empty() => Some(body_id.to_string()),
            _ => None,
        })
        .map(|selected| selected.trim().to_owned())
        .filter(|selected| !selected.is_empty())
}

fn settings_response(
    state: &SettingsState,
    browser: &BrowserSession,
    requested: Option<&str>,
    method: Method,
    body: Value,
) -> io::Result<Response> {
    let workspaces = (state.workspaces)()?;
    // Python resolves a registered workspace before touching user-level preferences.
    let selected = browser.selected_workspace_id();
    let workspace = requested
        .or(selected.as_deref())
        .and_then(|id| workspaces.by_id(id))
        .or_else(|| workspaces.default_workspace());
    let preferences = &state.preferences;
    if workspace.is_none() || requested.is_some_and(|id| workspace.is_some_and(|ws| id != ws.id)) {
        let english = preferences
            .chat_preferences()
            .is_ok_and(|prefs| prefs["ui_locale"] == "en-US");
        if workspace.is_none() {
            let message = if english {
                "No workspace has been created yet. Please create one manually in the workspace management UI."
            } else {
                "尚未创建任何工作区，请先在工作区管理界面中手动创建工作区"
            };
            return Ok(reply(
                StatusCode::OK,
                json!({
                    "success": false, "error": message, "code": "no_workspace",
                }),
            ));
        }
        let message = if english {
            "Workspace not found"
        } else {
            "工作区不存在"
        };
        return Ok(failure(StatusCode::NOT_FOUND, message));
    }

    let prefs = if method == Method::PUT {
        let patch = match settings_patch(&body["settings"]) {
            Ok(patch) => patch,
            Err(error) => return Ok(failure(StatusCode::BAD_REQUEST, error)),
        };
        // The raw account transaction shares the personalization and Chat update lock.
        // It merges the two owned keys into the current document, preserving other settings.
        preferences.chat_patch(&patch)?
    } else {
        preferences.chat_preferences()?
    };
    Ok(reply(
        StatusCode::OK,
        json!({"success": true, "settings": Settings::from(&prefs)}),
    ))
}

#[derive(Serialize)]
struct Settings {
    sub_agent_compress_threshold_tokens: i64,
    sub_agent_max_instances: i64,
}

impl From<&Value> for Settings {
    fn from(prefs: &Value) -> Self {
        let threshold = match &prefs[THRESHOLD] {
            Value::String(value) => decimal(value.trim()),
            Value::Number(number) => {
                integer(&prefs[THRESHOLD]).or_else(|| number.as_f64().map(|value| value as i64))
            }
            _ => None,
        };
        Self {
            sub_agent_compress_threshold_tokens: threshold
                .unwrap_or(DEFAULT_THRESHOLD)
                .clamp(MIN_THRESHOLD, MAX_THRESHOLD),
            sub_agent_max_instances: capacity(&prefs[CAPACITY]).unwrap_or(DEFAULT_CAPACITY),
        }
    }
}

fn settings_patch(settings: &Value) -> Result<serde_json::Map<String, Value>, &'static str> {
    let fields = settings.as_object().ok_or("settings必须为对象。")?;
    let threshold = fields
        .get(THRESHOLD)
        .and_then(integer)
        .filter(|value| *value >= MIN_THRESHOLD)
        .ok_or("子智能体压缩阈值必须是不小于10000的整数。")?;
    let max_instances = fields
        .get(CAPACITY)
        .and_then(capacity)
        .ok_or("允许同时存在的子智能体数量必须是大于等于1的有效整数。")?;
    Ok(serde_json::Map::from_iter([
        (THRESHOLD.into(), json!(threshold.min(MAX_THRESHOLD))),
        (CAPACITY.into(), json!(max_instances)),
    ]))
}

fn capacity(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .filter(|value| (1..=MAX_CAPACITY).contains(value))
}

fn integer(value: &Value) -> Option<i64> {
    let number = value.as_number()?;
    number.as_i64().or_else(|| decimal(&number.to_string()))
}

fn decimal(text: &str) -> Option<i64> {
    let sign = text.starts_with('-');
    let digits = text.strip_prefix(['-', '+']).unwrap_or(text);
    if !digits.is_empty()
        && digits
            .split('_')
            .all(|group| !group.is_empty() && group.bytes().all(|digit| digit.is_ascii_digit()))
    {
        let value = text.replace('_', "");
        Some(
            value
                .parse::<i64>()
                .unwrap_or(if sign { i64::MIN } else { i64::MAX }),
        )
    } else {
        None
    }
}

fn unavailable(error: impl std::fmt::Display) -> Response {
    eprintln!("Host sub-agent settings: {error}");
    failure(
        StatusCode::SERVICE_UNAVAILABLE,
        "Sub-agent settings are unavailable.",
    )
}

fn failure(status: StatusCode, error: &str) -> Response {
    reply(status, json!({"success": false, "error": error}))
}

fn reply(status: StatusCode, value: Value) -> Response {
    (status, [(header::CACHE_CONTROL, "no-store")], Json(value)).into_response()
}

#[cfg(test)]
#[path = "../../test/2026-11-25_Rust子智能体设置/settings_contract.rs"]
mod tests;
