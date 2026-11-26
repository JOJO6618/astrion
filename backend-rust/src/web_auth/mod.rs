//! Browser authentication for the full Host web server. Mount the Gateway
//! separately with its own authentication and listener policy.

mod credentials;
mod origin;
mod sessions;

use axum::body::{Body, to_bytes};
use axum::extract::{Request, State};
use axum::http::header::{CACHE_CONTROL, CONTENT_TYPE, COOKIE, LOCATION, RETRY_AFTER, SET_COOKIE};
use axum::http::{HeaderMap, HeaderName, HeaderValue, Method, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{MethodRouter, get, post};
use axum::{Json, Router};
use credentials::{Credentials, DisableError};
use origin::{OriginPolicy, single_header};
use serde::Deserialize;
use serde_json::{Value, json};
use sessions::{
    Authenticated, PasswordLimits, RotateError, SESSION_LIFETIME, Session, Sessions, new_secret,
};
use std::io;
use std::net::IpAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tokio::sync::Semaphore;

const CSRF: HeaderName = HeaderName::from_static("x-csrf-token");
const MAX_AUTH_BODY: usize = 64 * 1024;
const PASSWORD_UNAVAILABLE: &str = "Host password configuration is unavailable";
const SESSION_EXPIRED: &str = "Session expired. Please sign in again.";

pub struct WebAuthConfig {
    pub data_dir: PathBuf,
    pub port: u16,
    /// Additional externally visible origins. The reverse proxy must preserve Host.
    pub allowed_origins: Vec<String>,
    /// Direct peers allowed to supply X-Forwarded-For for rate limiting only.
    pub trusted_proxies: Vec<IpAddr>,
    pub secure_cookie: bool,
    pub cookie_name: Option<String>,
    /// Set only from the native launcher's environment, on a loopback-only listener.
    pub desktop: bool,
    pub workspace_ready: bool,
    pub default_thinking: bool,
}

impl WebAuthConfig {
    pub fn new(data_dir: impl Into<PathBuf>, port: u16) -> Self {
        Self {
            data_dir: data_dir.into(),
            port,
            allowed_origins: Vec::new(),
            trusted_proxies: Vec::new(),
            secure_cookie: false,
            cookie_name: None,
            desktop: false,
            workspace_ready: false,
            default_thinking: false,
        }
    }
}

/// Request extension for an authenticated Host browser session.
#[derive(Clone)]
pub struct BrowserSession {
    selected_workspace: Arc<Mutex<Option<String>>>,
    secondary_verified_at: Arc<Mutex<Option<std::time::Instant>>>,
}

impl BrowserSession {
    fn new() -> Self {
        Self {
            selected_workspace: Arc::default(),
            secondary_verified_at: Arc::default(),
        }
    }

    pub fn admin_secondary_verified(&self, ttl: std::time::Duration) -> bool {
        self.secondary_verified_at.lock().unwrap().is_some_and(|at| at.elapsed() < ttl)
    }

    pub fn mark_admin_secondary_verified(&self) {
        *self.secondary_verified_at.lock().unwrap() = Some(std::time::Instant::now());
    }

    /// Resolve this saved identifier against the trusted workspace catalog before use.
    pub fn selected_workspace_id(&self) -> Option<String> {
        self.selected_workspace.lock().unwrap().clone()
    }

    /// Call from a protected handler after verifying the workspace in the catalog.
    pub fn select_workspace(&self, workspace_id: Option<&str>) {
        *self.selected_workspace.lock().unwrap() = workspace_id.map(str::to_owned);
    }

    /// Update the selection only while it still matches the expected workspace.
    pub fn compare_and_select_workspace(
        &self,
        expected: Option<&str>,
        workspace_id: Option<&str>,
    ) -> bool {
        let mut selected = self.selected_workspace.lock().unwrap();
        if selected.as_deref() != expected {
            return false;
        }
        *selected = workspace_id.map(str::to_owned);
        true
    }

    #[cfg(test)]
    pub(crate) fn synthetic() -> Self {
        Self::new()
    }
}

#[derive(Clone)]
pub struct WebAuth(Arc<AuthState>);

struct AuthState {
    config: WebAuthConfig,
    origins: OriginPolicy,
    cookie_name: String,
    sessions: Mutex<Sessions>,
    password_limits: Mutex<PasswordLimits>,
    password_worker: Arc<Semaphore>,
}

impl WebAuth {
    pub fn new(config: WebAuthConfig) -> io::Result<Self> {
        let invalid = |reason| io::Error::new(io::ErrorKind::InvalidInput, reason);
        if config.port == 0 {
            return Err(invalid("Host web authentication requires the bound port"));
        }
        if config.desktop && !config.allowed_origins.is_empty() {
            return Err(invalid(
                "Desktop Host web authentication only accepts local origins",
            ));
        }
        let cookie_name = config
            .cookie_name
            .clone()
            .unwrap_or_else(|| format!("agents_session_{}", config.port));
        if cookie_name.is_empty()
            || !cookie_name
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&c))
            || cookie_name.starts_with("__Secure-") && !config.secure_cookie
            || cookie_name.starts_with("__Host-") && !config.secure_cookie
        {
            return Err(invalid("Invalid Host web cookie name"));
        }
        let origins = OriginPolicy::new(&config)?;
        Ok(Self(Arc::new(AuthState {
            config,
            origins,
            cookie_name,
            sessions: Mutex::default(),
            password_limits: Mutex::default(),
            password_worker: Arc::new(Semaphore::new(1)),
        })))
    }

    /// Authentication and password settings endpoints with a self-contained login page.
    pub fn router(&self) -> Router {
        let login_assets = Router::new().route("/auth/login.js", get(login_script));
        self.router_with_login(get(login_page))
            .merge(self.with_browser_session(login_assets))
    }

    /// Use the normal application login entry from the Web assets router.
    pub fn router_with_login(&self, login: MethodRouter) -> Router {
        let login = login.layer(middleware::from_fn(redirect_signed_in));
        let router = Router::new()
            .route("/api/host-mode-enabled", get(host_mode))
            .route("/api/csrf-token", get(csrf_token))
            .route("/api/session-status", get(session_status))
            .route("/api/host-password", get(password_status))
            .route("/api/host-password/disable", post(disable_password))
            .route("/host-login", post(host_login))
            .route("/logout", get(logout).post(logout))
            .with_state(self.clone())
            .route("/login", login);
        self.guard(router, Gate::Session)
    }

    /// Resolve the optional browser session for pages with individual access rules.
    pub fn with_browser_session(&self, router: Router) -> Router {
        self.guard(router, Gate::Session)
    }

    /// Require a browser session on every route of this Web API router.
    pub fn protect_api(&self, router: Router) -> Router {
        self.guard(router, Gate::Api)
    }

    /// Apply the browser branch of an explicitly dual-channel route. The caller
    /// dispatches Authorization requests through the Gateway before this method.
    pub async fn protect_request(&self, req: Request, next: Next) -> Response {
        guard(State((self.clone(), Gate::Api)), req, next).await
    }

    /// Redirect unauthenticated requests on this page router to the login page.
    pub fn protect_pages(&self, router: Router) -> Router {
        self.guard(router, Gate::Page)
    }

    fn guard(&self, router: Router, gate: Gate) -> Router {
        router.route_layer(middleware::from_fn_with_state((self.clone(), gate), guard))
    }

    async fn credentials(&self) -> io::Result<Credentials> {
        if self.0.config.desktop {
            return Ok(Credentials::default());
        }
        let directory = self.0.config.data_dir.clone();
        tokio::task::spawn_blocking(move || Credentials::load(&directory)).await?
    }

    fn cookie(&self, id: Option<&str>, lifetime: u64) -> HeaderValue {
        let secure = if self.0.config.secure_cookie {
            "; Secure"
        } else {
            ""
        };
        let value = id.map(|id| format!("r1.{id}")).unwrap_or_default();
        let cookie = format!(
            "{}={value}; Path=/; Max-Age={lifetime}; HttpOnly; SameSite=Strict{secure}",
            self.0.cookie_name
        );
        HeaderValue::from_str(&cookie).expect("cookie is assembled from validated values")
    }
}

