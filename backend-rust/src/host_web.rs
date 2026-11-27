mod client_debug;

use crate::{gateway::Gateway, host_paths::HostPaths};
use astrion_headless::workspace_catalog::RegisteredWorkspace;
use astrion_headless::{
    project_git::ProjectGit,
    storage_stats, usage,
    web_assets::{TerminalPolicy, WebAssets},
    web_auth::WebAuth,
    web_status::{self, WebStatusConfig},
    workflows,
};
use axum::response::IntoResponse;
use axum::{
    Json, Router,
    extract::{Request, State},
    middleware::{self, Next},
    response::Response,
    routing::get,
};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::{
    fs, io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

fn default_handoff_workspace() -> Option<PathBuf> {
    let home = std::env::home_dir().filter(|path| path.is_absolute())?;
    let desktop = ["Desktop", "OneDrive/Desktop", "OneDrive/桌面"].into_iter()
        .map(|path| home.join(path)).find(|path| path.is_dir()).unwrap_or(home);
    Some(desktop.join("Astrion/Default"))
}

fn status_config(gateway: &Gateway, data: &Path) -> WebStatusConfig {
    WebStatusConfig {
        runtime: gateway.runtime.clone(),
        catalog: gateway.catalog.clone(),
        data_dir: data.to_owned(),
        workspace_id: gateway.workspace.id.clone(),
        project_path: gateway.workspace.path.clone(),
        version: "v6.1".into(),
    }
}

pub(crate) fn headless_workspace_routes(gateway: &Arc<Gateway>, data: &Path) -> Router {
    let native_paths = gateway.paths.clone();
    let native = astrion_headless::native_actions::NativeActions::new(gateway.runtime.clone(), data.to_owned(), move || native_paths.catalog());
    let scoped = web_status::headless_router(status_config(gateway, data))
        .merge(native.workspace_router())
        .merge(astrion_headless::project_git::workspace_router(gateway.runtime.clone()))
        .merge(astrion_headless::knowledge::web::skills_router(data.to_owned())
            .layer(axum::Extension(gateway.preferences.clone())))
        .merge(astrion_headless::worktree::router_with_scope(gateway.runtime.clone(), data.to_owned()))
        .route_layer(middleware::from_fn_with_state(gateway.clone(), crate::gateway::workspace_scope));
    scoped.merge(native.system_router())
}

pub fn routes(
    auth: &WebAuth,
    gateway: &Arc<Gateway>,
    data: &Path,
    paths: &HostPaths,
    assets: &WebAssets,
    navigation: &astrion_headless::navigation::Navigation,
) -> io::Result<Router> {
    let runtime = &gateway.runtime;
    let policy_path = Arc::new(data.join("admin_policy.json"));
    let pages = assets
        .router()
        .route_layer(middleware::from_fn_with_state(policy_path, terminal_policy));
    let catalog_paths = paths.clone();
    let navigation_routes = navigation.host_router(
        runtime.clone(),
        move || {
            use astrion_headless::navigation::{NavigationError, NavigationWorkspace};
            let mut catalog = catalog_paths.catalog().map_err(NavigationError::storage)?;
            catalog
                .workspaces
                .sort_by_key(|ws| Some(&ws.id) != catalog.default_workspace_id.as_ref());
            Ok(catalog
                .workspaces
                .into_iter()
                .map(|ws| NavigationWorkspace {
                    id: ws.id,
                    label: ws.label,
                    path: ws.path,
                })
                .collect())
        },
    );
    let git = GitScopes {
        gateway: gateway.clone(),
        scopes: Default::default(),
    };
    let preview_paths = paths.clone();
    let preview = astrion_headless::preview::Preview::new(runtime.clone(), move |id| {
        Ok(preview_paths.catalog()?.by_id(id).cloned())
    })?;
    let native_paths = paths.clone();
    let native = astrion_headless::native_actions::NativeActions::new(runtime.clone(), data.to_owned(), move || native_paths.catalog());
    let scoped = ProjectGit::router_with_scope()
        .route_layer(middleware::from_fn_with_state(git, git_scope))
        .merge(preview.router())
        .merge(native.workspace_router())
        .merge(astrion_headless::project_git::workspace_router(gateway.runtime.clone()))
        .merge(web_status::scoped_router(status_config(gateway, data)))
        .merge(astrion_headless::runtime::conversation_review::router(gateway.runtime.clone(), gateway.catalog.clone()))
        .merge(astrion_headless::runtime::conversation_token_statistics::router(gateway.runtime.clone(), data))
        .merge(web_status::attachment_router(runtime.clone(), gateway.catalog.clone(), data.to_owned(), paths.logs.clone()))
        .merge(astrion_headless::runtime::versioning::browser_router(gateway.runtime.clone()))
        .merge(astrion_headless::runtime::versioning::mutation_router(gateway.runtime.clone()))
        .merge(astrion_headless::runtime::web_messages::router(gateway.runtime.clone()))
        .merge(astrion_headless::runtime::process_resources::browser_router(gateway.runtime.clone()))
        .merge(astrion_headless::knowledge::web::scoped_router(data.to_owned())
            .layer(axum::Extension(gateway.preferences.clone())))
        .merge(astrion_headless::worktree::router_with_scope(gateway.runtime.clone(), data.to_owned()))
        .merge(gateway.children.browser_router())
        .merge(workflows::browser_scoped_router(
            gateway.workflows.clone(),
            gateway.runtime.clone(),
        ))
        .route_layer(middleware::from_fn_with_state(
            gateway.clone(),
            crate::gateway::workspace_scope,
        ));
    let registry = astrion_headless::workspace_catalog::manage::WorkspaceRegistry::new(
        paths.workspace_config.clone(), paths.resources.clone());
    let chat = astrion_headless::chat_mode::ChatMode::open(
        gateway.runtime.clone(), data, &paths.resources, gateway.catalog.clone(),
    ).map_err(io::Error::other)?
        .with_personalization(gateway.preferences.clone());
    let chat = match default_handoff_workspace() {
        Some(path) => chat.with_handoff_workspace(registry.clone(), path),
        None => chat,
    };
    let browser_paths = paths.clone();
    let browser = astrion_headless::knowledge::browser::BrowserPreferences::host_router(
        data,
        runtime.clone(),
        move || browser_paths.catalog(),
    )?;
    let settings_paths = paths.clone();
    let fast_mode_paths = paths.clone();
    let transfer = Router::new()
        .route("/api/conversation-transfer/workspaces", get(transfer_workspaces))
        .with_state(paths.clone());
    let selection = Arc::new(astrion_headless::runtime::ConversationSelection::default());
    let transfer_gateway = gateway.clone();
    let transfer_paths = paths.clone();
    let transfer_routes = astrion_headless::runtime::transfer::router(
        runtime.clone(), navigation.clone(), selection.clone(), gateway.catalog.clone(),
        move |headers, query, body, session| transfer_gateway.mutation_workspace(headers, query, body, Some(session)),
        move |id| transfer_paths.catalog()
            .map_err(|error| astrion_headless::runtime::RuntimeError { status: 503, message: error.to_string() })?
            .by_id(id).cloned().ok_or_else(|| astrion_headless::runtime::RuntimeError {
                status: 404, message: "A Chat handoff workspace is unavailable".into(),
            }),
    );
    let mutation_gateway = gateway.clone();
    let api = Router::new()
        .route("/api/health", get(health))
        .merge(astrion_headless::runtime::process_resources::monitor_router(runtime.clone()))
        .merge(astrion_headless::mcp::router(data.to_owned(), registry.clone()))
        .merge(astrion_headless::knowledge::migration::router(data.to_owned(), registry))
        .merge(astrion_headless::conversation_db::migration::router(data.to_owned(),paths.logs.clone()))
        .merge(transfer)
        .merge(transfer_routes)
        .merge(native.system_router())
        .merge(web_status::chatgpt_fast_mode_router(runtime.clone(), gateway.catalog.clone(), move || fast_mode_paths.catalog()))
        .merge(astrion_headless::runtime::conversation_duplicate::browser_router(
            runtime.clone(), gateway.catalog.clone(), gateway.preferences.clone(),
            move |headers, query, body, session| mutation_gateway.mutation_workspace(headers, query, body, Some(session)),
        ))
        .merge(astrion_headless::sub_agent_settings::models_router(gateway.catalog.clone()))
        .merge(astrion_headless::sub_agent_settings::settings_router(gateway.preferences.clone(), move || settings_paths.catalog()))
        .merge(chat.router())
        .merge(crate::web_conversations::router(gateway.clone(), navigation.clone(), selection))
        .merge(navigation_routes)
        .merge(browser)
        .merge(scoped.layer(axum::Extension(preview)))
        .merge(storage_stats::router(data.to_owned(), paths.logs.clone()))
        .merge(usage::host_router(data, &paths.workspace_config));
    Ok(auth
        .router_with_login(assets.login())
        .merge(astrion_headless::admin_routes::AdminRoutes::for_host(gateway.catalog.clone()).router(auth))
        .merge(client_debug::router(paths.logs.clone()))
        .merge(auth.with_browser_session(pages))
        .merge(auth.protect_api(api)))
}

async fn transfer_workspaces(State(paths): State<HostPaths>) -> (axum::http::StatusCode, Json<Value>) {
    crate::gateway::run(axum::http::StatusCode::OK, move || {
        let catalog = paths.catalog().map_err(|error| astrion_headless::runtime::RuntimeError {
            status: 500, message: error.to_string(),
        })?;
        let projects: Vec<_> = catalog.workspaces.into_iter()
            .map(|workspace| json!({"id": workspace.id, "label": workspace.label}))
            .collect();
        Ok(json!({"success": true, "data": projects}))
    }).await
}

#[derive(Clone)]
struct GitScopes {
    gateway: Arc<Gateway>,
    scopes: Arc<Mutex<HashMap<String, (PathBuf, String, ProjectGit)>>>,
}

impl GitScopes {
    fn for_workspace(&self, ws: &RegisteredWorkspace) -> io::Result<ProjectGit> {
        let mut scopes = self
            .scopes
            .lock()
            .map_err(|_| io::Error::other("Git workspace cache unavailable"))?;
        if let Some((path, label, git)) = scopes.get(&ws.id)
            && path == &ws.path
            && label == &ws.label
        {
            return Ok(git.clone());
        }
        let git = ProjectGit::new(
            &ws.id,
            Some(ws.label.clone()),
            &ws.path,
            self.gateway.catalog.host_data_dir(),
        )?
        .with_runtime(
            self.gateway.runtime.clone(),
            self.gateway.catalog.clone(),
            self.gateway.fixed_model.clone(),
        );
        scopes.insert(
            ws.id.clone(),
            (ws.path.clone(), ws.label.clone(), git.clone()),
        );
        Ok(git)
    }
}

async fn git_scope(
    State(state): State<GitScopes>,
    axum::Extension(ws): axum::Extension<RegisteredWorkspace>,
    mut req: Request,
    next: Next,
) -> Response {
    match tokio::task::spawn_blocking(move || state.for_workspace(&ws)).await {
        Ok(Ok(git)) => {
            req.extensions_mut().insert(git);
            next.run(req).await
        }
        _ => (
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"success":false,"error":"Git workspace unavailable"})),
        )
            .into_response(),
    }
}
pub(crate) async fn health() -> Json<Value> {
    Json(
        json!({"success":true,"status":"ok","backend":"rust","pid":std::process::id(),"server_time_ms":SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis()}),
    )
}

