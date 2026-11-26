//! Host browser preferences in the original REST and DATA_DIR formats.

use std::{
    collections::{BTreeMap, BTreeSet},
    io::{self, ErrorKind},
    path::{Path, PathBuf},
    sync::Arc,
};

use axum::{
    Extension, Json, Router,
    extract::{Query, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
};
use cap_std::fs::Dir;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::{
    runtime::Runtime,
    web_auth::{BrowserSession, WebAuth},
    workspace_catalog::HostCatalog,
};

use super::{
    browser_retention as retention, browser_sites as sites,
    storage::{open_canonical_dir, optional_dir, read_bytes},
    web::{failure, workspace_header},
};

pub use retention::{BrowserKind, BrowserObservation, BrowserScope};

const UNSUPPORTED: &str = "内置浏览器工具仅在 macOS/Windows 桌面版 Agent 对话中可用";
const STORAGE_UNAVAILABLE: &str = "无法访问浏览器偏好。";

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BrowserPending {
    On,
    Off,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct BrowserMode {
    pub enabled: bool,
    pub pending: Option<BrowserPending>,
}

impl BrowserMode {
    pub fn from_metadata(metadata: &Value) -> Self {
        let pending = match metadata["pending_browser_tools"].as_str() {
            Some("on") => Some(BrowserPending::On),
            Some("off") => Some(BrowserPending::Off),
            _ => None,
        };
        Self {
            enabled: metadata["browser_tools_enabled"] == true,
            pending,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub enum BrowserError {
    Unavailable,
    NotFound,
    Unsupported,
    NoWorkspace,
    UnknownWorkspace,
    InvalidRequest(&'static str),
    UncreatedConversation,
}

impl BrowserError {
    fn response(self) -> Response {
        match self {
            Self::Unavailable => failure(StatusCode::SERVICE_UNAVAILABLE, STORAGE_UNAVAILABLE),
            Self::NotFound => failure(StatusCode::NOT_FOUND, "对话不存在"),
            Self::Unsupported => failure(StatusCode::BAD_REQUEST, UNSUPPORTED),
            Self::NoWorkspace => {
                Json(json!({"success": false, "code": "no_workspace", "error": "未选择工作区"}))
                    .into_response()
            }
            Self::UnknownWorkspace => failure(StatusCode::NOT_FOUND, "工作区不存在"),
            Self::InvalidRequest(message) => failure(StatusCode::BAD_REQUEST, message),
            Self::UncreatedConversation => failure(StatusCode::CONFLICT, "对话尚未创建"),
        }
    }
}

/// Adapter for the authenticated Host runtime. Once native execution exists, `change_mode`
/// must check activity and update the owning document under the shared task gate. A running
/// task receives a pending switch for the tool-batch boundary; `release_pages` handles off.
pub trait BrowserRuntime: Send + Sync {
    fn supported(&self) -> bool {
        false
    }

    fn mode(&self, workspace: &str, conversation: &str) -> Result<BrowserMode, BrowserError>;

    fn observe(
        &self,
        owners: &[BrowserScope],
    ) -> Result<BTreeMap<BrowserScope, BrowserObservation>, BrowserError>;

    fn change_mode(
        &self,
        _workspace: &str,
        _conversation: &str,
        _enabled: bool,
    ) -> Result<Option<BrowserPending>, BrowserError> {
        Err(BrowserError::Unsupported)
    }

    fn release_pages(&self, _workspace: &str, _conversation: &str) -> Result<(), BrowserError> {
        Err(BrowserError::Unavailable)
    }
}

#[derive(Clone)]
pub struct BrowserPreferences(Arc<Preferences>);

struct Preferences {
    data: Dir,
    workspaces: WorkspaceSource,
    runtime: Arc<dyn BrowserRuntime>,
}

#[derive(Clone)]
struct WorkspaceIndex {
    default: Option<String>,
    registered: BTreeSet<String>,
}

enum WorkspaceSource {
    Fixed(WorkspaceIndex),
    Host(Arc<dyn Fn() -> io::Result<HostCatalog> + Send + Sync>),
}

impl BrowserPreferences {
    /// Mount with the outer Host Web authentication layer. Resolve workspace identities
    /// from the live Host catalog without binding execution tools for preference queries.
    pub fn host_router(
        data_dir: &Path,
        runtime: Runtime,
        catalog: impl Fn() -> io::Result<HostCatalog> + Send + Sync + 'static,
    ) -> io::Result<Router> {
        Ok(
            Self::with_runtime(data_dir, WorkspaceSource::Host(Arc::new(catalog)), runtime)?
                .routes(),
        )
    }

    /// Bind to the existing runtime for passive queries. Native browser tools are selectable
    /// when the caller supplies a native execution adapter through `with_backend`.
    pub fn for_runtime(
        data_dir: &Path,
        default_workspace: &str,
        workspaces: Vec<String>,
        runtime: Runtime,
    ) -> io::Result<Self> {
        Self::with_runtime(
            data_dir,
            fixed_workspaces(default_workspace, workspaces),
            runtime,
        )
    }

    fn with_runtime(
        data_dir: &Path,
        workspaces: WorkspaceSource,
        runtime: Runtime,
    ) -> io::Result<Self> {
        let data = open_canonical_dir(&data_dir.canonicalize()?)?;
        let backend = Arc::new(RuntimeQueries {
            runtime,
            data: data.try_clone()?,
            data_path: data_dir.canonicalize()?,
        });
        Ok(Self(Arc::new(Preferences {
            data,
            workspaces,
            runtime: backend,
        })))
    }

    pub fn with_backend(
        data_dir: &Path,
        default_workspace: &str,
        workspaces: Vec<String>,
        runtime: Arc<dyn BrowserRuntime>,
    ) -> io::Result<Self> {
        let data = open_canonical_dir(&data_dir.canonicalize()?)?;
        let workspaces = fixed_workspaces(default_workspace, workspaces);
        Ok(Self(Arc::new(Preferences {
            data,
            workspaces,
            runtime,
        })))
    }

    /// Original Host endpoints, guarded by browser session and CSRF middleware.
    pub fn router(&self, auth: &WebAuth) -> Router {
        auth.protect_api(self.routes())
    }

    fn routes(&self) -> Router {
        Router::new()
            .route("/api/browser-retention", get(load_retention))
            .route("/api/browser-tools", get(load_tools).post(save_tools))
            .route(
                "/api/browser-site-permissions",
                get(load_sites).delete(remove_site),
            )
            .with_state(self.clone())
    }
}

fn fixed_workspaces(default: &str, workspaces: Vec<String>) -> WorkspaceSource {
    let default = valid_id(default, 256).then(|| default.to_owned());
    let registered = workspaces
        .into_iter()
        .filter(|id| valid_id(id, 256))
        .chain(default.iter().cloned())
        .collect();
    WorkspaceSource::Fixed(WorkspaceIndex {
        default,
        registered,
    })
}

impl WorkspaceSource {
    fn read(&self) -> Result<WorkspaceIndex, BrowserError> {
        match self {
            Self::Fixed(index) => Ok(index.clone()),
            Self::Host(load) => {
                let catalog = load().map_err(|_| BrowserError::Unavailable)?;
                Ok(WorkspaceIndex {
                    default: catalog.default_workspace_id,
                    registered: catalog
                        .workspaces
                        .into_iter()
                        .map(|workspace| workspace.id)
                        .collect(),
                })
            }
        }
    }

    fn resolve(
        &self,
        browser: &BrowserSession,
        headers: &HeaderMap,
        query: &ScopeQuery,
        body: &Value,
    ) -> Result<String, BrowserError> {
        let header = workspace_header(headers)
            .filter(|_| headers.get_all("x-astrion-workspace-id").iter().count() <= 1)
            .ok_or(BrowserError::InvalidRequest("Invalid workspace header"))?;
        let body_id = match &body["workspace_id"] {
            Value::Null => None,
            Value::String(id) => Some(id.as_str()),
            _ => return Err(BrowserError::InvalidRequest("Invalid workspace identity")),
        };
        let mut requested = [Some(header), query.workspace_id.as_deref(), body_id]
            .into_iter()
            .flatten()
            .map(str::trim)
            .filter(|id| !id.is_empty());
        let explicit = requested.next();
        if requested.any(|id| Some(id) != explicit) {
            return Err(BrowserError::InvalidRequest(
                "Conflicting workspace identity",
            ));
        }
        let index = self.read()?;
        if let Some(id) = explicit {
            return index
                .registered
                .get(id)
                .cloned()
                .ok_or(BrowserError::UnknownWorkspace);
        }
        browser
            .selected_workspace_id()
            .filter(|id| index.registered.contains(id))
            .or(index.default)
            .ok_or(BrowserError::NoWorkspace)
    }
}

impl WorkspaceIndex {
    fn permits(&self, scope: &BrowserScope) -> bool {
        match scope.0 {
            BrowserKind::Agent => self.registered.contains(&scope.1),
            BrowserKind::Chat => scope.1.is_empty(),
        }
    }
}

#[derive(Default, Deserialize)]
struct ScopeQuery {
    workspace_id: Option<String>,
    conversation_id: Option<String>,
}

async fn load_tools(
    State(state): State<BrowserPreferences>,
    Extension(browser): Extension<BrowserSession>,
    headers: HeaderMap,
    Query(query): Query<ScopeQuery>,
) -> Response {
    run(move || {
        let workspace = state.0.workspaces.resolve(&browser, &headers, &query, &Value::Null)?;
        let supplied = query.conversation_id.as_deref().unwrap_or_default().trim();
        let mode = if supplied.is_empty() {
            BrowserMode::default()
        } else {
            let conversation = conversation(supplied).ok_or(BrowserError::NotFound)?;
            state.0.runtime.mode(&workspace, &conversation)?
        };
        Ok(json!({"success": true, "supported": state.0.runtime.supported(), "enabled": mode.enabled, "pending": mode.pending}))
    }).await
}

async fn save_tools(
    State(state): State<BrowserPreferences>,
    Extension(browser): Extension<BrowserSession>,
    headers: HeaderMap,
    Query(query): Query<ScopeQuery>,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    let payload = body.map(|Json(payload)| payload).unwrap_or_default();
    run(move || {
        let workspace = state
            .0
            .workspaces
            .resolve(&browser, &headers, &query, &payload)?;
        if !state.0.runtime.supported() {
            return Err(BrowserError::Unsupported);
        }
        let enabled = payload["enabled"]
            .as_bool()
            .ok_or(BrowserError::InvalidRequest("enabled 必须是布尔值"))?;
        let raw = query
            .conversation_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .or_else(|| payload["conversation_id"].as_str())
            .unwrap_or_default()
            .trim();
        if raw.is_empty() {
            return Err(BrowserError::UncreatedConversation);
        }
        let conversation = conversation(raw).ok_or(BrowserError::NotFound)?;
        let pending = state
            .0
            .runtime
            .change_mode(&workspace, &conversation, enabled)?;
        let mut reply = json!({"success": true, "enabled": enabled, "pending": pending});
        if !enabled
            && state
                .0
                .runtime
                .release_pages(&workspace, &conversation)
                .is_err()
        {
            reply["release_failed"] = json!(true);
        }
        Ok(reply)
    })
    .await
}

async fn load_retention(
    State(state): State<BrowserPreferences>,
    Extension(_browser): Extension<BrowserSession>,
    headers: HeaderMap,
) -> Response {
    let mut state_headers = headers.get_all("x-astrion-browser-state").iter();
    let groups = state_headers
        .next()
        .filter(|_| state_headers.next().is_none())
        .and_then(|value| value.to_str().ok())
        .and_then(retention::parse);
    let Some(groups) = groups else {
        return failure(StatusCode::BAD_REQUEST, "invalid_browser_retention_state");
    };
    run(move || {
        let workspaces = state.0.workspaces.read()?;
        let owners: Vec<BrowserScope> = groups.keys().filter(|scope| workspaces.permits(scope)).cloned().collect();
        let observations = state.0.runtime.observe(&owners)?;
        let decision = retention::decide(groups, &observations);
        Ok(json!({"success": true, "username": "host", "deleted": decision.deleted, "release": decision.release}))
    }).await
}

async fn load_sites(
    State(state): State<BrowserPreferences>,
    Extension(browser): Extension<BrowserSession>,
    headers: HeaderMap,
    Query(query): Query<ScopeQuery>,
) -> Response {
    run(move || {
        state
            .0
            .workspaces
            .resolve(&browser, &headers, &query, &Value::Null)?;
        let sites = sites::list(&state.0.data).map_err(|_| BrowserError::Unavailable)?;
        Ok(json!({"success": true, "supported": state.0.runtime.supported(), "sites": sites}))
    })
    .await
}

async fn remove_site(
    State(state): State<BrowserPreferences>,
    Extension(browser): Extension<BrowserSession>,
    headers: HeaderMap,
    Query(query): Query<ScopeQuery>,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    let payload = body.map(|Json(value)| value).unwrap_or_default();
    run(move || {
        state
            .0
            .workspaces
            .resolve(&browser, &headers, &query, &payload)?;
        let site = payload["site"].as_str().unwrap_or_default().trim();
        if !sites::valid_site(site) {
            return Err(BrowserError::InvalidRequest("站点无效"));
        }
        let (removed, sites) =
            sites::revoke(&state.0.data, site).map_err(|_| BrowserError::Unavailable)?;
        Ok(json!({"success": true, "removed": removed, "sites": sites}))
    })
    .await
}

async fn run(action: impl FnOnce() -> Result<Value, BrowserError> + Send + 'static) -> Response {
    match tokio::task::spawn_blocking(action).await {
        Ok(Ok(value)) => Json(value).into_response(),
        Ok(Err(error)) => error.response(),
        Err(_) => BrowserError::Unavailable.response(),
    }
}

fn valid_id(value: &str, max: usize) -> bool {
    !value.is_empty()
        && value.len() <= max
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn conversation(raw: &str) -> Option<String> {
    let id = if raw.starts_with("conv_") {
        raw.into()
    } else {
        format!("conv_{raw}")
    };
    (id.len() > "conv_".len() && valid_id(&id, 185)).then_some(id)
}

struct RuntimeQueries {
    runtime: Runtime,
    data: Dir,
    data_path: PathBuf,
}

impl BrowserRuntime for RuntimeQueries {
    fn supported(&self) -> bool {
        self.runtime.desktop_browser_supported()
    }

    fn change_mode(
        &self,
        workspace: &str,
        conversation: &str,
        enabled: bool,
    ) -> Result<Option<BrowserPending>, BrowserError> {
        self.runtime
            .change_browser_tools(workspace, conversation, enabled)
    }

    fn release_pages(&self, workspace: &str, conversation: &str) -> Result<(), BrowserError> {
        self.runtime.release_browser_tools(workspace, conversation)
    }

    fn mode(&self, workspace: &str, conversation: &str) -> Result<BrowserMode, BrowserError> {
        let metadata = self
            .runtime
            .web_metadata(workspace, conversation)
            .map_err(|error| {
                if matches!(error.status, 400 | 404) {
                    BrowserError::NotFound
                } else {
                    BrowserError::Unavailable
                }
            })?;
        Ok(BrowserMode::from_metadata(&metadata))
    }

    fn observe(
        &self,
        owners: &[BrowserScope],
    ) -> Result<BTreeMap<BrowserScope, BrowserObservation>, BrowserError> {
        let open = self.open_scopes().map_err(|_| BrowserError::Unavailable)?;
        let tasks = self
            .runtime
            .navigation_runs()
            .map_err(|_| BrowserError::Unavailable)?;
        let running: BTreeSet<BrowserScope> = tasks
            .into_iter()
            .filter(|task| task.running)
            .map(|task| {
                BrowserScope(
                    if task.workspace_id.is_empty() {
                        BrowserKind::Chat
                    } else {
                        BrowserKind::Agent
                    },
                    task.workspace_id,
                    task.conversation_id,
                )
            })
            .collect();
        let mut directories = BTreeMap::new();
        Ok(owners
            .iter()
            .map(|scope| {
                let BrowserScope(kind, workspace, id) = scope;
                let exists = if !id.starts_with("conv_") {
                    Some(true)
                } else if id == "conv_" || !valid_id(id, 185) {
                    None
                } else {
                    let dir = directories
                        .entry((*kind, workspace.as_str()))
                        .or_insert_with(|| self.conversation_dir(*kind, workspace));
                    match dir {
                        Ok(Some(dir)) => {
                            let path = if *kind == BrowserKind::Agent {
                                self.data_path.join("conversations").join(workspace)
                            } else {
                                self.data_path.join("chat/conversations")
                            };
                            database_conversation(dir, &path, id)
                                .unwrap_or_else(|| conversation_exists(dir, id))
                        }
                        Ok(None) => Some(false),
                        Err(_) => None,
                    }
                };
                (
                    scope.clone(),
                    BrowserObservation {
                        exists,
                        open: open.contains(scope),
                        running: running.contains(scope),
                    },
                )
            })
            .collect())
    }
}

impl RuntimeQueries {
    fn conversation_dir(&self, kind: BrowserKind, workspace: &str) -> io::Result<Option<Dir>> {
        let path = match kind {
            BrowserKind::Agent => ["conversations", workspace],
            BrowserKind::Chat => ["chat", "conversations"],
        };
        let Some(parent) = optional_dir(&self.data, path[0])? else {
            return Ok(None);
        };
        optional_dir(&parent, path[1])
    }

    fn open_scopes(&self) -> io::Result<BTreeSet<BrowserScope>> {
        let bytes = match read_bytes(&self.data, "conversation_tabs.json", 8 * 1024 * 1024) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(BTreeSet::new()),
            Err(error) => return Err(error),
        };
        let document: Value = serde_json::from_slice(&bytes)?;
        Ok(document["host"]["tabs"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|tab| {
                if !matches!(tab["kind"].as_str(), Some("conv" | "new")) {
                    return None;
                }
                let kind = match tab["conversation_kind"].as_str() {
                    None | Some("agent") => BrowserKind::Agent,
                    Some("chat") => BrowserKind::Chat,
                    _ => return None,
                };
                let workspace = tab["workspace_id"].as_str().unwrap_or_default();
                let conversation = tab["conversation_id"]
                    .as_str()
                    .filter(|value| !value.is_empty())
                    .or_else(|| tab["key"].as_str())?;
                Some(BrowserScope(kind, workspace.into(), conversation.into()))
            })
            .collect())
    }
}

fn database_conversation(dir: &Dir, root: &Path, id: &str) -> Option<Option<bool>> {
    let metadata = dir
        .symlink_metadata(crate::conversation_db::FILENAME)
        .ok()?;
    if !metadata.is_file() {
        return Some(None);
    }
    let Ok(database) = crate::conversation_db::Database::open(root) else {
        return Some(None);
    };
    match (database.contains(id), database.imported(id)) {
        (Ok(true), _) => Some(Some(true)),
        (Ok(false), Ok(true)) => Some(Some(false)),
        (Ok(false), Ok(false)) => None,
        _ => Some(None),
    }
}

fn conversation_exists(dir: &Dir, id: &str) -> Option<bool> {
    match dir.symlink_metadata(format!("{id}.json")) {
        Ok(metadata) if metadata.is_file() => Some(true),
        Err(error) if error.kind() == ErrorKind::NotFound => Some(false),
        _ => None,
    }
}

pub(crate) fn authorized_sites(data: &Path) -> io::Result<Vec<String>> {
    let data = open_canonical_dir(&data.canonicalize()?)?;
    Ok(sites::list(&data)?
        .into_iter()
        .filter_map(|row| row["site"].as_str().map(str::to_owned))
        .collect())
}

pub(crate) fn remember_site(data: &Path, site: &str) -> io::Result<()> {
    let data = open_canonical_dir(&data.canonicalize()?)?;
    sites::grant(&data, site)
}
