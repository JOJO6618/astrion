use super::{Gateway, Reply, run};
use astrion_headless::{
    navigation::{Navigation, NavigationError, NavigationWorkspace},
    runtime::{ConversationSelection, RuntimeError},
    web_auth::BrowserSession,
    workspace_catalog::RegisteredWorkspace,
};
use axum::{
    Extension, Json, Router,
    extract::{Path, Query, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode},
    routing::{delete, get, put},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;

struct Lifecycle {
    gateway: Arc<Gateway>,
    navigation: Navigation,
    selection: Arc<ConversationSelection>,
}

pub(super) fn router(gateway: Arc<Gateway>, navigation: Navigation, selection: Arc<ConversationSelection>) -> Router {
    Router::new()
        .route("/api/conversations/current", get(current))
        .route("/api/conversations/{id}", delete(remove))
        .route("/api/conversations/{id}/load", put(load))
        .with_state(Arc::new(Lifecycle {
            gateway,
            navigation,
            selection,
        }))
}

#[derive(Deserialize)]
struct Scope {
    workspace_id: Option<String>,
    conversation_id: Option<String>,
}

impl Scope {
    fn workspace_id(&self) -> Option<&str> {
        self.workspace_id
            .as_deref()
            .map(str::trim)
            .filter(|id| !id.is_empty())
    }
}

type Body = Result<Option<Json<Value>>, JsonRejection>;

async fn current(
    State(app): State<Arc<Lifecycle>>,
    Extension(browser): Extension<BrowserSession>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
    body: Body,
) -> Reply {
    run(StatusCode::OK, move || {
        let body = request_body(body);
        let Some(workspace) = app.workspace(&browser, &headers, &scope, &body)? else {
            return Ok(no_workspace());
        };
        let requested = scope
            .conversation_id
            .as_deref()
            .filter(|id| !id.trim().is_empty())
            .or_else(|| body["conversation_id"].as_str());
        app.gateway
            .runtime
            .current_web_conversation(&workspace, &app.selection, requested)
    })
    .await
}

async fn load(
    State(app): State<Arc<Lifecycle>>,
    Extension(browser): Extension<BrowserSession>,
    Path(id): Path<String>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
    body: Body,
) -> Reply {
    run(StatusCode::OK, move || {
        let body = request_body(body);
        let Some(workspace) = app.workspace(&browser, &headers, &scope, &body)? else {
            return Ok(no_workspace());
        };
        let catalog = app.gateway.catalog.load();
        let default_model = catalog
            .default_key()
            .or_else(|| app.gateway.fixed_model.as_ref().map(|model| model.key()));
        app.gateway.runtime.load_web_conversation(
            &workspace,
            &app.selection,
            &id,
            scope.workspace_id().is_some(),
            default_model,
        )
    })
    .await
}

async fn remove(
    State(app): State<Arc<Lifecycle>>,
    Extension(browser): Extension<BrowserSession>,
    Path(id): Path<String>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
    body: Body,
) -> Reply {
    run(StatusCode::OK, move || {
        let body = request_body(body);
        let Some(workspace) = app.workspace(&browser, &headers, &scope, &body)? else {
            return Ok(no_workspace());
        };
        let runtime = &app.gateway.runtime;
        let (id, mut result) = runtime.delete_web_conversation(&workspace, &app.selection, &id)?;
        let paths = app.gateway.paths.clone();
        match app.navigation.deleted_snapshot(
            runtime.clone(),
            "host",
            &workspace.id,
            &id,
            move || {
                let catalog = paths.catalog().map_err(NavigationError::storage)?;
                Ok(catalog
                    .workspaces
                    .into_iter()
                    .map(|entry| NavigationWorkspace {
                        id: entry.id,
                        label: entry.label,
                        path: entry.path,
                    })
                    .collect())
            },
        ) {
            Ok(snapshot) => result["desktop_navigation"] = snapshot,
            Err(error) => result["desktop_navigation_error"] = json!(error.to_string()),
        }
        Ok(result)
    })
    .await
}

impl Lifecycle {
    fn workspace(
        &self,
        browser: &BrowserSession,
        headers: &HeaderMap,
        scope: &Scope,
        body: &Value,
    ) -> Result<Option<RegisteredWorkspace>, RuntimeError> {
        self.gateway
            .mutation_workspace(headers, scope.workspace_id(), body, Some(browser))
    }
}

fn request_body(body: Body) -> Value {
    body.ok()
        .flatten()
        .map(|Json(body)| body)
        .unwrap_or_default()
}

fn no_workspace() -> Value {
    json!({
        "success": false,
        "code": "no_workspace",
        "error": "No workspace has been created yet. Please create one manually in the workspace management UI.",
    })
}
