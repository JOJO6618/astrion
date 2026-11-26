use std::{collections::BTreeSet, io, path::{Component, Path, PathBuf}};

use axum::{Extension, Json, Router, extract::{State, rejection::JsonRejection}, http::StatusCode, routing::get};
use serde_json::{Value, json};

use crate::{
    approval::ExecutionMode,
    process_runtime::policy::{DEFAULT_DENY_PATHS, DEFAULT_DENY_REGEX, WINDOWS_DENY_PATHS,
        blocking_config, blocking_patch, blocking_path, expand, read_object, strings},
    tools::read_file::ReadFile,
    workspace_catalog::RegisteredWorkspace,
};

use super::{Runtime, RuntimeError, approval::task_scope, store::{Store, write_atomically}, verify_workspace_binding};

#[derive(Clone)]
struct Config {
    runtime: Runtime,
    deploy: PathBuf,
}

impl Runtime {
    pub fn host_network_permissions_available(&self) -> bool {
        self.store().is_ok_and(|store| store.process_resources.available())
    }

    pub fn host_direct_execution_available(&self) -> bool {
        self.store().is_ok_and(|store| store.process_resources.available_mode(ExecutionMode::Direct))
    }

    pub(super) async fn read_authorized_host_path(&self, task_id: &str, args: &Value) -> Result<Option<Value>, String> {
        let Some(raw) = args["path"].as_str() else { return Ok(None); };
        let runtime = self.clone();
        let task_id = task_id.to_owned();
        let raw = raw.to_owned();
        let args = args.clone();
        tokio::task::spawn_blocking(move || {
            let reader = {
                let store = runtime.store().map_err(|error| error.message)?;
                let root = match native_path_root(&store, &task_id, &raw, false) {
                    Ok(Some(root)) => root,
                    Ok(None) => return Ok(None),
                    Err(error) => return Ok(Some(json!({"success":false,"error":error}))),
                };
                ReadFile::open(&root, &store.data_dir).map_err(|error| error.to_string())?
            };
            Ok(Some(reader.execute(&args)))
        }).await.map_err(|_| "Host file reader interrupted".to_owned())?
    }
}

/// An absolute path outside the execution root receives its own capability
/// only when the current Host policy names it as an allowed root.
pub(super) fn native_path_root(store: &Store, task_id: &str, raw: &str, write: bool) -> Result<Option<PathBuf>, String> {
    let Some(deploy) = store.process_resources.deployment() else { return Ok(None); };
    let Ok((scope, settings)) = task_scope(store, task_id) else { return Ok(None); };
    if settings.process_modes().is_none() {
        return Err("Invalid Host file authority".into());
    }
    let execution_root = scope.workspace_root();
    let path = if let Some(relative) = raw.strip_prefix("/workspace/") {
        execution_root.join(relative)
    } else {
        execution_root.join(raw)
    };
    if path.components().any(|component| matches!(component, Component::ParentDir)) {
        return Err("Path traversal is not permitted".into());
    }
    let policy = load_paths(deploy).map_err(|error| error.message)?;
    let inside = path.starts_with(execution_root);
    if forbidden_path(&path, &policy, &store.data_dir, deploy).map_err(|error| error.message)? {
        return Err("The path is protected by the Host sandbox".into());
    }
    if inside { return Ok(None); }
    if scope.execution_mode() == ExecutionMode::Direct && store.process_resources.available_mode(ExecutionMode::Direct) {
        return path.ancestors().skip(1).find(|path| path.parent().is_some() && path.is_dir())
            .map(|path| Some(path.to_owned()))
            .ok_or_else(|| "An existing directory is required for direct native file access".into());
    }
    if !cfg!(any(target_os = "macos", target_os = "linux", windows)) {
        return Err("External native file authorization is unavailable on this platform".into());
    }
    let root = store.workspaces.get(scope.principal().workspace_id()).ok_or("The workspace is not bound")?;
    let paths = authorized_host_paths(&policy, root, write)?;
    for candidate in paths {
        let root = expand(&candidate).map_err(|error| error.to_string())?;
        let Ok(resolved) = root.canonicalize() else { continue; };
        if forbidden_path(&resolved, &policy, &store.data_dir, deploy).map_err(|error| error.message)? {
            continue;
        }
        // Use the caller's matching spelling, preserving the no-follow
        // relative components enforced by the opened capability.
        let alias = if path.starts_with(&root) { &root } else if path.starts_with(&resolved) { &resolved } else { continue; };
        let suffix = path.strip_prefix(alias).expect("matching authorized root");
        if forbidden_path(&resolved.join(suffix), &policy, &store.data_dir, deploy).map_err(|error| error.message)? {
            continue;
        }
        if resolved.is_dir() { return Ok(Some(alias.clone())); }
        if suffix.as_os_str().is_empty() && resolved.is_file() && let Some(parent) = alias.parent() {
            return Ok(Some(parent.to_owned()));
        }
    }
    Err("The path is outside the authorized Host file roots".into())
}

