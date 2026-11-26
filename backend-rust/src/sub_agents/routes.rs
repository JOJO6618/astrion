use std::{fmt, io};

use axum::{
    Extension, Json, Router,
    extract::{Path, Query, State, rejection::{JsonRejection, QueryRejection}},
    http::{HeaderMap, StatusCode, header::ACCEPT_LANGUAGE},
    routing::{get, post},
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::{web_auth::BrowserSession, workspace_catalog::RegisteredWorkspace};

use super::{
    Record, SubAgents,
    activity::ActivityPage,
    normalize_conversation, string, valid_task_id,
};

#[derive(Deserialize)]
struct ScopeQuery {
    conversation_id: Option<String>,
}

#[derive(Deserialize)]
struct ActivityQuery {
    conversation_id: Option<String>,
    offset: Option<String>,
    before: Option<String>,
    limit: Option<String>,
}

#[derive(Serialize)]
struct Activity {
    task_id: String,
    status: Value,
    #[serde(flatten)]
    page: ActivityPage,
}

struct PageQuery {
    offset: Option<u64>,
    before: Option<u64>,
    limit: usize,
}

impl ActivityQuery {
    fn page(&self) -> Result<PageQuery, RouteError> {
        let offset = cursor(self.offset.as_deref(), "invalid offset")?;
        let before = cursor(self.before.as_deref(), "invalid before")?;
        if offset.is_some() && before.is_some() {
            return Err(RouteError::bad_request(
                "offset and before are mutually exclusive",
            ));
        }
        let (default, maximum) = if offset.is_some() || before.is_some() {
            (200, 500)
        } else {
            (100_000, 100_000)
        };
        let limit = self
            .limit
            .as_deref()
            .and_then(|value| value.trim().parse::<i64>().ok())
            .map_or(default, |limit| limit.clamp(1, maximum) as usize);
        Ok(PageQuery {
            offset,
            before,
            limit,
        })
    }
}

fn cursor(raw: Option<&str>, error: &'static str) -> Result<Option<u64>, RouteError> {
    raw.filter(|value| !value.is_empty())
        .map(|value| {
            value
                .trim()
                .parse::<u64>()
                .map_err(|_| RouteError::bad_request(error))
        })
        .transpose()
}

impl SubAgents {
    /// Mount once behind the shared Browser/Bearer authentication. The parent
    /// injects a current `RegisteredWorkspace` after resolving the scope.
    pub fn list_router(&self) -> Router {
        Router::new()
            .route("/api/sub_agents", get(list))
            .with_state(self.clone())
    }

    /// Mount behind `WebAuth::protect_api` and the same workspace resolver.
    pub fn browser_router(&self) -> Router {
        Router::new()
            .route("/api/sub_agents/{task_id}/activity", get(activity))
            .route("/api/sub_agents/{task_id}/stop", post(stop))
            .route("/api/sub_agents/{task_id}/terminate", post(terminate))
            .route("/api/sub_agents/stop_all", post(stop_all))
            .with_state(self.clone())
    }
}

async fn list(
    State(app): State<SubAgents>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    query: Result<Query<ScopeQuery>, QueryRejection>,
    headers: HeaderMap,
) -> Reply {
    respond(move || {
        let Extension(workspace) = workspace.ok_or_else(RouteError::workspace_missing)?;
        let Query(query) = query.map_err(|_| RouteError::bad_request("Invalid sub-agent query"))?;
        let conversation = query
            .conversation_id
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty());
        let Some(conversation) = conversation else {
            app.check_workspace(&workspace)?;
            return Ok(json!({"success": true, "data": []}));
        };
        let parent = normalize_conversation(conversation).ok_or_else(RouteError::parent_missing)?;
        let rows = app.records(&workspace, &parent)?;
        let english = headers
            .get(ACCEPT_LANGUAGE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| {
                value
                    .get(..2)
                    .is_some_and(|tag| tag.eq_ignore_ascii_case("en"))
            });
        let mut rows: Vec<_> = rows.into_iter().collect();
        rows.sort_by(|(left_id, left), (right_id, right)| {
            created_at(right)
                .total_cmp(&created_at(left))
                .then_with(|| left_id.cmp(right_id))
        });
        let rows: Vec<_> = rows
            .iter()
            .map(|(id, record)| project(&app, id, &parent, record, english))
            .collect();
        Ok(json!({"success": true, "data": rows}))
    })
    .await
}

