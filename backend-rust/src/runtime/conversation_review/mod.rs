mod lines;
pub(super) mod agent_tools;

use super::{Runtime, RuntimeError, verify_workspace_binding};
use crate::{
    catalog::CatalogSource, knowledge::storage, web_assets::personalization::english,
    web_status::admin_policy, workspace_catalog::RegisteredWorkspace,
};
use axum::{
    Extension, Json, Router,
    extract::{Path, Query, State, rejection::JsonRejection},
    http::StatusCode,
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::io;
use time::OffsetDateTime;
use uuid::Uuid;

type Reply = (StatusCode, Json<Value>);
type App = (Runtime, CatalogSource);

pub fn router(runtime: Runtime, catalog: CatalogSource) -> Router {
    Router::new()
        .route("/api/conversations/{id}/review_preview", get(preview))
        .route("/api/conversations/{id}/review", post(export))
        .with_state((runtime, catalog))
}

#[derive(Deserialize)]
struct PreviewQuery {
    limit: Option<String>,
    content_mode: Option<String>,
}

async fn preview(
    State(app): State<App>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
    Query(query): Query<PreviewQuery>,
) -> Reply {
    let limit = query
        .limit
        .and_then(|s| s.trim().parse().ok())
        .filter(|n| *n != 0)
        .unwrap_or(20);
    review(app, ws, id, query.content_mode.as_deref(), Some(limit)).await
}

async fn export(
    State(app): State<App>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
    body: Result<Json<Value>, JsonRejection>,
) -> Reply {
    let body = body.ok().map(|j| j.0).unwrap_or(Value::Null);
    review(app, ws, id, body["content_mode"].as_str(), None).await
}

async fn review(
    (runtime, catalog): App,
    ws: RegisteredWorkspace,
    id: String,
    mode: Option<&str>,
    limit: Option<i64>,
) -> Reply {
    let full = mode.is_some_and(|m| m.trim().eq_ignore_ascii_case("full"));
    let result = tokio::task::spawn_blocking(move || -> Result<Reply, RuntimeError> {
        let data = catalog.host_data_dir();
        let en = english(data);
        if admin_policy(data, &catalog.load()).map_err(RuntimeError::storage)?["ui_blocks"]["block_conversation_review"] == true {
            let error = if en { "Conversation review has been disabled by the admin" } else { "对话引用已被管理员禁用" };
            return Ok((StatusCode::FORBIDDEN, Json(json!({"success": false, "error":error}))));
        }
        let source = {
            let store = runtime.store()?;
            verify_workspace_binding(&store, &ws.id, Some(&ws.path))?;
            store.read(&ws.id, &id).and_then(|doc| {
                super::goals::verify_conversation_root(&ws.path, doc.raw())
                    .map_err(|_| RuntimeError::session_not_found())?;
                Ok(doc)
            })
        };
        let doc = match source {
            Ok(doc) => doc,
            Err(e) if matches!(e.status, 400 | 404) => {
                let message = if en { format!("Conversation {id} not found") } else { format!("对话 {id} 不存在") };
                return Ok((StatusCode::NOT_FOUND, Json(json!({"success":false,"error":"Conversation not found","message":message}))));
            }
            Err(e) => return Err(e),
        };
        let lines = lines::build(&doc.raw()["messages"], full, limit);
        let payload = if limit.is_some() {
            json!({"count":lines.len(), "preview":lines})
        } else {
            let body = lines.join("\n") + "\n";
            let annotation = match (en, full) {
                (true, true) => "Review mode: full record (including tool calls and results)",
                (true, false) => "Review mode: clean dialogue (only actual user/assistant messages)",
                (false, true) => "回顾模式：完整记录（含工具调用与结果）",
                (false, false) => "回顾模式：纯净对话（仅含用户与助手的实际对话内容）",
            };
            let folder = {
                let store = runtime.store()?;
                verify_workspace_binding(&store, &ws.id, Some(&ws.path))?;
                storage::open_canonical_dir(&ws.path).map_err(RuntimeError::storage)?
            };
            let folder = storage::ensure_dir(&folder, ".astrion").and_then(|root| storage::ensure_dir(&root, "review")).map_err(RuntimeError::storage)?;
            let title = safe_title(doc.raw()["title"].as_str().unwrap_or_default());
            let now = OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc());
            let stem = format!("review_{title}_{:04}{:02}{:02}{:02}{:02}{:02}", now.year(), u8::from(now.month()), now.day(), now.hour(), now.minute(), now.second());
            let mut name = format!("{stem}.md");
            let markdown = format!("> {annotation}\n\n{body}");
            if let Err(error) = storage::publish(&folder, &name, markdown.as_bytes(), false) {
                if error.kind() != io::ErrorKind::AlreadyExists { return Err(RuntimeError::storage(error)); }
                name = format!("{stem}_{}.md", Uuid::new_v4().simple());
                storage::publish(&folder, &name, markdown.as_bytes(), false).map_err(RuntimeError::storage)?;
            }
            json!({"path":format!(".astrion/review/{name}"),"char_count":body.chars().count(),"content_mode":if full {"full"} else {"dialogue"}})
        };
        Ok((StatusCode::OK, Json(json!({"success":true,"data":payload}))))
    }).await;
    match result {
        Ok(Ok(reply)) => reply,
        Ok(Err(e)) => (
            StatusCode::from_u16(e.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            Json(json!({"success":false,"error":e.message})),
        ),
        Err(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"success":false,"error":"Review interrupted"})),
        ),
    }
}

fn safe_title(title: &str) -> String {
    let mut result = String::new();
    let mut replacing = false;
    for c in title.trim().chars() {
        let invalid = "\\/:*?\"<>|".contains(c) || c == '\0';
        if !invalid {
            result.push(c);
        } else if !replacing {
            result.push('_');
        }
        replacing = invalid;
    }
    if result.is_empty() {
        "untitled".into()
    } else {
        result
    }
}
