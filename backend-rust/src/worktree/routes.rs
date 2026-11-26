use std::{
    fs::File,
    io::{self, BufReader},
    path::{Path, PathBuf},
    sync::Arc,
};

use axum::{
    Extension, Json, Router,
    extract::{Query, State, rejection::JsonRejection},
    http::StatusCode,
    routing::get,
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::{
    runtime::{Runtime, RuntimeError},
    workspace_catalog::RegisteredWorkspace,
};

use super::Target;

#[derive(Clone)]
struct App {
    runtime: Runtime,
    data_dir: Arc<PathBuf>,
}

type Reply = (StatusCode, Json<Value>);

/// Mount behind browser authentication and trusted RegisteredWorkspace scope middleware.
pub fn router_with_scope(runtime: Runtime, data_dir: PathBuf) -> Router {
    Router::new()
        .route("/api/git-worktree", get(view).post(change))
        .route("/api/git-worktree/inspect", axum::routing::post(inspect))
        .with_state(App {
            runtime,
            data_dir: Arc::new(data_dir),
        })
}

#[derive(Deserialize)]
struct ViewQuery {
    conversation_id: Option<String>,
}

async fn view(
    State(app): State<App>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    Query(query): Query<ViewQuery>,
) -> Reply {
    run(move || {
        let enabled = if query.conversation_id.is_none() {
            default_enabled(&app.data_dir)?
        } else {
            false
        };
        let data =
            app.runtime
                .worktree_view(&workspace.id, query.conversation_id.as_deref(), enabled)?;
        Ok(json!({"success":true,"data":data}))
    })
    .await
}

#[derive(Deserialize)]
struct Inspect {
    path: String,
}

async fn inspect(
    State(app): State<App>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    body: Result<Json<Inspect>, JsonRejection>,
) -> Reply {
    run(move || {
        let Json(payload) = body.map_err(|_| invalid("必须提供工作树路径。"))?;
        if payload.path.trim().is_empty() {
            return Err(invalid("必须提供工作树路径。"));
        }
        let data = app
            .runtime
            .inspect_worktree(&workspace.id, Path::new(payload.path.trim()))?;
        Ok(json!({"success":true,"data":data}))
    })
    .await
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum Action {
    Enter,
    Exit,
}

#[derive(Deserialize)]
struct Change {
    conversation_id: String,
    action: Action,
    target: Option<Target>,
}

async fn change(
    State(app): State<App>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    body: Result<Json<Change>, JsonRejection>,
) -> Reply {
    run(move || {
        let Json(payload) = body.map_err(|_| invalid("必须提供对话身份和有效的工作树操作。"))?;
        match (&payload.action, &payload.target) {
            (Action::Enter, Some(_)) | (Action::Exit, None) => {
                app.runtime
                    .change_worktree(&workspace.id, &payload.conversation_id, payload.target)
            }
            _ => Err(invalid("必须明确选择进入工作树或返回主目录。")),
        }
    })
    .await
}

fn default_enabled(data: &Path) -> Result<bool, RuntimeError> {
    fn storage(error: impl std::fmt::Display) -> RuntimeError {
        RuntimeError {
            status: 503,
            message: format!("无法读取工作区个性化：{error}"),
        }
    }
    let path = data.join("personalization.json");
    match File::open(path) {
        Ok(file) => serde_json::from_reader::<_, Value>(BufReader::new(file))
            .map(|doc| doc["git_worktree_enabled"] == true)
            .map_err(storage),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(storage(error)),
    }
}

fn invalid(message: impl Into<String>) -> RuntimeError {
    RuntimeError {
        status: 400,
        message: message.into(),
    }
}

async fn run(operation: impl FnOnce() -> Result<Value, RuntimeError> + Send + 'static) -> Reply {
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(body)) => (StatusCode::OK, Json(body)),
        Ok(Err(error)) => (
            StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            Json(json!({"success":false,"error":error.message})),
        ),
        Err(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"success":false,"error":"Git 工作树操作中断。"})),
        ),
    }
}
