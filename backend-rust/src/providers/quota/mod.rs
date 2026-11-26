//! Subscription quota endpoints for ChatGPT/Codex and OpenCode Go.
//!
//! This module is intentionally mounted by the Host-only provider router: the
//! Python blueprint exists only in `app_legacy.py`, not in headless mode. It
//! never stores or returns credentials. All account/provider selection is
//! explicit; an unknown id never falls back to another account.
//!
//! The module is self-contained aside from the public provider config and the
//! `codex_oauth` helpers it shares with model refresh. Tests use temporary data
//! roots, synthetic credentials, and loopback upstreams only.

mod codex_api;
mod opencode;
#[cfg(test)]
#[path = "../../../../test/2026-11-27_Rust订阅额度/contract.rs"]
mod tests;
mod weekly;

use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
    time::{Duration, Instant},
};

use axum::{
    Json, Router,
    http::StatusCode,
    middleware,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Map, Value, json};

use super::{ProviderConfig, codex, codex_oauth, json_response, require_admin_session};

const USAGE_URL: &str = "https://chatgpt.com/backend-api/wham/usage";
const RESET_CREDITS_URL: &str = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits";
const OPENCODE_USAGE_URL: &str = "https://opencode.ai/zen/go/v1/usage";
const USAGE_TIMEOUT: Duration = Duration::from_secs(10);
const USAGE_CACHE_TTL: Duration = Duration::from_secs(30);
const WEEKLY_TIMEOUT: Duration = Duration::from_secs(20);

/// Loopback overrides for isolated contract tests. Production always uses the
/// fixed constants above.
#[derive(Clone)]
pub struct Endpoints {
    usage_url: String,
    reset_credits_url: String,
    reset_credits_consume_url: String,
    opencode_usage_url: String,
}

impl Default for Endpoints {
    fn default() -> Self {
        Self {
            usage_url: USAGE_URL.into(),
            reset_credits_url: RESET_CREDITS_URL.into(),
            reset_credits_consume_url: format!("{RESET_CREDITS_URL}/consume"),
            opencode_usage_url: OPENCODE_USAGE_URL.into(),
        }
    }
}

#[cfg(test)]
impl Endpoints {
    pub fn with_reset_credits(
        mut self,
        list_url: impl Into<String>,
        consume_url: impl Into<String>,
    ) -> Self {
        self.reset_credits_url = list_url.into();
        self.reset_credits_consume_url = consume_url.into();
        self
    }

    pub fn with_opencode_usage(mut self, url: impl Into<String>) -> Self {
        self.opencode_usage_url = url.into();
        self
    }

    pub fn with_usage(mut self, url: impl Into<String>) -> Self {
        self.usage_url = url.into();
        self
    }
}

/// Host-only quota router. Mount inside `providers::codex_router` (or another
/// Host-only, `WebAuth::protect_api`-protected scope).
pub fn router(config: ProviderConfig) -> Router {
    let periodic = config.client.is_none();
    let endpoints = Endpoints {
        usage_url: config.codex.usage_url().to_owned(),
        ..Endpoints::default()
    };
    assemble_router(config, endpoints, periodic)
}

#[cfg(test)]
pub fn router_with_endpoints(config: ProviderConfig, endpoints: Endpoints) -> Router {
    assemble_router(config, endpoints, false)
}

fn assemble_router(config: ProviderConfig, endpoints: Endpoints, periodic: bool) -> Router {
    let state = Arc::new(QuotaState::new(config, endpoints));
    weekly::start(&state, periodic);
    Router::new()
        .route("/api/codex/usage", get(codex_api::usage))
        .route("/api/codex/weekly-value", get(weekly::weekly_value))
        .route("/api/codex/reset-credits", get(codex_api::reset_credits))
        .route(
            "/api/codex/reset-credits/consume",
            post(codex_api::consume_reset_credits),
        )
        .route(
            "/api/subscription/opencode-usage",
            get(opencode::opencode_usage),
        )
        .route_layer(middleware::from_fn(require_admin_session))
        .with_state(state)
}

struct QuotaState {
    config: ProviderConfig,
    endpoints: Endpoints,
    usage: Mutex<HashMap<String, UsageSlot>>,
    opencode: Mutex<OpenCodeCache>,
    weekly_refresh: Arc<tokio::sync::Notify>,
}

impl QuotaState {
    fn new(config: ProviderConfig, endpoints: Endpoints) -> Self {
        Self {
            config,
            endpoints,
            usage: Mutex::default(),
            opencode: Mutex::new(OpenCodeCache::default()),
            weekly_refresh: Arc::default(),
        }
    }

    fn usage_slot(&self, account_id: &str) -> UsageSlot {
        self.usage
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .entry(account_id.to_owned())
            .or_default()
            .clone()
    }

    fn opencode_cache(&self) -> std::sync::MutexGuard<'_, OpenCodeCache> {
        self.opencode.lock().unwrap_or_else(PoisonError::into_inner)
    }

    async fn invalidate_usage(&self, account_id: &str) {
        *self.usage_slot(account_id).lock().await = None;
    }
}

impl Drop for QuotaState {
    fn drop(&mut self) {
        self.weekly_refresh.notify_one();
    }
}

type UsageSlot = Arc<tokio::sync::Mutex<Option<CachedUsage>>>;

struct CachedUsage {
    payload: Value,
    signature: CredentialSignature,
    fetched_at: Instant,
}

#[derive(Default)]
struct OpenCodeCache {
    entries: HashMap<(String, String), CachedOpenCode>,
}

struct CachedOpenCode {
    payload: Value,
    fingerprint: [u8; 32],
    fetched_at: Instant,
}

