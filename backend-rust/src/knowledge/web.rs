//! Host endpoints used by the Agent monitor and the compose menu.

use std::path::PathBuf;

use axum::{
    Extension, Json, Router,
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{Knowledge, global_memory::MemoryKind, skills};
use crate::workspace_catalog::RegisteredWorkspace;

/// Mount behind Host Web authentication and the Gateway workspace scope.
pub fn scoped_router(data: PathBuf) -> Router {
    Router::new()
        .route("/api/memory", get(memory))
        .with_state(data.clone())
        .merge(skills_router(data))
}

pub fn skills_router(data: PathBuf) -> Router {
    Router::new().route("/api/skills", get(workspace_menu)).with_state(data)
}

#[derive(Deserialize, Default)]
struct MemoryQuery {
    #[serde(rename = "type")]
    kind: Option<String>,
}

async fn memory(
    State(data): State<PathBuf>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Query(query): Query<MemoryQuery>,
) -> Response {
    let Some(kind) = MemoryKind::parse(query.kind.as_deref().unwrap_or("main")) else {
        return failure(StatusCode::BAD_REQUEST, "type 必须是 main 或 task");
    };
    run(move || {
        let knowledge = Knowledge::open(&workspace.path, &data)?;
        let memory = knowledge.global_memory()?;
        Ok(json!({"success": true, "type": kind.name(), "entries": memory.entries(kind)?}))
    }).await
}

async fn workspace_menu(
    State(data): State<PathBuf>,
    Extension(workspace): Extension<RegisteredWorkspace>,
) -> Response {
    run(move || {
        let knowledge = Knowledge::open(&workspace.path, &data)?;
        Ok(json!({"success": true, "skills": skills::workspace_menu(&knowledge)?}))
    }).await
}

async fn run(action: impl FnOnce() -> std::io::Result<Value> + Send + 'static) -> Response {
    match tokio::task::spawn_blocking(action).await {
        Ok(Ok(value)) => Json(value).into_response(),
        _ => failure(StatusCode::INTERNAL_SERVER_ERROR, "无法读取知识数据。"),
    }
}

pub(super) fn workspace_header(headers: &HeaderMap) -> Option<&str> {
    match headers.get("x-astrion-workspace-id") {
        Some(value) => value.to_str().ok().map(str::trim),
        None => Some(""),
    }
}

pub(super) fn failure(status: StatusCode, error: &str) -> Response {
    (status, Json(json!({"success": false, "error": error}))).into_response()
}
