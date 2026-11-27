use crate::host_paths::Workspace as LaunchWorkspace;
use astrion_headless::{
    approval::{self, ApprovalAccess, ApprovalPrincipal},
    catalog::CatalogSource,
    runtime::{ModelConfig, Runtime, RuntimeError, resolve_task_approval_settings},
    schedules::{self, HostPrincipal, ScheduleService},
    web_assets::personalization::Personalization,
    web_auth::{BrowserSession, WebAuth},
    workflows::{self, WorkflowRegistry},
    workspace_catalog::RegisteredWorkspace as Workspace,
};
use axum::{
    Json, Router,
    extract::{Path, Query, Request, State},
    http::{
        StatusCode,
        header::{AUTHORIZATION, CACHE_CONTROL},
    },
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;
use subtle::ConstantTimeEq;

pub struct Gateway {
    pub runtime: Runtime,
    pub fixed_model: Option<ModelConfig>,
    pub catalog: CatalogSource,
    pub workspace: LaunchWorkspace,
    pub paths: crate::host_paths::HostPaths,
    pub workflows: WorkflowRegistry,
    pub preferences: Personalization,
    pub schedules: ScheduleService,
    pub children: astrion_headless::sub_agents::SubAgents,
    pub token: String,
    pub web: Option<WebAuth>,
}

type App = Arc<Gateway>;
type Reply = (StatusCode, Json<Value>);
fn error(status: StatusCode, message: &str) -> Reply {
    if status==StatusCode::CONFLICT && message==astrion_headless::conversation_db::MIGRATION_REQUIRED {return (status,Json(astrion_headless::conversation_db::migration_required_payload()));}
    (status, Json(json!({"success":false,"error":message})))
}

pub fn router(app: Arc<Gateway>) -> std::io::Result<Router> {
    Ok(Router::new()
        .route("/api/host/workspaces", get(workspaces))
        .route("/api/runtime/sessions", get(sessions))
        .route("/api/runtime/sessions/{id}/history", get(history))
        .route("/api/runtime/sessions/{id}/token-stats", get(stats))
        .route("/api/tasks", get(task_index).post(create_task))
        .route("/api/tasks/{id}", get(poll))
        .route("/api/tasks/{id}/cancel", post(cancel))
        .with_state(app.clone())
        .merge(approval::router())
        .merge(astrion_headless::interactions::router(app.runtime.clone()))
        .merge(astrion_headless::goals::router_with_notice_models(
            app.runtime.clone(), app.catalog.clone(), app.fixed_model.clone(),
        ))
        .merge(workflows::registry_list_router(app.workflows.clone()))
        .merge(app.preferences.router())
        .merge(app.children.list_router())
        .merge(astrion_headless::runtime::versioning::host_token_router(app.runtime.clone()))
        .merge(astrion_headless::runtime::guidance::router(app.runtime.clone()))
        .merge(astrion_headless::runtime::process_resources::gateway_router(app.runtime.clone()))
        .merge(astrion_headless::runtime::host_policy::path_router(app.runtime.clone(), app.paths.deploy.clone()))
        .merge(schedules::router(app.schedules.clone()))
        .route_layer(middleware::from_fn_with_state(app.clone(), workspace_scope))
        .merge(Router::new().route("/api/runtime/sessions", post(create_session)).with_state(app.clone()))
        .merge(crate::workspace_routes::router(&app.paths, app.runtime.clone()))
        .merge(astrion_headless::runtime::host_policy::settings_router(app.runtime.clone(), app.paths.resources.clone()))
        .route_layer(middleware::from_fn_with_state(app.clone(), authorize))
        .merge(
            Router::new()
                .route("/api/v1/models", get(models))
                .with_state(app),
        ))
}

#[derive(Deserialize)]
pub(crate) struct WorkspaceQuery {
    workspace_id: Option<String>,
}
async fn authorize(State(app): State<App>, req: Request, next: Next) -> Response {
    let headers = req.headers();
    let auth = headers.get(AUTHORIZATION);
    if let Some(value) = auth {
        let local_peer = req
            .extensions()
            .get::<axum::extract::ConnectInfo<std::net::SocketAddr>>()
            .is_some_and(|peer| peer.0.ip().is_loopback());
        if !local_peer || headers.get_all(AUTHORIZATION).iter().count() != 1 {
            return error(StatusCode::UNAUTHORIZED, "Invalid host Bearer token").into_response();
        }
        let token = value
            .to_str()
            .ok()
            .and_then(|value| value.split_once(' '))
            .filter(|(scheme, _)| scheme.eq_ignore_ascii_case("bearer"))
            .map(|(_, token)| token.trim());
        if !token.is_some_and(|token| bool::from(token.as_bytes().ct_eq(app.token.as_bytes()))) {
            return error(StatusCode::UNAUTHORIZED, "Invalid host Bearer token").into_response();
        }
    } else if app.web.is_none() {
        return error(StatusCode::UNAUTHORIZED, "Missing host Bearer token").into_response();
    }
    if auth.is_none()
        && let Some(web) = &app.web
    {
        return web.protect_request(req, next).await;
    }
    next.run(req).await
}

pub(crate) async fn workspace_scope(
    State(app): State<App>,
    Query(scope): Query<WorkspaceQuery>,
    mut req: Request,
    next: Next,
) -> Response {
    let schedule_filter =
        req.method() == axum::http::Method::GET && req.uri().path() == "/api/schedules";
    let query = scope
        .workspace_id
        .as_deref()
        .filter(|id| !schedule_filter && !id.is_empty());
    let git_view = matches!(req.uri().path(), "/api/project/git-summary" | "/api/project/git-diff");
    let requested = if git_view {
        match git_workspace(&mut req, query).await {
            Ok(value) => value,
            Err(response) => return response,
        }
    } else {
        match requested_workspace(req.headers(), query) {
            Ok(value) => value,
            Err(err) => return error(StatusCode::BAD_REQUEST, &err.message).into_response(),
        }
    };
    let browser = req.extensions().get::<BrowserSession>().cloned();
    let path = req.uri().path();
    let neutral = git_view || path.starts_with("/api/host/workspaces")
        || path.starts_with("/api/schedules")
        || path.starts_with("/api/workflows")
        || matches!(
            path,
            "/api/personalization"
                | "/api/workspace-view"
                | "/api/status"
                | "/api/new-conversation/settings"
        );
    let source = app.clone();
    let selected = tokio::task::spawn_blocking(move || {
        source.resolve_workspace(requested.as_deref(), browser.as_ref(), neutral)
    })
    .await;
    match selected {
        Ok(Ok(Some(workspace))) => {
            let principal = match ApprovalPrincipal::new("host", &workspace.id) {
                Ok(principal) => principal,
                Err(_) => {
                    return error(StatusCode::BAD_REQUEST, "Invalid workspace identity")
                        .into_response();
                }
            };
            let extensions = req.extensions_mut();
            extensions.insert(ApprovalAccess::new(
                principal.clone(),
                Arc::new(app.runtime.clone()),
            ));
            extensions.insert(principal);
            extensions.insert(HostPrincipal::new(Some(workspace.id.clone())));
            extensions.insert(workspace);
        }
        Ok(Ok(None)) => {
            req.extensions_mut().insert(HostPrincipal::new(None));
        }
        Ok(Err(err)) => {
            return error(
                StatusCode::from_u16(err.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
                &err.message,
            )
            .into_response();
        }
        Err(_) => {
            return error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Workspace lookup interrupted",
            )
            .into_response();
        }
    }
    next.run(req).await
}

async fn git_workspace(req: &mut Request, query: Option<&str>) -> Result<Option<String>, Response> {
    if let Some(id) = query.map(str::trim).filter(|id| !id.is_empty()) {
        return Ok(Some(id.to_owned()));
    }
    let header = requested_workspace(req.headers(), None)
        .map_err(|err| error(StatusCode::BAD_REQUEST, &err.message).into_response())?;
    if let Some(id) = header.as_deref().map(str::trim).filter(|id| !id.is_empty()) {
        return Ok(Some(id.to_owned()));
    }
    if !req.headers().get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.split(';').next().is_some_and(|v| v.trim().eq_ignore_ascii_case("application/json"))) {
        return Ok(None);
    }
    let bytes = axum::body::to_bytes(std::mem::take(req.body_mut()), 2 * 1024 * 1024).await
        .map_err(|_| error(StatusCode::PAYLOAD_TOO_LARGE, "Workspace selection body too large").into_response())?;
    let body: Value = serde_json::from_slice(&bytes).unwrap_or_default();
    *req.body_mut() = axum::body::Body::from(bytes);
    Ok(body["workspace_id"].as_str().map(str::trim).filter(|id| !id.is_empty()).map(str::to_owned))
}