#[derive(Clone, Copy)]
enum Gate {
    Session,
    Api,
    Page,
}

#[derive(Default, Clone)]
struct RequestSession {
    had_cookie: bool,
    id: Option<String>,
    session: Option<Session>,
    authenticated: bool,
}

fn session_cookie(headers: &HeaderMap, name: &str) -> (bool, Option<String>) {
    let mut found = false;
    let mut id = None;
    for value in headers.get_all(COOKIE) {
        let Ok(cookies) = value.to_str() else {
            return (true, None);
        };
        for cookie in cookies.split(';') {
            let Some((key, value)) = cookie.trim().split_once('=') else {
                continue;
            };
            if key.trim() != name {
                continue;
            }
            if found {
                return (true, None);
            }
            found = true;
            id = value
                .strip_prefix("r1.")
                .filter(|id| {
                    id.len() == 64
                        && id
                            .bytes()
                            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                })
                .map(str::to_owned);
        }
    }
    (found, id)
}

async fn browser_session(app: &WebAuth, headers: &HeaderMap) -> RequestSession {
    let (had_cookie, id) = session_cookie(headers, &app.0.cookie_name);
    let session = id
        .as_deref()
        .and_then(|id| app.0.sessions.lock().unwrap().get(id));
    let mut state = RequestSession {
        had_cookie,
        id,
        session,
        authenticated: false,
    };
    if let Some(authenticated) = state
        .session
        .as_ref()
        .and_then(|session| session.authenticated.as_ref())
    {
        state.authenticated = app.credentials().await.is_ok_and(|current| {
            authenticated.generation == current.generation
                && authenticated.password_enabled == current.enabled
        });
        if !state.authenticated {
            app.0.sessions.lock().unwrap().remove(state.id.as_deref());
            state.session = None;
        }
    }
    state
}

