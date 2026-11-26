//! Project Git queries and review admission for one trusted workspace. Mount
//! under the host's authentication and CSRF layer.

mod discovery;
mod history;
mod review_tools;
mod reviews;
pub(crate) mod runner;
mod workspace;

pub use workspace::workspace_router;

use crate::{
    catalog::CatalogSource,
    runtime::{ModelConfig, Runtime},
};
pub use review_tools::ReviewTools;

use std::{
    ffi::OsStr,
    io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use axum::{
    Extension, Json, Router,
    extract::{
        FromRequestParts, Path as RoutePath, Query,
        rejection::{JsonRejection, QueryRejection},
    },
    http::{StatusCode, request::Parts},
    routing::get,
};
use serde::Deserialize;
use serde_json::{Value, json};

use self::discovery::{Discovery, Repository};

#[derive(Clone)]
pub struct ProjectGit {
    workspace: Arc<Workspace>,
    reviews: Option<Arc<reviews::ReviewRuntime>>,
}

struct Workspace {
    id: String,
    label: String,
    root: PathBuf,
    data_dir: PathBuf,
    discovery: Mutex<Option<Discovery>>,
}

type Reply = (StatusCode, Json<Value>);
type ParsedQuery = Result<Query<Params>, QueryRejection>;
pub(crate) type GitResult<T> = Result<T, GitError>;

#[derive(Debug)]
pub(crate) struct GitError(pub(crate) String);

impl std::fmt::Display for GitError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for GitError {}

impl From<&str> for GitError {
    fn from(message: &str) -> Self {
        Self(message.to_owned())
    }
}

impl From<io::Error> for GitError {
    fn from(error: io::Error) -> Self {
        Self(error.to_string())
    }
}

impl ProjectGit {
    pub fn new(
        workspace_id: impl Into<String>,
        label: Option<String>,
        workspace_root: impl AsRef<Path>,
        data_dir: impl AsRef<Path>,
    ) -> io::Result<Self> {
        let id = workspace_id.into();
        // The same scope key identifies its legacy conversation directory.
        if id.is_empty() || Path::new(&id).file_name() != Some(OsStr::new(&id)) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Invalid workspace ID",
            ));
        }
        let label = label
            .filter(|label| !label.is_empty())
            .unwrap_or_else(|| id.clone());
        Ok(Self {
            workspace: Arc::new(Workspace {
                id,
                label,
                root: canonical_directory(workspace_root.as_ref())?,
                data_dir: canonical_directory(data_dir.as_ref())?,
                discovery: Mutex::new(None),
            }),
            reviews: None,
        })
    }

    /// Enables review creation and status lookup using the bound Host runtime
    /// and its trusted model configuration.
    pub fn with_runtime(
        mut self,
        runtime: Runtime,
        catalog: CatalogSource,
        fixed_model: Option<ModelConfig>,
    ) -> Self {
        self.reviews = Some(Arc::new(reviews::ReviewRuntime::new(
            runtime,
            catalog,
            fixed_model,
        )));
        self
    }

    pub fn router(self) -> Router {
        Self::router_with_scope().layer(Extension(self))
    }

    /// Mount under middleware that authenticates the requester and inserts the
    /// selected workspace's `ProjectGit` into request extensions.
    pub fn router_with_scope() -> Router {
        Router::new()
            .route("/api/project-git/repositories", get(repositories))
            .route("/api/project-git/{repo_id}/branches", get(branches))
            .route("/api/project-git/{repo_id}/history", get(history))
            .route("/api/project-git/{repo_id}/comparison", get(comparison))
            .route("/api/project-git/{repo_id}/diff", get(diff))
            .route(
                "/api/project-git/{repo_id}/reviews",
                get(reviews).post(create_review),
            )
    }
}

struct Scope(ProjectGit);