impl Gateway {
    fn resolve_workspace(
        &self,
        requested: Option<&str>,
        browser: Option<&BrowserSession>,
        neutral: bool,
    ) -> Result<Option<Workspace>, RuntimeError> {
        let catalog = self.paths.catalog().map_err(|e| RuntimeError {
            status: 500,
            message: e.to_string(),
        })?;
        let chosen = browser.and_then(BrowserSession::selected_workspace_id);
        let selected = if let Some(id) = requested {
            catalog.workspaces.iter().find(|w| w.id == id)
        } else if browser.is_some() {
            catalog
                .workspaces
                .iter()
                .find(|w| Some(&w.id) == chosen.as_ref())
                .or_else(|| catalog.default_workspace())
        } else {
            catalog.for_directory(&self.workspace.path)
        };
        let Some(entry) = selected else {
            if neutral && requested.is_none() {
                return Ok(None);
            }
            return Err(RuntimeError {
                status: 404,
                message: "Unknown workspace".into(),
            });
        };
        if !neutral {
            self.ensure_bound(entry)?;
        }
        Ok(Some(entry.clone()))
    }

    pub(crate) fn mutation_workspace(
        &self,
        headers: &axum::http::HeaderMap,
        query: Option<&str>,
        body: &Value,
        browser: Option<&BrowserSession>,
    ) -> Result<Option<Workspace>, RuntimeError> {
        let explicit = requested_workspace(headers, query.filter(|id| !id.is_empty()))?;
        let body_id = match &body["workspace_id"] {
            Value::Null => None,
            Value::String(id) => Some(id.as_str()).filter(|id| !id.is_empty()),
            _ => return Err(RuntimeError { status: 400, message: "Invalid workspace identity".into() }),
        };
        if matches!((explicit.as_deref(), body_id), (Some(a), Some(b)) if a != b) {
            return Err(RuntimeError { status: 404, message: "Unknown workspace for this request".into() });
        }
        let workspace = self.resolve_workspace(explicit.as_deref().or(body_id), browser, true)?;
        if let Some(entry) = &workspace {
            self.ensure_bound(entry)?;
        }
        Ok(workspace)
    }

