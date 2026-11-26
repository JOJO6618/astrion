use std::path::PathBuf;

use axum::{
    Json, Router,
    extract::{Path, State, rejection::JsonRejection},
    http::StatusCode,
    routing::{get, post, put},
};
use serde_json::{Map, Value, json};

use crate::workspace_catalog::manage::WorkspaceRegistry;

use super::{
    access::{self, Actor, LegacyPolicy},
    registry::{Error, Registry},
    runtime::Runtime,
};

#[derive(Clone)]
struct Settings {
    data: PathBuf,
    workspaces: WorkspaceRegistry,
    registry: Registry,
}

type Reply = (StatusCode, Json<Value>);
type Body = Result<Json<Value>, JsonRejection>;

/// Mount on the authenticated Host browser API with its CSRF guard.
pub fn router(data: PathBuf, registry: WorkspaceRegistry) -> Router {
    let mcp = Registry::for_data(&data);
    router_with_registry(data, registry, mcp)
}

/// Explicit registry injection also lets local verification use a temporary
/// config independently of any production MCP environment variables.
pub fn router_with_registry(
    data: PathBuf,
    workspaces: WorkspaceRegistry,
    registry: Registry,
) -> Router {
    let settings = Settings {
        data,
        workspaces,
        registry,
    };
    Router::new()
        .route("/api/mcp/settings", get(settings_view))
        .route("/api/mcp/access/{actor}", put(save_defaults))
        .route(
            "/api/mcp/access/{actor}/{workspace_id}",
            put(save_workspace),
        )
        .route("/api/mcp/servers", post(create))
        .route(
            "/api/mcp/servers/{server_id}",
            get(detail).put(update).delete(remove),
        )
        .route("/api/mcp/sync", post(sync))
        .with_state(settings)
}

async fn settings_view(State(settings): State<Settings>) -> Reply {
    blocking(move || {
        let (servers, access) = settings.registry.settings();
        let legacy = LegacyPolicy::load(&settings.data).selection(&servers);
        let defaults = |actor| {
            access::defaults(&access, actor).map_or_else(|| json!(legacy), |ids| json!(ids))
        };
        let catalog = settings.workspaces.load()?;
        let workspaces: Vec<_> = catalog
            .workspaces
            .iter()
            .map(|row| {
                json!({
                    "id": row.id, "label": row.label, "access": {
                        "main": access::workspace(&access, &row.id, Actor::Main),
                        "sub": access::workspace(&access, &row.id, Actor::Sub),
                    }
                })
            })
            .collect();
        let summaries: Vec<_> = servers.iter().map(|server| server.summary()).collect();
        Ok(json!({"data": {
            "bridge_enabled": settings.registry.enabled(), "servers": summaries,
            "defaults": {"main": defaults(Actor::Main), "sub": defaults(Actor::Sub)},
            "workspaces": workspaces, "default_workspace_id": catalog.default_workspace_id,
        }}))
    })
    .await
}

async fn save_defaults(
    State(settings): State<Settings>,
    Path(actor): Path<String>,
    body: Body,
) -> Reply {
    save_selection(settings, actor, None, body).await
}

async fn save_workspace(
    State(settings): State<Settings>,
    Path((actor, id)): Path<(String, String)>,
    body: Body,
) -> Reply {
    save_selection(settings, actor, Some(id), body).await
}

async fn save_selection(
    settings: Settings,
    actor: String,
    workspace: Option<String>,
    body: Body,
) -> Reply {
    let Some(actor) = Actor::parse(&actor) else {
        return reply(StatusCode::NOT_FOUND, json!({"success": false}));
    };
    let payload = object(body);
    let result = tokio::task::spawn_blocking(move || {
        if let Some(id) = &workspace
            && settings.workspaces.load()?.by_id(id).is_none()
        {
            return Ok(None);
        }
        settings.registry.save_access(
            actor.name(),
            workspace.as_deref(),
            &Value::Object(payload?),
        )?;
        Ok(Some(json!({})))
    })
    .await;
    match result {
        Ok(Ok(None)) => reply(StatusCode::NOT_FOUND, json!({"success": false})),
        Ok(Ok(Some(body))) => success(body),
        Ok(Err(error)) => failure(error),
        Err(_) => internal(),
    }
}

async fn create(State(settings): State<Settings>, body: Body) -> Reply {
    let body = object(body);
    blocking(move || Ok(json!({"data": settings.registry.upsert(None, body?)?.summary()}))).await
}

async fn detail(State(settings): State<Settings>, Path(id): Path<String>) -> Reply {
    blocking(move || {
        let (servers, _) = settings.registry.settings();
        let server = servers
            .into_iter()
            .find(|server| server.id == id.trim())
            .ok_or(Error::Missing)?;
        Ok(json!({"data": server.detail()}))
    })
    .await
}

async fn update(State(settings): State<Settings>, Path(id): Path<String>, body: Body) -> Reply {
    let body = object(body);
    blocking(move || Ok(json!({"data": settings.registry.upsert(Some(&id), body?)?.summary()})))
        .await
}

async fn remove(State(settings): State<Settings>, Path(id): Path<String>) -> Reply {
    blocking(move || {
        settings.registry.delete(&id)?;
        Ok(json!({}))
    })
    .await
}

async fn sync(State(settings): State<Settings>, body: Body) -> Reply {
    let payload = match object(body) {
        Ok(payload) => payload,
        Err(error) => return failure(error),
    };
    let id = match payload.get("id") {
        None | Some(Value::Null) => None,
        Some(Value::String(id)) if !id.is_empty() => Some(id.as_str()),
        _ => return failure(Error::Invalid("Invalid MCP server ID".into())),
    };
    let runtime = Runtime::management(settings.registry);
    let result = runtime.sync(id).await;
    runtime.close().await;
    let status = if result["success"] == true {
        StatusCode::OK
    } else {
        StatusCode::BAD_GATEWAY
    };
    reply(
        status,
        json!({"success": result["success"], "error": result["error"]}),
    )
}

fn object(body: Body) -> Result<Map<String, Value>, Error> {
    match body {
        Ok(Json(Value::Object(body))) => Ok(body),
        _ => Err(Error::Invalid("Expected an MCP settings object".into())),
    }
}

async fn blocking(action: impl FnOnce() -> Result<Value, Error> + Send + 'static) -> Reply {
    match tokio::task::spawn_blocking(action).await {
        Ok(Ok(body)) => success(body),
        Ok(Err(error)) => failure(error),
        Err(_) => internal(),
    }
}

fn success(mut body: Value) -> Reply {
    body["success"] = json!(true);
    reply(StatusCode::OK, body)
}

fn failure(error: Error) -> Reply {
    match error {
        Error::Invalid(message) => reply(
            StatusCode::BAD_REQUEST,
            json!({"success": false, "error": message}),
        ),
        Error::Missing => reply(
            StatusCode::NOT_FOUND,
            json!({"success": false, "error": "服务器不存在"}),
        ),
        Error::Storage(error) => {
            eprintln!("MCP settings storage failed: {}", error.kind());
            internal()
        }
    }
}

fn internal() -> Reply {
    reply(StatusCode::INTERNAL_SERVER_ERROR, json!({"success": false}))
}
fn reply(status: StatusCode, body: Value) -> Reply {
    (status, Json(body))
}
