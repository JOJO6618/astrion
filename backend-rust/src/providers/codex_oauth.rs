//! Codex / ChatGPT OAuth connected-account model directory refresh.
//!
//! Scope of this module: resolve one registered account, obtain a usable access
//! token (refreshing and atomically rotating `refresh_token` when the JWT is
//! within five minutes of expiry), fetch `{api_base}/models` with the Codex CLI
//! headers and ETag conditional request, and persist the per-account model
//! cache. Login/logout remain in the Python Codex API.
//!
//! Network endpoints are configurable so contract tests can use a loopback
//! upstream with synthetic credentials; production defaults match
//! `utils/api_client/codex/settings.py`.

use std::{
    collections::{HashMap, HashSet},
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
    time::Duration,
};

use axum::{
    Router,
    body::Bytes,
    extract::{Query, State},
    http::StatusCode,
    middleware,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Map, Value, json};

use super::{
    codex,
    store::replace_file,
    {Failure, ProviderConfig, Providers, json_response, object_body, require_admin_session},
};

const MODELS_URL: &str = "https://chatgpt.com/backend-api/codex/models";
const RESPONSES_URL: &str = "https://chatgpt.com/backend-api/codex/responses";
const TOKEN_URL: &str = "https://auth.openai.com/oauth/token";
const NPM_LATEST_URL: &str = "https://registry.npmjs.org/@openai%2fcodex/latest";
const USAGE_URL: &str = "https://chatgpt.com/backend-api/wham/usage";
pub(super) const OAUTH_CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
const DEFAULT_CLIENT_VERSION: &str = "0.153.4";
const REFRESH_SKEW_SECONDS: i64 = 300;

mod settings;

const MODELS_TIMEOUT: Duration = Duration::from_secs(30);
const TOKEN_TIMEOUT: Duration = Duration::from_secs(10);
const VERSION_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub(super) struct CodexConfig {
    data_dir: PathBuf,
    client: Option<reqwest::Client>,
    models_url: String,
    responses_url: String,
    token_url: String,
    npm_latest_url: String,
    usage_url: String,
}

impl CodexConfig {
    pub(super) fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            data_dir: data_dir.into(),
            client: None,
            models_url: MODELS_URL.into(),
            responses_url: RESPONSES_URL.into(),
            token_url: TOKEN_URL.into(),
            npm_latest_url: NPM_LATEST_URL.into(),
            usage_url: USAGE_URL.into(),
        }
    }

    pub(super) fn with_client(mut self, client: reqwest::Client) -> Self {
        self.client = Some(client);
        self
    }

    /// Loopback endpoints for contract tests. An empty npm URL disables the
    /// latest-version discovery (the cached/default version is still probed).
    pub(super) fn with_endpoints(
        mut self,
        models_url: impl Into<String>,
        token_url: impl Into<String>,
        npm_latest_url: impl Into<String>,
    ) -> Self {
        self.models_url = models_url.into();
        self.token_url = token_url.into();
        self.npm_latest_url = npm_latest_url.into();
        self
    }

    pub(super) fn token_url(&self) -> &str {
        &self.token_url
    }

    pub(super) fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    pub(super) fn client_handle(&self) -> Option<&reqwest::Client> {
        self.client.as_ref()
    }

    pub(super) fn usage_url(&self) -> &str {
        &self.usage_url
    }

    pub(super) fn with_usage_url(mut self, url: impl Into<String>) -> Self {
        self.usage_url = url.into();
        self
    }

    pub(super) fn responses_url(&self) -> &str {
        &self.responses_url
    }

    /// Loopback override for Responses contract tests; production is fixed.
    pub(super) fn with_responses_url(mut self, url: impl Into<String>) -> Self {
        self.responses_url = url.into();
        self
    }

    pub(super) fn responses_transport(&self, account_id: &str) -> CodexTransport {
        CodexTransport {
            config: self.clone(),
            account_id: account_id.trim().to_owned(),
        }
    }
}

/// Per-connection Codex Responses transport. The connection id is explicit:
/// there is no primary-account fallback. Authorization and the single 401
/// retry share the process-wide per-account lock with model refresh.
#[derive(Clone)]
pub struct CodexTransport {
    config: CodexConfig,
    account_id: String,
}

