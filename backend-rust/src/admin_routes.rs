//! Host browser administration routes. The Host browser session is the sole
//! administrator identity and secondary verification is bound to that login.

mod policy;
mod secondary;
mod store;

use std::{
    io,
    sync::{Arc, PoisonError},
    time::Instant,
};

use axum::{
    Extension, Json, Router,
    extract::{Request, State, rejection::JsonRejection},
    http::{HeaderValue, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, options, post},
};
use serde_json::{Value, json};

use crate::{
    catalog::CatalogSource,
    web_auth::{BrowserSession, WebAuth},
};

use policy::Policy;
use secondary::Secondary;

type Result<T> = std::result::Result<T, AdminError>;
type Payload = std::result::Result<Json<Value>, JsonRejection>;

#[derive(Clone)]
pub struct AdminRoutes(Arc<AdminState>);

struct AdminState {
    catalog: CatalogSource,
    policy: Policy,
    secondary: Secondary,
}

impl AdminRoutes {
    pub fn for_host(catalog: CatalogSource) -> Self {
        let secondary = Secondary::for_host(catalog.host_data_dir());
        Self(Arc::new(AdminState {
            catalog,
            policy: Policy::default(),
            secondary,
        }))
    }

    pub fn router(&self, auth: &WebAuth) -> Router {
        let protected = Router::new()
            .route("/api/admin/policy", get(describe_policy).post(save_policy))
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
        state
            .policy
            .effective(state.catalog.host_data_dir(), &state.catalog.load())
            .map(Json)
    })
    .await
}

async fn describe_policy(State(admin): State<AdminRoutes>) -> Result<Json<Value>> {
    blocking(move || {
        let state = &admin.0;
        state
            .policy
            .describe(state.catalog.host_data_dir(), &state.catalog.load())
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