async fn guard(
    State((app, gate)): State<(WebAuth, Gate)>,
    mut req: Request,
    next: Next,
) -> Response {
    let unsafe_method = !matches!(
        *req.method(),
        Method::GET | Method::HEAD | Method::OPTIONS | Method::TRACE
    );
    let sensitive = unsafe_method
        || matches!(
            req.uri().path(),
            "/api/csrf-token"
                | "/logout"
                | "/api/host/workspaces/create"
                | "/api/host/workspaces/select"
        );
    let response = if !app.0.origins.allows(&req, sensitive) {
        error(StatusCode::FORBIDDEN, "Invalid Host web origin")
    } else {
        let prior = browser_session(&app, req.headers()).await;
        let secondary_verify = matches!(gate, Gate::Page) && req.method() == Method::POST
            && req.uri().path() == "/api/admin/secondary/verify";
        if unsafe_method && !secondary_verify && !validate_csrf(&mut req, prior.session.as_ref()).await {
            csrf_failure()
        } else if !prior.authenticated && matches!(gate, Gate::Api) {
            error(StatusCode::UNAUTHORIZED, SESSION_EXPIRED)
        } else if !prior.authenticated && matches!(gate, Gate::Page) {
            redirect("/login")
        } else {
            req.extensions_mut().remove::<BrowserSession>();
            if let Some(authenticated) = prior
                .session
                .as_ref()
                .and_then(|session| session.authenticated.as_ref())
            {
                req.extensions_mut().insert(authenticated.browser.clone());
            }
            req.extensions_mut().insert(prior);
            next.run(req).await
        }
    };
    let response = if matches!(gate, Gate::Api | Gate::Page) {
        no_store(response)
    } else {
        response
    };
    security_headers(response, app.0.config.secure_cookie)
}

#[derive(Deserialize)]
struct CsrfForm {
    csrf_token: String,
}

fn media_type(headers: &HeaderMap) -> Option<&str> {
    single_header(headers, CONTENT_TYPE)
        .ok()
        .flatten()?
        .split(';')
        .next()
        .map(str::trim)
}