/// One successfully authorized credential snapshot. Opaque to callers except
/// for `fingerprint`, the stable encrypted-reasoning scope.
pub struct CodexResponsesAuthorization {
    client: reqwest::Client,
    endpoint: String,
    access_token: String,
    subscription: Option<String>,
    account_id: String,
    config_key: String,
    fingerprint: String,
}

impl CodexResponsesAuthorization {
    pub fn client(&self) -> &reqwest::Client {
        &self.client
    }

    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub fn connection_id(&self) -> &str {
        &self.account_id
    }

    /// Stable per-subscription scope for encrypted reasoning history. Token
    /// rotation and file rewrites keep it; switching the subscription behind
    /// the same connection id changes it.
    pub fn fingerprint(&self) -> &str {
        &self.fingerprint
    }

    fn headers(&self, session_id: &str) -> Result<reqwest::header::HeaderMap, String> {
        use reqwest::header::{ACCEPT, AUTHORIZATION, CONTENT_TYPE, HeaderMap, HeaderValue};
        if session_id.trim().is_empty() {
            return Err("request_error: empty session id".into());
        }
        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(ACCEPT, HeaderValue::from_static("text/event-stream"));
        headers.insert(
            "OpenAI-Beta",
            HeaderValue::from_static("responses=experimental"),
        );
        headers.insert("originator", HeaderValue::from_static("codex_cli_rs"));
        headers.insert(
            "session_id",
            HeaderValue::from_str(session_id)
                .map_err(|error| format!("request_error: {error}"))?,
        );
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Bearer {}", self.access_token))
                .map_err(|error| format!("request_error: {error}"))?,
        );
        if let Some(subscription) = self.subscription.as_deref() {
            headers.insert(
                "chatgpt-account-id",
                HeaderValue::from_str(subscription)
                    .map_err(|error| format!("request_error: {error}"))?,
            );
        }
        Ok(headers)
    }
}

impl CodexTransport {
    pub fn account_id(&self) -> &str {
        &self.account_id
    }

    pub async fn authorize(&self) -> Result<CodexResponsesAuthorization, String> {
        if !codex::valid_connection_id(&self.account_id) {
            return Err("account_not_found".into());
        }
        let lock = account_lock(&self.config.data_dir, &self.account_id);
        let _guard = lock.lock().await;
        let auth_path = codex::credential_path_for(&self.config.data_dir, &self.account_id);
        if !auth_path.exists() {
            return Err("account_not_found".into());
        }
        let mut credential = read_object(&auth_path);
        if !credential.is_object() {
            return Err("credential_invalid".into());
        }
        let access = usable_access_token(&self.config, &auth_path, &mut credential).await?;
        self.build_authorization(credential, access).await
    }

    async fn build_authorization(
        &self,
        credential: Value,
        access: String,
    ) -> Result<CodexResponsesAuthorization, String> {
        let subscription = credential["tokens"]["account_id"]
            .as_str()
            .map(str::trim)
            .filter(|id| !id.is_empty())
            .map(str::to_owned);
        let fingerprint = responses_fingerprint(&self.account_id, &credential, &access);
        let config_key = fs::canonicalize(&self.config.data_dir)
            .unwrap_or_else(|_| self.config.data_dir.clone())
            .display()
            .to_string();
        let client = oauth_client(&self.config).await?;
        Ok(CodexResponsesAuthorization {
            client,
            endpoint: self.config.responses_url().to_owned(),
            access_token: access,
            fingerprint,
            subscription,
            account_id: self.account_id.clone(),
            config_key,
        })
    }

