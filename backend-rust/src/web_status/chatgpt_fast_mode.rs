//! Conversation-local ChatGPT Fast preference for the authenticated Host Web UI.

use crate::{
    catalog::CatalogSource,
    runtime::{Runtime, RuntimeError},
    web_assets::personalization::english,
    web_auth::BrowserSession,
    workspace_catalog::HostCatalog,
};
use axum::{
    Extension, Json, Router,
    extract::{RawQuery, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode},
    routing::post,
};
use serde_json::{Value, json};
use std::{io, sync::Arc};

struct FastMode {
    runtime: Runtime,
    catalog: CatalogSource,
    load_workspaces: Box<dyn Fn() -> io::Result<HostCatalog> + Send + Sync>,
}

type Reply = (StatusCode, Json<Value>);

/// Mount behind the Host Web browser-session API guard. This route resolves its
/// workspace from query, header, or JSON body, in that order.
pub fn chatgpt_fast_mode_router(
    runtime: Runtime,
    catalog: CatalogSource,
    load_workspaces: impl Fn() -> io::Result<HostCatalog> + Send + Sync + 'static,
) -> Router {
    Router::new()
        .route("/api/chatgpt-fast-mode", post(write_chatgpt_fast_mode))
        .with_state(Arc::new(FastMode {
            runtime,
            catalog,
            load_workspaces: Box::new(load_workspaces),
        }))
}

async fn write_chatgpt_fast_mode(
    State(app): State<Arc<FastMode>>,
    Extension(browser): Extension<BrowserSession>,
    RawQuery(query): RawQuery,
    headers: HeaderMap,
    payload: Result<Json<Value>, JsonRejection>,
) -> Reply {
    let query =
        serde_urlencoded::from_str::<Vec<(String, String)>>(query.as_deref().unwrap_or_default())
            .unwrap_or_default();
    let body = payload.map(|Json(value)| value).unwrap_or_default();
    let requested = requested_workspace(&query, &headers, &body);
    let selected = browser.selected_workspace_id();
    let view = parameter(&query, "conversation_id").map(str::to_owned);
    tokio::task::spawn_blocking(move || {
        app.update(
            requested.as_deref(),
            selected.as_deref(),
            view.as_deref(),
            &body,
        )
    })
    .await
    .unwrap_or_else(|_| {
        failure(
            StatusCode::SERVICE_UNAVAILABLE,
            "Workspace view is unavailable",
        )
    })
}

fn parameter<'a>(query: &'a [(String, String)], key: &str) -> Option<&'a str> {
    query
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, value)| value.as_str())
}

fn requested_workspace(
    query: &[(String, String)],
    headers: &HeaderMap,
    body: &Value,
) -> Option<String> {
    let header = || {
        headers
            .get("x-astrion-workspace-id")
            .and_then(|value| value.to_str().ok())
    };
    parameter(query, "workspace_id")
        .filter(|value| !value.is_empty())
        .or_else(|| header().filter(|value| !value.is_empty()))
        .map(str::to_owned)
        .or_else(|| match &body["workspace_id"] {
            Value::String(value) => Some(value.clone()),
            Value::Number(value) if value.as_f64() != Some(0.0) => Some(value.to_string()),
            Value::Bool(true) => Some("True".into()),
            _ => None,
        })
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

impl FastMode {
    fn update(
        &self,
        requested: Option<&str>,
        selected: Option<&str>,
        view: Option<&str>,
        body: &Value,
    ) -> Reply {
        let workspaces = match (self.load_workspaces)() {
            Ok(workspaces) => workspaces,
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::InvalidData | io::ErrorKind::UnexpectedEof
                ) =>
            {
                HostCatalog::default()
            }
            Err(error) => {
                return (
                    StatusCode::SERVICE_UNAVAILABLE,
                    Json(json!({"success":false,"code":"resource_busy","error":error.to_string()})),
                );
            }
        };
        let Some(default) = workspaces.default_workspace() else {
            let error = if english(self.catalog.host_data_dir()) {
                "No workspace has been created yet. Please create one manually in the workspace management UI."
            } else {
                "尚未创建任何工作区，请先在工作区管理界面中手动创建工作区"
            };
            return (
                StatusCode::OK,
                Json(json!({"success":false,"code":"no_workspace","error":error})),
            );
        };
        let workspace = match requested {
            Some(id) => match workspaces.by_id(id) {
                Some(workspace) => workspace,
                None => {
                    return failure(
                        StatusCode::NOT_FOUND,
                        if english(self.catalog.host_data_dir()) {
                            "Workspace not found"
                        } else {
                            "工作区不存在"
                        },
                    );
                }
            },
            None => selected
                .and_then(|id| workspaces.by_id(id))
                .unwrap_or(default),
        };
        if !body.is_object() {
            return failure(StatusCode::BAD_REQUEST, "Invalid settings");
        }
        let Some(enabled) = body["enabled"].as_bool() else {
            return failure(
                StatusCode::BAD_REQUEST,
                "ChatGPT fast mode must be a boolean",
            );
        };
        let not_found = || failure(StatusCode::NOT_FOUND, "Conversation not found");
        let Some(id) = body["conversation_id"]
            .as_str()
            .filter(|id| valid_conversation_id(id))
        else {
            return not_found();
        };
        if let Some(view) = view.map(str::trim).filter(|view| !view.is_empty())
            && view.strip_prefix("conv_").unwrap_or(view) != &id["conv_".len()..]
        {
            return not_found();
        }

        let catalog = self.catalog.load();
        let invalid = |message| RuntimeError {
            status: 400,
            message,
        };
        let result = self
            .runtime
            .try_update_idle_web_metadata(&workspace.id, id, |metadata| {
                let model = match metadata.get("model_key") {
                    None | Some(Value::Null) => None,
                    Some(Value::String(model)) => Some(model.as_str()),
                    _ => return Err(invalid("Invalid model key".into())),
                };
                let supported = catalog.supports_chatgpt_fast_mode(model).map_err(invalid)?;
                if enabled && !supported {
                    return Err(invalid(
                        "Selected ChatGPT model does not support Fast mode".into(),
                    ));
                }
                metadata.insert("chatgpt_fast_mode".into(), json!(enabled));
                Ok(())
            });
        match result {
            Ok(()) => (
                StatusCode::OK,
                Json(json!({"success":true,"data":{"chatgpt_fast_mode":enabled}})),
            ),
            Err(error) if error.status == 400 => failure(StatusCode::BAD_REQUEST, error.message),
            Err(error) if error.status == 404 => not_found(),
            Err(error) if error.status == 409 => failure(StatusCode::CONFLICT, error.message),
            Err(_) => failure(
                StatusCode::SERVICE_UNAVAILABLE,
                "Conversation storage is unavailable",
            ),
        }
    }
}

fn valid_conversation_id(id: &str) -> bool {
    id.strip_prefix("conv_").is_some_and(|suffix| {
        !suffix.is_empty()
            && suffix
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    })
}

fn failure(status: StatusCode, message: impl Into<String>) -> Reply {
    (
        status,
        Json(json!({"success":false,"error":message.into()})),
    )
}
