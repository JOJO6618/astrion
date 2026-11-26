//! Provider management API: catalog, connect, model discovery/refresh,
//! disconnect, and the custom-model CRUD that backs the same settings page.
//!
//! ## Mounting
//!
//! ```ignore
//! let providers = astrion_headless::providers::router(
//!     astrion_headless::providers::ProviderConfig::host(&paths.resources, &paths.deploy, data),
//! );
//! let api = Router::new().merge(providers);
//! let app = auth.protect_api(api);
//! ```
//!
//! `WebAuth::protect_api` supplies the authenticated Host browser session and
//! CSRF validation for unsafe methods. Host sessions are administrators, so the
//! module also rejects requests without a `BrowserSession` extension (defense in
//! depth for a future multi-user Docker mount). Do not mount it outside an
//! authenticated API scope.
//!
//! ## Contracts preserved
//!
//! - Credentials live only in `<DATA_DIR>/providers.json` (0600, atomic
//!   replace); responses contain only `api_key_masked`.
//! - Connection IDs are random and never reused; display indices are stable.
//! - Discovery is `GET {base_url}/models`, honors the same environment proxies
//!   as httpx, and does not follow redirects.
//! - Existing Python data (`providers.json` v1) is migrated in place.
//!
//! See `providers/README.md` for the behavior gaps that are not migrated yet.

mod catalog;
mod codex;
mod codex_lifecycle;
mod codex_oauth;
mod quota;
mod crosspet;
mod custom;
mod discovery;
mod modelsdev;
mod store;

pub(crate) use codex::load_index as discover_codex_accounts;
pub use codex_oauth::{CodexResponsesAuthorization, CodexTransport};
pub use crosspet::CrosspetIdentity;

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/providers_contract.rs"]
mod tests;

use std::{
    collections::{BTreeSet, HashMap},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
    time::{SystemTime, UNIX_EPOCH},
};

use axum::{
    Json, Router,
    body::Bytes,
    extract::{Path as AxumPath, Request, State},
    http::StatusCode,
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{delete, get, post},
};
use serde_json::{Map, Value, json};

use crate::web_auth::BrowserSession;
use modelsdev::Registry;
use store::Store;

const AUTO_REFRESH_GAP_SECONDS: f64 = 60.0 * 60.0;
const PROVIDER_ID_PATTERN_MAX: usize = 64;

/// Trusted server-side paths. `repo_config` holds the read-only
/// `providers_catalog.json`/`modelsdev_snapshot.json`; `data_dir` is the Host
/// data directory; `deploy_config` receives `custom_models.json`.
#[derive(Clone)]
pub struct ProviderConfig {
    repo_config: PathBuf,
    data_dir: PathBuf,
    deploy_config: PathBuf,
    client: Option<reqwest::Client>,
    /// Transport override for the models.dev cache refresh. `None` disables
    /// the online refresh (isolation tests); production uses the fixed URL.
    modelsdev_url: Option<String>,
    /// Codex model-refresh transport/endpoints (loopback overrides in tests).
    codex: codex_oauth::CodexConfig,
}

impl ProviderConfig {
    pub fn new(
        repo_config: impl Into<PathBuf>,
        data_dir: impl Into<PathBuf>,
        deploy_config: impl Into<PathBuf>,
    ) -> Self {
        let data_dir = data_dir.into();
        Self {
            repo_config: repo_config.into(),
            data_dir: data_dir.clone(),
            deploy_config: deploy_config.into(),
            client: None,
            modelsdev_url: Some(modelsdev::API_URL.to_owned()),
            codex: codex_oauth::CodexConfig::new(data_dir),
        }
    }

    /// Host layout: `repo_root/config`, `<DATA_DIR>`, and the deployment config.
    pub fn host(repo_root: &Path, deploy_config: &Path, data_dir: &Path) -> Self {
        Self::new(repo_root.join("config"), data_dir, deploy_config)
    }

    /// Inject a transport for isolation tests or for reusing a preconfigured
    /// client. Production must leave this unset so environment proxies apply.
    pub fn with_client(mut self, client: reqwest::Client) -> Self {
        self.client = Some(client.clone());
        self.codex = self.codex.with_client(client);
        self
    }

    /// Loopback Codex endpoints for contract tests (models, token, npm latest).
    pub fn with_codex_endpoints(
        mut self,
        models_url: impl Into<String>,
        token_url: impl Into<String>,
        npm_latest_url: impl Into<String>,
    ) -> Self {
        self.codex = self
            .codex
            .with_endpoints(models_url, token_url, npm_latest_url);
        self
    }