fn authorized_host_paths(policy: &Value, root: &Path, write: bool) -> Result<Vec<String>, String> {
    let workspace = &policy["workspaces"][root.to_string_lossy().as_ref()];
    let read_paths = [
        policy.get("macos_readable_extra_paths"),
        workspace.get("readable_extra"),
    ];
    let mut paths = Vec::new();
    for entry in [policy.get("macos_writable_paths"), workspace.get("writable")].into_iter()
        .chain(read_paths.into_iter().filter(|_| !write))
    {
        paths.extend(strings(entry).map_err(|error| error.to_string())?);
    }
    if let Some(additional) = std::env::var_os("HOST_SANDBOX_MACOS_WRITABLE_PATHS") {
        let additional = additional.into_string().map_err(|_| "Invalid configured Host paths")?;
        paths.extend(additional.split(',').map(str::trim).filter(|path| !path.is_empty()).map(str::to_owned));
    }
    Ok(paths)
}

pub(super) fn scheduled_worktree_authorized(
    store: &Store,
    workspace: &Path,
    directory: &Path,
    direct: bool,
) -> Result<bool, RuntimeError> {
    let inside = directory.starts_with(workspace);
    let Some(deploy) = store.process_resources.deployment() else { return Ok(inside); };
    let policy = load_paths(deploy)?;
    if forbidden_path(directory, &policy, &store.data_dir, deploy)? {
        return Ok(false);
    }
    if inside || (direct && store.process_resources.available_mode(ExecutionMode::Direct)) {
        return Ok(true);
    }
    if !cfg!(any(target_os = "macos", target_os = "linux")) {
        return Ok(false);
    }
    for candidate in authorized_host_paths(&policy, workspace, true).map_err(RuntimeError::storage)? {
        let candidate = expand(&candidate).map_err(RuntimeError::storage)?;
        let Ok(root) = candidate.canonicalize() else { continue; };
        if directory.starts_with(&root) && !forbidden_path(&root, &policy, &store.data_dir, deploy)? {
            return Ok(true);
        }
    }
    Ok(false)
}

pub fn path_router(runtime: Runtime, deploy: PathBuf) -> Router {
    Router::new().route("/api/path-authorization", get(paths).post(set_paths).options(options))
        .with_state(Config { runtime, deploy })
}

pub fn settings_router(runtime: Runtime, resources: PathBuf) -> Router {
    Router::new()
        .route("/api/command-blocking", get(command_blocking).post(set_command_blocking).options(options))
        .with_state(runtime.clone())
        .merge(super::host_policy_setup::router(runtime, resources))
}

pub(super) type Reply = (StatusCode, Json<Value>);
pub(super) type Payload = Result<Json<Value>, JsonRejection>;

pub(super) async fn options() -> StatusCode { StatusCode::NO_CONTENT }

async fn paths(State(config): State<Config>, Extension(workspace): Extension<RegisteredWorkspace>) -> Reply {
    blocking(move || {
        let store = config.runtime.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        let policy = load_paths(&config.deploy)?;
        Ok(path_response(&policy, &workspace.path))
    }).await
}