    fn ensure_bound(
        &self,
        entry: &astrion_headless::workspace_catalog::RegisteredWorkspace,
    ) -> Result<(), RuntimeError> {
        if self.runtime.bound_workspace(&entry.id)?.as_ref() == Some(&entry.path)
            && entry.path.is_dir()
        {
            return self.runtime.refresh_workspace_label(entry);
        }
        let tools = astrion_headless::tools::ToolSet::for_workspace(
            &entry.path,
            self.catalog.host_data_dir(),
        )
        .map_err(|e| RuntimeError {
            status: 409,
            message: e.to_string(),
        })?;
        self.runtime.bind_named_workspace(entry, tools, || {
            let current = self.paths.catalog().map_err(|e| RuntimeError { status: 500, message: e.to_string() })?;
            if current.workspaces.iter().any(|ws| ws.id == entry.id && ws.path == entry.path) {
                Ok(())
            } else {
                Err(RuntimeError { status: 409, message: "Workspace registration changed".into() })
            }
        })
    }
}
async fn workspaces(State(app): State<App>, selected: Option<axum::Extension<Workspace>>) -> Reply {
    run(StatusCode::OK, move || {
        let catalog = app.paths.catalog().map_err(|e| RuntimeError { status: 500, message: e.to_string() })?;
        let mut counts = std::collections::HashMap::new();
        for task in app.runtime.navigation_runs()?.into_iter().filter(|run| run.running) {
            *counts.entry(task.workspace_id).or_insert(0_u64) += 1;
        }
        let current = selected.as_ref().map(|scope| scope.0.id.as_str());
        let rows: Vec<_> = catalog.workspaces.iter().map(|ws| json!({
            "workspace_id":ws.id, "path":ws.path, "label":ws.label, "is_current":Some(ws.id.as_str()) == current,
            "running_task_count":counts.get(&ws.id).copied().unwrap_or_default(),
        })).collect();
        Ok(json!({"success":true,"data":{ "source_path":app.paths.workspace_config, "default_workspace_id":catalog.default_workspace_id.as_deref().unwrap_or(""),
            "current_workspace_id":current,"workspaces":rows }}))
    }).await
}
async fn models(State(app): State<App>) -> Response {
    let result = tokio::task::spawn_blocking(move || app.catalog.load().models_response()).await;
    match result {
        Ok(v) => ([(CACHE_CONTROL, "no-store")], Json(v)).into_response(),
        Err(_) => error(StatusCode::INTERNAL_SERVER_ERROR, "Catalog unavailable").into_response(),
    }
}
pub(crate) async fn run(
    status: StatusCode,
    operation: impl FnOnce() -> Result<Value, RuntimeError> + Send + 'static,
) -> Reply {
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(value)) => (status, Json(value)),
        Ok(Err(e)) => error(
            StatusCode::from_u16(e.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            &e.message,
        ),
        Err(_) => error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Runtime operation interrupted",
        ),
    }
}
#[derive(Deserialize)]
struct SessionList {
    limit: Option<usize>,
    offset: Option<usize>,
    quick_entry: Option<String>,
}
#[derive(Deserialize)]
struct Cursor {
    from: Option<u64>,
}
#[derive(Deserialize)]
struct TaskFilter {
    status: Option<String>,
}
async fn task_index(
    State(app): State<App>,
    axum::Extension(ws): axum::Extension<Workspace>,
    Query(query): Query<TaskFilter>,
) -> Reply {
    run(StatusCode::OK, move || {
        app.runtime
            .list_tasks(&ws.id, &ws.label, query.status.as_deref())
    })
    .await
}
async fn sessions(
    State(app): State<App>,
    axum::Extension(ws): axum::Extension<Workspace>,
    Query(q): Query<SessionList>,
) -> Reply {
    run(StatusCode::OK, move || {
        app.runtime.list_sessions(
            &ws.id,
            q.limit.unwrap_or(50),
            q.offset.unwrap_or(0),
            q.quick_entry
                .as_deref()
                .map(|s| matches!(s, "1" | "true" | "True")),
        )
    })
    .await
}
fn requested_workspace(headers: &axum::http::HeaderMap, query: Option<&str>) -> Result<Option<String>, RuntimeError> {
    let mut ids = headers.get_all("x-astrion-workspace-id").iter();
    let header = match ids.next().map(|v| v.to_str()).transpose() {
        Ok(id) if ids.next().is_none() => id,
        _ => return Err(RuntimeError { status: 400, message: "Invalid workspace header".into() }),
    };
    if matches!((header, query), (Some(a), Some(b)) if a != b) {
        return Err(RuntimeError { status: 400, message: "Conflicting workspace identity".into() });
    }
    Ok(header.or(query).map(str::to_owned))
}

