use super::{Result, WorkflowError, WorkflowRegistry};
use crate::{approval::ApprovalPrincipal, runtime::Runtime, web_auth::BrowserSession};
use axum::{
    Extension, Json, Router,
    extract::{Path, Query, Request, State, rejection::JsonRejection},
    http::StatusCode,
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};

/// Mount in the Gateway's authenticated Bearer-or-browser route group.
pub fn registry_list_router(registry: WorkflowRegistry) -> Router {
    Router::new()
        .route("/api/workflows", get(list))
        .with_state(registry)
}

/// Fixed-workspace entry for callers with a single trusted workspace.
pub fn browser_router(
    registry: WorkflowRegistry,
    runtime: Runtime,
    workspace_id: String,
) -> Router {
    browser_routes(registry, runtime, Some(workspace_id))
}

/// Mount under browser authentication and CSRF. The authenticated workspace
/// resolver supplies `ApprovalPrincipal` per request. Registry CRUD also works
/// when an authenticated browser has no selected workspace.
pub fn browser_scoped_router(registry: WorkflowRegistry, runtime: Runtime) -> Router {
    browser_routes(registry, runtime, None)
}

fn browser_routes(
    registry: WorkflowRegistry,
    runtime: Runtime,
    fixed_workspace: Option<String>,
) -> Router {
    let app = BrowserWorkflows {
        registry,
        runtime,
        fixed_workspace,
    };
    Router::new()
        .route(
            "/api/workflows/{*name}",
            get(detail).put(save).delete(remove),
        )
        .route("/api/workflow/status", get(status))
        .route("/api/workflow/activate", post(activate))
        .route("/api/workflow/deactivate", post(deactivate))
        .route_layer(middleware::from_fn_with_state(
            app.clone(),
            require_browser_workspace,
        ))
        .with_state(app)
}

#[derive(Clone)]
struct BrowserWorkflows {
    registry: WorkflowRegistry,
    runtime: Runtime,
    fixed_workspace: Option<String>,
}

#[derive(Deserialize)]
struct WorkspaceQuery {
    workspace_id: Option<String>,
}

async fn require_browser_workspace(
    State(app): State<BrowserWorkflows>,
    Query(query): Query<WorkspaceQuery>,
    mut request: Request,
    next: Next,
) -> Response {
    if request.extensions().get::<BrowserSession>().is_none() {
        return reply_error(WorkflowError::new(401, "请先登录"), false);
    }
    if let Some(fixed) = &app.fixed_workspace {
        if request
            .headers()
            .get("x-astrion-workspace-id")
            .is_some_and(|id| id.as_bytes() != fixed.as_bytes())
            || query
                .workspace_id
                .as_deref()
                .is_some_and(|id| !id.is_empty() && id != fixed)
        {
            return reply_error(WorkflowError::new(404, "Unknown workspace"), false);
        }
        if let Ok(principal) = ApprovalPrincipal::new("host", fixed) {
            request.extensions_mut().insert(principal);
        }
    }
    next.run(request).await
}

async fn list(State(registry): State<WorkflowRegistry>) -> Response {
    blocking(move || registry.list_response(), false).await
}

async fn detail(State(app): State<BrowserWorkflows>, Path(name): Path<String>) -> Response {
    blocking(move || app.registry.get_response(&name), false).await
}

async fn save(
    State(app): State<BrowserWorkflows>,
    Path(name): Path<String>,
    payload: std::result::Result<Json<Value>, JsonRejection>,
) -> Response {
    let body = payload.map(|Json(value)| value).unwrap_or_default();
    blocking(move || app.registry.put_response(&name, body), false).await
}

async fn remove(State(app): State<BrowserWorkflows>, Path(name): Path<String>) -> Response {
    blocking(move || app.registry.delete_response(&name), false).await
}

#[derive(Deserialize)]
struct StatusQuery {
    conversation_id: Option<String>,
}

async fn status(
    State(app): State<BrowserWorkflows>,
    principal: Option<Extension<ApprovalPrincipal>>,
    Query(query): Query<StatusQuery>,
) -> Response {
    let conversation_id = query.conversation_id.unwrap_or_default();
    blocking(
        move || {
            app.runtime
                .workflow_status(workspace(&principal)?, conversation_id.trim())
        },
        true,
    )
    .await
}

async fn activate(
    State(app): State<BrowserWorkflows>,
    principal: Option<Extension<ApprovalPrincipal>>,
    payload: std::result::Result<Json<Value>, JsonRejection>,
) -> Response {
    let body = payload.map(|Json(value)| value).unwrap_or_default();
    blocking(
        move || {
            let name = body["name"].as_str().unwrap_or_default().trim();
            let conversation_id = body["conversation_id"]
                .as_str()
                .map(str::trim)
                .filter(|id| !id.is_empty());
            app.runtime
                .activate_workflow(workspace(&principal)?, conversation_id, name)
        },
        false,
    )
    .await
}

async fn deactivate(
    State(app): State<BrowserWorkflows>,
    principal: Option<Extension<ApprovalPrincipal>>,
    payload: std::result::Result<Json<Value>, JsonRejection>,
) -> Response {
    let body = payload.map(|Json(value)| value).unwrap_or_default();
    blocking(
        move || {
            let id = body["conversation_id"].as_str().unwrap_or_default().trim();
            app.runtime.deactivate_workflow(workspace(&principal)?, id)
        },
        false,
    )
    .await
}

fn workspace(principal: &Option<Extension<ApprovalPrincipal>>) -> Result<&str> {
    principal
        .as_ref()
        .map(|Extension(principal)| principal.workspace_id())
        .ok_or_else(|| WorkflowError::new(400, "请先选择工作区"))
}

async fn blocking(
    call: impl FnOnce() -> Result<Value> + Send + 'static,
    status_route: bool,
) -> Response {
    match tokio::task::spawn_blocking(call).await {
        Ok(Ok(data)) => Json(data).into_response(),
        Ok(Err(error)) => reply_error(error, status_route),
        Err(_) => reply_error(WorkflowError::new(500, "工作流操作中断"), status_route),
    }
}

fn reply_error(error: WorkflowError, status_route: bool) -> Response {
    let status = StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let body = if status_route && status == StatusCode::NOT_FOUND {
        json!({"success":false, "error":"Conversation not found"})
    } else {
        json!({"error":error.message})
    };
    (status, Json(body)).into_response()
}
