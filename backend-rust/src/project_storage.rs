//! Read-only storage usage for the workspace selected by an authenticated Host browser.

use crate::{
    web_assets::personalization::english,
    web_auth::BrowserSession,
    workspace_catalog::{HostCatalog, RegisteredWorkspace},
};
use axum::{
    Extension, Json, Router,
    extract::{RawQuery, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode},
    routing::get,
};
use cap_fs_ext::DirExt;
use cap_std::{ambient_authority, fs::Dir};
use serde::Serialize;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    env, fs, io,
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
    time::Instant,
};

const DEFAULT_LIMIT_MB: i64 = 2048;
const DEFAULT_CACHE_TTL: f64 = 30.0;
const LIMIT_VAR: &str = "PROJECT_MAX_STORAGE_MB";
const TTL_VAR: &str = "PROJECT_STORAGE_CACHE_TTL";

#[derive(Clone)]
pub struct ProjectStorage(Arc<Storage>);

struct Storage {
    registry: PathBuf,
    resources: PathBuf,
    data: PathBuf,
    limit_mb: i64,
    ttl_seconds: f64,
    cache: Mutex<HashMap<String, Cached>>,
}

#[derive(Clone)]
struct Cached {
    at: Instant,
    root: PathBuf,
    data: Usage,
}

#[derive(Clone, Serialize)]
struct Usage {
    used_bytes: u64,
    limit_bytes: Option<i128>,
    limit_label: String,
    usage_percent: Option<f64>,
}

impl ProjectStorage {
    pub fn from_env(registry: PathBuf, resources: PathBuf, data: PathBuf) -> io::Result<Self> {
        let root = env::var_os("ASTRION_DATA_ROOT")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if data.ends_with("host/data") {
                    data.parent()
                        .and_then(Path::parent)
                        .unwrap_or(&data)
                        .to_owned()
                } else {
                    data.clone()
                }
            });
        let settings: Value = fs::read(root.join("settings.json"))
            .ok()
            .and_then(|raw| serde_json::from_slice(&raw).ok())
            .unwrap_or_default();
        let limit_mb = configured(
            LIMIT_VAR,
            &settings,
            Some(&settings["terminal"]["project_max_storage_mb"]),
            DEFAULT_LIMIT_MB,
        )?;
        let ttl_seconds = configured(TTL_VAR, &settings, None, DEFAULT_CACHE_TTL)?;
        Ok(Self(Arc::new(Storage {
            registry,
            resources,
            data,
            limit_mb,
            ttl_seconds,
            cache: Mutex::default(),
        })))
    }

    /// Mount behind the entry point's browser-session API guard.
    pub fn router(&self) -> Router {
        Router::new()
            .route("/api/project-storage", get(project_storage))
            .with_state(self.0.clone())
    }
}

fn configured<T: std::str::FromStr>(
    name: &str,
    settings: &Value,
    legacy: Option<&Value>,
    default: T,
) -> io::Result<T> {
    let value = match env::var(name) {
        Ok(value) => Some(value),
        Err(env::VarError::NotPresent) => {
            let from_settings = &settings["env_vars"][name];
            let value = legacy
                .filter(|value| !value.is_null())
                .or_else(|| match from_settings {
                    Value::Null | Value::Bool(false) => None,
                    Value::Number(value) if value.as_f64() == Some(0.0) => None,
                    Value::String(value) if value.is_empty() => None,
                    Value::Array(value) if value.is_empty() => None,
                    Value::Object(value) if value.is_empty() => None,
                    _ => Some(from_settings),
                });
            value.map(|value| {
                value
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| value.to_string())
            })
        }
        Err(error) => return Err(io::Error::new(io::ErrorKind::InvalidInput, error)),
    };
    match value {
        None => Ok(default),
        Some(value) => value
            .trim()
            .parse()
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, format!("Invalid {name}"))),
    }
}

type Reply = (StatusCode, Json<Value>);

async fn project_storage(
    State(storage): State<Arc<Storage>>,
    Extension(browser): Extension<BrowserSession>,
    RawQuery(query): RawQuery,
    headers: HeaderMap,
    body: Result<Json<Value>, JsonRejection>,
) -> Reply {
    let selected = browser.selected_workspace_id();
    let explicit = requested_workspace(
        query.as_deref(),
        &headers,
        body.ok().map(|Json(value)| value),
    );
    tokio::task::spawn_blocking(move || storage.lookup(explicit.as_deref(), selected.as_deref()))
        .await
        .unwrap_or_else(|_| {
            failure(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Workspace storage lookup interrupted",
            )
        })
}