    /// Loopback Responses endpoint for contract tests; production is fixed at
    /// `https://chatgpt.com/backend-api/codex/responses`.
    pub fn with_codex_responses_url(mut self, url: impl Into<String>) -> Self {
        self.codex = self.codex.with_responses_url(url);
        self
    }

    /// Explicit-connection Codex Responses transport. Unknown/removed ids are
    /// errors; there is no primary-account fallback.
    pub fn codex_transport(&self, account_id: &str) -> CodexTransport {
        self.codex.responses_transport(account_id)
    }

    /// Override the models.dev endpoint (loopback tests) or re-enable the
    /// online refresh after `without_modelsdev_refresh`.
    pub fn with_modelsdev_url(mut self, url: impl Into<String>) -> Self {
        self.modelsdev_url = Some(url.into());
        self
    }

    /// Disable the best-effort models.dev refresh for isolated tests.
    pub fn without_modelsdev_refresh(mut self) -> Self {
        self.modelsdev_url = None;
        self
    }

    /// Test-only usage endpoint injection; production stays on the fixed
    /// `https://chatgpt.com/backend-api/wham/usage` URL.
    pub fn with_crosspet_usage_url(mut self, url: impl Into<String>) -> Self {
        self.codex = self.codex.with_usage_url(url);
        self
    }

    /// Opaque primary Codex account for CrossPet. Registered-but-missing
    /// credentials return `Ok(None)` without falling back to another account;
    /// corrupt/unreadable credentials return `Err`.
    pub fn crosspet_account(&self) -> Result<Option<CrosspetIdentity>, String> {
        crosspet::account(&self.codex)
    }

    /// One usage request under the same per-account lock as model refresh.
    /// Replace the caller's identity with the returned one. A request/HTTP
    /// failure is returned as `Err`; use `crosspet_usage_detailed` to keep the
    /// returned identity when this very request rotated the credential.
    pub async fn crosspet_usage(
        &self,
        identity: &CrosspetIdentity,
    ) -> Result<(CrosspetIdentity, Value), String> {
        let (identity, result) = self.crosspet_usage_detailed(identity).await?;
        result.map(|usage| (identity, usage))
    }

    /// Detailed variant: the outer error means the account/credential changed;
    /// once the request was attempted, the inner error carries the HTTP outcome
    /// together with the current identity (which may have been rotated here).
    pub async fn crosspet_usage_detailed(
        &self,
        identity: &CrosspetIdentity,
    ) -> Result<(CrosspetIdentity, Result<Value, String>), String> {
        crosspet::usage(&self.codex, identity).await
    }
}

pub fn router(config: ProviderConfig) -> Router {
    let state = Providers::new(config);
    Router::new()
        .route("/api/providers/catalog", get(provider_catalog))
        .route("/api/providers", get(provider_list))
        .route("/api/providers/connect", post(provider_connect))
        .route("/api/providers/refresh-all", post(provider_refresh_all))
        .route(
            "/api/providers/{provider_id}/refresh",
            post(provider_refresh),
        )
        .route("/api/providers/{provider_id}", delete(provider_disconnect))
        .route("/api/custom-models", get(custom_list).post(custom_create))
        .route(
            "/api/custom-models/{model_name}",
            axum::routing::put(custom_update).delete(custom_delete),
        )
        .route_layer(middleware::from_fn(require_admin_session))
        .with_state(state)
}

/// Codex connected-account model refresh endpoint. Mount inside
/// `WebAuth::protect_api` next to the provider router; it shares the same
/// `ProviderConfig` paths and client but owns its own route.
pub fn codex_router(config: ProviderConfig) -> Router {
    let quota_routes = quota::router(config.clone());
    codex_oauth::router(config).merge(quota_routes)
}

struct Providers {
    config: ProviderConfig,
    store: Arc<Store>,
    custom: custom::CustomModels,
    throttle: Mutex<Throttle>,
}

#[derive(Default)]
struct Throttle {
    running: bool,
    last: Option<f64>,
}

/// Clears the in-process `running` flag even if the request future is dropped
/// (client disconnect), so a cancelled refresh cannot wedge the hourly gate.
struct RefreshGuard(Arc<Providers>);

