//! Workspace, conversation and settings views for the authenticated Host web UI.

mod bootstrap;
mod attachments;
mod chatgpt_fast_mode;
mod preferences;
mod project_files;
mod gui_files;
mod downloads;
mod file_response;
mod settings;

pub use chatgpt_fast_mode::chatgpt_fast_mode_router;
pub use attachments::attachment_router;
pub use preferences::admin_policy;

use crate::catalog::CatalogSource;
use crate::runtime::{NavigationRun, Runtime, RuntimeError};
use crate::workspace_catalog::RegisteredWorkspace;
use axum::extract::{Path, Query, Request, State, rejection::JsonRejection};
use axum::http::{HeaderMap, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use preferences::{Personalization, memory_count, pending_modes, run_mode, truthy};
use serde::Deserialize;
use serde_json::{Value, json};
use std::path::PathBuf;
use std::sync::Arc;

#[derive(Clone)]
pub struct WebStatusConfig {
    pub runtime: Runtime,
    pub catalog: CatalogSource,
    pub data_dir: PathBuf,
    pub workspace_id: String,
    pub project_path: PathBuf,
    pub version: String,
}

type App = Arc<WebStatusConfig>;
type Result<T> = std::result::Result<T, WebError>;

/// Mount views for a fixed workspace behind `WebAuth::protect_api`.
pub fn router(config: WebStatusConfig) -> Router {
    routes(config, false)
}

/// Resolve each view against the workspace selected by trusted request middleware.
/// Run that middleware inside the Web authentication layer and before this router.
pub fn scoped_router(config: WebStatusConfig) -> Router {
    routes(config, true)
}

pub fn headless_router(config: WebStatusConfig) -> Router {
    bind_routes(status_route(), config, true)
}

fn status_route() -> Router {
    Router::new().route("/api/status", get(status))
}

fn routes(config: WebStatusConfig, scoped: bool) -> Router {
    let routes = status_route()
        .route("/api/files", get(project_files::tree))
        .route("/api/commands", post(system_command))
        .route("/api/focused", get(|| async { Json(json!({})) }))
        .route("/api/project/files/search", get(project_files::search))
        .route("/api/gui/files/entries", get(gui_files::entries))
        .route("/api/gui/files/text", get(gui_files::text).post(gui_files::save))
        .route("/api/gui/files/download", get(gui_files::download))
        .route("/api/download/file", get(downloads::attachment))
        .route("/api/file/content", get(downloads::inline))
        .route("/api/conversations/media/{*id}", get(downloads::media))
        .route("/api/workspace-view", get(workspace_view))
        .route(
            "/api/new-conversation/settings",
            post(new_conversation_settings),
        )
        .route(
            "/api/conversations/{id}/metadata",
            get(conversation_metadata),
        )
        .route(
            "/api/conversations/{id}/bootstrap",
            get(conversation_bootstrap),
        )
        .merge(settings::router());
    bind_routes(routes, config, scoped)
}

fn bind_routes(routes: Router, config: WebStatusConfig, scoped: bool) -> Router {
    routes.route_layer(middleware::from_fn_with_state(
        (Arc::new(config), scoped), bind_workspace,
    ))
}

async fn bind_workspace(
    State((config, scoped)): State<(App, bool)>,
    mut request: Request,
    next: Next,
) -> Response {
    let resolved = if scoped {
        let Some(workspace) = request.extensions().get::<RegisteredWorkspace>() else {
            return Json(json!({"success": false, "code": "no_workspace", "error": "No workspace is selected"})).into_response();
        };
        let mut resolved = (*config).clone();
        resolved.workspace_id.clone_from(&workspace.id);
        resolved.project_path.clone_from(&workspace.path);
        Arc::new(resolved)
    } else {
        config
    };
    request.extensions_mut().insert(resolved);
    next.run(request).await
}

impl WebStatusConfig {
    fn check_workspace(&self, headers: &HeaderMap, query: &Scope, body: &Value) -> Result<()> {
        let mut header_values = headers.get_all("x-astrion-workspace-id").iter();
        let header = header_values
            .next()
            .map(|value| value.to_str())
            .transpose()
            .map_err(|_| WebError::invalid("Invalid workspace identity"))?;
        if header_values.next().is_some() {
            return Err(WebError::invalid("Duplicate workspace identity"));
        }
        let body = optional_string(body, "workspace_id")?;
        if [query.workspace_id.as_deref(), header, body]
            .into_iter()
            .flatten()
            .any(|id| !id.is_empty() && id != self.workspace_id)
        {
            return Err(WebError(StatusCode::NOT_FOUND, "Unknown workspace"));
        }
        Ok(())
    }

    fn workspace(&self) -> Value {
        json!({"workspace_id": self.workspace_id, "project_path": self.project_path})
    }

    fn command_terminals(&self, conversation: Option<&str>) -> Result<Value> {
        let workspace = RegisteredWorkspace { id: self.workspace_id.clone(), label: self.workspace_id.clone(), path: self.project_path.clone() };
        Ok(self.runtime.command_terminals(&workspace, conversation)?)
    }

    fn active_run(&self) -> Result<Option<NavigationRun>> {
        Ok(self
            .runtime
            .navigation_runs()?
            .into_iter()
            .filter(|run| run.running && run.workspace_id == self.workspace_id)
            .max_by(|a, b| {
                a.created_at
                    .total_cmp(&b.created_at)
                    .then(a.task_id.cmp(&b.task_id))
            }))
    }
}

#[derive(Deserialize)]
struct Scope {
    workspace_id: Option<String>,
    conversation_id: Option<String>,
}

struct WebError(StatusCode, &'static str);

impl WebError {
    fn invalid(message: &'static str) -> Self {
        Self(StatusCode::BAD_REQUEST, message)
    }
}

impl From<RuntimeError> for WebError {
    fn from(error: RuntimeError) -> Self {
        if error.status==409 && error.message==crate::conversation_db::MIGRATION_REQUIRED {return Self(StatusCode::CONFLICT,crate::conversation_db::MIGRATION_REQUIRED);}
        match error.status {
            400 => Self::invalid("Invalid conversation ID or document"),
            404 => Self(StatusCode::NOT_FOUND, "Conversation not found in workspace"),
            409 => Self(
                StatusCode::CONFLICT,
                "Change settings after the current task finishes",
            ),
            _ => Self(
                StatusCode::SERVICE_UNAVAILABLE,
                "Conversation storage is unavailable",
            ),
        }
    }
}

impl IntoResponse for WebError {
    fn into_response(self) -> Response {
        if self.0==StatusCode::CONFLICT && self.1==crate::conversation_db::MIGRATION_REQUIRED {return (self.0,Json(crate::conversation_db::migration_required_payload())).into_response();}
        (self.0, Json(json!({"success": false, "error": self.1}))).into_response()
    }
}

async fn snapshot(
    operation: impl FnOnce() -> Result<Value> + Send + 'static,
) -> Result<Json<Value>> {
    let value = tokio::task::spawn_blocking(operation).await.map_err(|_| {
        WebError(
            StatusCode::SERVICE_UNAVAILABLE,
            "Workspace view is unavailable",
        )
    })??;
    Ok(Json(value))
}

fn success(data: Value) -> Value {
    json!({"success": true, "data": data})
}

fn optional_string<'a>(value: &'a Value, field: &str) -> Result<Option<&'a str>> {
    match value.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(text)) => Ok(Some(text)),
        _ => Err(WebError::invalid(match field {
            "previous_conversation_id" => "Invalid previous conversation ID",
            "conversation_id" => "Invalid conversation ID",
            _ => "Invalid workspace identity",
        })),
    }
}