fn requested_workspace(
    query: Option<&str>,
    headers: &HeaderMap,
    body: Option<Value>,
) -> Option<String> {
    let query = serde_urlencoded::from_str::<Vec<(String, String)>>(query.unwrap_or_default())
        .unwrap_or_default()
        .into_iter()
        .find_map(|(name, value)| (name == "workspace_id").then_some(value));
    let header = headers
        .get("x-astrion-workspace-id")
        .and_then(|value| value.to_str().ok());
    let value = query
        .as_deref()
        .filter(|value| !value.is_empty())
        .or_else(|| header.filter(|value| !value.is_empty()))
        .map(str::to_owned)
        .or_else(|| match body.as_ref()?.get("workspace_id")? {
            Value::String(value) => Some(value.clone()),
            Value::Number(value) if value.as_f64() != Some(0.0) => Some(value.to_string()),
            Value::Bool(true) => Some("True".into()),
            _ => None,
        })?;
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

impl Storage {
    fn lookup(&self, explicit: Option<&str>, selected: Option<&str>) -> Reply {
        let catalog = match HostCatalog::load(&self.registry, &self.resources) {
            Ok(catalog) => catalog.unwrap_or_default(),
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::InvalidData | io::ErrorKind::UnexpectedEof
                ) =>
            {
                HostCatalog::default()
            }
            Err(error) => return failure(StatusCode::SERVICE_UNAVAILABLE, error.to_string()),
        };
        let Some(default) = catalog.default_workspace() else {
            let message = if english(&self.data) {
                "No workspace has been created yet. Please create one manually in the workspace management UI."
            } else {
                "尚未创建任何工作区，请先在工作区管理界面中手动创建工作区"
            };
            return (
                StatusCode::OK,
                Json(json!({"success":false,"error":message,"code":"no_workspace"})),
            );
        };
        let workspace = match explicit {
            Some(id) => match catalog.by_id(id) {
                Some(workspace) => workspace,
                None => {
                    return failure(
                        StatusCode::NOT_FOUND,
                        if english(&self.data) {
                            "Workspace not found"
                        } else {
                            "工作区不存在"
                        },
                    );
                }
            },
            None => selected.and_then(|id| catalog.by_id(id)).unwrap_or(default),
        };
        self.measure(workspace, directory_size)
    }

    fn cached(&self, workspace: &RegisteredWorkspace) -> Option<Cached> {
        self.cache
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .get(&workspace.id)
            .filter(|entry| entry.root == workspace.path)
            .cloned()
    }

    fn measure(
        &self,
        workspace: &RegisteredWorkspace,
        scan: impl FnOnce(&Path) -> io::Result<u64>,
    ) -> Reply {
        let now = Instant::now();
        if let Some(entry) = self.cached(workspace)
            && now.saturating_duration_since(entry.at).as_secs_f64() < self.ttl_seconds
        {
            return success(entry.data);
        }
        let used_bytes = match scan(&workspace.path) {
            Ok(used_bytes) => used_bytes,
            Err(error) => {
                return if let Some(stale) = self.cached(workspace) {
                    (
                        StatusCode::OK,
                        Json(json!({"success":true,"data":stale.data,"stale":true})),
                    )
                } else {
                    failure(StatusCode::INTERNAL_SERVER_ERROR, error.to_string())
                };
            }
        };
        let limit_bytes = (self.limit_mb != 0).then_some(i128::from(self.limit_mb) * 1024 * 1024);
        let data = Usage {
            used_bytes,
            limit_bytes,
            limit_label: limit_bytes
                .map_or_else(|| "未限制".into(), |_| format!("{}MB", self.limit_mb)),
            usage_percent: limit_bytes.map(|limit| used_bytes as f64 / limit as f64 * 100.0),
        };
        let mut cache = self.cache.lock().unwrap_or_else(PoisonError::into_inner);
        if cache.get(&workspace.id).is_none_or(|entry| entry.at <= now) {
            cache.insert(
                workspace.id.clone(),
                Cached {
                    at: now,
                    root: workspace.path.clone(),
                    data: data.clone(),
                },
            );
        }
        success(data)
    }
}

fn success(data: Usage) -> Reply {
    (StatusCode::OK, Json(json!({"success":true,"data":data})))
}

fn failure(status: StatusCode, error: impl Into<String>) -> Reply {
    (status, Json(json!({"success":false,"error":error.into()})))
}

fn missing(error: &io::Error) -> bool {
    if matches!(
        error.kind(),
        io::ErrorKind::NotFound | io::ErrorKind::NotADirectory
    ) {
        return true;
    }
    #[cfg(unix)]
    if error.raw_os_error() == Some(libc::ELOOP) {
        return true;
    }
    #[cfg(windows)]
    if error.raw_os_error() == Some(windows_sys::Win32::Foundation::ERROR_STOPPED_ON_SYMLINK as i32)
    {
        return true;
    }
    false
}