/// File metadata is the cache invalidation boundary, matching Python's
/// `(path, inode, mtime_ns, size)` signature.
#[derive(Clone, PartialEq, Eq)]
struct CredentialSignature {
    inode: u64,
    mtime_ns: i128,
    size: u64,
}

fn credential_signature(path: &Path) -> Option<CredentialSignature> {
    let metadata = fs::metadata(path).ok()?;
    let modified = metadata.modified().ok()?;
    let mtime_ns = modified
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as i128)
        .unwrap_or_default();
    #[cfg(unix)]
    let inode = {
        use std::os::unix::fs::MetadataExt;
        metadata.ino()
    };
    #[cfg(not(unix))]
    let inode = 0;
    Some(CredentialSignature {
        inode,
        mtime_ns,
        size: metadata.len(),
    })
}

#[derive(Deserialize)]
pub(super) struct AccountQuery {
    account_id: Option<String>,
    refresh: Option<String>,
}

/// Query wins over JSON body, including an explicitly empty query value.
fn requested_account(query: &AccountQuery, payload: &Map<String, Value>) -> String {
    match &query.account_id {
        Some(id) => id.trim().to_owned(),
        None => payload
            .get("account_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_owned(),
    }
}

async fn resolve_account(data_dir: PathBuf, requested: String) -> Result<codex::Account, Response> {
    let account = tokio::task::spawn_blocking(move || {
        codex::lifecycle_index(&data_dir)?;
        Ok::<_, String>(codex::resolve_account(&data_dir, &requested))
    })
    .await
    .map_err(|_| error_response(StatusCode::INTERNAL_SERVER_ERROR, "account_registry_error"))?
    .map_err(|error| {
        error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            &format!("account_registry_error: {error}"),
        )
    })?;
    account.ok_or_else(|| error_response(StatusCode::NOT_FOUND, "account_not_found"))
}

pub(super) fn error_response(status: StatusCode, error: &str) -> Response {
    (status, Json(json!({"success": false, "error": error}))).into_response()
}

fn success_response(value: Value) -> Response {
    json_response(StatusCode::OK, value)
}

fn merge_object(mut base: Map<String, Value>, extra: Value) -> Map<String, Value> {
    if let Value::Object(extra) = extra {
        for (key, value) in extra {
            base.insert(key, value);
        }
    }
    base
}

/// Fetch one wham endpoint with the selected account's OAuth token. The
/// per-account lock is held across token refresh and the request, so a
/// concurrent model refresh cannot rotate the same refresh token twice.
async fn wham_request(
    state: &QuotaState,
    account_id: &str,
    method: &str,
    url: &str,
    body: Option<Value>,
    timeout: Duration,
    retry_connect: bool,
) -> Result<(Value, Option<CredentialSignature>), (StatusCode, String)> {
    if !codex::valid_connection_id(account_id) {
        return Err((StatusCode::NOT_FOUND, "account_not_found".into()));
    }
    let lock = codex_oauth::account_lock(&state.config.data_dir, account_id);
    let _guard = lock.lock().await;
    let auth_path = codex::credential_path_for(&state.config.data_dir, account_id);
    let mut credential = codex_oauth::read_object(&auth_path);
    let access = codex_oauth::usable_access_token(&state.config.codex, &auth_path, &mut credential)
        .await
        .map_err(|error| {
            if error == "no_credentials" {
                (StatusCode::UNAUTHORIZED, "no_credentials".into())
            } else {
                (StatusCode::BAD_GATEWAY, format!("refresh_failed: {error}"))
            }
        })?;
    let mut headers = codex_oauth::request_headers(&credential, &access)
        .map_err(|error| (StatusCode::BAD_GATEWAY, error))?;
    // `/wham/*` uses the OAuth identity but not the Codex models beta header.
    headers.remove("OpenAI-Beta");
    let client =
        codex_oauth::oauth_client_with_connect_timeout(&state.config.codex, Duration::from_secs(5))
            .await
            .map_err(|error| (StatusCode::BAD_GATEWAY, error))?;

    let mut attempt = 0;
    let response = loop {
        let request = client.request(
            method
                .parse()
                .map_err(|_| (StatusCode::BAD_GATEWAY, "invalid_method".into()))?,
            url,
        );
        let request = if let Some(body) = body.as_ref() {
            request.json(body)
        } else {
            request
        };
        match request
            .timeout(timeout)
            .headers(headers.clone())
            .send()
            .await
        {
            Ok(response) => break response,
            Err(error) if retry_connect && attempt == 0 && error.is_connect() => {
                attempt += 1;
            }
            Err(error) if error.is_connect() && error.is_timeout() => {
                return Err((
                    StatusCode::BAD_GATEWAY,
                    "连接 ChatGPT 超时，请检查订阅代理连接。".into(),
                ));
            }
            Err(error) if error.is_timeout() => {
                return Err((
                    StatusCode::BAD_GATEWAY,
                    "ChatGPT 额度服务响应超时，请稍后刷新。".into(),
                ));
            }
            Err(error) => {
                return Err((StatusCode::BAD_GATEWAY, error.without_url().to_string()));
            }
        }
    };

    if response.status() != reqwest::StatusCode::OK {
        return Err((
            StatusCode::BAD_GATEWAY,
            format!("http_{}", response.status().as_u16()),
        ));
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|error| (StatusCode::BAD_GATEWAY, error.without_url().to_string()))?;
    let payload = serde_json::from_slice(&bytes).map_err(|error| {
        (
            StatusCode::BAD_GATEWAY,
            format!("invalid_response: {error}"),
        )
    })?;
    Ok((payload, credential_signature(&auth_path)))
}