impl Drop for RefreshGuard {
    fn drop(&mut self) {
        self.0.finish_refresh();
    }
}

impl Providers {
    fn new(config: ProviderConfig) -> Arc<Self> {
        Arc::new(Self {
            store: Arc::new(Store::new(&config.data_dir)),
            custom: custom::CustomModels::new(&config.repo_config, &config.deploy_config),
            throttle: Mutex::new(Throttle::default()),
            config,
        })
    }

    fn entries(&self) -> Vec<Value> {
        catalog::entries(&self.config.repo_config)
    }

    fn names(&self, entries: &[Value]) -> HashMap<String, String> {
        catalog::names(entries)
    }

    fn client(&self) -> Result<reqwest::Client, String> {
        match &self.config.client {
            Some(client) => Ok(client.clone()),
            None => discovery::system_client(),
        }
    }

    fn begin_refresh(self: &Arc<Self>) -> Result<RefreshGuard, Value> {
        let now = epoch_seconds();
        let mut throttle = self.throttle.lock().unwrap_or_else(PoisonError::into_inner);
        if throttle.running {
            return Err(json!({
                "success": true, "skipped": true, "reason": "in_progress",
            }));
        }
        if let Some(last) = throttle.last
            && now - last < AUTO_REFRESH_GAP_SECONDS
        {
            return Err(json!({
                "success": true,
                "skipped": true,
                "reason": "throttled",
                "last_refresh_at": last,
                "next_refresh_at": last + AUTO_REFRESH_GAP_SECONDS,
            }));
        }
        throttle.running = true;
        Ok(RefreshGuard(self.clone()))
    }

    fn finish_refresh(&self) {
        let mut throttle = self.throttle.lock().unwrap_or_else(PoisonError::into_inner);
        throttle.running = false;
        throttle.last = Some(epoch_seconds());
    }

    /// Python refreshes the 6-hour models.dev cache after `connect` and after a
    /// successful `refresh_models`; failures never affect the provider result.
    async fn refresh_modelsdev_cache(&self) {
        let Some(url) = self.config.modelsdev_url.as_deref() else {
            return;
        };
        let client = match self.client() {
            Ok(client) => client,
            Err(_) => return,
        };
        let keys = modelsdev_keys(&self.entries());
        let _ = modelsdev::refresh_if_stale(&client, url, &keys, &self.config.data_dir).await;
    }

    async fn refresh_one(&self, provider_id: &str) -> Result<Value, Failure> {
        let entries = self.entries();
        let names = self.names(&entries);
        let provider_id = provider_id.to_owned();
        let snapshot = tokio::task::spawn_blocking({
            let store = self.store.clone();
            let provider_id = provider_id.clone();
            move || store.load(&names).record(&provider_id).cloned()
        })
        .await
        .map_err(|_| Failure::internal("provider store unavailable"))?;
        let Some(record) = snapshot else {
            return Err(Failure::bad_request("provider_not_found"));
        };
        let base_url = record["base_url"].as_str().unwrap_or_default().to_owned();
        let api_key = record["api_key"].as_str().unwrap_or_default().to_owned();
        let headers = record
            .get("headers")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let snapshot_key = (
            record["connected_at"].as_str().map(str::to_owned),
            base_url.clone(),
            api_key.clone(),
        );
        let catalog_id = record["catalog_id"].as_str().unwrap_or_default().to_owned();
        let entry = catalog::find(&entries, &catalog_id).cloned();
        let client = self.client().map_err(Failure::internal)?;
        let discovery = discovery::fetch_models(&client, &base_url, &api_key, &headers).await;
        let discovery = discovery::static_fallback(entry.as_ref(), discovery);
        let error = discovery.error.clone();
        let record_error = error.clone();
        let record_models = discovery.models.clone();
        let fetched_count = record_models.len();
        let names = self.names(&entries);
        let summary = tokio::task::spawn_blocking({
            let store = self.store.clone();
            let provider_id = provider_id.clone();
            move || {
                store.transact(&names, |data| {
                    let Some(record) = data
                        .records_mut()
                        .get_mut(&provider_id)
                        .and_then(Value::as_object_mut)
                    else {
                        return Err("provider_not_found".into());
                    };
                    let current = (
                        record
                            .get("connected_at")
                            .and_then(Value::as_str)
                            .map(str::to_owned),
                        record["base_url"].as_str().unwrap_or_default().to_owned(),
                        record["api_key"].as_str().unwrap_or_default().to_owned(),
                    );
                    if current != snapshot_key {
                        // Deleted and re-created while the probe was in flight:
                        // never write the old connection's models into the new one.
                        return Err("provider_not_found".into());
                    }
                    if record_error.is_none() || !record_models.is_empty() {
                        record.insert("models".into(), Value::Array(record_models.clone()));
                        record.insert("models_fetched_at".into(), json!(store::now_iso()));
                    }
                    record.insert(
                        "models_error".into(),
                        record_error
                            .as_deref()
                            .map(|error| json!(error))
                            .unwrap_or(Value::Null),
                    );
                    Ok(catalog::summary(
                        &provider_id,
                        &Value::Object(record.clone()),
                    ))
                })
            }
        })
        .await
        .map_err(|_| Failure::internal("provider store unavailable"))?
        .map_err(|error| Failure::internal(format!("provider store unavailable: {error}")))?
        .map_err(Failure::bad_request)?;
        let models_count = summary["models"]
            .as_array()
            .map(Vec::len)
            .unwrap_or_default();
        self.refresh_modelsdev_cache().await;
        let mut result = json!({
            // Python reports success for the fresh probe, not the retained list.
            "success": error.is_none() || fetched_count > 0,
            "models_count": models_count,
            "provider": summary,
        });
        if let Some(error) = error {
            result["models_error"] = json!(error);
        }
        Ok(result)
    }
}