async fn activity(
    State(app): State<SubAgents>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    session: Option<Extension<BrowserSession>>,
    Path(task_id): Path<String>,
    query: Result<Query<ActivityQuery>, QueryRejection>,
) -> Reply {
    if session.is_none() {
        return RouteError::unauthorized().reply();
    }
    respond(move || {
        let Extension(workspace) = workspace.ok_or_else(RouteError::workspace_missing)?;
        let Query(query) = query.map_err(|_| RouteError::bad_request("Invalid sub-agent query"))?;
        let parent = query
            .conversation_id
            .as_deref()
            .and_then(normalize_conversation)
            .ok_or_else(RouteError::parent_missing)?;
        if !valid_task_id(&task_id) {
            return Err(RouteError::task_missing());
        }
        let PageQuery {
            offset,
            before,
            limit,
        } = query.page()?;
        let records = app.records(&workspace, &parent)?;
        let record = records.get(&task_id).ok_or_else(RouteError::task_missing)?;
        let page = app.registry.activity(&task_id, offset, before, limit)
            .map_err(RouteError::storage)?;
        let status = field(record, "status").clone();
        Ok(json!({"success": true, "data": Activity { task_id, status, page }}))
    })
    .await
}

async fn stop(
    State(app): State<SubAgents>, workspace: Option<Extension<RegisteredWorkspace>>,
    session: Option<Extension<BrowserSession>>, Path(id): Path<String>, Query(query): Query<ScopeQuery>,
) -> Reply {
    mutate(app, workspace, session, query, move |app, parent| {
        if !valid_task_id(&id) { return Err(RouteError::task_missing()); }
        app.stop_task(parent, &id, false).map_err(RouteError::operation)
    }).await
}

async fn terminate(
    State(app): State<SubAgents>, workspace: Option<Extension<RegisteredWorkspace>>,
    session: Option<Extension<BrowserSession>>, Path(id): Path<String>, Query(query): Query<ScopeQuery>,
) -> Reply {
    mutate(app, workspace, session, query, move |app, parent| {
        if !valid_task_id(&id) { return Err(RouteError::task_missing()); }
        app.stop_task(parent, &id, true).map_err(RouteError::operation)
    }).await
}

async fn stop_all(
    State(app): State<SubAgents>, workspace: Option<Extension<RegisteredWorkspace>>,
    session: Option<Extension<BrowserSession>>, Query(query): Query<ScopeQuery>,
    body: Result<Json<Value>, JsonRejection>,
) -> Reply {
    let body = body.map(|Json(body)| body).unwrap_or_default();
    let mode = body["mode"].as_str().map(str::trim).filter(|value| !value.is_empty()).unwrap_or("soft_stop").to_lowercase();
    if !matches!(mode.as_str(), "soft_stop" | "terminate") {
        return RouteError::bad_request("Unsupported sub-agent stop mode").reply();
    }
    mutate(app, workspace, session, query, move |app, parent| {
        let count = app.stop_all(parent, mode == "terminate").map_err(RouteError::operation)?;
        Ok(json!({"stopped_count":count,"mode":mode}))
    }).await
}

async fn mutate(app: SubAgents, workspace: Option<Extension<RegisteredWorkspace>>, session: Option<Extension<BrowserSession>>,
    query: ScopeQuery, action: impl FnOnce(&SubAgents, &super::Parent) -> Result<Value, RouteError> + Send + 'static,
) -> Reply {
    if session.is_none() { return RouteError::unauthorized().reply(); }
    respond(move || {
        let Extension(workspace) = workspace.ok_or_else(RouteError::workspace_missing)?;
        let parent = app.verified_parent(&workspace, query.conversation_id.as_deref().unwrap_or_default())?;
        let result = action(&app, &parent)?;
        Ok(json!({"success":true,"data":result}))
    }).await
}

fn field<'a>(record: &'a Record, key: &str) -> &'a Value {
    record.get(key).unwrap_or(&Value::Null)
}

fn created_at(record: &Record) -> f64 {
    field(record, "created_at")
        .as_f64()
        .or_else(|| string(record, "created_at").and_then(|time| time.parse().ok()))
        .filter(|time: &f64| time.is_finite())
        .unwrap_or_default()
}