async fn create_session(
    State(app): State<App>,
    headers: axum::http::HeaderMap,
    Query(scope): Query<WorkspaceQuery>,
    browser: Option<axum::Extension<BrowserSession>>,
    Json(body): Json<Value>,
) -> Reply {
    run(StatusCode::OK, move || {
        let ws = app.mutation_workspace(&headers, scope.workspace_id.as_deref(), &body, browser.as_deref())?
            .ok_or_else(|| RuntimeError { status: 404, message: "Unknown workspace".into() })?;
        if let Some(model) = &app.fixed_model {
            model.validate_model_key(&body)?;
        }
        app.runtime.create_registered_session(&ws, &body)
    })
    .await
}
async fn history(
    State(app): State<App>,
    axum::Extension(ws): axum::Extension<Workspace>,
    Path(id): Path<String>,
) -> Reply {
    run(StatusCode::OK, move || {
        app.runtime.session_history(&ws.id, &id)
    })
    .await
}
async fn stats(
    State(app): State<App>,
    axum::Extension(ws): axum::Extension<Workspace>,
    Path(id): Path<String>,
) -> Reply {
    run(StatusCode::OK, move || {
        app.runtime.session_token_stats(&ws.id, &id)
    })
    .await
}
async fn create_task(
    State(app): State<App>,
    axum::Extension(ws): axum::Extension<Workspace>,
    axum::Extension(principal): axum::Extension<ApprovalPrincipal>,
    Json(body): Json<Value>,
) -> Reply {
    run(StatusCode::ACCEPTED, move || {
        let model = app.turn_model(&ws, &body)?;
        let settings = resolve_task_approval_settings(principal, &app.catalog)?;
        app.runtime
            .create_task_scoped(&ws, &body, model, settings)
    })
    .await
}

