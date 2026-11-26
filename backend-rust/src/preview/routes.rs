use axum::{
    Extension, Json, Router,
    body::{Body, to_bytes},
    extract::{Path as RoutePath, Request, State},
    http::{
        StatusCode, Uri,
        header::{ALLOW, REFERER},
    },
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{MethodFilter, get, on, post},
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::workspace_catalog::RegisteredWorkspace;

use super::{Context, FileScope, Preview, RuntimeError, Scope, host, transport};

const PROXY_METHODS: MethodFilter = MethodFilter::GET
    .or(MethodFilter::HEAD)
    .or(MethodFilter::POST)
    .or(MethodFilter::PUT)
    .or(MethodFilter::DELETE)
    .or(MethodFilter::PATCH);
const PROXY_ALLOW: &str = "GET, HEAD, POST, PUT, DELETE, PATCH, OPTIONS";

impl Preview {
    /// Mount only under authenticated Host Web API and workspace selection.
    pub fn router(&self) -> Router {
        Router::new()
            .route("/api/preview/targets", get(targets).options(read_options))
            .route(
                "/api/preview/targets/remove",
                post(remove).options(remove_options),
            )
            .route(
                "/api/preview/file/{*path}",
                get(web_file).options(read_options),
            )
            .route(
                "/api/preview/proxy/{port}/",
                on(PROXY_METHODS, web_proxy).options(proxy_options),
            )
            .route(
                "/api/preview/proxy/{port}/{*path}",
                on(PROXY_METHODS, web_proxy).options(proxy_options),
            )
            .with_state(self.clone())
    }

    pub(super) fn independent_router(&self, port: u16) -> Router {
        Router::new()
            .route(
                "/{token}/file/{*path}",
                get(token_file).options(read_options),
            )
            .route(
                "/{token}/proxy/{port}/",
                on(PROXY_METHODS, token_proxy).options(proxy_options),
            )
            .route(
                "/{token}/proxy/{port}/{*path}",
                on(PROXY_METHODS, token_proxy).options(proxy_options),
            )
            .with_state(self.clone())
            .layer(middleware::from_fn_with_state(port, guard_host))
    }
}

async fn guard_host(State(port): State<u16>, request: Request, next: Next) -> Response {
    if !host(request.headers()).is_some_and(|host| {
        host.as_str()
            .eq_ignore_ascii_case(&format!("localhost:{port}"))
            || host.as_str() == format!("127.0.0.1:{port}")
    }) {
        return error(StatusCode::FORBIDDEN, "forbidden");
    }
    next.run(request).await
}

async fn targets(
    State(preview): State<Preview>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    request: Request,
) -> Result<Response, Failure> {
    let Some(scope) = web_scope(&preview, &workspace, &request, None) else {
        return Ok(Json(json!({"success": true, "preview_targets": [], "preview_base": null, "preview_token": null})).into_response());
    };
    let service = preview.clone();
    let conversation = scope.conversation.clone();
    let targets = blocking(move || Ok(service.context(&scope)?.targets())).await?;
    let (base, token) = preview
        .runtime_for(request.headers(), &workspace, &conversation)
        .await;
    let mut response = Json(json!({
        "success": true, "preview_targets": targets, "preview_base": base, "preview_token": token,
    }))
    .into_response();
    response.headers_mut().insert(
        axum::http::header::CACHE_CONTROL,
        "no-store".parse().unwrap(),
    );
    Ok(response)
}

async fn remove(
    State(preview): State<Preview>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    request: Request,
) -> Result<Response, Failure> {
    let (parts, body) = request.into_parts();
    let request = Request::from_parts(parts, Body::empty());
    let data: Value = to_bytes(body, 64 * 1024)
        .await
        .ok()
        .and_then(|body| serde_json::from_slice(&body).ok())
        .unwrap_or_default();
    let key = data["key"]
        .as_str()
        .map(str::trim)
        .filter(|key| !key.is_empty())
        .ok_or_else(|| Failure::message(StatusCode::BAD_REQUEST, "缺少有效的预览目标标识"))?;
    let scope = web_scope(
        &preview,
        &workspace,
        &request,
        data["conversation_id"].as_str(),
    );
    let key = key.to_owned();
    let targets = match scope {
        Some(scope) => blocking(move || Ok(preview.remove(&scope, &key)?)).await?,
        None => Vec::new(),
    };
    Ok(Json(json!({"success": true, "preview_targets": targets})).into_response())
}

async fn web_file(
    State(preview): State<Preview>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    RoutePath(path): RoutePath<String>,
    request: Request,
) -> Result<Response, Failure> {
    let scope = request_conversation(&workspace, &request, None)
        .map(|conversation| Scope::new(&workspace, &conversation));
    let uri = request.uri().clone();
    let files = blocking(move || {
        if let Some(scope) = scope {
            let context = preview.context(&scope)?;
            return read_file(&context, &path);
        }
        let store = preview.0.runtime.store()?;
        super::verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        let data = store.data_dir.clone();
        drop(store);
        read_bytes(
            &FileScope::open(&workspace.path, Some(&workspace.path), &data),
            &path,
        )
    })
    .await?;
    Ok(transport::file_response(&uri, files, false))
}

async fn token_file(
    State(preview): State<Preview>,
    RoutePath((token, path)): RoutePath<(String, String)>,
    request: Request,
) -> Result<Response, Failure> {
    let uri = request.uri().clone();
    let files = blocking(move || {
        let context = token_context(&preview, &token)?;
        read_file(&context, &path)
    })
    .await?;
    Ok(transport::file_response(&uri, files, true))
}

#[derive(Deserialize)]
struct ProxyPath {
    token: Option<String>,
    port: String,
}

async fn web_proxy(
    State(preview): State<Preview>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    RoutePath(path): RoutePath<ProxyPath>,
    request: Request,
) -> Result<Response, Failure> {
    let port = proxy_port(&path.port)?;
    let scope = web_scope(&preview, &workspace, &request, None).ok_or_else(unregistered)?;
    let service = preview.clone();
    blocking(move || registered_port(service.context(&scope)?, port)).await?;
    transport::proxy(&preview.0.client, port, request, false).await
}

async fn token_proxy(
    State(preview): State<Preview>,
    RoutePath(path): RoutePath<ProxyPath>,
    request: Request,
) -> Result<Response, Failure> {
    let port = proxy_port(&path.port)?;
    let token = path.token.ok_or_else(unknown_token)?;
    let service = preview.clone();
    blocking(move || registered_port(token_context(&service, &token)?, port)).await?;
    transport::proxy(&preview.0.client, port, request, true).await
}

fn web_scope(
    preview: &Preview,
    workspace: &RegisteredWorkspace,
    request: &Request,
    body: Option<&str>,
) -> Option<Scope> {
    request_conversation(workspace, request, body)
        .map(|id| Scope::new(workspace, &id))
        .or_else(|| preview.current(workspace))
}

fn request_conversation(
    workspace: &RegisteredWorkspace,
    request: &Request,
    body: Option<&str>,
) -> Option<String> {
    query(request.uri(), "conversation_id")
        .or_else(|| {
            body.map(str::trim)
                .filter(|id| !id.is_empty())
                .map(str::to_owned)
        })
        .or_else(|| {
            let referer: Uri = request
                .headers()
                .get(REFERER)?
                .to_str()
                .ok()?
                .parse()
                .ok()?;
            let authority = host(request.headers())?;
            if !referer
                .authority()?
                .as_str()
                .eq_ignore_ascii_case(authority.as_str())
                || !referer.path().starts_with("/api/preview/")
                || query(&referer, "workspace_id").is_some_and(|id| id != workspace.id)
            {
                return None;
            }
            query(&referer, "conversation_id")
        })
}

fn query(uri: &Uri, key: &str) -> Option<String> {
    serde_urlencoded::from_str::<Vec<(String, String)>>(uri.query()?)
        .ok()?
        .into_iter()
        .find(|(name, _)| name == key)
        .map(|(_, value)| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn token_context(preview: &Preview, token: &str) -> Result<Context, Failure> {
    let scope = preview.registered(token).ok_or_else(unknown_token)?;
    preview.context(&scope).map_err(|_| unknown_token())
}

fn read_file(context: &Context, path: &str) -> Result<(String, Vec<u8>), Failure> {
    let files = FileScope::open(
        &context.scope.root,
        context.execution_root().as_deref(),
        &context.data,
    );
    read_bytes(&files, path)
}

fn read_bytes(files: &FileScope, path: &str) -> Result<(String, Vec<u8>), Failure> {
    let bytes = files.read(path).map_err(|reason| {
        if reason == "读取文件失败" {
            Failure::message(StatusCode::INTERNAL_SERVER_ERROR, reason)
        } else {
            Failure::message(StatusCode::NOT_FOUND, "文件不可用或不在可访问范围内")
        }
    })?;
    let mime = mime_guess::from_path(path.trim())
        .first_or_octet_stream()
        .to_string();
    Ok((mime, bytes))
}

fn proxy_port(raw: &str) -> Result<u16, Failure> {
    if raw.is_empty() || !raw.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(Failure::message(StatusCode::NOT_FOUND, "Not found"));
    }
    raw.parse::<u16>()
        .ok()
        .filter(|port| *port > 0)
        .ok_or_else(unregistered)
}

fn registered_port(context: Context, port: u16) -> Result<(), Failure> {
    if context.port_registered(port) {
        Ok(())
    } else {
        Err(unregistered())
    }
}

fn unknown_token() -> Failure {
    Failure::message(StatusCode::NOT_FOUND, "unknown token")
}

fn unregistered() -> Failure {
    Failure::message(
        StatusCode::FORBIDDEN,
        "该端口未登记为本对话的预览目标，拒绝代理",
    )
}

async fn read_options() -> impl IntoResponse {
    [(ALLOW, "GET, HEAD, OPTIONS")]
}
async fn remove_options() -> impl IntoResponse {
    [(ALLOW, "POST, OPTIONS")]
}
async fn proxy_options() -> impl IntoResponse {
    [(ALLOW, PROXY_ALLOW)]
}

async fn blocking<T: Send + 'static>(
    operation: impl FnOnce() -> Result<T, Failure> + Send + 'static,
) -> Result<T, Failure> {
    tokio::task::spawn_blocking(operation).await.map_err(|_| {
        Failure::message(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Preview operation interrupted",
        )
    })?
}

pub(super) struct Failure(StatusCode, String);

impl Failure {
    pub(super) fn message(status: StatusCode, message: impl Into<String>) -> Self {
        Self(status, message.into())
    }
}

impl From<RuntimeError> for Failure {
    fn from(error: RuntimeError) -> Self {
        let status =
            StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        if status == StatusCode::NOT_FOUND || status == StatusCode::BAD_REQUEST {
            return Self::message(StatusCode::NOT_FOUND, "Conversation not found");
        }
        Self::message(status, error.message)
    }
}

impl IntoResponse for Failure {
    fn into_response(self) -> Response {
        error(self.0, &self.1)
    }
}

fn error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"success": false, "error": message}))).into_response()
}
