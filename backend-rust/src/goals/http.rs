use axum::{
    Extension, Json, Router,
    extract::{Path, State, rejection::JsonRejection},
    http::StatusCode,
    routing::{get, post},
};
use serde_json::{Value, json};

use crate::{approval::ApprovalPrincipal, catalog::CatalogSource, runtime::{ModelConfig, Runtime}, workspace_catalog::RegisteredWorkspace};

#[derive(Clone)]
struct NoticeModels {
    catalog: CatalogSource,
    fixed: Option<ModelConfig>,
}

/// Mount inside the gateway workspace scope and authentication layers.
pub fn router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/conversations/{id}/goal", post(control))
        .route("/api/conversations/{id}/running-status", get(running_status))
        .with_state(runtime)
}

pub fn router_with_notice_models(runtime: Runtime, catalog: CatalogSource, fixed: Option<ModelConfig>) -> Router {
    router(runtime).layer(Extension(NoticeModels { catalog, fixed }))
}

async fn control(
    State(runtime): State<Runtime>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Path(conversation_id): Path<String>,
    payload: Result<Json<Value>, JsonRejection>,
) -> (StatusCode, Json<Value>) {
    let Ok(Json(payload)) = payload else {
        return error(StatusCode::BAD_REQUEST, "Invalid goal action");
    };
    run(move || runtime.control_goal(&workspace, &conversation_id, &payload)).await
}

async fn running_status(
    State(runtime): State<Runtime>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Path(conversation_id): Path<String>,
    principal: Option<Extension<ApprovalPrincipal>>,
    models: Option<Extension<NoticeModels>>,
) -> (StatusCode, Json<Value>) {
    run(move || {
        let status = runtime.conversation_running_status(&workspace, &conversation_id)?;
        if status["data"]["is_main_running"] != true
            && let (Some(Extension(principal)), Some(Extension(models))) = (principal, models)
        {
            let id = status["data"]["conversation_id"].as_str().unwrap_or_default();
            // Report running resources even if the configured model cannot start a notice.
            if runtime.poll_background_notices(&workspace, id, principal, &models.catalog, models.fixed.as_ref()).is_ok() {
                return runtime.conversation_running_status(&workspace, id);
            }
        }
        Ok(status)
    }).await
}

async fn run(
    operation: impl FnOnce() -> Result<Value, crate::runtime::RuntimeError> + Send + 'static,
) -> (StatusCode, Json<Value>) {
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(progress)) => (StatusCode::OK, Json(progress)),
        Ok(Err(failure)) => error(
            StatusCode::from_u16(failure.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            &failure.message,
        ),
        Err(_) => error(StatusCode::INTERNAL_SERVER_ERROR, "Runtime operation interrupted"),
    }
}

fn error(status: StatusCode, message: &str) -> (StatusCode, Json<Value>) {
    (status, Json(json!({"success":false,"error":message})))
}
