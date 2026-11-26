//! Main-agent token counters and the complete request ledger for a conversation.

use std::path::Path;

use axum::{
    Extension, Json, Router,
    extract::{Path as RoutePath, State},
    http::StatusCode,
    routing::get,
};
use serde_json::{Map, Number, Value, json};
use time::OffsetDateTime;

use crate::{
    usage::{ExchangeRateRefresh, Ledger, SubAgent},
    workspace_catalog::RegisteredWorkspace,
};

use super::{
    Runtime, RuntimeError,
    goals::{verify_conversation_root, verify_goal_identity},
    verify_workspace_binding,
};

pub(super) const TOKEN_COUNTERS: [&str; 6] = [
    "total_input_tokens",
    "total_output_tokens",
    "total_tokens",
    "total_cached_input_tokens",
    "cache_exempt_input_tokens",
    "current_context_tokens",
];

#[derive(Clone)]
struct Statistics {
    runtime: Runtime,
    exchange: ExchangeRateRefresh,
}

/// Mount inside the authenticated Web router after resolving the workspace.
pub fn router(runtime: Runtime, data_dir: &Path) -> Router {
    Router::new()
        .route("/api/conversations/{id}/token-statistics", get(read))
        .with_state(Statistics {
            runtime,
            exchange: ExchangeRateRefresh::new(data_dir),
        })
}

async fn read(
    State(statistics): State<Statistics>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    RoutePath(id): RoutePath<String>,
) -> (StatusCode, Json<Value>) {
    let raw_id = id.trim();
    let id = if raw_id.is_empty() || raw_id.starts_with("conv_") {
        raw_id.to_owned()
    } else {
        format!("conv_{raw_id}")
    };
    let requested_id = id.clone();
    let service = statistics.clone();
    match tokio::task::spawn_blocking(move || service.read(&workspace, &requested_id)).await {
        Ok(Ok(data)) => {
            statistics
                .exchange
                .refresh_if_stale(&data["costs"]["exchange_rate"]);
            (StatusCode::OK, Json(json!({"success": true, "data": data})))
        }
        Ok(Err(error)) if error.status == 404 => (
            StatusCode::NOT_FOUND,
            Json(json!({
                "success": false, "error": "Conversation not found",
                "message": format!("对话 {id} 不存在"),
            })),
        ),
        Ok(Err(error)) => (
            StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            Json(
                json!({"success": false, "error": error.message, "message": "获取token统计时发生异常"}),
            ),
        ),
        Err(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(
                json!({"success": false, "error": "Runtime operation interrupted", "message": "获取token统计时发生异常"}),
            ),
        ),
    }
}

impl Statistics {
    fn read(&self, workspace: &RegisteredWorkspace, id: &str) -> Result<Value, RuntimeError> {
        let (data_dir, mut stats) = {
            let store = self.runtime.store()?;
            verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
            let document = store.read(&workspace.id, id).map_err(|error| {
                if error.status == 400 {
                    RuntimeError::session_not_found()
                } else {
                    error
                }
            })?;
            verify_conversation_root(&workspace.path, document.raw())
                .map_err(|_| RuntimeError::session_not_found())?;
            // Host workspaces share costs by conversation ID. An ambiguous ID
            // cannot identify one workspace's ledger, even if its document exists.
            verify_goal_identity(&store, &workspace.id, id)?;
            let stats = validated_token_statistics(document.raw().get("token_statistics"))
                .ok_or_else(RuntimeError::session_not_found)?;
            (store.data_dir.clone(), stats)
        };
        let children = crate::sub_agents::historical_registry(&data_dir)
            .map_err(|error| RuntimeError::new(500, error.to_string()))?;
        let children = historical_sub_agents(&children, id, &workspace.path)?;
        let input = &stats["total_input_tokens"];
        // The ledger only needs to know whether there was positive historical input.
        let main_input = input
            .as_u64()
            .unwrap_or_else(|| u64::from(!input.to_string().starts_with('-')));
        let mut costs = Ledger::new(&data_dir)
            .summarize(id, main_input, &children)
            .map_err(|error| RuntimeError::new(500, error.to_string()))?;
        costs["exchange_rate"] = self.exchange.cached();
        stats["costs"] = costs;
        stats["conversation_id"] = json!(id);
        Ok(stats)
    }
}