    /// 401 recovery: under the same per-account lock, reuse a credential that a
    /// concurrent request already rotated; otherwise force one refresh. The
    /// connection id never changes and no other account is considered.
    pub async fn refresh_after_unauthorized(
        &self,
        rejected: &CodexResponsesAuthorization,
    ) -> Result<CodexResponsesAuthorization, String> {
        if !codex::valid_connection_id(&self.account_id) {
            return Err("account_not_found".into());
        }
        let lock = account_lock(&self.config.data_dir, &self.account_id);
        let _guard = lock.lock().await;
        let auth_path = codex::credential_path_for(&self.config.data_dir, &self.account_id);
        if !auth_path.exists() {
            return Err("account_changed".into());
        }
        let mut credential = read_object(&auth_path);
        if !credential.is_object() {
            return Err("credential_invalid".into());
        }
        let current_subscription = credential["tokens"]["account_id"]
            .as_str()
            .map(str::trim)
            .filter(|id| !id.is_empty());
        if current_subscription != rejected.subscription.as_deref() {
            return Err("account_changed".into());
        }
        let current_access = credential["tokens"]["access_token"]
            .as_str()
            .unwrap_or_default();
        if responses_fingerprint(&self.account_id, &credential, current_access)
            != rejected.fingerprint
        {
            // A different account/sub (or, without a stable remote identity,
            // a rewritten token) cannot inherit the rejected authorization.
            return Err("account_changed".into());
        }
        let access = if !current_access.is_empty()
            && current_access != rejected.access_token
            && token_is_fresh(current_access)
        {
            current_access.to_owned()
        } else {
            refresh_access_token(&self.config, &auth_path, &mut credential)
                .await
                .map_err(|error| format!("auth_refresh_failed: {error}"))?
        };
        self.build_authorization(credential, access).await
    }

    /// Send one Responses request and retry exactly once after a 401. The same
    /// endpoint, body, session id and (possibly rotated) connection are used.
    pub async fn post_responses(
        &self,
        authorization: CodexResponsesAuthorization,
        session_id: &str,
        body: &Value,
    ) -> Result<reqwest::Response, String> {
        self.validate_authorization(&authorization)?;
        let response = self.send_once(&authorization, session_id, body).await?;
        if response.status() != reqwest::StatusCode::UNAUTHORIZED {
            self.ensure_same_subscription(&authorization)?;
            return Ok(response);
        }
        // Release the rejected body before rotating the token.
        let _ = response.bytes().await;
        let refreshed = self.refresh_after_unauthorized(&authorization).await?;
        let response = self.send_once(&refreshed, session_id, body).await?;
        self.ensure_same_subscription(&refreshed)?;
        Ok(response)
    }

    async fn send_once(
        &self,
        authorization: &CodexResponsesAuthorization,
        session_id: &str,
        body: &Value,
    ) -> Result<reqwest::Response, String> {
        let headers = authorization.headers(session_id)?;
        authorization
            .client
            .post(&authorization.endpoint)
            .headers(headers)
            .json(body)
            .send()
            .await
            .map_err(|error| format!("request_error: {}", crate::net::describe_error(&error)))
    }

    fn validate_authorization(
        &self,
        authorization: &CodexResponsesAuthorization,
    ) -> Result<(), String> {
        if authorization.account_id != self.account_id
            || authorization.endpoint != self.config.responses_url()
        {
            return Err("account_changed".into());
        }
        let config_key = fs::canonicalize(&self.config.data_dir)
            .unwrap_or_else(|_| self.config.data_dir.clone())
            .display()
            .to_string();
        if authorization.config_key != config_key {
            return Err("account_changed".into());
        }
        self.ensure_same_subscription(authorization)
    }

    fn ensure_same_subscription(
        &self,
        authorization: &CodexResponsesAuthorization,
    ) -> Result<(), String> {
        let auth_path = codex::credential_path_for(&self.config.data_dir, &self.account_id);
        let credential = read_object(&auth_path);
        if !credential.is_object() {
            return Err("account_changed".into());
        }
        let current = credential["tokens"]["account_id"]
            .as_str()
            .map(str::trim)
            .filter(|id| !id.is_empty());
        let access = credential["tokens"]["access_token"]
            .as_str()
            .unwrap_or_default();
        if current != authorization.subscription.as_deref()
            || responses_fingerprint(&self.account_id, &credential, access)
                != authorization.fingerprint
        {
            return Err("account_changed".into());
        }
        Ok(())
    }
}