// ------------------------------------------------------------------ handlers

async fn provider_catalog(State(app): State<Arc<Providers>>) -> Response {
    let result = tokio::task::spawn_blocking({
        let app = app.clone();
        move || {
            let entries = app.entries();
            let names = app.names(&entries);
            let data = app.store.load(&names);
            let accounts = codex::accounts(&app.config.data_dir);
            let registry = Registry::load(&app.config.repo_config, &app.config.data_dir);
            json!({
                "success": true,
                "catalog": catalog::rows(&data, &entries, &accounts, &registry),
            })
        }
    })
    .await;
    match result {
        Ok(value) => json_response(StatusCode::OK, value),
        Err(_) => Failure::internal("provider catalog unavailable").into_response(),
    }
}

async fn provider_list(State(app): State<Arc<Providers>>) -> Response {
    let result = tokio::task::spawn_blocking({
        let app = app.clone();
        move || {
            let entries = app.entries();
            let names = app.names(&entries);
            let data = app.store.load(&names);
            let providers: Vec<Value> = data
                .records()
                .iter()
                .map(|(id, record)| catalog::summary(id, record))
                .collect();
            json!({"success": true, "providers": providers})
        }
    })
    .await;
    match result {
        Ok(value) => json_response(StatusCode::OK, value),
        Err(_) => Failure::internal("provider store unavailable").into_response(),
    }
}

async fn provider_connect(State(app): State<Arc<Providers>>, body: Bytes) -> Response {
    let payload = object_body(&body);
    let entries = app.entries();
    let spec = match connect_spec(&entries, &payload) {
        Ok(spec) => spec,
        Err(failure) => return failure.into_response(),
    };
    let client = match app.client() {
        Ok(client) => client,
        Err(error) => return Failure::internal(error).into_response(),
    };
    let discovery =
        discovery::fetch_models(&client, &spec.base_url, &spec.api_key, &spec.headers).await;
    let discovery = discovery::static_fallback(spec.entry.as_ref(), discovery);
    let models = discovery.models.clone();
    let error = discovery.error.clone();
    let models_count = models.len();
    let record_error = error.clone();
    let names = app.names(&entries);
    let catalog_ids = catalog::ids(&entries);
    let committed = tokio::task::spawn_blocking({
        let store = app.store.clone();
        move || {
            store.transact(&names, |data| {
                let provider_id = commit_connection(
                    data.records_mut(),
                    &catalog_ids,
                    &spec,
                    discovery.models,
                    record_error,
                )?;
                let record = data.records()[&provider_id].clone();
                Ok(catalog::summary(&provider_id, &record))
            })
        }
    })
    .await;
    match committed {
        Ok(Ok(Ok(summary))) => {
            app.refresh_modelsdev_cache().await;
            let mut result = json!({
                "success": true,
                "provider": summary,
                "models_count": models_count,
            });
            if let Some(error) = error {
                result["models_error"] = json!(error);
            }
            json_response(StatusCode::OK, result)
        }
        Ok(Ok(Err(error))) => Failure::bad_request(error).into_response(),
        Ok(Err(error)) => {
            Failure::internal(format!("provider store unavailable: {error}")).into_response()
        }
        Err(_) => Failure::internal("provider store unavailable").into_response(),
    }
}

