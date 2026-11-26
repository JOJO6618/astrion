use super::{
    platform::{Application, Platform, System},
    save::{SaveError, open_regular, save_with},
    scope::{FileError, Scope, registered_root},
};
use crate::{
    runtime::{Runtime, RuntimeError},
    web_assets::personalization::english,
    web_auth::BrowserSession,
    workspace_catalog::{HostCatalog, RegisteredWorkspace},
};
use axum::{
    Extension, Json, Router,
    extract::{RawQuery, Request, State, rejection::JsonRejection},
    http::{StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use percent_encoding::{NON_ALPHANUMERIC, utf8_percent_encode};
use ring::{hmac, rand::SystemRandom};
use serde_json::{Value, json};
use std::{io, path::{Path, PathBuf}, sync::Arc};

#[derive(Clone)]
pub struct NativeActions(Arc<Actions>);

struct Actions {
    runtime: Runtime,
    data: PathBuf,
    catalog: Box<dyn Fn() -> io::Result<HostCatalog> + Send + Sync>,
    platform: Arc<dyn Platform>,
    icon_key: hmac::Key,
}

impl NativeActions {
    pub fn new(
        runtime: Runtime, data: PathBuf,
        catalog: impl Fn() -> io::Result<HostCatalog> + Send + Sync + 'static,
    ) -> Self {
        Self::with_platform(runtime, data, catalog, Arc::new(System))
    }

    fn with_platform(
        runtime: Runtime, data: PathBuf,
        catalog: impl Fn() -> io::Result<HostCatalog> + Send + Sync + 'static,
        platform: Arc<dyn Platform>,
    ) -> Self {
        // If the operating system cannot provide entropy, never mint predictable proofs.
        let icon_key = hmac::Key::generate(hmac::HMAC_SHA256, &SystemRandom::new())
            .expect("Host icon proof entropy unavailable");
        Self(Arc::new(Actions { runtime, data, catalog: Box::new(catalog), platform, icon_key }))
    }

    /// Mount under the Host browser session guard and the selected workspace scope.
    pub fn workspace_router(&self) -> Router {
        Router::new()
            .route("/api/project/open-in-file-manager", post(open_in_manager))
            .route("/api/project/file-open-apps", get(file_applications))
            .route("/api/project/open-file-with-app", post(open_with_app))
            .route("/api/system/open-file", post(open_file))
            .route("/api/system/save-file", post(save_file))
            .route_layer(middleware::from_fn(require_host))
            .with_state(self.0.clone())
    }

    /// Mount under the Host browser session guard. No workspace is required.
    pub fn system_router(&self) -> Router {
        Router::new()
            .route("/api/project/app-icon", get(app_icon))
            .route("/api/project/pick-folder", post(pick_folder))
            .route("/api/system/open-external", post(open_external))
            .route_layer(middleware::from_fn(require_host))
            .with_state(self.0.clone())
    }
}

async fn require_host(req: Request, next: Next) -> Response {
    if req.extensions().get::<BrowserSession>().is_none() {
        return Failure::new(StatusCode::FORBIDDEN, "仅宿主机模式可用", "Available in host mode only").reply(false);
    }
    next.run(req).await
}

type Payload = Result<Json<Value>, JsonRejection>;
type ActionResult = Result<Response, Failure>;

#[derive(Debug)]
struct Failure {
    status: StatusCode,
    zh: String,
    en: String,
}

impl Failure {
    fn new(status: StatusCode, zh: impl Into<String>, en: impl Into<String>) -> Self {
        Self { status, zh: zh.into(), en: en.into() }
    }

    fn reply(self, en: bool) -> Response {
        (self.status, Json(json!({"success": false, "error": if en { self.en } else { self.zh }}))).into_response()
    }
}

impl From<RuntimeError> for Failure {
    fn from(error: RuntimeError) -> Self {
        Self::new(StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR), error.message.clone(), error.message)
    }
}

const BAD: StatusCode = StatusCode::BAD_REQUEST;
const MISSING: StatusCode = StatusCode::NOT_FOUND;
const FAILED: StatusCode = StatusCode::INTERNAL_SERVER_ERROR;

fn file_error(error: FileError, missing: StatusCode) -> Failure {
    match error {
        FileError::Invalid => Failure::new(BAD, "文件路径无效", "Invalid file path"),
        FileError::Missing => Failure::new(missing, "文件不存在", "File not found"),
    }
}

fn success(data: Option<Value>) -> Response {
    let body = data.map_or_else(|| json!({"success": true}), |data| json!({"success": true, "data": data}));
    Json(body).into_response()
}

fn body(value: Payload) -> Value {
    value.ok().map(|value| value.0).unwrap_or_default()
}

fn text<'a>(value: &'a Value, name: &str) -> &'a str {
    value[name].as_str().unwrap_or_default().trim()
}

