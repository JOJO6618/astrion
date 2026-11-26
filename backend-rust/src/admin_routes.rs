//! Host browser administration routes. The Host browser session is the sole
//! administrator identity and secondary verification is bound to that login.

mod policy;
mod secondary;
mod store;
mod tools;

use std::{
    io,
    sync::{Arc, PoisonError},
    time::Instant,
};

use axum::{
    Extension, Json, Router,
    body::Bytes,
    extract::{Query, Request, State, rejection::JsonRejection},
    http::{HeaderValue, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, options, post},
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::{
    catalog::CatalogSource,
    web_auth::{BrowserSession, WebAuth},
};

use policy::Policy;
use secondary::Secondary;
use tools::ToolRegistry;

type Result<T> = std::result::Result<T, AdminError>;
type Payload = std::result::Result<Json<Value>, JsonRejection>;

#[derive(Clone)]
pub struct AdminRoutes(Arc<AdminState>);

struct AdminState {
    catalog: CatalogSource,
    policy: Policy,
    secondary: Secondary,
    tools: ToolRegistry,
}

impl AdminRoutes {
    pub fn for_host(catalog: CatalogSource) -> io::Result<Self> {
        let data = catalog.host_data_dir();
        let secondary = Secondary::for_host(data);
        let tools = ToolRegistry::for_host(data)?;
        Ok(Self(Arc::new(AdminState {
            catalog,
            policy: Policy::default(),
            secondary,
            tools,
        })))
    }

    pub fn router(&self, auth: &WebAuth) -> Router {
        let protected = Router::new()
            .route("/api/admin/policy", get(describe_policy).post(save_policy))
            .route(
                "/api/admin/custom-tools",
                get(list_tools).post(upsert_tool).delete(delete_tool),
            )
            .route(
                "/api/admin/custom-tools/file",
                get(read_tool_file).post(write_tool_file),
            )
            .route("/api/admin/custom-tools/reload", post(reload_tools))
            .route_layer(middleware::from_fn_with_state(
                self.clone(),
                require_secondary,
            ));
        let api = Router::new()
            .route("/api/effective-policy", get(effective_policy))
            .merge(protected)
            .with_state(self.clone());
        let login = Router::new()
            .route("/api/admin/secondary/status", get(secondary_status))
            .route("/api/admin/secondary/verify", post(verify_secondary))
            .with_state(self.clone());
        let mut preflight = Router::new();
        for (path, allowed) in [
            ("/api/effective-policy", "GET, HEAD, OPTIONS"),
            ("/api/admin/policy", "GET, HEAD, POST, OPTIONS"),
            ("/api/admin/secondary/status", "GET, HEAD, OPTIONS"),
            ("/api/admin/secondary/verify", "POST, OPTIONS"),
            (
                "/api/admin/custom-tools",
                "GET, HEAD, POST, DELETE, OPTIONS",
            ),
            ("/api/admin/custom-tools/file", "GET, HEAD, POST, OPTIONS"),
            ("/api/admin/custom-tools/reload", "POST, OPTIONS"),
        ] {
            preflight = preflight.route(
                path,
                options(move || async move {
                    (
                        StatusCode::OK,
                        [(header::ALLOW, HeaderValue::from_static(allowed))],
                    )
                }),
            );
        }
        auth.protect_api(api)
            .merge(auth.protect_pages(login))
            .merge(auth.with_browser_session(preflight))
    }
}

async fn require_secondary(
    State(admin): State<AdminRoutes>,
    request: Request,
    next: Next,
) -> Response {
    if !request
        .extensions()
        .get::<BrowserSession>()
        .is_some_and(|session| session.admin_secondary_verified(admin.0.secondary.ttl))
    {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"success": false, "error": "需要二级密码", "code": "secondary_required"})),
        )
            .into_response();
    }
    next.run(request).await
}

async fn secondary_status(
    State(admin): State<AdminRoutes>,
    Extension(session): Extension<BrowserSession>,
) -> Json<Value> {
    let secondary = &admin.0.secondary;
    Json(
        json!({"success": true, "verified": session.admin_secondary_verified(secondary.ttl),
        "configured": secondary.configured(), "ttl": secondary.ttl.as_secs()}),
    )
}

async fn verify_secondary(
    State(admin): State<AdminRoutes>,
    Extension(session): Extension<BrowserSession>,
    payload: Payload,
) -> Result<Response> {
    if let Some(retry_after) = admin.0.secondary.attempt(Instant::now())? {
        return Ok((StatusCode::TOO_MANY_REQUESTS, Json(json!({"success": false, "error": "操作过于频繁，请稍后再试", "retry_after": retry_after}))).into_response());
    }
    let data = payload_object(payload)?;
    let password = data["password"]
        .as_str()
        .unwrap_or_default()
        .trim()
        .to_owned();
    if password.is_empty() {
        return Err(AdminError::bad("请输入二级密码"));
    }
    let ttl = admin.0.secondary.ttl.as_secs();
    let verified = blocking(move || Ok(admin.0.secondary.verify(&password))).await?;
    if !verified {
        return Err(AdminError(StatusCode::UNAUTHORIZED, "二级密码错误"));
    }
    session.mark_admin_secondary_verified();
    Ok(Json(json!({"success": true, "ttl": ttl})).into_response())
}