async fn validate_csrf(req: &mut Request, session: Option<&Session>) -> bool {
    let Some(session) = session else { return false };
    match single_header(req.headers(), CSRF) {
        Ok(Some(provided)) => return session.matches_csrf(provided),
        Err(()) => return false,
        Ok(None) => {}
    }
    if !media_type(req.headers())
        .is_some_and(|kind| kind.eq_ignore_ascii_case("application/x-www-form-urlencoded"))
    {
        return false;
    }
    let body = std::mem::replace(req.body_mut(), Body::empty());
    let Ok(bytes) = to_bytes(body, MAX_AUTH_BODY).await else {
        return false;
    };
    let valid = serde_urlencoded::from_bytes::<CsrfForm>(&bytes)
        .is_ok_and(|form| session.matches_csrf(&form.csrf_token));
    *req.body_mut() = Body::from(bytes);
    valid
}

fn security_headers(mut response: Response, secure: bool) -> Response {
    let headers = response.headers_mut();
    for (name, value) in [
        ("x-frame-options", "SAMEORIGIN"),
        ("x-content-type-options", "nosniff"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
    ] {
        headers
            .entry(HeaderName::from_static(name))
            .or_insert(HeaderValue::from_static(value));
    }
    if secure {
        headers
            .entry(HeaderName::from_static("strict-transport-security"))
            .or_insert(HeaderValue::from_static(
                "max-age=31536000; includeSubDomains",
            ));
    }
    response
}

fn no_store(mut response: Response) -> Response {
    let headers = response.headers_mut();
    let private = headers.get_all(CACHE_CONTROL).iter().filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(',')).any(|v| v.trim().eq_ignore_ascii_case("private"));
    let policy = if private { "private, no-store" } else { "no-store" };
    headers.insert(CACHE_CONTROL, HeaderValue::from_static(policy));
    response
}

fn json_response(status: StatusCode, data: Value) -> Response {
    no_store((status, Json(data)).into_response())
}

fn error(status: StatusCode, message: &str) -> Response {
    json_response(status, json!({"success": false, "error": message}))
}

fn redirect(path: &'static str) -> Response {
    no_store((StatusCode::FOUND, [(LOCATION, path)]).into_response())
}

fn unavailable() -> Response {
    error(StatusCode::SERVICE_UNAVAILABLE, PASSWORD_UNAVAILABLE)
}

fn csrf_failure() -> Response {
    error(StatusCode::FORBIDDEN, "CSRF validation failed")
}

fn limited(message: &str, seconds: u64) -> Response {
    let mut response = json_response(
        StatusCode::TOO_MANY_REQUESTS,
        json!({"success": false, "error": message, "retry_after": seconds}),
    );
    response
        .headers_mut()
        .insert(RETRY_AFTER, HeaderValue::from(seconds));
    response
}

async fn redirect_signed_in(req: Request, next: Next) -> Response {
    if req.extensions().get::<BrowserSession>().is_some() {
        return redirect("/new");
    }
    // A stale cookie is harmless. Retaining it here avoids an old login-page
    // response deleting the new cookie set by a concurrent successful login.
    no_store(next.run(req).await)
}

async fn host_mode(State(app): State<WebAuth>) -> Response {
    let credentials = app.credentials().await;
    let mut data = json!({
        "success": true,
        "enabled": true,
        "backend": "rust",
        "pid": std::process::id(),
        "password_required": credentials.as_ref().map_or(true, |current| current.enabled),
        "config_valid": credentials.is_ok(),
    });
    if credentials.is_err() {
        data["error"] = json!(PASSWORD_UNAVAILABLE);
    }
    json_response(StatusCode::OK, data)
}

fn prior_session(req: &Request) -> RequestSession {
    req.extensions()
        .get::<RequestSession>()
        .cloned()
        .unwrap_or_default()
}

async fn csrf_token(State(app): State<WebAuth>, req: Request) -> Response {
    let prior = prior_session(&req);
    if let Some(session) = prior.session {
        return json_response(
            StatusCode::OK,
            json!({"success": true, "token": session.csrf}),
        );
    }
    let Ok((id, csrf)) = new_secret().and_then(|id| Ok((id, new_secret()?))) else {
        return unavailable();
    };
    let cookie = app.cookie(Some(&id), 15 * 60);
    let response = json_response(StatusCode::OK, json!({"success": true, "token": csrf}));
    let source = app.0.origins.source(&req);
    if !app
        .0
        .sessions
        .lock()
        .unwrap()
        .issue_challenge(prior.id.as_deref(), source.client, id, csrf)
    {
        return limited("Too many pending browser sessions", 60);
    }
    ([(SET_COOKIE, cookie)], response).into_response()
}