async fn set_paths(
    State(config): State<Config>, Extension(workspace): Extension<RegisteredWorkspace>, payload: Payload,
) -> Reply {
    blocking(move || {
        let Json(body) = payload.map_err(|_| RuntimeError::bad_request("Expected a JSON object"))?;
        let scope = match body.get("scope") {
            None => "global",
            Some(value) => value.as_str().filter(|value| matches!(*value, "global" | "workspace"))
                .ok_or_else(|| RuntimeError::bad_request("Invalid path authorization scope"))?,
        };
        let mut store = config.runtime.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        let mut policy = load_paths(&config.deploy)?;
        let previous = policy.clone();
        let writable = validate_paths(&body["writable_paths"], &policy, &store.data_dir, &config.deploy)?;
        let readable = validate_paths(&body["readable_extra_paths"], &policy, &store.data_dir, &config.deploy)?;
        if scope == "global" {
            policy["macos_writable_paths"] = json!(writable);
            policy["macos_readable_extra_paths"] = json!(readable);
        } else {
            let table = policy["workspaces"].as_object_mut().expect("validated workspace settings");
            let key = workspace.path.to_str().ok_or_else(|| RuntimeError::bad_request("Invalid workspace path"))?;
            if writable.is_empty() && readable.is_empty() {
                table.shift_remove(key);
            } else {
                let mut entry = table.get(key).cloned().filter(Value::is_object).unwrap_or_else(|| json!({}));
                entry["writable"] = json!(writable);
                entry["readable_extra"] = json!(readable);
                table.insert(key.into(), entry);
            }
        }
        if policy != previous {
            write_atomically(&config.deploy.join("host_sandbox_policy.json"), &policy)?;
            store.process_resources.close_terminals((scope == "workspace").then_some(workspace.id.as_str()), None);
        }
        let mut response = path_response(&policy, &workspace.path);
        response["scope"] = json!(scope);
        Ok(response)
    }).await
}

pub(super) fn load_paths(deploy: &Path) -> Result<Value, RuntimeError> {
    let mut policy = read_object(&deploy.join("host_sandbox_policy.json")).map_err(RuntimeError::storage)?;
    for (field, fallback) in [
        ("macos_writable_paths", json!([])),
        ("macos_readable_extra_paths", json!([])),
        ("macos_deny_read_paths", json!(DEFAULT_DENY_PATHS)),
        ("macos_deny_read_regexes", json!([DEFAULT_DENY_REGEX])),
        ("windows_deny_read_paths", json!(WINDOWS_DENY_PATHS)),
    ] {
        if policy.get(field).is_none() {
            policy[field] = fallback;
        } else {
            strings(policy.get(field)).map_err(RuntimeError::storage)?;
        }
    }
    if policy.get("workspaces").is_none() { policy["workspaces"] = json!({}); }
    if !policy["workspaces"].is_object() {
        return Err(RuntimeError::storage("Invalid workspace path authorization"));
    }
    Ok(policy)
}

fn path_response(policy: &Value, workspace: &Path) -> Value {
    let entry = &policy["workspaces"][workspace.to_string_lossy().as_ref()];
    json!({"success":true,"enabled":true,"writable_paths":policy["macos_writable_paths"],
        "readable_extra_paths":policy["macos_readable_extra_paths"],"deny_read_paths":policy["macos_deny_read_paths"],
        "deny_read_regexes":policy["macos_deny_read_regexes"],"windows_deny_read_paths":policy["windows_deny_read_paths"],
        "workspace_path":workspace,"workspace_writable_paths":entry["writable"].as_array().cloned().unwrap_or_default(),
        "workspace_readable_extra_paths":entry["readable_extra"].as_array().cloned().unwrap_or_default()})
}