/// Project Python's token validator onto the detached counters returned by REST.
/// `None` means the statistics field was present but was not an object.
pub(super) fn validated_token_statistics(raw: Option<&Value>) -> Option<Value> {
    let empty = Map::new();
    let raw = match raw {
        None => &empty,
        Some(value) => value.as_object()?,
    };
    let counters = TOKEN_COUNTERS
        .into_iter()
        .map(|name| {
            let value = match raw.get(name) {
                None => json!(0),
                Some(value) => integer_value(value)?,
            };
            Some((name.into(), value))
        })
        .collect::<Option<Map<String, Value>>>();
    let (mut stats, updated_at) = match counters {
        Some(stats) => (
            stats,
            raw.get("updated_at").cloned().unwrap_or_else(current_time),
        ),
        None => (
            TOKEN_COUNTERS
                .into_iter()
                .map(|name| (name.into(), json!(0)))
                .collect(),
            current_time(),
        ),
    };
    stats.insert("updated_at".into(), updated_at);
    Some(Value::Object(stats))
}

pub(super) fn integer_value(value: &Value) -> Option<Value> {
    match value {
        Value::Bool(value) => Some(json!(u8::from(*value))),
        Value::Number(number) => {
            if !number.to_string().contains(['.', 'e', 'E']) {
                return Some(value.clone());
            }
            let number = number.as_f64()?.trunc();
            if !number.is_finite() {
                return None;
            }
            if number == 0.0 {
                return Some(json!(0));
            }
            serde_json::from_str::<Number>(&format!("{number:.0}"))
                .ok()
                .map(Value::Number)
        }
        Value::String(value) => {
            let value = value.trim();
            let (negative, digits) = match value.strip_prefix(['-', '+']) {
                Some(digits) => (value.starts_with('-'), digits),
                None => (false, value),
            };
            if !digits
                .split('_')
                .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
            {
                return None;
            }
            let digits = digits.replace('_', "");
            let digits = digits.trim_start_matches('0');
            if digits.is_empty() {
                return Some(json!(0));
            }
            let sign = if negative { "-" } else { "" };
            serde_json::from_str::<Number>(&format!("{sign}{digits}"))
                .ok()
                .map(Value::Number)
        }
        _ => None,
    }
}

fn current_time() -> Value {
    let now = OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc());
    json!(format!(
        "{}T{:02}:{:02}:{:02}.{:06}",
        now.date(),
        now.hour(),
        now.minute(),
        now.second(),
        now.microsecond()
    ))
}

fn historical_sub_agents<'a>(
    document: &'a Value,
    id: &str,
    workspace: &Path,
) -> Result<Vec<SubAgent<'a>>, RuntimeError> {
    let invalid = || RuntimeError::new(500, "Invalid sub-agent storage");
    if document.is_null() {
        return Ok(Vec::new());
    }
    if !document.is_object() {
        return Err(invalid());
    }
    let tasks = &document["tasks"];
    if !truthy(tasks) {
        return Ok(Vec::new());
    }
    let mut children = Vec::new();
    for task in tasks.as_object().ok_or_else(invalid)?.values() {
        if task["conversation_id"] != id {
            continue;
        }
        let Some(task_id) = task["task_id"].as_str().filter(|id| !id.is_empty()) else {
            continue;
        };
        if let Some(root) = task["workspace_root"]
            .as_str()
            .filter(|root| !root.is_empty())
        {
            let root = Path::new(root);
            if root != workspace && root.canonicalize().ok().as_deref() != Some(workspace) {
                continue;
            }
        }
        children.push(SubAgent {
            task_id,
            display_name: task["display_name"].as_str(),
            summary: task["summary"].as_str(),
            cost_tracking_version: truthy(&task["cost_tracking_version"]),
        });
    }
    Ok(children)
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

#[cfg(test)]
#[path = "../../../test/2026-11-25_Rust单会话用量统计/conversation_token_statistics.rs"]
mod tests;