impl Gateway {
    pub(crate) fn turn_model(&self, ws: &Workspace, body: &Value) -> Result<ModelConfig, RuntimeError> {
        let app = self;
        let model = if let Some(model) = &app.fixed_model {
            model.clone()
        } else {
            let conversation = body["conversation_id"].as_str().filter(|id| !id.is_empty());
            let saved = conversation
                .map(|id| app.runtime.web_metadata(&ws.id, id))
                .transpose()?
                .unwrap_or_default();
            let key = body["model_key"].as_str().filter(|key| !key.is_empty())
                .or_else(|| saved["model_key"].as_str().filter(|key| !key.is_empty()));
            let indexed_model = match (key, conversation) {
                (None, Some(id)) => app.runtime.session_model_key(&ws.id, id)?,
                _ => None,
            };
            let thinking = task_thinking(&body).or_else(|| task_thinking(&saved)).unwrap_or(false);
            let effort = body.get("reasoning_effort").unwrap_or(&saved["reasoning_effort"]).as_str();
            let unavailable = |message| RuntimeError {
                status: 503,
                message,
            };
            let selected = app
                .catalog
                .load()
                .select_for_turn(key.or(indexed_model.as_deref()), thinking, effort)
                .map_err(unavailable)?;
            ModelConfig::from_catalog(selected).map_err(unavailable)?
        };
        Ok(model)
    }
}

fn task_thinking(settings: &Value) -> Option<bool> {
    settings["thinking_mode"].as_bool().or_else(|| {
        match settings["run_mode"].as_str() {
            Some("thinking" | "deep") => Some(true),
            Some("fast") => Some(false),
            _ => None,
        }
    })
}

async fn poll(
    State(app): State<App>,
    axum::Extension(ws): axum::Extension<Workspace>,
    Path(id): Path<String>,
    Query(q): Query<Cursor>,
) -> Reply {
    run(StatusCode::OK, move || {
        app.runtime.poll_task(&ws.id, &id, q.from.unwrap_or(0))
    })
    .await
}
async fn cancel(
    State(app): State<App>,
    axum::Extension(ws): axum::Extension<Workspace>,
    Path(id): Path<String>,
) -> Reply {
    run(StatusCode::OK, move || app.runtime.cancel_task(&ws.id, &id)).await
}