/// Encrypted-reasoning scope. A stable account/sub pair survives token
/// rotation; if neither remote identity exists, bind to the current token so
/// history cannot move to another user after a rewrite (it becomes unusable
/// on rotation, which is safe). Output is exactly 64 lowercase hex digits, the
/// persisted format `legacy/agent.rs` filters on.
fn responses_fingerprint(connection_id: &str, credential: &Value, access: &str) -> String {
    use ring::digest::{Context, SHA256};
    fn feed(context: &mut Context, value: &[u8]) {
        context.update(&(value.len() as u64).to_be_bytes());
        context.update(value);
    }
    let tokens = &credential["tokens"];
    let account = tokens["account_id"]
        .as_str()
        .map(str::trim)
        .filter(|id| !id.is_empty());
    let id_token = tokens["id_token"].as_str().unwrap_or_default();
    let claims = codex::jwt_payload(id_token);
    let claim_account = claims["https://api.openai.com/auth"]["chatgpt_account_id"]
        .as_str()
        .map(str::trim)
        .filter(|id| !id.is_empty());
    let sub = claims["sub"]
        .as_str()
        .map(str::trim)
        .filter(|sub| !sub.is_empty());
    let account = account.or(claim_account);

    let mut context = Context::new(&SHA256);
    feed(&mut context, b"astrion.codex.responses.identity.v1");
    feed(&mut context, connection_id.as_bytes());
    match account {
        Some(account) => {
            feed(&mut context, b"account=1");
            feed(&mut context, account.as_bytes());
        }
        None => feed(&mut context, b"account=0"),
    }
    match sub {
        Some(sub) => {
            feed(&mut context, b"sub=1");
            feed(&mut context, sub.as_bytes());
        }
        None => feed(&mut context, b"sub=0"),
    }
    if account.is_none() && sub.is_none() {
        // No stable remote identity: bind to the credential we actually use.
        feed(&mut context, b"token");
        feed(&mut context, access.as_bytes());
    }
    let digest = context.finish();
    let mut hash = String::with_capacity(64);
    for byte in digest.as_ref() {
        use std::fmt::Write;
        let _ = write!(hash, "{byte:02x}");
    }
    hash
}

/// Result of one account refresh. `count` is Python's `result["count"]` (only
/// present on a 200/304); `total_models` backs the refresh endpoint's
/// `models_count` fallback when the online path failed.
pub(super) struct Outcome {
    pub result: Map<String, Value>,
    pub count: Option<usize>,
    pub total_models: usize,
}

impl Outcome {
    pub(super) fn error(&self) -> Option<&str> {
        self.result
            .get("error")
            .and_then(Value::as_str)
            .filter(|error| !error.is_empty())
    }
}

/// `POST /api/codex/models/refresh?account_id=` (admin session + CSRF are
/// supplied by `protect_api` at the mount site).
pub(super) fn router(config: ProviderConfig) -> Router {
    let lifecycle = super::codex_lifecycle::router(config.codex.clone());
    let state = Providers::new(config);
    Router::new()
        .route("/api/codex/accounts", get(accounts_endpoint))
        .route("/api/codex/settings", get(settings::get_settings).post(settings::save_settings))
        .route("/api/codex/models/refresh", post(refresh_endpoint))
        .route_layer(middleware::from_fn(require_admin_session))
        .with_state(state)
        .merge(lifecycle)
}

async fn accounts_endpoint(State(app): State<std::sync::Arc<Providers>>) -> Response {
    let data_dir = app.config.data_dir.clone();
    let payload = tokio::task::spawn_blocking(move || {
        let (accounts, primary) = codex::public_accounts(&data_dir);
        json!({
            "success": true,
            "accounts": accounts,
            "primary_account_id": primary,
        })
    })
    .await;
    match payload {
        Ok(payload) => json_response(StatusCode::OK, payload),
        Err(error) => Failure::internal(format!("accounts_read_failed: {error}")).into_response(),
    }
}

#[derive(Deserialize)]
struct AccountQuery {
    account_id: Option<String>,
}