async fn terminal_policy(
    State(policy): State<Arc<PathBuf>>,
    mut req: Request,
    next: Next,
) -> Response {
    if matches!(req.uri().path(), "/terminal" | "/static/terminal.html") {
        let value = tokio::task::spawn_blocking(move || load_terminal_policy(&policy)).await;
        if let Ok(Ok(policy)) = value {
            req.extensions_mut().insert(policy);
        }
    }
    next.run(req).await
}
fn load_terminal_policy(path: &Path) -> io::Result<TerminalPolicy> {
    let doc: Value = match fs::read(path) {
        Ok(v) => serde_json::from_slice(&v)?,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(TerminalPolicy::Allowed),
        Err(e) => return Err(e),
    };
    if !doc.is_object() {
        return Err(io::Error::other("Invalid host admin policy"));
    }
    let mut blocked = false;
    for scope in [
        &doc["global"],
        &doc["roles"]["admin"],
        &doc["invites"][""],
        &doc["users"]["host"],
    ] {
        if let Some(value) = scope
            .get("ui_blocks")
            .and_then(|b| b.get("block_realtime_terminal"))
        {
            blocked = value
                .as_bool()
                .ok_or_else(|| io::Error::other("Invalid terminal policy"))?;
        }
    }
    Ok(if blocked {
        TerminalPolicy::Blocked
    } else {
        TerminalPolicy::Allowed
    })
}