struct Args(Vec<(String, String)>);
impl Args {
    fn parse(RawQuery(raw): RawQuery) -> Self {
        Self(serde_urlencoded::from_str(raw.as_deref().unwrap_or_default()).unwrap_or_default())
    }

    fn get(&self, key: &str) -> &str {
        self.0.iter().find(|(name, _)| name == key).map_or("", |(_, value)| value.trim())
    }

    fn conversation<'a>(&'a self, payload: &'a Value) -> &'a str {
        let from_query = self.get("conversation_id");
        if from_query.is_empty() { text(payload, "conversation_id") } else { from_query }
    }
}

async fn run(actions: Arc<Actions>, op: impl FnOnce(&Actions, bool) -> ActionResult + Send + 'static) -> Response {
    tokio::task::spawn_blocking(move || {
        let en = english(&actions.data);
        op(&actions, en).unwrap_or_else(|failure| failure.reply(en))
    }).await.unwrap_or_else(|_| Failure::new(FAILED, "系统操作被中断", "Native action interrupted").reply(false))
}

async fn open_in_manager(
    State(actions): State<Arc<Actions>>, Extension(workspace): Extension<RegisteredWorkspace>, query: RawQuery, payload: Payload,
) -> Response {
    run(actions, move |app, _| {
        let payload = body(payload);
        let args = Args::parse(query);
        let scope = Scope::for_request(&app.runtime, &workspace, args.conversation(&payload))?;
        let explicit = text(&payload, "workspace_id");
        let target = if explicit.is_empty() || explicit == workspace.id {
            scope.project_directory()
        } else {
            let catalog = (app.catalog)().map_err(|_| Failure::new(FAILED, "无法读取工作区", "Cannot load workspaces"))?;
            let other = catalog.by_id(explicit).ok_or_else(|| Failure::new(MISSING, "工作区不存在", "Workspace not found"))?;
            let root = registered_root(other).map_err(|_| Failure::new(MISSING, "工作区不存在", "Workspace not found"))?;
            root
        };
        app.platform.open_path(&target).map_err(|_| Failure::new(FAILED, "无法打开文件管理器", "Cannot open file manager"))?;
        Ok(success(None))
    }).await
}

async fn file_applications(
    State(actions): State<Arc<Actions>>, Extension(workspace): Extension<RegisteredWorkspace>, query: RawQuery,
) -> Response {
    run(actions, move |app, _| {
        let args = Args::parse(query);
        let scope = Scope::for_request(&app.runtime, &workspace, args.get("conversation_id"))?;
        let file = scope.git_file(args.get("path")).map_err(|error| file_error(error, BAD))?;
        let apps: Vec<_> = app.platform.applications(&file).iter().filter_map(|row| app.application(row)).collect();
        Ok(success(Some(json!({"apps": apps}))))
    }).await
}

impl Actions {
    fn application(&self, row: &Application) -> Option<Value> {
        let id = row.path.to_str()?;
        let label = if row.is_default { format!("{}（默认）", row.label) } else { row.label.clone() };
        let mut item = json!({"id": id, "label": label});
        if let Some(bundle_id) = &row.bundle_id {
            let proof = URL_SAFE_NO_PAD.encode(hmac::sign(&self.icon_key, id.as_bytes()));
            let encoded = utf8_percent_encode(id, NON_ALPHANUMERIC);
            item["bundle_id"] = json!(bundle_id);
            item["icon_url"] = json!(format!("/api/project/app-icon?app_id={encoded}&proof={proof}"));
        }
        Some(item)
    }
}

async fn app_icon(State(actions): State<Arc<Actions>>, query: RawQuery) -> Response {
    run(actions, move |app, _| {
        let args = Args::parse(query);
        let id = args.get("app_id");
        let unavailable = || Failure::new(MISSING, "应用图标不可用", "App icon unavailable");
        let proof = URL_SAFE_NO_PAD.decode(args.get("proof")).map_err(|_| unavailable())?;
        hmac::verify(&app.icon_key, id.as_bytes(), &proof).map_err(|_| unavailable())?;
        let bytes = app.platform.app_icon(Path::new(id)).map_err(|_| unavailable())?;
        if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") { return Err(unavailable()); }
        Ok(([(header::CONTENT_TYPE, "image/png")], bytes).into_response())
    }).await
}