async fn refresh_endpoint(
    State(app): State<std::sync::Arc<Providers>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Response {
    let requested = match query.account_id {
        Some(id) => id.trim().to_owned(),
        None => object_body(&body)
            .get("account_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_owned(),
    };
    let data_dir = app.config.data_dir.clone();
    let account =
        tokio::task::spawn_blocking(move || codex::resolve_account(&data_dir, &requested))
            .await
            .ok()
            .flatten();
    let Some(account) = account else {
        return Failure::not_found("account_not_found").into_response();
    };
    let outcome = refresh_account(&app.config.codex, &account.id).await;
    let mut payload = Map::new();
    payload.insert("success".into(), json!(true));
    payload.insert("account_id".into(), json!(account.id));
    payload.insert(
        "models_count".into(),
        json!(outcome.count.unwrap_or(outcome.total_models)),
    );
    for (key, value) in &outcome.result {
        payload.insert(key.clone(), value.clone());
    }
    json_response(StatusCode::OK, Value::Object(payload))
}

/// Refresh one connected account. Never panics: credential/token/network/
/// payload failures become `result.error`, and the existing cache is retained.
/// A process-wide per-account lock serializes token rotation and cache writes
/// even when the same data directory is mounted by more than one router.
pub(super) async fn refresh_account(config: &CodexConfig, account_id: &str) -> Outcome {
    if !codex::valid_connection_id(account_id) {
        let mut result = Map::new();
        result.insert("refreshed".into(), json!(false));
        result.insert("error".into(), json!("account_not_found"));
        return Outcome {
            result,
            count: None,
            total_models: 0,
        };
    }
    let lock = account_lock(&config.data_dir, account_id);
    let _guard = lock.lock().await;
    refresh_account_locked(config, account_id).await
}

pub(super) fn account_lock(data_dir: &Path, account_id: &str) -> Arc<tokio::sync::Mutex<()>> {
    static LOCKS: OnceLock<Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>> = OnceLock::new();
    let canonical = fs::canonicalize(data_dir).unwrap_or_else(|_| data_dir.to_path_buf());
    let key = format!("{}::{}", canonical.display(), account_id);
    let mut locks = LOCKS
        .get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    locks
        .entry(key)
        .or_insert_with(|| Arc::new(tokio::sync::Mutex::new(())))
        .clone()
}

async fn refresh_account_locked(config: &CodexConfig, account_id: &str) -> Outcome {
    let auth_path = codex::credential_path_for(&config.data_dir, account_id);
    let cache_path = codex::models_path_for(&config.data_dir, account_id);
    let mut credential = read_object(&auth_path);
    let mut result = Map::new();
    result.insert("refreshed".into(), json!(false));

    let access = match usable_access_token(config, &auth_path, &mut credential).await {
        Ok(access) => access,
        Err(error) => return fallback(cache_path, result, error),
    };
    let cache = read_object(&cache_path);
    let cached_version = cache["client_version"].as_str();
    let global_version = settings::version(&read_object(&config.data_dir.join("codex_models.json")));
    let discovered = discover_version(config).await;
    let mut candidates = Vec::new();
    for version in discovered.iter().chain(std::iter::once(&global_version)) {
        if !version.is_empty() && !candidates.contains(version) {
            candidates.push(version.clone());
        }
    }
    let headers = match request_headers(&credential, &access) {
        Ok(headers) => headers,
        Err(error) => return fallback(cache_path, result, error),
    };
    let client = match oauth_client(config).await {
        Ok(client) => client,
        Err(error) => return fallback(cache_path, result, error),
    };
    let mut last_error = "models_request_failed".to_owned();
    for version in candidates {
        let mut request = client
            .get(&config.models_url)
            .query(&[("client_version", version.as_str())])
            .timeout(MODELS_TIMEOUT)
            .headers(headers.clone());
        let matches_cache = Some(version.as_str()) == cached_version;
        if matches_cache
            && let Some(etag) = cache["etag"].as_str().filter(|etag| !etag.is_empty())
        {
            request = request.header(reqwest::header::IF_NONE_MATCH, etag);
        }
        let response = match request.send().await {
            Ok(response) => response,
            Err(error) => {
                last_error = error.to_string();
                continue;
            }
        };
        if response.status() == reqwest::StatusCode::NOT_MODIFIED && matches_cache {
            let mut current = read_object(&cache_path);
            if let Some(current) = current.as_object_mut() {
                current.insert("fetched_at".into(), json!(now_iso()));
                current.insert("source".into(), json!("online"));
            }
            if let Err(error) = write_private_json(&cache_path, &current).await {
                return fallback(cache_path, result, error);
            }
            result.insert("refreshed".into(), json!(true));
            result.insert("not_modified".into(), json!(true));
            let count = current["models"].as_array().map(Vec::len).unwrap_or(0);
            result.insert("count".into(), json!(count));
            result.insert("client_version".into(), json!(version));
            result.insert("version_source".into(), json!("cache"));
            return Outcome {
                result,
                count: Some(count),
                total_models: count,
            };
        }
        if response.status() != reqwest::StatusCode::OK {
            last_error = format!("http_{}", response.status().as_u16());
            continue;
        }
        let etag = response
            .headers()
            .get(reqwest::header::ETAG)
            .and_then(|value| value.to_str().ok())
            .filter(|value| !value.is_empty())
            .map(str::to_owned);
        let body: Value = match response.json().await {
            Ok(body) => body,
            Err(error) => {
                last_error = format!("bad_json: {error}");
                continue;
            }
        };
        let models = body["models"].as_array().cloned().unwrap_or_default();
        if models.is_empty() {
            last_error = "bad_payload".into();
            continue;
        }
        let count = models.len();
        let mut current = read_object(&cache_path);
        if !current.is_object() {
            current = Value::Object(Map::new());
        }
        let current = current.as_object_mut().expect("object was normalized");
        current.insert("models".into(), Value::Array(models));
        current.insert("fetched_at".into(), json!(now_iso()));
        current.insert(
            "etag".into(),
            etag.map(Value::String).unwrap_or(Value::Null),
        );
        current.insert("client_version".into(), json!(version));
        current.insert("source".into(), json!("online"));
        if let Err(error) = write_private_json(&cache_path, &Value::Object(current.clone())).await {
            return fallback(cache_path, result, error);
        }
        result.insert("refreshed".into(), json!(true));
        result.insert("count".into(), json!(count));
        result.insert("client_version".into(), json!(version));
        result.insert(
            "version_source".into(),
            json!(if discovered.as_deref() == Some(version.as_str()) {
                "npm_latest"
            } else {
                "cache"
            }),
        );
        if version != global_version {
            result.insert("version_fallback".into(), json!(false));
        }
        return Outcome {
            result,
            count: Some(count),
            total_models: count,
        };
    }
    result.insert("error".into(), json!(last_error));
    if discovered
        .as_deref()
        .is_some_and(|version| version != global_version)
    {
        result.insert("version_fallback".into(), json!(true));
    }
    fallback(cache_path, result, last_error)
}

fn fallback(cache_path: PathBuf, mut result: Map<String, Value>, error: String) -> Outcome {
    result.insert("error".into(), json!(error));
    let cache = read_object(&cache_path);
    let total = cache["models"].as_array().map(Vec::len).unwrap_or(0);
    if total > 0 {
        result.insert("fallback".into(), json!("cache"));
    } else {
        result.insert("fallback".into(), json!("empty"));
    }
    Outcome {
        result,
        count: None,
        total_models: total,
    }
}

/// OAuth HTTP client: injected test client, otherwise environment proxy plus
/// the optional `codex_models.json` `proxy` key. No total timeout so an SSE
/// Responses body can stream; individual callers add their own deadlines.
pub(super) async fn oauth_client(config: &CodexConfig) -> Result<reqwest::Client, String> {
    oauth_client_with_connect_timeout(config, Duration::from_secs(10)).await
}

pub(super) async fn oauth_client_with_connect_timeout(
    config: &CodexConfig,
    connect_timeout: Duration,
) -> Result<reqwest::Client, String> {
    if let Some(client) = config.client_handle() {
        return Ok(client.clone());
    }
    let data_dir = config.data_dir().to_owned();
    let config_file = tokio::task::spawn_blocking(move || {
        read_object(&data_dir.join("codex_models.json"))
    })
    .await
    .map_err(|error| format!("proxy_read_error: {error}"))?;
    // 显式配置低于环境变量、高于系统代理；三者都为空时直连。
    let configured = settings::configured_proxy(&config_file);
    let proxy = crate::net::resolve(configured.as_deref());
    let builder = crate::net::apply(
        reqwest::Client::builder()
            .connect_timeout(connect_timeout)
            .redirect(reqwest::redirect::Policy::none()),
        proxy.as_ref(),
    );
    builder
        .build()
        .map_err(|error| format!("client_error: {error}"))
}

pub(super) async fn usable_access_token(
    config: &CodexConfig,
    auth_path: &Path,
    credential: &mut Value,
) -> Result<String, String> {
    if !credential.is_object() {
        return Err("no_credentials".into());
    }
    let tokens = credential
        .get("tokens")
        .and_then(Value::as_object)
        .ok_or_else(|| "no_credentials".to_owned())?;
    let access = tokens
        .get("access_token")
        .and_then(Value::as_str)
        .filter(|token| !token.is_empty())
        .ok_or_else(|| "no_credentials".to_owned())?
        .to_owned();
    if token_is_fresh(&access) {
        return Ok(access);
    }
    refresh_access_token(config, auth_path, credential).await
}

/// Force a refresh-token exchange and atomically rewrite the credential. The
/// caller must hold the per-account lock.
pub(super) async fn refresh_access_token(
    config: &CodexConfig,
    auth_path: &Path,
    credential: &mut Value,
) -> Result<String, String> {
    let tokens = credential
        .get("tokens")
        .and_then(Value::as_object)
        .ok_or_else(|| "no_credentials".to_owned())?;
    let refresh_token = tokens
        .get("refresh_token")
        .and_then(Value::as_str)
        .filter(|token| !token.is_empty())
        .ok_or_else(|| "刷新 Codex 凭证缺少 refresh_token，请重新登录。".to_owned())?
        .to_owned();
    let form = serde_urlencoded::to_string([
        ("grant_type", "refresh_token"),
        ("refresh_token", refresh_token.as_str()),
        ("client_id", OAUTH_CLIENT_ID),
    ])
    .map_err(|error| format!("刷新 Codex token 失败: {error}"))?;
    let client = oauth_client(config).await?;
    let response = client
        .post(&config.token_url)
        .timeout(TOKEN_TIMEOUT)
        .header(
            reqwest::header::CONTENT_TYPE,
            "application/x-www-form-urlencoded",
        )
        .body(form)
        .send()
        .await
        .map_err(|error| {
            format!(
                "刷新 Codex token 网络失败: {}",
                crate::net::describe_error(&error)
            )
        })?;
    if response.status() != reqwest::StatusCode::OK {
        let status = response.status().as_u16();
        let text = response.text().await.unwrap_or_default();
        return Err(format!(
            "刷新 Codex token 失败 (HTTP {status}): {}",
            text.chars().take(300).collect::<String>()
        ));
    }
    let payload: Value = response
        .json()
        .await
        .map_err(|error| format!("刷新 Codex token 响应无效: {error}"))?;
    let new_access = payload["access_token"]
        .as_str()
        .filter(|token| !token.is_empty())
        .ok_or_else(|| "刷新 Codex token 响应缺少 access_token/refresh_token。".to_owned())?
        .to_owned();
    let new_refresh = payload["refresh_token"]
        .as_str()
        .filter(|token| !token.is_empty())
        .ok_or_else(|| "刷新 Codex token 响应缺少 access_token/refresh_token。".to_owned())?
        .to_owned();
    let id_token = payload["id_token"]
        .as_str()
        .filter(|token| !token.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            credential["tokens"]["id_token"]
                .as_str()
                .filter(|token| !token.is_empty())
                .map(str::to_owned)
        })
        .unwrap_or_default();
    let account_id = credential["tokens"]["account_id"]
        .as_str()
        .filter(|id| !id.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            codex::jwt_payload(&id_token)["https://api.openai.com/auth"]["chatgpt_account_id"]
                .as_str()
                .map(str::to_owned)
        })
        .unwrap_or_default();
    let tokens = credential
        .get_mut("tokens")
        .and_then(Value::as_object_mut)
        .expect("credential tokens were validated");
    tokens.insert("id_token".into(), json!(id_token));
    tokens.insert("access_token".into(), json!(new_access));
    tokens.insert("refresh_token".into(), json!(new_refresh));
    tokens.insert("account_id".into(), json!(account_id));
    credential["last_refresh"] = json!(now_iso());
    write_private_json(auth_path, credential).await?;
    Ok(new_access)
}