async fn provider_refresh(
    State(app): State<Arc<Providers>>,
    AxumPath(provider_id): AxumPath<String>,
) -> Response {
    match app.refresh_one(&provider_id).await {
        Ok(result) => {
            let status = if result["success"].as_bool() == Some(true) {
                StatusCode::OK
            } else {
                StatusCode::BAD_REQUEST
            };
            json_response(status, result)
        }
        Err(failure) => failure.into_response(),
    }
}

async fn provider_refresh_all(State(app): State<Arc<Providers>>) -> Response {
    let _guard = match app.begin_refresh() {
        Ok(guard) => guard,
        Err(skipped) => return json_response(StatusCode::OK, skipped),
    };
    let ids = {
        let app = app.clone();
        tokio::task::spawn_blocking(move || {
            let entries = app.entries();
            let names = app.names(&entries);
            let data = app.store.load(&names);
            data.records()
                .iter()
                .filter(|(_, record)| record.get("enabled").map(truthy).unwrap_or(true))
                .map(|(id, _)| id.clone())
                .collect::<Vec<String>>()
        })
        .await
        .unwrap_or_default()
    };
    let mut results = Vec::new();
    for id in ids {
        let outcome = app.refresh_one(&id).await;
        match outcome {
            Ok(result) => results.push(json!({
                "kind": "provider",
                "id": id,
                "success": result["success"].as_bool().unwrap_or(false),
                "models_count": result["models_count"].clone(),
                "error": result["error"].as_str().or_else(|| result["models_error"].as_str()).unwrap_or(""),
            })),
            Err(failure) => results.push(json!({
                "kind": "provider",
                "id": id,
                "success": false,
                "models_count": Value::Null,
                "error": failure.error_code(),
            })),
        }
    }
    results.extend(codex_oauth::refresh_ready_accounts(&app.config.codex).await);
    let failed = results
        .iter()
        .filter(|result| result["success"].as_bool() != Some(true))
        .count();
    json_response(
        StatusCode::OK,
        json!({
            "success": true,
            "skipped": false,
            "count": results.len(),
            "failed": failed,
            "results": results,
        }),
    )
}

async fn provider_disconnect(
    State(app): State<Arc<Providers>>,
    AxumPath(provider_id): AxumPath<String>,
) -> Response {
    let entries = app.entries();
    let names = app.names(&entries);
    let removed = tokio::task::spawn_blocking({
        let store = app.store.clone();
        move || {
            store.transact(&names, |data| {
                if data.records_mut().shift_remove(&provider_id).is_none() {
                    return Err("provider_not_found".into());
                }
                Ok(())
            })
        }
    })
    .await;
    match removed {
        Ok(Ok(Ok(()))) => json_response(StatusCode::OK, json!({"success": true})),
        Ok(Ok(Err(error))) => Failure::not_found(error).into_response(),
        Ok(Err(error)) => {
            Failure::internal(format!("provider store unavailable: {error}")).into_response()
        }
        Err(_) => Failure::internal("provider store unavailable").into_response(),
    }
}

// ------------------------------------------------------------- custom models

async fn custom_list(State(app): State<Arc<Providers>>) -> Response {
    let models = app.custom.clone();
    let result = tokio::task::spawn_blocking(move || {
        let (items, effective) = models.read()?;
        Ok::<_, String>(json!({
            "success": true,
            "models": items,
            "source_path": effective.to_string_lossy(),
        }))
    })
    .await;
    match result {
        Ok(Ok(value)) => json_response(StatusCode::OK, value),
        Ok(Err(error)) => Failure::internal(error).into_response(),
        Err(_) => Failure::internal("custom models unavailable").into_response(),
    }
}

async fn custom_create(State(app): State<Arc<Providers>>, body: Bytes) -> Response {
    let payload = object_body(&body);
    let item = match custom::sanitize(&payload) {
        Ok(item) => item,
        Err(error) => return Failure::bad_request(error).into_response(),
    };
    let models = app.custom.clone();
    let result = tokio::task::spawn_blocking(move || {
        let (mut items, _) = models.read()?;
        let name = item["model_name"].as_str().unwrap_or_default();
        if custom::find_index(&items, name).is_some() {
            return Ok::<_, String>(Err(("model_name_exists", StatusCode::CONFLICT)));
        }
        items.push(item.clone());
        models.write(&items)?;
        Ok(Ok(json!({"success": true, "model": item})))
    })
    .await;
    custom_write_response(result)
}