async fn open_with_app(
    State(actions): State<Arc<Actions>>, Extension(workspace): Extension<RegisteredWorkspace>, query: RawQuery, payload: Payload,
) -> Response {
    run(actions, move |app, _| {
        let args = Args::parse(query);
        let payload = body(payload);
        let scope = Scope::for_request(&app.runtime, &workspace, args.conversation(&payload))?;
        let id = text(&payload, "app_id");
        if id.is_empty() { return Err(Failure::new(BAD, "未选择应用", "No app selected")); }
        let file = scope.git_file(text(&payload, "path")).map_err(|error| file_error(error, BAD))?;
        let selected = app.platform.applications(&file).into_iter().find(|candidate| candidate.path.to_str() == Some(id))
            .ok_or_else(|| Failure::new(BAD, "应用不可用", "App unavailable"))?;
        app.platform.open_with(&file, &selected.path).map_err(|_| open_failed())?;
        Ok(success(None))
    }).await
}

async fn pick_folder(State(actions): State<Arc<Actions>>) -> Response {
    run(actions, move |app, en| {
        if !app.platform.has_dialogs() {
            return Err(Failure::new(BAD, "当前平台不支持系统文件夹选择", "Native folder picker is not supported on this platform"));
        }
        let prompt = if en { "Choose a workspace folder" } else { "选择工作区文件夹" };
        let picked = app.platform.pick_folder(prompt).map_err(|_| Failure::new(FAILED, "无法打开系统文件夹选择窗口", "Failed to open the native folder picker"))?;
        Ok(success(Some(json!({"path": picked.unwrap_or_default()}))))
    }).await
}

async fn open_external(State(actions): State<Arc<Actions>>, payload: Payload) -> Response {
    run(actions, move |app, _| {
        let payload = body(payload);
        let url = external_url(text(&payload, "url"))
            .ok_or_else(|| Failure::new(BAD, "无效的外部链接", "Invalid external URL"))?;
        app.platform.open_url(url.as_str()).map_err(|_| Failure::new(FAILED, "无法在系统浏览器中打开链接", "Failed to open the link in the system browser"))?;
        Ok(success(None))
    }).await
}

fn external_url(raw: &str) -> Option<reqwest::Url> {
    let (scheme, authority) = raw.split_once("://")?;
    if !["http", "https"].iter().any(|value| value.eq_ignore_ascii_case(scheme))
        || authority.starts_with(['/','?','#']) || raw.contains('\\') || raw.chars().any(|ch| ch.is_ascii_control())
    { return None; }
    let url = reqwest::Url::parse(raw).ok()?;
    url.host_str()?;
    Some(url)
}

async fn open_file(
    State(actions): State<Arc<Actions>>, Extension(workspace): Extension<RegisteredWorkspace>, query: RawQuery, payload: Payload,
) -> Response {
    run(actions, move |app, _| {
        let args = Args::parse(query);
        let payload = body(payload);
        let scope = Scope::for_request(&app.runtime, &workspace, args.conversation(&payload))?;
        let file = scope.workspace_file(text(&payload, "path"), true).map_err(|error| file_error(error, MISSING))?;
        app.platform.open_path(&file).map_err(|_| open_failed())?;
        Ok(success(Some(json!({"path": file}))))
    }).await
}

fn open_failed() -> Failure {
    Failure::new(FAILED, "打开文件失败", "Failed to open file")
}

async fn save_file(
    State(actions): State<Arc<Actions>>, Extension(workspace): Extension<RegisteredWorkspace>, query: RawQuery, payload: Payload,
) -> Response {
    run(actions, move |app, en| {
        let args = Args::parse(query);
        let payload = body(payload);
        let scope = Scope::for_request(&app.runtime, &workspace, args.conversation(&payload))?;
        let path = text(&payload, "path");
        if path.is_empty() { return Err(file_error(FileError::Invalid, MISSING)); }
        let kind = text(&payload, "kind");
        if !kind.is_empty() && !kind.eq_ignore_ascii_case("file") {
            return Err(Failure::new(BAD, "暂不支持另存文件夹", "Saving a folder is not supported yet"));
        }
        let source = scope.workspace_file(path, false).map_err(|error| file_error(error, MISSING))?;
        if !app.platform.has_dialogs() { return Err(save_error(SaveError::Unsupported)); }
        let file = open_regular(&source).map_err(|error| save_error(SaveError::Copy(error)))?;
        let data = match save_with(&*app.platform, file, &source, text(&payload, "default_name"), en).map_err(save_error)? {
            Some(path) => json!({"saved": true, "path": path}),
            None => json!({"saved": false, "cancelled": true}),
        };
        Ok(success(Some(data)))
    }).await
}

fn save_error(error: SaveError) -> Failure {
    match error {
        SaveError::Unsupported => Failure::new(BAD, "当前平台不支持系统保存窗口", "Native save dialog is not supported on this platform"),
        SaveError::Dialog(_) => Failure::new(FAILED, "无法打开系统保存窗口", "Failed to open the native save dialog"),
        SaveError::Copy(error) => Failure::new(FAILED, format!("保存失败：{error}"), format!("Failed to save file: {error}")),
    }
}

#[cfg(test)]
pub(super) use {NativeActions as TestNativeActions};