pub(super) fn request_headers(
    credential: &Value,
    access_token: &str,
) -> Result<reqwest::header::HeaderMap, String> {
    use reqwest::header::{HeaderMap, HeaderValue};
    let mut headers = HeaderMap::new();
    headers.insert(
        "OpenAI-Beta",
        HeaderValue::from_static("responses=experimental"),
    );
    headers.insert("originator", HeaderValue::from_static("codex_cli_rs"));
    headers.insert(
        reqwest::header::ACCEPT,
        HeaderValue::from_static("application/json"),
    );
    headers.insert(
        reqwest::header::AUTHORIZATION,
        HeaderValue::from_str(&format!("Bearer {access_token}"))
            .map_err(|error| format!("request_error: {error}"))?,
    );
    if let Some(account_id) = credential["tokens"]["account_id"]
        .as_str()
        .filter(|id| !id.is_empty())
    {
        headers.insert(
            "chatgpt-account-id",
            HeaderValue::from_str(account_id).map_err(|error| format!("request_error: {error}"))?,
        );
    }
    Ok(headers)
}

fn token_is_fresh(token: &str) -> bool {
    let claims = codex::jwt_payload(token);
    let exp = claims["exp"]
        .as_i64()
        .or_else(|| claims["exp"].as_str().and_then(|value| value.parse().ok()))
        .or_else(|| {
            claims["exp"]
                .as_f64()
                .filter(|value| value.is_finite())
                .map(|value| value as i64)
        });
    match exp {
        Some(exp) => exp - now_seconds() > REFRESH_SKEW_SECONDS,
        // Python treats an unparsable exp as fresh and lets the server 401.
        None => true,
    }
}