async fn custom_update(
    State(app): State<Arc<Providers>>,
    AxumPath(model_name): AxumPath<String>,
    body: Bytes,
) -> Response {
    let payload = object_body(&body);
    let item = match custom::sanitize(&payload) {
        Ok(item) => item,
        Err(error) => return Failure::bad_request(error).into_response(),
    };
    let models = app.custom.clone();
    let result = tokio::task::spawn_blocking(move || {
        let (mut items, _) = models.read()?;
        let Some(index) = custom::find_index(&items, &model_name) else {
            return Ok::<_, String>(Err(("model_not_found", StatusCode::NOT_FOUND)));
        };
        let new_name = item["model_name"].as_str().unwrap_or_default();
        if new_name != model_name && custom::find_index(&items, new_name).is_some() {
            return Ok(Err(("model_name_exists", StatusCode::CONFLICT)));
        }
        items[index] = item.clone();
        models.write(&items)?;
        Ok(Ok(json!({"success": true, "model": item})))
    })
    .await;
    custom_write_response(result)
}

async fn custom_delete(
    State(app): State<Arc<Providers>>,
    AxumPath(model_name): AxumPath<String>,
) -> Response {
    let models = app.custom.clone();
    let result = tokio::task::spawn_blocking(move || {
        let (items, _) = models.read()?;
        let remaining: Vec<Value> = items
            .iter()
            .filter(|item| item["model_name"].as_str() != Some(model_name.as_str()))
            .cloned()
            .collect();
        if remaining.len() == items.len() {
            return Ok::<_, String>(Err(("model_not_found", StatusCode::NOT_FOUND)));
        }
        models.write(&remaining)?;
        Ok(Ok(json!({"success": true})))
    })
    .await;
    custom_write_response(result)
}

