use std::sync::Arc;

use axum::{
    Extension, Json, Router,
    body::Body,
    extract::{DefaultBodyLimit, Multipart, Path, Query, State, rejection::JsonRejection},
    http::{HeaderMap, Method, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio_util::io::ReaderStream;

use crate::{
    catalog::CatalogSource,
    conversation_db::{MIGRATION_REQUIRED, migration_required_payload},
    native_actions::save_file_from,
    navigation::Navigation,
    web_assets::personalization::english,
    web_auth::BrowserSession,
    workspace_catalog::RegisteredWorkspace,
};

use super::{
    ConversationSelection, DEFAULT_FILENAME, MAX_UPLOAD, Runtime, RuntimeError, Scope, Transfer,
    archive, bad,
};

pub(super) type WorkspaceResolver = dyn Fn(
        &HeaderMap,
        Option<&str>,
        &Value,
        &BrowserSession,
    ) -> Result<Option<RegisteredWorkspace>, RuntimeError>
    + Send
    + Sync;

pub fn router(
    runtime: Runtime,
    navigation: Navigation,
    selection: Arc<ConversationSelection>,
    catalog: CatalogSource,
    workspace: impl Fn(
        &HeaderMap,
        Option<&str>,
        &Value,
        &BrowserSession,
    ) -> Result<Option<RegisteredWorkspace>, RuntimeError>
    + Send
    + Sync
    + 'static,
    material_workspace: impl Fn(&str) -> Result<RegisteredWorkspace, RuntimeError>
    + Send
    + Sync
    + 'static,
) -> Router {
    Router::new()
        .route(
            "/api/conversations/import",
            post(import).layer(DefaultBodyLimit::max(MAX_UPLOAD + 65536)),
        )
        .route("/api/conversations/{id}/export", post(export))
        .route("/api/conversations/{id}/migrate", post(migrate))
        .route(
            "/api/conversation-transfer/download/{token}",
            get(download).post(download),
        )
        .with_state(Transfer {
            runtime,
            navigation,
            selection,
            catalog,
            workspace: Arc::new(workspace),
            material_workspace: Arc::new(material_workspace),
        })
}

#[derive(Deserialize)]
struct RequestedScope {
    kind: Option<String>,
    workspace_id: Option<String>,
}

impl Transfer {
    fn scope(
        &self,
        requested: &RequestedScope,
        headers: &HeaderMap,
        body: &Value,
        browser: &BrowserSession,
    ) -> Result<Scope, RuntimeError> {
        match requested.kind.as_deref() {
            Some("chat") => Ok(Scope::Chat),
            Some("agent") | None => {
                let workspace =
                    (self.workspace)(headers, requested.workspace_id.as_deref(), body, browser)?
                        .ok_or_else(|| RuntimeError::new(404, "Workspace is unavailable"))?;
                Ok(Scope::Agent(workspace))
            }
            _ => Err(bad("Invalid conversation kind")),
        }
    }
}

type RequestJson = Result<Option<Json<Value>>, JsonRejection>;

fn body(body: RequestJson) -> Result<Value, RuntimeError> {
    match body {
        Ok(None) => Ok(json!({})),
        Ok(Some(Json(body))) if body.is_object() => Ok(body),
        _ => Err(bad("Expected a JSON object")),
    }
}

async fn export(
    State(transfer): State<Transfer>,
    Extension(browser): Extension<BrowserSession>,
    Path(id): Path<String>,
    Query(scope): Query<RequestedScope>,
    headers: HeaderMap,
    payload: RequestJson,
) -> Response {
    work(move || {
        let scope = transfer.scope(&scope, &headers, &body(payload)?, &browser)?;
        transfer.export(&scope, &id).map(success)
    })
    .await
}

async fn migrate(
    State(transfer): State<Transfer>,
    Extension(browser): Extension<BrowserSession>,
    Path(id): Path<String>,
    Query(scope): Query<RequestedScope>,
    headers: HeaderMap,
    payload: RequestJson,
) -> Response {
    work(move || {
        let body = body(payload)?;
        let op = operation(body["operation_id"].as_str())?;
        let Scope::Agent(from) = transfer.scope(&scope, &headers, &body, &browser)? else {
            return Err(bad("Chat conversations do not belong to workspaces"));
        };
        let target = body["target_workspace_id"]
            .as_str()
            .filter(|id| !id.is_empty())
            .ok_or_else(|| bad("Choose a different target workspace"))?;
        let to = (transfer.workspace)(&HeaderMap::new(), Some(target), &Value::Null, &browser)?
            .ok_or_else(|| RuntimeError::new(404, "Target workspace is unavailable"))?;
        transfer.migrate(&from, &to, &id, op).map(success)
    })
    .await
}

async fn download(
    State(transfer): State<Transfer>,
    Extension(browser): Extension<BrowserSession>,
    Path(token): Path<String>,
    Query(scope): Query<RequestedScope>,
    headers: HeaderMap,
    method: Method,
    payload: RequestJson,
) -> Response {
    work(move || {
        let body = body(payload)?;
        let scope = transfer.scope(&scope, &headers, &body, &browser)?;
        let (file, path) = transfer.open_download(&scope, &token)?;
        if method == Method::POST {
            let filename = match &body["filename"] {
                Value::Null => DEFAULT_FILENAME,
                Value::String(name)
                    if name.len() <= 200
                        && name.ends_with(".astrion.zip")
                        && name
                            .bytes()
                            .all(|byte| byte.is_ascii_alphanumeric() || b"_.-".contains(&byte)) =>
                {
                    name
                }
                _ => return Err(bad("Invalid conversation export filename")),
            };
            let saved = save_file_from(
                file,
                &path,
                filename,
                english(transfer.catalog.host_data_dir()),
            )
            .map_err(|error| RuntimeError::new(503, error.to_string()))?;
            return Ok(success(json!({"saved":saved.is_some()})));
        }
        Ok((
            [
                (header::CONTENT_TYPE, "application/zip"),
                (
                    header::CONTENT_DISPOSITION,
                    "attachment; filename=conversation.astrion.zip",
                ),
                (header::CACHE_CONTROL, "no-store"),
                (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
            ],
            Body::from_stream(ReaderStream::new(tokio::fs::File::from_std(file))),
        )
            .into_response())
    })
    .await
}

#[derive(Default)]
struct Upload {
    bytes: Option<Vec<u8>>,
    operation: Option<String>,
    model: Option<String>,
}

async fn upload(
    form: Result<Multipart, axum::extract::multipart::MultipartRejection>,
) -> Result<Upload, RuntimeError> {
    let mut form = form.map_err(|_| bad("Select an Astrion conversation file"))?;
    let mut upload = Upload::default();
    while let Some(mut field) = form
        .next_field()
        .await
        .map_err(|_| bad("Invalid conversation upload"))?
    {
        let (limit, slot) = match field.name() {
            Some("file") if upload.bytes.is_none() => (MAX_UPLOAD, None),
            Some("operation_id") if upload.operation.is_none() => {
                (512, Some(&mut upload.operation))
            }
            Some("model_key") if upload.model.is_none() => (512, Some(&mut upload.model)),
            Some("file" | "operation_id" | "model_key") => {
                return Err(bad("Duplicate conversation upload field"));
            }
            _ => continue,
        };
        let mut bytes = Vec::new();
        while let Some(chunk) = field
            .chunk()
            .await
            .map_err(|_| bad("Invalid conversation upload"))?
        {
            if chunk.len() > limit.saturating_sub(bytes.len()) {
                return Err(bad("Conversation file exceeds the import size limit"));
            }
            bytes.extend_from_slice(&chunk);
        }
        if let Some(slot) = slot {
            *slot = Some(
                String::from_utf8(bytes).map_err(|_| bad("Invalid conversation upload field"))?,
            );
        } else {
            upload.bytes = Some(bytes);
        }
    }
    Ok(upload)
}

async fn import(
    State(transfer): State<Transfer>,
    Extension(browser): Extension<BrowserSession>,
    Query(scope): Query<RequestedScope>,
    headers: HeaderMap,
    form: Result<Multipart, axum::extract::multipart::MultipartRejection>,
) -> Response {
    let upload = match upload(form).await {
        Ok(upload) => upload,
        Err(error) => return failure(error),
    };
    work(move || {
        let scope = transfer.scope(&scope, &headers, &Value::Null, &browser)?;
        let op = operation(upload.operation.as_deref())?;
        let bytes = upload
            .bytes
            .ok_or_else(|| bad("Select an Astrion conversation file"))?;
        let archive = archive::read(&bytes)?;
        drop(bytes);
        transfer
            .import(
                &scope,
                archive,
                op,
                upload.model.as_deref().filter(|value| !value.is_empty()),
            )
            .map(success)
    })
    .await
}

fn operation(value: Option<&str>) -> Result<&str, RuntimeError> {
    value
        .filter(|value| {
            !value.trim().is_empty()
                && value.chars().count() <= 120
                && !value.chars().any(char::is_control)
        })
        .ok_or_else(|| bad("Missing or invalid operation identity"))
}

fn success(value: Value) -> Response {
    Json(json!({"success":true,"data":value})).into_response()
}

fn failure(error: RuntimeError) -> Response {
    let status = StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let value = if status == StatusCode::CONFLICT && error.message == MIGRATION_REQUIRED {
        migration_required_payload()
    } else {
        json!({"success":false,"error":error.message})
    };
    (status, Json(value)).into_response()
}

async fn work(
    action: impl FnOnce() -> Result<Response, RuntimeError> + Send + 'static,
) -> Response {
    match tokio::task::spawn_blocking(action).await {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => failure(error),
        Err(error) => failure(RuntimeError::storage(error)),
    }
}