fn validate_paths(value: &Value, policy: &Value, data: &Path, deploy: &Path) -> Result<Vec<String>, RuntimeError> {
    let list = value.as_array().filter(|list| list.len() <= 500).ok_or_else(|| RuntimeError::bad_request("Paths must be an array"))?;
    let mut unique = BTreeSet::new();
    let mut result = Vec::new();
    for item in list {
        let raw = item.as_str().ok_or_else(|| RuntimeError::bad_request("Paths must be strings"))?.trim();
        if raw.is_empty() { continue; }
        if raw.len() > 4096 || raw.chars().any(char::is_control) {
            return Err(RuntimeError::bad_request("Invalid authorized path"));
        }
        let path = expand(raw).map_err(|_| RuntimeError::bad_request("An authorized path must be an absolute non-root path"))?;
        let resolved = match path.canonicalize() {
            Ok(path) if path.parent().is_some() => Some(path),
            Ok(_) => return Err(RuntimeError::bad_request("A filesystem root cannot be authorized")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(RuntimeError::storage(error)),
        };
        for path in std::iter::once(&path).chain(resolved.as_ref()) {
            if forbidden_path(path, policy, data, deploy)? {
                return Err(RuntimeError::bad_request("A sensitive path cannot be authorized"));
            }
        }
        if unique.insert(raw.to_owned()) { result.push(raw.to_owned()); }
    }
    Ok(result)
}

pub(super) fn forbidden_path(path: &Path, policy: &Value, data: &Path, deploy: &Path) -> Result<bool, RuntimeError> {
    if path.starts_with(data) || path.starts_with(deploy) {
        return Ok(true);
    }
    let text = path.to_string_lossy().replace('\\', "/");
    let normalized = text.to_lowercase();
    let mut blocked = strings(policy.get("macos_deny_read_paths")).map_err(RuntimeError::storage)?;
    blocked.extend(DEFAULT_DENY_PATHS.iter().map(|path| (*path).to_owned()));
    if cfg!(windows) {
        blocked.extend(strings(policy.get("windows_deny_read_paths")).map_err(RuntimeError::storage)?);
        blocked.extend(["SystemRoot", "ProgramFiles", "ProgramFiles(x86)"].into_iter().filter_map(|key| std::env::var(key).ok()));
    }
    for raw in blocked {
        let root = expand(&raw).map_err(RuntimeError::storage)?;
        let root = root.to_string_lossy().replace('\\', "/").to_lowercase();
        let root = root.trim_end_matches('/');
        if normalized == root || normalized.strip_prefix(root).is_some_and(|tail| tail.starts_with('/')) {
            return Ok(true);
        }
    }
    if let Some(leaf) = normalized.rsplit('/').next()
        && (leaf == ".env" || leaf.starts_with(".env."))
    {
        return Ok(true);
    }
    for regex in strings(policy.get("macos_deny_read_regexes")).map_err(RuntimeError::storage)? {
        if regex != DEFAULT_DENY_REGEX && regex_matches(&regex, &text)? { return Ok(true); }
    }
    Ok(false)
}

#[cfg(unix)]
fn regex_matches(pattern: &str, text: &str) -> Result<bool, RuntimeError> {
    use std::{ffi::CString, mem::MaybeUninit};
    let pattern = CString::new(pattern).map_err(RuntimeError::storage)?;
    let text = CString::new(text).map_err(RuntimeError::storage)?;
    let mut regex = MaybeUninit::<libc::regex_t>::uninit();
    // REG_EXTENDED handles ordinary deployed deny rules, while invalid rules
    // fail closed rather than widening native authorization.
    if unsafe { libc::regcomp(regex.as_mut_ptr(), pattern.as_ptr(), libc::REG_EXTENDED | libc::REG_NOSUB) } != 0 {
        return Err(RuntimeError::storage("Invalid path deny expression"));
    }
    let result = unsafe { libc::regexec(regex.as_ptr(), text.as_ptr(), 0, std::ptr::null_mut(), 0) };
    unsafe { libc::regfree(regex.as_mut_ptr()); }
    match result {
        0 => Ok(true),
        libc::REG_NOMATCH => Ok(false),
        _ => Err(RuntimeError::storage("Path deny expression could not be applied")),
    }
}

#[cfg(not(unix))]
fn regex_matches(_: &str, _: &str) -> Result<bool, RuntimeError> {
    Err(RuntimeError::storage("Custom path deny expressions require a supported sandbox platform"))
}

async fn command_blocking(State(runtime): State<Runtime>) -> Reply {
    blocking(move || {
        let store = runtime.store()?;
        let config = blocking_config(&store.data_dir).map_err(RuntimeError::storage)?;
        Ok(blocking_reply(config))
    }).await
}

async fn set_command_blocking(State(runtime): State<Runtime>, payload: Payload) -> Reply {
    blocking(move || {
        let Json(body) = payload.map_err(|_| RuntimeError::bad_request("Expected a JSON object"))?;
        let patch = blocking_patch(&body).map_err(|error| RuntimeError::bad_request(error.to_string()))?;
        let store = runtime.store()?;
        let mut config = blocking_config(&store.data_dir).map_err(RuntimeError::storage)?;
        config.as_object_mut().expect("validated command policy").extend(patch);
        write_atomically(&blocking_path(&store.data_dir).map_err(RuntimeError::storage)?, &config)?;
        Ok(blocking_reply(config))
    }).await
}

fn blocking_reply(mut config: Value) -> Value {
    let recommended: Value = serde_json::from_str(include_str!("../../../config/command_blocking_recommended.json"))
        .expect("valid recommended command blocking rules");
    config["success"] = json!(true);
    config["recommended_rules"] = recommended["rules"].clone();
    config
}

pub(super) async fn blocking(run: impl FnOnce() -> Result<Value, RuntimeError> + Send + 'static) -> Reply {
    match tokio::task::spawn_blocking(run).await {
        Ok(Ok(value)) => (StatusCode::OK, Json(value)),
        Ok(Err(error)) => failure(error),
        Err(_) => failure(RuntimeError::new(500, "Host settings interrupted")),
    }
}

pub(super) fn failure(error: RuntimeError) -> Reply {
    let status = StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    (status, Json(json!({"success":false,"error":error.message})))
}