type CustomResult = Result<Result<Value, (&'static str, StatusCode)>, String>;

fn custom_write_response(result: Result<CustomResult, tokio::task::JoinError>) -> Response {
    match result {
        Ok(Ok(Ok(value))) => json_response(StatusCode::OK, value),
        Ok(Ok(Err((error, status)))) => {
            json_response(status, json!({"success": false, "error": error}))
        }
        Ok(Err(error)) => Failure::internal(error).into_response(),
        Err(_) => Failure::internal("custom models unavailable").into_response(),
    }
}

// ------------------------------------------------------------------ helpers

struct ConnectSpec {
    catalog_id: Option<String>,
    requested_id: String,
    base_name: String,
    base_url: String,
    api_key: String,
    headers: Map<String, Value>,
    entry: Option<Value>,
}

fn connect_spec(entries: &[Value], payload: &Map<String, Value>) -> Result<ConnectSpec, Failure> {
    let catalog_id = store::trimmed(payload.get("catalog_id").unwrap_or(&Value::Null));
    let mut api_key = store::trimmed(payload.get("api_key").unwrap_or(&Value::Null));
    let (entry, requested_id, base_name, base_url, auth) = if !catalog_id.is_empty() {
        let entry = catalog::find(entries, &catalog_id)
            .ok_or_else(|| Failure::bad_request("unknown_catalog_id"))?;
        if entry["auth"].as_str() == Some("codex_oauth") {
            return Err(Failure::bad_request("codex_oauth_use_codex_api"));
        }
        (
            Some(entry.clone()),
            catalog_id.clone(),
            entry["name"]
                .as_str()
                .filter(|name| !name.is_empty())
                .unwrap_or(&catalog_id)
                .to_owned(),
            entry["base_url"].as_str().unwrap_or_default().to_owned(),
            entry["auth"]
                .as_str()
                .filter(|auth| !auth.is_empty())
                .unwrap_or("api_key")
                .to_owned(),
        )
    } else {
        let requested_id = store::trimmed(payload.get("provider_id").unwrap_or(&Value::Null));
        let base_name = {
            let name = store::trimmed(payload.get("name").unwrap_or(&Value::Null));
            if name.is_empty() {
                requested_id.clone()
            } else {
                name
            }
        };
        let base_url = store::trimmed(payload.get("base_url").unwrap_or(&Value::Null));
        if !valid_provider_id(&requested_id) {
            return Err(Failure::bad_request("invalid_provider_id"));
        }
        if !(base_url.starts_with("http://") || base_url.starts_with("https://")) {
            return Err(Failure::bad_request("invalid_base_url"));
        }
        let auth = if api_key.is_empty() {
            "none"
        } else {
            "api_key"
        };
        (None, requested_id, base_name, base_url, auth.to_owned())
    };
    if auth == "api_key" && api_key.is_empty() {
        return Err(Failure::bad_request("api_key_required"));
    }
    if auth == "none" && api_key.is_empty() {
        // Local services (Ollama/LM Studio) have no key. The placeholder passes
        // downstream profile validation and is never displayed as a credential.
        api_key = "not-required".into();
    }
    let mut headers = Map::new();
    if let Some(values) = payload.get("headers").and_then(Value::as_object) {
        for (name, value) in values {
            let name = name.trim();
            if !name.is_empty() {
                headers.insert(
                    name.to_owned(),
                    Value::String(discovery::python_text(value)),
                );
            }
        }
    }
    Ok(ConnectSpec {
        catalog_id: (!catalog_id.is_empty()).then_some(catalog_id),
        requested_id,
        base_name,
        base_url,
        api_key,
        headers,
        entry,
    })
}

fn commit_connection(
    records: &mut Map<String, Value>,
    catalog_ids: &std::collections::HashSet<String>,
    spec: &ConnectSpec,
    models: Vec<Value>,
    error: Option<String>,
) -> Result<String, String> {
    let (display_index, provider_id) = if let Some(catalog_id) = &spec.catalog_id {
        for existing in records.values() {
            if existing["catalog_id"].as_str() == Some(catalog_id)
                && existing.get("enabled").map(truthy).unwrap_or(true)
                && existing["api_key"].as_str() == Some(spec.api_key.as_str())
            {
                return Err("duplicate_connection".into());
            }
        }
        let index = store::allocate_display_index(records, catalog_id);
        let provider_id = store::new_connection_id(records, catalog_id)
            .ok_or_else(|| "provider_id_allocation_failed".to_owned())?;
        (index, provider_id)
    } else {
        for (existing_id, existing) in records.iter() {
            if existing["catalog_id"]
                .as_str()
                .is_some_and(|value| !value.is_empty())
            {
                continue;
            }
            if catalog::requested_id(existing_id, existing) == spec.requested_id {
                return Err("provider_id_exists".into());
            }
        }
        let provider_id = store::new_custom_connection_id(records, catalog_ids, &spec.requested_id)
            .ok_or_else(|| "provider_id_allocation_failed".to_owned())?;
        (0, provider_id)
    };
    let now = store::now_iso();
    let record = json!({
        "catalog_id": spec.catalog_id.clone(),
        "requested_id": spec.requested_id.clone(),
        "base_name": spec.base_name.clone(),
        "display_index": display_index,
        "name": store::display_name(&spec.base_name, display_index),
        "base_url": spec.base_url.trim_end_matches('/').to_owned(),
        "api_key": spec.api_key.clone(),
        "headers": spec.headers.clone(),
        "enabled": true,
        "connected_at": now,
        "models_fetched_at": now,
        "models": models,
        "models_error": error,
    });
    records.insert(provider_id.clone(), record);
    Ok(provider_id)
}

fn valid_provider_id(value: &str) -> bool {
    let bytes = value.as_bytes();
    let Some(first) = bytes.first() else {
        return false;
    };
    if bytes.len() > PROVIDER_ID_PATTERN_MAX
        || !(first.is_ascii_lowercase() || first.is_ascii_digit())
    {
        return false;
    }
    bytes[1..].iter().all(|byte| {
        byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'_' | b'-')
    })
}

fn object_body(bytes: &Bytes) -> Map<String, Value> {
    serde_json::from_slice::<Value>(bytes)
        .ok()
        .and_then(|value| value.as_object().cloned())
        .unwrap_or_default()
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn epoch_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or_default()
}

/// Python `_modelsdev_keys`: only catalog entries that opt into models.dev are
/// kept in the slim cache.
fn modelsdev_keys(entries: &[Value]) -> BTreeSet<String> {
    entries
        .iter()
        .filter_map(|entry| entry["modelsdev_key"].as_str())
        .filter(|key| !key.is_empty())
        .map(str::to_owned)
        .collect()
}