fn project(app: &SubAgents, id: &str, parent: &str, record: &Record, english: bool) -> Value {
    let fields = [
        "status",
        "summary",
        "created_at",
        "updated_at",
        "model_key",
        "intelligence",
        "access_level",
        "round_id",
        "end_reason",
        "final_result",
        "name",
        "working_dir",
        "last_tool",
    ];
    let mut row: Map<String, Value> = fields
        .into_iter()
        .map(|key| (key.into(), field(record, key).clone()))
        .collect();
    row.insert("task_id".into(), json!(id));
    row.insert("conversation_id".into(), json!(parent));
    row.insert("display_name".into(), field(record, "name").clone());
    let stats = app
        .registry
        .stats(id)
        .ok()
        .flatten()
        .filter(Value::is_object);
    row.insert(
        "current_context_tokens".into(),
        json!(
            stats
                .as_ref()
                .map_or(0, |stats| count(&stats["current_context_tokens"]))
        ),
    );
    if let Some(stats) = stats {
        row.insert(
            "stats_summary".into(),
            json!(stats_summary(&stats, english)),
        );
    }
    Value::Object(row)
}

fn count(value: &Value) -> u64 {
    value
        .as_u64()
        .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
        .or_else(|| {
            value
                .as_f64()
                .filter(|value| *value >= 0.0)
                .map(|value| value as u64)
        })
        .unwrap_or_default()
}

fn stats_summary(stats: &Value, english: bool) -> String {
    let calls = match count(&stats["api_calls"]) {
        0 => count(&stats["turn_count"]),
        calls => calls,
    };
    if english {
        format!(
            "API calls: {calls}\nFiles read: {}\nFiles written: {}\nFiles edited: {}\nSearches: {}\nPages viewed: {}\nCommands run: {}",
            count(&stats["files_read"]),
            count(&stats["write_files"]),
            count(&stats["edit_files"]),
            count(&stats["searches"]),
            count(&stats["web_pages"]),
            count(&stats["commands"])
        )
    } else {
        format!(
            "调用了{calls}次\n阅读了{}次文件\n写入了{}次文件\n编辑了{}次文件\n搜索了{}次内容\n查看了{}个网页\n运行了{}个指令",
            count(&stats["files_read"]),
            count(&stats["write_files"]),
            count(&stats["edit_files"]),
            count(&stats["searches"]),
            count(&stats["web_pages"]),
            count(&stats["commands"])
        )
    }
}

type Reply = (StatusCode, Json<Value>);

#[derive(Debug)]
pub(super) struct RouteError(StatusCode, String);

impl fmt::Display for RouteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { f.write_str(&self.1) }
}

impl std::error::Error for RouteError {}

impl RouteError {
    pub fn bad_request(message: &'static str) -> Self {
        Self(StatusCode::BAD_REQUEST, message.into())
    }
    pub fn unauthorized() -> Self {
        Self(
            StatusCode::UNAUTHORIZED,
            "Session expired. Please sign in again.".into(),
        )
    }
    pub fn workspace_missing() -> Self {
        Self(StatusCode::NOT_FOUND, "Unknown workspace".into())
    }
    pub fn parent_missing() -> Self {
        Self(StatusCode::NOT_FOUND, "Parent conversation not found".into())
    }
    pub fn task_missing() -> Self {
        Self(StatusCode::NOT_FOUND, "Sub-agent task not found".into())
    }
    pub fn storage(_: impl fmt::Display) -> Self {
        Self(
            StatusCode::SERVICE_UNAVAILABLE,
            "Sub-agent storage is unavailable".into(),
        )
    }
    pub fn operation(message: String) -> Self {
        if message == "子智能体任务不存在。" { return Self::task_missing(); }
        if message.starts_with("子智能体存储不可用") { return Self::storage(message); }
        Self(StatusCode::BAD_REQUEST, message)
    }
    fn reply(self) -> Reply {
        (self.0, Json(json!({"success": false, "error": self.1})))
    }
}

async fn respond(operation: impl FnOnce() -> Result<Value, RouteError> + Send + 'static) -> Reply {
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(value)) => (StatusCode::OK, Json(value)),
        Ok(Err(error)) => error.reply(),
        Err(_) => RouteError::storage(io::Error::other("Child registry query interrupted")).reply(),
    }
}