impl<S: Send + Sync> FromRequestParts<S> for Scope {
    type Rejection = Reply;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        parts
            .extensions
            .get::<ProjectGit>()
            .cloned()
            .map(Self)
            .ok_or_else(|| error(StatusCode::FORBIDDEN, "工作区不存在或无权访问。"))
    }
}

fn canonical_directory(path: &Path) -> io::Result<PathBuf> {
    let path = canonicalize(path)?;
    if !path.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::NotADirectory,
            "Expected a directory",
        ));
    }
    Ok(path)
}

pub(crate) fn canonicalize(path: &Path) -> io::Result<PathBuf> {
    let path = path.canonicalize()?;
    // Keep the same Windows path spelling as the legacy workspace/repository IDs.
    #[cfg(windows)]
    if let Some(path) = path.to_str() {
        if let Some(share) = path.strip_prefix(r"\\?\UNC\") {
            return Ok(PathBuf::from(format!(r"\\{share}")));
        }
        if let Some(disk) = path
            .strip_prefix(r"\\?\")
            .filter(|disk| disk.as_bytes().get(1) == Some(&b':'))
        {
            return Ok(PathBuf::from(disk));
        }
    }
    Ok(path)
}

fn error(status: StatusCode, message: &str) -> Reply {
    (status, Json(json!({"success": false, "error": message})))
}

#[derive(Default, Deserialize)]
struct Params {
    workspace_id: Option<String>,
    #[serde(default)]
    refresh: String,
    #[serde(default, rename = "ref")]
    reference: String,
    #[serde(default)]
    search: String,
    #[serde(default)]
    sha: String,
    #[serde(default)]
    parent: String,
    #[serde(default)]
    base: String,
    head: Option<String>,
    #[serde(default)]
    path: String,
    offset: Option<String>,
    context: Option<String>,
}

impl Params {
    fn compare(&self, repo: &Repository) -> GitResult<history::Comparison> {
        history::compare(
            repo,
            &self.sha,
            &self.parent,
            &self.base,
            self.head.as_deref().unwrap_or("HEAD"),
        )
    }
}

fn int_arg(value: Option<&str>, default: usize, maximum: usize) -> GitResult<usize> {
    let Some(value) = value else {
        return Ok(default);
    };
    let value = value.trim();
    let digits = value.strip_prefix(['+', '-']).unwrap_or(value);
    if digits.is_empty() || !digits.bytes().all(|digit| digit.is_ascii_digit()) {
        return Err("无效的分页参数。".into());
    }
    if value.starts_with('-') {
        return Ok(0);
    }
    Ok(digits.bytes().fold(0usize, |number, digit| {
        (number * 10 + usize::from(digit - b'0')).min(maximum)
    }))
}

async fn scoped_query(
    app: ProjectGit,
    query: ParsedQuery,
    run: impl FnOnce(ProjectGit, Params) -> GitResult<Value> + Send + 'static,
) -> Reply {
    let Ok(Query(params)) = query else {
        return error(StatusCode::BAD_REQUEST, "无效的查询参数。");
    };
    if let Err(reply) = assert_scope(&app, params.workspace_id.as_deref()) {
        return reply;
    }
    match tokio::task::spawn_blocking(move || run(app, params)).await {
        Ok(Ok(data)) => (StatusCode::OK, Json(json!({"success": true, "data": data}))),
        Ok(Err(GitError(message))) => error(StatusCode::BAD_REQUEST, &message),
        Err(_) => error(StatusCode::INTERNAL_SERVER_ERROR, "Git 查询失败。"),
    }
}

fn assert_scope(app: &ProjectGit, workspace_id: Option<&str>) -> Result<(), Reply> {
    match workspace_id {
        None | Some("") => Err(error(StatusCode::BAD_REQUEST, "缺少工作区身份。")),
        Some(id) if id != app.workspace.id => {
            Err(error(StatusCode::BAD_REQUEST, "工作区不存在或无权访问。"))
        }
        _ => Ok(()),
    }
}

async fn repo_query(
    app: ProjectGit,
    repo_id: String,
    query: ParsedQuery,
    run: impl FnOnce(&ProjectGit, &Repository, &Params) -> GitResult<Value> + Send + 'static,
) -> Reply {
    scoped_query(app, query, move |app, params| {
        let repo = app.workspace.repository(&repo_id)?;
        run(&app, &repo, &params)
    })
    .await
}

async fn repositories(Scope(app): Scope, query: ParsedQuery) -> Reply {
    scoped_query(app, query, |app, params| {
        app.workspace.repositories(params.refresh == "1")
    })
    .await
}

async fn branches(
    Scope(app): Scope,
    RoutePath(id): RoutePath<String>,
    query: ParsedQuery,
) -> Reply {
    repo_query(app, id, query, |_, repo, _| history::branches(repo)).await
}

async fn history(Scope(app): Scope, RoutePath(id): RoutePath<String>, query: ParsedQuery) -> Reply {
    repo_query(app, id, query, |_, repo, params| {
        history::history(
            repo,
            &params.reference,
            &params.search,
            int_arg(params.offset.as_deref(), 0, 1_000_000)?,
        )
    })
    .await
}

async fn comparison(
    Scope(app): Scope,
    RoutePath(id): RoutePath<String>,
    query: ParsedQuery,
) -> Reply {
    repo_query(app, id, query, |_, repo, params| {
        let compare = params.compare(repo)?;
        history::comparison_summary(repo, compare)
    })
    .await
}

async fn diff(Scope(app): Scope, RoutePath(id): RoutePath<String>, query: ParsedQuery) -> Reply {
    repo_query(app, id, query, |_, repo, params| {
        let compare = params.compare(repo)?;
        history::file_diff(
            repo,
            &compare,
            &params.path,
            int_arg(params.offset.as_deref(), 0, 10_000_000)?,
            int_arg(params.context.as_deref(), 3, 100)?,
        )
    })
    .await
}

async fn reviews(Scope(app): Scope, RoutePath(id): RoutePath<String>, query: ParsedQuery) -> Reply {
    repo_query(app, id, query, |app, repo, _| reviews::list(app, &repo.id)).await
}

async fn create_review(
    Scope(app): Scope,
    RoutePath(repo_id): RoutePath<String>,
    query: ParsedQuery,
    body: Result<Json<reviews::ReviewRequest>, JsonRejection>,
) -> Reply {
    if app.reviews.is_none() {
        return error(StatusCode::METHOD_NOT_ALLOWED, "该工作区未启用评审启动。");
    }
    let Ok(Query(query)) = query else {
        return error(StatusCode::BAD_REQUEST, "无效的查询参数。");
    };
    let Ok(Json(body)) = body else {
        return error(
            StatusCode::BAD_REQUEST,
            "请求参数必须是对象，提交和分支参数必须是字符串。",
        );
    };
    if let Err(reply) = assert_scope(
        &app,
        query
            .workspace_id
            .as_deref()
            .filter(|id| !id.is_empty())
            .or(body.workspace_id.as_deref()),
    ) {
        return reply;
    }
    if body
        .workspace_id
        .as_deref()
        .is_some_and(|id| id != app.workspace.id)
    {
        return error(StatusCode::BAD_REQUEST, "工作区不存在或无权访问。");
    }
    match tokio::task::spawn_blocking(move || reviews::launch(&app, &repo_id, body)).await {
        Ok(Ok(data)) => (
            StatusCode::ACCEPTED,
            Json(json!({"success": true, "data": data})),
        ),
        Ok(Err(failure)) => error(
            StatusCode::from_u16(failure.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            &failure.message,
        ),
        Err(_) => error(StatusCode::INTERNAL_SERVER_ERROR, "评审启动失败。"),
    }
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/project_git_routes.rs"]
mod tests;
