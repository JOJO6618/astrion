//! Read the Git state of a workspace or its conversation-bound checkout.

mod checkout;
mod patch;

use std::{collections::HashMap, path::Path};

use axum::{
    Extension, Json, Router,
    extract::{RawQuery, State},
    http::StatusCode,
    routing::get,
};
use serde_json::{Value, json};

use crate::{runtime::Runtime, workspace_catalog::RegisteredWorkspace};

use self::{checkout::Checkout, patch::DiffOptions};
use super::{Reply, canonicalize, error};

const BINDING_UNAVAILABLE: &str = "工作树执行目录已失效，请先返回主目录或选择有效工作树。";
const BINDING_UNAUTHORIZED: &str =
    "工作树执行目录位于工作区外且未记录授权，请先返回主目录或选择有效工作树。";
const GIT_UNAVAILABLE: &str = "工作树 Git 记录已失效，请返回主目录或选择有效工作树。";
const CONVERSATION_MISSING: &str = "找不到当前工作区中的对话。";

/// Mount under browser authentication and a scope that resolves the trusted
/// `RegisteredWorkspace`. An empty catalog is handled by this router.
pub fn workspace_router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/project/git-summary", get(summary))
        .route("/api/project/git-diff", get(diff))
        .with_state(runtime)
}

async fn summary(
    State(runtime): State<Runtime>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    RawQuery(query): RawQuery,
) -> Reply {
    respond(runtime, workspace, query, false).await
}

async fn diff(
    State(runtime): State<Runtime>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    RawQuery(query): RawQuery,
) -> Reply {
    respond(runtime, workspace, query, true).await
}

async fn respond(
    runtime: Runtime,
    workspace: Option<Extension<RegisteredWorkspace>>,
    query: Option<String>,
    diff: bool,
) -> Reply {
    let Some(Extension(workspace)) = workspace else {
        return (
            StatusCode::OK,
            Json(json!({
                "success": false, "code": "no_workspace", "error": "No workspace is selected",
            })),
        );
    };
    match tokio::task::spawn_blocking(move || read(&runtime, &workspace, query.as_deref(), diff))
        .await
    {
        Ok(Ok(data)) => (StatusCode::OK, Json(json!({"success": true, "data": data}))),
        Ok(Err(message)) => error(StatusCode::BAD_REQUEST, message),
        Err(_) => error(StatusCode::INTERNAL_SERVER_ERROR, "Git 查询被中断。"),
    }
}

fn read(
    runtime: &Runtime,
    workspace: &RegisteredWorkspace,
    query: Option<&str>,
    diff: bool,
) -> Result<Value, &'static str> {
    let mut params = HashMap::new();
    for (key, value) in
        serde_urlencoded::from_str::<Vec<(String, String)>>(query.unwrap_or_default())
            .unwrap_or_default()
    {
        // Flask's MultiDict uses the first value for a repeated parameter.
        params.entry(key).or_insert(value);
    }
    let metadata = match params.get("conversation_id").filter(|id| !id.is_empty()) {
        Some(id) => runtime
            .web_metadata(&workspace.id, id)
            .map_err(|_| CONVERSATION_MISSING)?,
        None => Value::Null,
    };
    let enabled = metadata["git_worktree_enabled"] == true;
    let binding = &metadata["git_worktree"];
    let workspace_root = canonicalize(&workspace.path).unwrap_or_else(|_| workspace.path.clone());
    let unavailable = |message| {
        enabled
            .then(|| unavailable_binding(&workspace_root, binding, message))
            .flatten()
    };
    let execution = match execution_root(&workspace_root, enabled, binding) {
        Ok(path) => path,
        Err(message) if !diff => return unavailable(message).ok_or(message),
        Err(message) => return Err(message),
    };
    let Some(checkout) = Checkout::discover(&workspace_root, execution) else {
        return Ok(if diff {
            json!({"has_git": false, "files": []})
        } else {
            unavailable(GIT_UNAVAILABLE).unwrap_or_else(|| json!({"has_git": false}))
        });
    };
    let branch = checkout.branch();
    let upstream = checkout.upstream();
    let changes = checkout.changes();
    let (additions, deletions) = changes.totals();
    let mut data = json!({
        "has_git": true, "project_path": checkout.root().to_string_lossy(),
        "branch": branch, "upstream": upstream, "additions": additions, "deletions": deletions,
    });
    if diff {
        let options = DiffOptions::from_query(&params);
        data["project_name"] = json!(name(checkout.root()));
        data["context"] = json!(options.context);
        data["files"] = json!(patch::files(&checkout, &changes, &options));
    } else {
        data["project_name"] = json!(name(&workspace_root));
        data["git_worktree_enabled"] = json!(enabled);
        data["git_worktree"] = binding.clone();
    }
    Ok(data)
}

fn execution_root<'a>(
    workspace: &'a Path,
    enabled: bool,
    binding: &'a Value,
) -> Result<&'a Path, &'static str> {
    if !enabled {
        return Ok(workspace);
    }
    let directory = match &binding["directory"] {
        Value::Null => workspace,
        Value::String(path) if path.is_empty() => workspace,
        Value::String(path) => Path::new(path),
        _ => return Err(BINDING_UNAVAILABLE),
    };
    if !directory.is_absolute()
        || !directory.is_dir()
        || !canonicalize(directory).is_ok_and(|path| path == directory)
    {
        return Err(BINDING_UNAVAILABLE);
    }
    if !directory.starts_with(workspace) {
        let granted = binding["grant"]["path"]
            .as_str()
            .map(str::trim)
            .filter(|path| !path.is_empty());
        if granted
            .and_then(|path| canonicalize(Path::new(path)).ok())
            .as_deref()
            != Some(directory)
        {
            return Err(BINDING_UNAUTHORIZED);
        }
    }
    Ok(directory)
}

fn unavailable_binding(workspace: &Path, binding: &Value, message: &str) -> Option<Value> {
    if binding["active"] != true {
        return None;
    }
    let branch = binding["branch"]
        .as_str()
        .filter(|branch| !branch.is_empty())
        .unwrap_or("HEAD");
    Some(json!({
        "has_git": true, "project_name": name(workspace), "project_path": binding["directory"],
        "branch": branch, "git_worktree_enabled": true, "git_worktree": binding, "binding_error": message,
    }))
}

fn name(path: &Path) -> std::borrow::Cow<'_, str> {
    path.file_name().unwrap_or_default().to_string_lossy()
}

#[cfg(test)]
#[path = "../../../test/2026-11-26_RustGit工作区概览/workspace_git_contract.rs"]
mod tests;