fn directory_size(root: &Path) -> io::Result<u64> {
    let root_dir = match Dir::open_ambient_dir(root, ambient_authority()) {
        Ok(directory) => directory,
        Err(error) if missing(&error) => return Ok(0),
        Err(error) => return Err(error),
    };
    let mut bytes = 0_u64;
    let Some(entries) = open_entries(&root_dir)? else {
        return Ok(0);
    };
    let mut stack = vec![(PathBuf::new(), root_dir.try_clone()?, entries)];
    while let Some((prefix, dir, entries)) = stack.last_mut() {
        let Some(entry) = entries.next() else {
            stack.pop();
            continue;
        };
        let entry = match entry {
            Ok(entry) => entry,
            // pathlib skips directories it cannot enumerate.
            Err(error) if error.kind() == io::ErrorKind::PermissionDenied => {
                stack.pop();
                continue;
            }
            Err(error) if missing(&error) => continue,
            Err(error) => return Err(error),
        };
        let kind = match entry.file_type() {
            Ok(kind) => kind,
            Err(error) if missing(&error) => continue,
            Err(error) => return Err(error),
        };
        if kind.is_dir() {
            let name = entry.file_name();
            let child = match dir.open_dir_nofollow(&name) {
                Ok(child) => child,
                Err(error)
                    if missing(&error) || error.kind() == io::ErrorKind::PermissionDenied =>
                {
                    continue;
                }
                Err(error) => return Err(error),
            };
            if let Some(entries) = open_entries(&child)? {
                let path = prefix.join(name);
                stack.push((path, child, entries));
            }
            continue;
        }
        let metadata = if kind.is_symlink() {
            link_file(&root_dir, root, prefix.join(entry.file_name()))
        } else if kind.is_file() {
            entry.metadata().map(Some)
        } else {
            continue;
        };
        match metadata {
            Ok(Some(info)) if info.is_file() => {
                bytes = bytes
                    .checked_add(info.len())
                    .ok_or_else(|| io::Error::other("Workspace storage size overflow"))?;
            }
            Ok(_) => {}
            Err(error) if missing(&error) => {}
            Err(error) => return Err(error),
        }
    }
    Ok(bytes)
}

fn link_file(
    dir: &Dir,
    root: &Path,
    mut target: PathBuf,
) -> io::Result<Option<cap_std::fs::Metadata>> {
    // Resolve each hop through the registered root. Absolute links inside it are
    // allowed. Even a nested link must stay inside before its target is inspected.
    'resolve: for _ in 0..=40 {
        let mut relative = PathBuf::new();
        let mut parts = target.components();
        while let Some(part) = parts.next() {
            match part {
                Component::CurDir => continue,
                Component::ParentDir => {
                    if !relative.pop() {
                        return Ok(None);
                    }
                    continue;
                }
                Component::Normal(name) => relative.push(name),
                _ => return Ok(None),
            }
            let info = dir.symlink_metadata(&relative)?;
            if info.is_symlink() {
                let contents = dir.read_link_contents(&relative)?;
                relative.pop();
                let mut next = if contents.is_absolute() {
                    let Some(tail) = absolute_link_tail(root, &contents) else {
                        return Ok(None);
                    };
                    tail
                } else {
                    relative.join(contents)
                };
                if !parts.as_path().as_os_str().is_empty() {
                    next.push(parts.as_path());
                }
                target = next;
                continue 'resolve;
            }
            if !info.is_dir() {
                return Ok(parts.as_path().as_os_str().is_empty().then_some(info));
            }
        }
        return Ok(None);
    }
    Ok(None)
}

fn absolute_link_tail(root: &Path, target: &Path) -> Option<PathBuf> {
    if let Ok(path) = target.strip_prefix(root) {
        return Some(path.to_owned());
    }
    #[cfg(windows)]
    {
        fn standard_path(path: &Path) -> Option<PathBuf> {
            let path = path.to_str()?;
            Some(match path.strip_prefix(r"\\?\UNC\") {
                Some(path) => PathBuf::from(format!(r"\\{path}")),
                None => PathBuf::from(path.strip_prefix(r"\\?\").unwrap_or(path)),
            })
        }
        return standard_path(target)?
            .strip_prefix(standard_path(root)?)
            .ok()
            .map(Path::to_owned);
    }
    #[cfg(not(windows))]
    None
}

fn open_entries(dir: &Dir) -> io::Result<Option<cap_std::fs::ReadDir>> {
    match dir.entries() {
        Ok(entries) => Ok(Some(entries)),
        Err(error) if missing(&error) || error.kind() == io::ErrorKind::PermissionDenied => {
            Ok(None)
        }
        Err(error) => Err(error),
    }
}

#[cfg(test)]
#[path = "../../test/2026-11-25_Rust项目存储/project_storage_tests.rs"]
mod tests;