async fn session_status(State(app): State<WebAuth>, req: Request) -> Response {
    let logged_in = req.extensions().get::<BrowserSession>().is_some();
    let thinking = app.0.config.default_thinking;
    json_response(
        StatusCode::OK,
        json!({
            "success": true, "logged_in": logged_in,
            "session": {
                "logged_in": logged_in,
                "username": logged_in.then_some("host"),
                "role": logged_in.then_some("admin"),
                "run_mode": logged_in.then_some(if thinking { "deep" } else { "fast" }),
                "thinking_mode": logged_in.then_some(thinking),
                "host_mode": logged_in,
            }
        }),
    )
}

#[derive(Deserialize)]
struct PasswordJson {
    password: Option<String>,
}

async fn read_password(req: Request) -> Option<String> {
    let kind = media_type(req.headers())?.to_ascii_lowercase();
    if kind != "application/json" && !(kind.starts_with("application/") && kind.ends_with("+json"))
    {
        return None;
    }
    let raw = to_bytes(req.into_body(), MAX_AUTH_BODY).await.ok()?;
    serde_json::from_slice::<PasswordJson>(&raw).ok()?.password
}

async fn host_login(State(app): State<WebAuth>, req: Request) -> Response {
    let prior = prior_session(&req);
    let (Some(previous_id), Some(previous)) = (prior.id, prior.session) else {
        return csrf_failure();
    };
    let source = app.0.origins.source(&req);
    let Ok(current) = app.credentials().await else {
        return unavailable();
    };
    let authentication = Authenticated {
        generation: current.generation.clone(),
        password_enabled: current.enabled,
        browser: BrowserSession::new(),
    };
    if current.enabled {
        let password = read_password(req).await;
        if let Err(error) = password_attempt(&app, source.client, current, password, None).await {
            return error.into_response();
        }
        match app.credentials().await {
            Ok(recent) if recent.enabled && recent.generation == authentication.generation => {}
            Ok(_) => return error(StatusCode::UNAUTHORIZED, SESSION_EXPIRED),
            Err(_) => return unavailable(),
        }
    } else if !source.direct_loopback {
        return error(
            StatusCode::FORBIDDEN,
            "Passwordless Host login requires a direct loopback connection",
        );
    }
    let Ok((id, csrf)) = new_secret().and_then(|id| Ok((id, new_secret()?))) else {
        return unavailable();
    };
    let cookie = app.cookie(Some(&id), SESSION_LIFETIME.as_secs());
    let rotated = app.0.sessions.lock().unwrap().rotate(
        &previous_id,
        &previous.csrf,
        id,
        csrf,
        authentication,
    );
    match rotated {
        Ok(()) => (
            [(SET_COOKIE, cookie)],
            json_response(
                StatusCode::OK,
                json!({"success": true, "workspace_required": !app.0.config.workspace_ready}),
            ),
        )
            .into_response(),
        Err(RotateError::InvalidChallenge) => csrf_failure(),
        Err(RotateError::SessionLimit) => limited("Too many active browser sessions", 60),
    }
}

async fn logout(State(app): State<WebAuth>, req: Request) -> Response {
    if req.method() == Method::HEAD {
        return redirect("/login?logged_out=1");
    }
    let prior = prior_session(&req);
    app.0.sessions.lock().unwrap().remove(prior.id.as_deref());
    let response = if req.method() == Method::GET {
        redirect("/login?logged_out=1")
    } else {
        json_response(StatusCode::OK, json!({"success": true}))
    };
    if prior.had_cookie {
        ([(SET_COOKIE, app.cookie(None, 0))], response).into_response()
    } else {
        response
    }
}

async fn password_status(State(app): State<WebAuth>, req: Request) -> Response {
    if req.extensions().get::<BrowserSession>().is_none() {
        return error(StatusCode::UNAUTHORIZED, SESSION_EXPIRED);
    }
    if app.0.config.desktop {
        return json_response(
            StatusCode::OK,
            json!({"success": true, "applicable": false}),
        );
    }
    match app.credentials().await {
        Ok(current) => json_response(
            StatusCode::OK,
            json!({"success": true, "applicable": true, "enabled": current.enabled}),
        ),
        Err(_) => unavailable(),
    }
}