fn json_response(status: StatusCode, value: Value) -> Response {
    (status, Json(value)).into_response()
}

async fn require_admin_session(req: Request, next: Next) -> Response {
    if req.extensions().get::<BrowserSession>().is_none() {
        return Failure::forbidden("admin_required").into_response();
    }
    next.run(req).await
}

pub(crate) struct Failure {
    status: StatusCode,
    body: Value,
}

impl Failure {
    fn bad_request(error: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            body: json!({"success": false, "error": error.into()}),
        }
    }

    fn not_found(error: impl Into<String>) -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            body: json!({"success": false, "error": error.into()}),
        }
    }

    fn forbidden(error: impl Into<String>) -> Self {
        Self {
            status: StatusCode::FORBIDDEN,
            body: json!({"success": false, "error": error.into()}),
        }
    }

    fn internal(error: impl Into<String>) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            body: json!({"success": false, "error": error.into()}),
        }
    }

    fn error_code(&self) -> String {
        self.body["error"].as_str().unwrap_or_default().to_owned()
    }
}

impl IntoResponse for Failure {
    fn into_response(self) -> Response {
        json_response(self.status, self.body)
    }
}

#[cfg(test)]
mod unit_tests {
    use super::*;

    #[test]
    fn provider_ids_follow_the_python_pattern() {
        assert!(valid_provider_id("myprovider"));
        assert!(valid_provider_id("a1-b_2"));
        assert!(!valid_provider_id(""));
        assert!(!valid_provider_id("-start"));
        assert!(!valid_provider_id("Upper"));
        assert!(!valid_provider_id("has space"));
    }

    #[test]
    fn connection_ids_are_unique_and_shaped() {
        let mut records = Map::new();
        let id = store::new_connection_id(&records, "deepseek").unwrap();
        assert!(id.starts_with("deepseek-"));
        assert_eq!(id.len(), "deepseek-".len() + 8);
        records.insert(id.clone(), json!({}));
        assert_ne!(store::new_connection_id(&records, "deepseek").unwrap(), id);
    }

    #[test]
    fn mask_helpers_share_the_public_summary() {
        assert_eq!(
            catalog::mask_key(Some("sk-abcdefghijklmnop")).as_deref(),
            Some("sk-ab...mnop")
        );
        assert_eq!(catalog::mask_key(Some("not-required")), None);
        assert_eq!(catalog::mask_key(None), None);
    }

    fn provider_state() -> (std::path::PathBuf, Arc<Providers>) {
        let directory = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!("providers-gate-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(directory.join("config")).unwrap();
        std::fs::create_dir_all(directory.join("data")).unwrap();
        std::fs::create_dir_all(directory.join("deploy")).unwrap();
        let state = Arc::new(Providers {
            config: ProviderConfig::new(
                directory.join("config"),
                directory.join("data"),
                directory.join("deploy"),
            ),
            store: Arc::new(Store::new(&directory.join("data"))),
            custom: custom::CustomModels::new(&directory.join("config"), &directory.join("deploy")),
            throttle: Mutex::new(Throttle::default()),
        });
        (directory, state)
    }

    #[test]
    fn auto_refresh_gate_reports_in_progress_then_throttles() {
        let (directory, state) = provider_state();
        let guard = state.begin_refresh().expect("first run starts");
        let skipped = state.begin_refresh().err().expect("second run is skipped");
        assert_eq!(skipped["reason"], json!("in_progress"));
        drop(guard);
        let throttled = state.begin_refresh().err().expect("the hour is gated");
        assert_eq!(throttled["reason"], json!("throttled"));
        assert!(throttled["last_refresh_at"].is_number());
        assert!(throttled["next_refresh_at"].as_f64() > throttled["last_refresh_at"].as_f64());
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn local_store_round_trips_0600_atomically() {
        let directory = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!("providers-unit-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&directory).unwrap();
        let store = Store::new(&directory);
        let data = store.load(&HashMap::new());
        assert!(data.records().is_empty());
        std::fs::write(directory.join("providers.json"), "{}").unwrap();
        assert!(store.load(&HashMap::new()).records().is_empty());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(directory.join("providers.json"))
                .unwrap()
                .permissions()
                .mode();
            assert_eq!(mode & 0o777, 0o600);
        }
        std::fs::remove_dir_all(&directory).unwrap();
    }
}