async fn discover_version(config: &CodexConfig) -> Option<String> {
    if config.npm_latest_url.is_empty() {
        return None;
    }
    let client = oauth_client(config).await.ok()?;
    let response = client
        .get(&config.npm_latest_url)
        .timeout(VERSION_TIMEOUT)
        .header(reqwest::header::ACCEPT, "application/json")
        .send()
        .await
        .ok()?;
    if response.status() != reqwest::StatusCode::OK {
        return None;
    }
    let version = response.json::<Value>().await.ok()?["version"]
        .as_str()
        .map(str::trim)
        .filter(|version| valid_version(version))?
        .to_owned();
    Some(version)
}

fn valid_version(version: &str) -> bool {
    if version.is_empty() || version.len() > 64 {
        return false;
    }
    let core = version.split(['-', '+']).next().unwrap_or_default();
    let parts: Vec<&str> = core.split('.').collect();
    parts.len() == 3
        && parts
            .iter()
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
}

fn now_seconds() -> i64 {
    time::OffsetDateTime::now_utc().unix_timestamp()
}

fn now_iso() -> String {
    let now = time::OffsetDateTime::now_utc();
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}Z",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second(),
        now.microsecond()
    )
}

pub(super) fn read_object(path: &Path) -> Value {
    fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or(Value::Null)
}