async fn disable_password(State(app): State<WebAuth>, req: Request) -> Response {
    let prior = prior_session(&req);
    if !prior.authenticated {
        return error(StatusCode::UNAUTHORIZED, SESSION_EXPIRED);
    }
    if app.0.config.desktop {
        return error(
            StatusCode::FORBIDDEN,
            "Host password settings are unavailable for desktop sessions",
        );
    }
    let Ok(current) = app.credentials().await else {
        return unavailable();
    };
    if !current.enabled {
        return error(
            StatusCode::CONFLICT,
            "Host password protection is already disabled",
        );
    }
    if !prior
        .session
        .as_ref()
        .and_then(|session| session.authenticated.as_ref())
        .is_some_and(|verified| verified.generation == current.generation)
    {
        return error(StatusCode::UNAUTHORIZED, SESSION_EXPIRED);
    }
    let Ok(generation) = new_secret() else {
        return unavailable();
    };
    let source = app.0.origins.source(&req);
    let password = read_password(req).await;
    if let Err(error) =
        password_attempt(&app, source.client, current, password, Some(generation)).await
    {
        return error.into_response();
    }
    app.0.sessions.lock().unwrap().remove(prior.id.as_deref());
    let response = json_response(StatusCode::OK, json!({"success": true, "enabled": false}));
    ([(SET_COOKIE, app.cookie(None, 0))], response).into_response()
}

enum PasswordFailure {
    Incorrect,
    RateLimited(u64),
    Busy,
    Update(DisableError),
    Unavailable,
}

impl IntoResponse for PasswordFailure {
    fn into_response(self) -> Response {
        match self {
            Self::Incorrect => error(StatusCode::UNAUTHORIZED, "Incorrect Host password"),
            Self::RateLimited(retry) => limited("Too many Host password attempts", retry),
            Self::Busy => limited("Host password verification is busy", 1),
            Self::Update(DisableError::Changed) => error(StatusCode::UNAUTHORIZED, SESSION_EXPIRED),
            Self::Update(DisableError::Incorrect) => {
                error(StatusCode::CONFLICT, "Incorrect Host password")
            }
            Self::Update(DisableError::Unavailable) | Self::Unavailable => unavailable(),
        }
    }
}

async fn password_attempt(
    app: &WebAuth,
    source: Option<IpAddr>,
    current: Credentials,
    password: Option<String>,
    disable_generation: Option<String>,
) -> Result<(), PasswordFailure> {
    let app = app.clone();
    tokio::task::spawn_blocking(move || {
        let _permit = app
            .0
            .password_worker
            .try_acquire()
            .map_err(|_| PasswordFailure::Busy)?;
        app.0
            .password_limits
            .lock()
            .unwrap()
            .admit(source)
            .map_err(PasswordFailure::RateLimited)?;
        let accepted = current
            .verify(password.as_deref())
            .map_err(|_| PasswordFailure::Unavailable)?;
        app.0
            .password_limits
            .lock()
            .unwrap()
            .record(source, accepted);
        if !accepted {
            return Err(PasswordFailure::Incorrect);
        }
        if let Some(generation) = disable_generation {
            credentials::disable(
                &app.0.config.data_dir,
                &current.generation,
                generation,
                password.as_deref(),
            )
            .map_err(PasswordFailure::Update)?;
        }
        Ok(())
    })
    .await
    .map_err(|_| PasswordFailure::Unavailable)?
}

async fn login_page() -> Response {
    let mut response = Html(include_str!("login.html")).into_response();
    response.headers_mut().insert(HeaderName::from_static("content-security-policy"),
        HeaderValue::from_static("default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"));
    response
}

async fn login_script() -> Response {
    no_store(
        (
            [(CONTENT_TYPE, "text/javascript; charset=utf-8")],
            include_str!("login.js"),
        )
            .into_response(),
    )
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/auth_routes.rs"]
mod tests;