fn conversation_id(value: &str) -> Result<String> {
    let value = value.trim().strip_prefix("conv_").unwrap_or(value.trim());
    if value.is_empty()
        || value.len() > 160
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
    {
        return Err(WebError::invalid("Invalid conversation ID"));
    }
    Ok(format!("conv_{value}"))
}

fn read_document(app: &WebStatusConfig, id: &str) -> Result<Value> {
    Ok(app.runtime.web_header(&app.workspace_id,id)?.0)
}

async fn workspace_view(
    Extension(app): Extension<App>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
) -> Result<Json<Value>> {
    app.check_workspace(&headers, &scope, &Value::Null)?;
    snapshot(move || {
        Ok(success(json!({
            "draft_owner": draft_owner(),
            "host_mode": true,
            "active_conversation_id": app.active_run()?.map(|run| run.conversation_id),
            "workspace": app.workspace(),
        })))
    })
    .await
}

fn draft_owner() -> String {
    // Persisted drafts use the first 24 hexadecimal digits of SHA-256(username).
    ring::digest::digest(&ring::digest::SHA256, b"host").as_ref()[..12]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

async fn system_command(
    Extension(app): Extension<App>, Query(mut scope): Query<Scope>, headers: HeaderMap, Json(body): Json<Value>,
) -> Result<Response> {
    app.check_workspace(&headers, &scope, &body)?;
    let requested = optional_string(&body,"conversation_id")?.filter(|id| !id.is_empty()).map(conversation_id).transpose()?;
    if let (Some(body_id), Some(query_id)) = (&requested,&scope.conversation_id) {
        if body_id != &conversation_id(query_id)? { return Err(WebError::invalid("Conflicting conversation IDs")); }
    }
    scope.conversation_id = requested.or(scope.conversation_id).or(app.active_run()?.map(|run|run.conversation_id));
    let cmd = body["command"].as_str().unwrap_or_default().trim().trim_start_matches('/').split_whitespace().next().unwrap_or_default().to_lowercase();
    let data = match cmd.as_str() {
        "status" => status(Extension(app),Query(scope),headers).await?.0,
        "terminals" => app.command_terminals(scope.conversation_id.as_deref())?,
        "clear" => {
            if let Some(id) = &scope.conversation_id { app.runtime.clear_context(&app.workspace_id,id)?; }
            return Ok(Json(json!({"command":cmd,"success":true,"message":"对话已清空"})).into_response());
        }
        _ => return Ok((StatusCode::BAD_REQUEST, Json(json!({"success":false,"command":cmd,"message":if cmd.is_empty() {"缺少命令".to_owned()} else {format!("未知命令 {cmd}")}}))).into_response()),
    };
    Ok(Json(json!({"success":true,"command":cmd,"data":data})).into_response())
}

async fn status(
    Extension(app): Extension<App>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
) -> Result<Json<Value>> {
    app.check_workspace(&headers, &scope, &Value::Null)?;
    let requested = scope
        .conversation_id
        .as_deref()
        .filter(|id| !id.trim().is_empty())
        .map(conversation_id)
        .transpose()?;
    snapshot(move || {
        let active = app.active_run()?;
        let current = requested.or_else(|| active.as_ref().map(|run| run.conversation_id.clone()));
        let (document, message_count) = current.as_deref().map(|id| app.runtime.web_header(&app.workspace_id,id)).transpose()?.unwrap_or_default();
        let (tool_results, has_images, has_videos) = current.as_deref().map(|id| app.runtime.web_activity_counts(&app.workspace_id,id)).transpose()?.unwrap_or_default();
        let meta = &document["metadata"];
        let prefs = Personalization::load(&app.data_dir);
        let catalog = app.catalog.load();
        let modes = prefs.modes(meta);
        let policy = admin_policy(&app.data_dir, &catalog)
            .map_err(|_| WebError(StatusCode::SERVICE_UNAVAILABLE, "Host administrator policy is unavailable"))?;
        let terminals = app.command_terminals(current.as_deref())?;
        let mut status = json!({
            "thinking_mode": prefs.run_mode == "thinking", "run_mode": prefs.run_mode,
            "thinking_status": if prefs.run_mode == "thinking" { "思考模式" } else { "快速模式" },
            "model_key": prefs.model_key(&catalog), "reasoning_effort": prefs.reasoning_effort,
            "pending_runtime_modes": pending_modes(meta),
            "execution_mode_enabled": false, "network_permission_enabled": false,
            "focused_files": {}, "focused_files_count": 0,
            "terminals": terminals,
            "memory": {"main": memory_count(&app.data_dir, "memory.md"), "task": memory_count(&app.data_dir, "task_memory.md")},
            "conversation": {"current_id": current, "total_conversations": app.runtime.list_sessions(&app.workspace_id, 0, 0, None)?["total"],
                "total_messages": message_count, "total_tools": tool_results},
            "has_images": has_images, "has_videos": has_videos,
            "todo_list": document["todo_list"],
            "project_path": app.project_path,
            "workspace_id": app.workspace_id,
            "container": {"success": true, "mode": "host", "workspace_id": app.workspace_id},
            "version": app.version,
            "admin_policy": policy,
        });
        modes.write_fields(&mut status);
        status["execution_mode"] = json!({"mode": modes.execution_mode, "default_mode": "sandbox"});
        if current.is_some() {
            for key in ["title", "created_at", "updated_at"] {
                status["conversation"][key] = document[key].clone();
            }
            let running = active.as_ref().is_some_and(|run| Some(run.conversation_id.as_str()) == current.as_deref());
            status["conversation"]["compression"] = bootstrap::compression(meta, current.as_deref().unwrap_or_default(), running);
        }
        Ok(status)
    }).await
}

async fn new_conversation_settings(
    Extension(app): Extension<App>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
    payload: std::result::Result<Option<Json<Value>>, JsonRejection>,
) -> Result<Json<Value>> {
    let body = payload
        .map_err(|_| WebError::invalid("Expected a JSON object"))?
        .map_or_else(|| json!({}), |Json(body)| body);
    if !body.is_object() {
        return Err(WebError::invalid("Expected a JSON object"));
    }
    app.check_workspace(&headers, &scope, &body)?;
    let previous = optional_string(&body, "previous_conversation_id")?
        .filter(|id| !id.trim().is_empty())
        .map(conversation_id)
        .transpose()?;
    snapshot(move || {
        let previous = if let Some(id) = previous.as_deref() {
            match read_document(&app, id) {
                Ok(document) => document,
                Err(WebError(StatusCode::NOT_FOUND, _)) => Value::Null,
                Err(error) => return Err(error),
            }
        } else {
            Value::Null
        };
        let prefs = Personalization::load(&app.data_dir);
        let settings = prefs.draft_settings(&app.catalog.load(), &previous["metadata"]);
        Ok(success(json!({
            "workspace": app.workspace(), "settings": settings,
            "execution_mode_enabled": false, "network_permission_enabled": false,
        })))
    })
    .await
}

async fn conversation_metadata(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
) -> Result<Json<Value>> {
    app.check_workspace(&headers, &scope, &Value::Null)?;
    let id = conversation_id(&id)?;
    snapshot(move || {
        let document = read_document(&app, &id)?;
        Ok(success(json!({
            "id": id, "workspace_id": app.workspace_id,
            "title": document["title"].as_str().unwrap_or_default(),
            "title_locked": truthy(&document["metadata"]["title_locked"]),
        })))
    })
    .await
}

async fn conversation_bootstrap(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    Query(scope): Query<Scope>,
    preview: Option<Extension<crate::preview::Preview>>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    headers: HeaderMap,
) -> Result<Json<Value>> {
    app.check_workspace(&headers, &scope, &Value::Null)?;
    let id = conversation_id(&id)?;
    let preview_id = id.clone();
    let Json(mut payload) = snapshot(move || {
        let snapshot = app.runtime.web_snapshot(&app.workspace_id, &id)?;
        let prefs = Personalization::load(&app.data_dir);
        Ok(success(bootstrap::build(
            &app,
            &id,
            &prefs,
            snapshot,
        )))
    }).await?;
    if let (Some(Extension(preview)), Some(Extension(workspace))) = (preview, workspace) {
        let (base, token) = preview.runtime_for(&headers, &workspace, &preview_id).await;
        payload["data"]["preview_base"] = json!(base);
        payload["data"]["preview_token"] = json!(token);
    }
    Ok(Json(payload))
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/web_status_contract.rs"]
mod tests;