async fn effective_policy(State(admin): State<AdminRoutes>) -> Result<Json<Value>> {
    blocking(move || {
        let state = &admin.0;
        let custom_ids = state.tools.ids()?;
        state
            .policy
            .effective(
                state.catalog.host_data_dir(),
                &state.catalog.load(),
                &custom_ids,
            )
            .map(Json)
    })
    .await
}

async fn describe_policy(State(admin): State<AdminRoutes>) -> Result<Json<Value>> {
    blocking(move || {
        let state = &admin.0;
        let custom_ids = state.tools.ids()?;
        state
            .policy
            .describe(
                state.catalog.host_data_dir(),
                &state.catalog.load(),
                &custom_ids,
            )
            .map(Json)
    })
    .await
}

async fn save_policy(State(admin): State<AdminRoutes>, payload: Payload) -> Result<Json<Value>> {
    let payload = payload_object(payload)?;
    blocking(move || {
        let state = &admin.0;
        state
            .policy
            .save(
                state.catalog.host_data_dir(),
                &state.catalog.load(),
                &payload,
            )
            .map(Json)
    })
    .await
}

async fn list_tools(State(admin): State<AdminRoutes>) -> Result<Json<Value>> {
    blocking(move || admin.0.tools.list().map(Json)).await
}

async fn upsert_tool(State(admin): State<AdminRoutes>, payload: Payload) -> Result<Json<Value>> {
    let payload = payload_object(payload)?;
    blocking(move || admin.0.tools.upsert(&payload).map(Json)).await
}

#[derive(Default, Deserialize)]
struct ToolQuery {
    id: Option<String>,
    name: Option<String>,
}

impl ToolQuery {
    fn delete_id(&self, body: &Value) -> Result<String> {
        self.id
            .as_deref()
            .filter(|id| !id.is_empty())
            .or_else(|| store::nonempty(&body["id"]))
            .map(str::to_owned)
            .ok_or_else(|| AdminError::bad("Missing id"))
    }

    fn file(&self, body: &Value) -> Result<(String, String)> {
        let field = |query: Option<&str>, key| {
            query
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
                .or_else(|| store::nonempty(&body[key]).map(str::to_owned))
        };
        field(self.id.as_deref(), "id")
            .zip(field(self.name.as_deref(), "name"))
            .ok_or_else(|| AdminError::bad("Missing id or name"))
    }
}

async fn delete_tool(
    State(admin): State<AdminRoutes>,
    Query(query): Query<ToolQuery>,
    bytes: Bytes,
) -> Result<Json<Value>> {
    let body = if query.id.as_deref().is_none_or(str::is_empty) && !bytes.is_empty() {
        serde_json::from_slice(&bytes).map_err(|_| AdminError::bad("Invalid JSON"))?
    } else {
        Value::Null
    };
    let id = query.delete_id(&body)?;
    blocking(move || admin.0.tools.delete(&id).map(Json)).await
}

async fn read_tool_file(
    State(admin): State<AdminRoutes>,
    Query(query): Query<ToolQuery>,
) -> Result<String> {
    let (id, name) = query.file(&Value::Null)?;
    blocking(move || {
        admin
            .0
            .tools
            .file(&id, &name, None)?
            .ok_or_else(|| AdminError::missing("File not found"))
    })
    .await
}

async fn write_tool_file(
    State(admin): State<AdminRoutes>,
    Query(query): Query<ToolQuery>,
    payload: Payload,
) -> Result<Json<Value>> {
    let payload = payload_object(payload)?;
    let (id, name) = query.file(&payload)?;
    blocking(move || {
        let content = if payload["content"].is_null() {
            ""
        } else {
            payload["content"]
                .as_str()
                .ok_or_else(|| AdminError::bad("Invalid file content"))?
        };
        admin.0.tools.file(&id, &name, Some(content))?;
        Ok(Json(json!({"success": true})))
    })
    .await
}

async fn reload_tools(State(admin): State<AdminRoutes>) -> Result<Json<Value>> {
    blocking(move || admin.0.tools.reload().map(Json)).await
}

fn payload_object(payload: Payload) -> Result<Value> {
    let Json(value) = payload.map_err(|error| AdminError(error.status(), "Invalid JSON"))?;
    if value.is_null() {
        return Ok(json!({}));
    }
    if !value.is_object() {
        return Err(AdminError::bad("Expected a JSON object"));
    }
    Ok(value)
}

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> Result<T> + Send + 'static) -> Result<T> {
    tokio::task::spawn_blocking(f)
        .await
        .map_err(|_| AdminError::unavailable())?
}

struct AdminError(StatusCode, &'static str);

impl AdminError {
    fn bad(message: &'static str) -> Self {
        Self(StatusCode::BAD_REQUEST, message)
    }
    fn missing(message: &'static str) -> Self {
        Self(StatusCode::NOT_FOUND, message)
    }
    fn unavailable() -> Self {
        Self(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Administrator settings unavailable",
        )
    }
    fn invalid_policy() -> Self {
        Self(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Invalid stored administrator policy",
        )
    }
    fn lock<T>(_: PoisonError<T>) -> Self {
        Self::unavailable()
    }
}

impl From<io::Error> for AdminError {
    fn from(error: io::Error) -> Self {
        eprintln!("Administrator settings I/O error ({:?})", error.kind());
        Self::unavailable()
    }
}

impl IntoResponse for AdminError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"success": false, "error": self.1}))).into_response()
    }
}

#[cfg(test)]
#[path = "../../test/2026-11-25_RustAdminRoutes/admin_routes_contract.rs"]
mod tests;