async fn write_private_json(path: &Path, value: &Value) -> Result<(), String> {
    let path = path.to_owned();
    let value = value.clone();
    tokio::task::spawn_blocking(move || write_private_json_blocking(&path, &value))
        .await
        .map_err(|error| format!("write_error: {error}"))?
        .map_err(|error| format!("write_error: {error}"))
}

pub(super) fn write_private_json_blocking(path: &Path, value: &Value) -> io::Result<()> {
    let directory = path.parent().unwrap_or(Path::new("."));
    fs::create_dir_all(directory)?;
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("codex.json");
    let temporary = directory.join(format!(".{name}.{}.tmp", uuid::Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    let result = (|| {
        let mut writer = io::BufWriter::new(&mut file);
        serde_json::to_writer_pretty(&mut writer, value)?;
        writer.flush()?;
        drop(writer);
        file.sync_all()?;
        replace_file(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

/// Refresh every `ready` registered account for `refresh-all`, preserving
/// Python's per-account result shape and failure visibility.
pub(super) async fn refresh_ready_accounts(config: &CodexConfig) -> Vec<Value> {
    let accounts = codex::accounts(&config.data_dir);
    let mut results = Vec::new();
    let mut seen = HashSet::new();
    for account in accounts {
        if account.state != "ready" || !seen.insert(account.id.clone()) {
            continue;
        }
        let outcome = refresh_account(config, &account.id).await;
        let error = outcome.error().unwrap_or_default();
        results.push(json!({
            "kind": "codex",
            "id": account.id,
            "success": error.is_empty(),
            "models_count": outcome.count,
            "error": error,
        }));
    }
    results
}
