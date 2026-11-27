//! Isolated contract tests for the Rust provider management API.
//!
//! Every test uses a temporary Host data directory and a loopback mock upstream.
//! No real provider catalog credentials, `providers.json`, or network hosts are
//! read. The shared `catalog_model_limits.json` fixture keeps the `/models`
//! parser aligned with the Rust model catalog.

use std::{
    collections::{HashMap, VecDeque},
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

use axum::{
    Json, Router,
    body::{Body, Bytes, to_bytes},
    http::{HeaderMap, Method, Request, StatusCode, header::CONTENT_TYPE},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::{Value, json};
use tokio::{net::TcpListener, task::JoinHandle};
use tower::ServiceExt;
use uuid::Uuid;

use super::{ProviderConfig, codex_router, router};
use crate::web_auth::BrowserSession;

const SECRET: &str = "sk-fixture-provider-secret-0123456789";

struct Fixture {
    root: PathBuf,
    repo: PathBuf,
    data: PathBuf,
    deploy: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-24_Rust后端")
            .join(format!("providers_tmp_{}", Uuid::new_v4().simple()));
        fs::create_dir(&root).unwrap();
        let repo = root.join("config");
        let data = root.join("data");
        let deploy = root.join("deploy");
        fs::create_dir_all(&repo).unwrap();
        fs::create_dir_all(&data).unwrap();
        fs::create_dir_all(&deploy).unwrap();
        Self {
            root,
            repo,
            data,
            deploy,
        }
    }

    fn app(&self) -> Router {
        self.app_with_client(None)
    }

    fn app_with_modelsdev(&self, url: &str) -> Router {
        self.app_with_client(Some(url))
    }

    fn config_with_codex(
        &self,
        modelsdev_url: Option<&str>,
        codex: Option<(&str, &str, &str)>,
    ) -> ProviderConfig {
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(5))
            .build()
            .unwrap();
        let mut config = ProviderConfig::new(&self.repo, &self.data, &self.deploy)
            .with_client(client)
            // Every non-models.dev test must stay completely offline.
            .without_modelsdev_refresh();
        if let Some(url) = modelsdev_url {
            config = config.with_modelsdev_url(url);
        }
        if let Some((models_url, token_url, npm_url)) = codex {
            config = config.with_codex_endpoints(models_url, token_url, npm_url);
        }
        config
    }

    fn app_with_client(&self, modelsdev_url: Option<&str>) -> Router {
        router(self.config_with_codex(modelsdev_url, None))
    }

    fn app_with_codex(&self, models_url: &str, token_url: &str, npm_url: &str) -> Router {
        let config = self.config_with_codex(None, Some((models_url, token_url, npm_url)));
        router(config.clone()).merge(codex_router(config))
    }

    fn web_router_with_codex(&self, models_url: &str, token_url: &str, npm_url: &str) -> Router {
        let config = self.config_with_codex(None, Some((models_url, token_url, npm_url)));
        Router::new()
            .merge(router(config.clone()))
            .merge(codex_router(config))
            .layer(axum::middleware::from_fn(inject_test_session))
    }

    fn headless_router_with_codex(
        &self,
        models_url: &str,
        token_url: &str,
        npm_url: &str,
    ) -> Router {
        Router::new()
            .merge(router(self.config_with_codex(
                None,
                Some((models_url, token_url, npm_url)),
            )))
            .layer(axum::middleware::from_fn(inject_test_session))
    }

    fn write_json(&self, path: &Path, value: Value) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
    }

    fn catalog(&self, entries: Value) {
        self.write_json(
            &self.repo.join("providers_catalog.json"),
            json!({"providers": entries}),
        );
    }

    fn providers(&self, value: Value) -> PathBuf {
        let path = self.data.join("providers.json");
        self.write_json(&path, value);
        path
    }

    fn read_providers(&self) -> Value {
        serde_json::from_slice(&fs::read(self.data.join("providers.json")).unwrap()).unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[derive(Clone)]
struct MockResponse {
    status: StatusCode,
    body: Value,
}

struct Upstream {
    base_url: String,
    seen: Arc<Mutex<Vec<HeaderMap>>>,
    task: JoinHandle<()>,
}

impl Drop for Upstream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn upstream(responses: Vec<(StatusCode, Value)>) -> Upstream {
    let responses: VecDeque<MockResponse> = responses
        .into_iter()
        .map(|(status, body)| MockResponse { status, body })
        .collect();
    let queue = Arc::new(Mutex::new(responses));
    let seen = Arc::new(Mutex::new(Vec::new()));
    let app = Router::new().route(
        "/v1/models",
        axum::routing::get({
            let queue = queue.clone();
            let seen = seen.clone();
            move |headers: HeaderMap| {
                let queue = queue.clone();
                let seen = seen.clone();
                async move {
                    seen.lock().unwrap().push(headers);
                    let response = {
                        let mut queue = queue.lock().unwrap();
                        if queue.len() > 1 {
                            queue.pop_front().unwrap()
                        } else {
                            queue.front().cloned().expect("a mock response is queued")
                        }
                    };
                    (response.status, Json(response.body))
                }
            }
        }),
    );
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    Upstream {
        base_url: format!("http://{address}/v1"),
        seen,
        task,
    }
}

struct ModelsDevUpstream {
    url: String,
    hits: Arc<Mutex<usize>>,
    task: JoinHandle<()>,
}

impl ModelsDevUpstream {
    fn hits(&self) -> usize {
        *self.hits.lock().unwrap()
    }
}

impl Drop for ModelsDevUpstream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn modelsdev_upstream(responses: Vec<(StatusCode, Value)>) -> ModelsDevUpstream {
    let responses: VecDeque<MockResponse> = responses
        .into_iter()
        .map(|(status, body)| MockResponse { status, body })
        .collect();
    let queue = Arc::new(Mutex::new(responses));
    let hits = Arc::new(Mutex::new(0usize));
    let app = Router::new().route(
        "/api.json",
        axum::routing::get({
            let queue = queue.clone();
            let hits = hits.clone();
            move || {
                let queue = queue.clone();
                let hits = hits.clone();
                async move {
                    *hits.lock().unwrap() += 1;
                    let response = {
                        let mut queue = queue.lock().unwrap();
                        if queue.len() > 1 {
                            queue.pop_front().unwrap()
                        } else {
                            queue.front().cloned().expect("a mock response is queued")
                        }
                    };
                    (response.status, Json(response.body))
                }
            }
        }),
    );
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    ModelsDevUpstream {
        url: format!("http://{address}/api.json"),
        hits,
        task,
    }
}

fn discovery_body() -> Value {
    json!({"object":"list","data":[
        {"id":"chat-model","name":"Chat Model","context_window":128000,"max_context_length":1000000,"max_prompt_tokens":900000,"max_output_tokens":64000},
        {"id":"thinking-model","context_length":256000,"max_output_tokens":32000},
        {"id":"text-embedding-3","display_name":"Embedding"},
        {"id":"dall-e-3"},
        {"id":"whisper-1"}
    ]})
}

async fn call(
    app: &Router,
    method: Method,
    uri: &str,
    body: Option<Value>,
    session: bool,
) -> (StatusCode, Value) {
    let mut builder = Request::builder().method(method).uri(uri);
    let body = match body {
        Some(body) => {
            builder = builder.header(CONTENT_TYPE, "application/json");
            Body::from(serde_json::to_vec(&body).unwrap())
        }
        None => Body::empty(),
    };
    let mut request = builder.body(body).unwrap();
    if session {
        request.extensions_mut().insert(BrowserSession::synthetic());
    }
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

fn record(_id: &str, catalog_id: Option<&str>, index: i64, api_key: &str, models: Value) -> Value {
    let base = "Fixture";
    json!({
        "catalog_id": catalog_id,
        "requested_id": catalog_id.unwrap_or("fixture-custom"),
        "base_name": base,
        "display_index": index,
        "name": if index > 0 { format!("{base}-{index}") } else { base.into() },
        "base_url": "https://upstream.invalid/v1",
        "api_key": api_key,
        "headers": {},
        "enabled": true,
        "connected_at": "2026-11-24T00:00:00.000000Z",
        "models": models,
        "models_fetched_at": "2026-11-24T00:00:00.000000Z",
        "models_error": null,
    })
}

fn entry(id: &str, auth: &str, extra: Value) -> Value {
    let mut entry = json!({
        "id": id,
        "name": format!("Fixture {id}"),
        "base_url": "https://upstream.invalid/v1",
        "icon": format!("{id}.svg"),
        "auth": auth,
    });
    for (key, value) in extra.as_object().unwrap() {
        entry[key] = value.clone();
    }
    entry
}

fn assert_no_secret(value: &Value) {
    let text = value.to_string();
    assert!(!text.contains(SECRET), "response leaked an API key: {text}");
    assert!(
        !text.contains("not-required"),
        "placeholder leaked as a credential"
    );
}

fn header<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    headers.get(name)?.to_str().ok()
}

#[tokio::test]
async fn catalog_separates_menu_rows_from_connection_rows() {
    let fixture = Fixture::new();
    fixture.catalog(json!([
        entry("fixture", "api_key", json!({})),
        entry("localonly", "none", json!({"local":true,"badge":"本地"})),
        entry(
            "gateway",
            "api_key",
            json!({"protocol_source":"models.dev","modelsdev_key":"fixture-gateway"})
        ),
        entry(
            "codex",
            "codex_oauth",
            json!({"badge":"ChatGPT 订阅 · OAuth"})
        )
    ]));
    fixture.write_json(
        &fixture.data.join("modelsdev_cache.json"),
        json!({"providers":{"fixture-gateway":{"models":{"raw-model":{"npm":"@ai-sdk/anthropic"}}}}}),
    );
    fixture.write_json(
        &fixture.data.join("codex_auth.json"),
        json!({"tokens":{"access_token":"fixture-access","refresh_token":"fixture-refresh","account_id":"fixture-account"},"last_refresh":"2026-11-24T00:00:00Z"}),
    );
    fixture.write_json(
        &fixture.data.join("codex_models.json"),
        json!({"fetched_at":"2026-11-24T01:00:00Z","models":[
            {"slug":"visible","visibility":"list","supported_in_api":true},
            {"slug":"hidden","visibility":"hide","supported_in_api":true},
            {"slug":"internal","visibility":"list","supported_in_api":false}
        ]}),
    );
    fixture.providers(json!({"version":2,"providers":{
        "fixture-aaaaaaaa": record("fixture-aaaaaaaa", Some("fixture"), 0, SECRET, json!([{"id":"chat-model"}])),
        "fixture-bbbbbbbb": record("fixture-bbbbbbbb", Some("fixture"), 1, "sk-second-key-12345678", json!([{"id":"chat-model"},{"id":"thinking-model"}])),
        "localonly-cccccccc": record("localonly-cccccccc", Some("localonly"), 0, "not-required", json!([{"id":"llama"}])),
        "gateway-dddddddd": record("gateway-dddddddd", Some("gateway"), 0, "sk-gateway-key-12345678", json!([{"id":"raw-model"},{"id":"chat"}]))
    }}));

    let (status, body) = call(
        &fixture.app(),
        Method::GET,
        "/api/providers/catalog",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["success"], json!(true));
    assert_no_secret(&body);
    let rows = body["catalog"].as_array().unwrap();
    assert_eq!(rows.len(), 4 + 4 + 1);

    let menu = |id: &str| {
        rows.iter()
            .find(|row| row["entry_kind"] == "catalog" && row["id"] == id)
            .unwrap()
    };
    assert_eq!(menu("fixture")["entry_kind"], json!("catalog"));
    assert_eq!(menu("fixture")["connection"], json!(false));
    assert_eq!(menu("fixture")["connected"], json!(false));
    assert_eq!(menu("fixture")["connection_count"], json!(2));
    assert_eq!(menu("fixture")["has_connections"], json!(true));
    assert_eq!(menu("fixture")["can_add"], json!(true));
    assert_eq!(
        menu("fixture")["icon_url"],
        json!("/static/icons/providers/fixture.svg")
    );
    assert_eq!(
        menu("localonly")["can_add"],
        json!(false),
        "auth=none already connected"
    );
    assert_eq!(menu("codex")["connection_count"], json!(1));

    let connection = |id: &str| {
        rows.iter()
            .find(|row| row["entry_kind"] == "connection" && row["id"] == id)
            .unwrap()
    };
    assert_eq!(connection("fixture-aaaaaaaa")["name"], json!("Fixture"));
    assert_eq!(connection("fixture-aaaaaaaa")["display_index"], json!(0));
    assert_eq!(connection("fixture-aaaaaaaa")["auth"], json!("api_key"));
    assert_eq!(
        connection("fixture-aaaaaaaa")["api_key_masked"],
        json!("sk-fi...6789")
    );
    assert_eq!(connection("fixture-aaaaaaaa")["models_count"], json!(1));
    assert_eq!(connection("fixture-aaaaaaaa")["state"], json!("ready"));
    assert_eq!(connection("fixture-bbbbbbbb")["name"], json!("Fixture-1"));
    assert_eq!(connection("localonly-cccccccc")["auth"], json!("none"));
    assert_eq!(connection("localonly-cccccccc")["local"], json!(true));
    assert_eq!(
        connection("localonly-cccccccc")["api_key_masked"],
        Value::Null
    );
    assert_eq!(
        connection("gateway-dddddddd")["unsupported_protocol_count"],
        json!(1)
    );

    let codex = connection("openai-codex");
    assert_eq!(codex["auth"], json!("codex_oauth"));
    assert_eq!(codex["connected"], json!(true));
    assert_eq!(codex["models_count"], json!(1));
    assert!(
        codex["name"]
            .as_str()
            .unwrap()
            .starts_with("OpenAI（ChatGPT 订阅）-")
    );
}

#[tokio::test]
async fn credential_missing_codex_accounts_stay_visible_without_stale_models() {
    let fixture = Fixture::new();
    fixture.catalog(json!([entry("openai-codex", "codex_oauth", json!({}))]));
    fixture.write_json(
        &fixture.data.join("codex_accounts.json"),
        json!({"accounts":{
            "openai-codex-aaaaaaaa":{"display_hash":"abcdef","created_at":"2026-11-24T00:00:00.000000Z","legacy":false}
        }}),
    );
    fixture.write_json(
        &fixture.data.join("codex_models/openai-codex-aaaaaaaa.json"),
        json!({"fetched_at":"2026-11-24T01:00:00Z","models":[{"slug":"visible","visibility":"list","supported_in_api":true}]}),
    );
    let (status, body) = call(
        &fixture.app(),
        Method::GET,
        "/api/providers/catalog",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let rows = body["catalog"].as_array().unwrap();
    let menu = rows
        .iter()
        .find(|row| row["entry_kind"] == "catalog")
        .unwrap();
    assert_eq!(menu["connection_count"], json!(1));
    let account = rows
        .iter()
        .find(|row| row["id"] == "openai-codex-aaaaaaaa")
        .unwrap();
    assert_eq!(account["connected"], json!(false));
    assert_eq!(account["state"], json!("credential_missing"));
    assert_eq!(account["credential_missing"], json!(true));
    assert_eq!(account["models_count"], json!(0));
    assert_eq!(account["models_fetched_at"], Value::Null);
    assert_eq!(account["name"], json!("OpenAI（ChatGPT 订阅）-abcdef"));
}

#[tokio::test]
async fn list_returns_masked_summaries_only_and_requires_a_host_session() {
    let fixture = Fixture::new();
    fixture.providers(json!({"version":2,"providers":{
        "fixture-aaaaaaaa": record("fixture-aaaaaaaa", Some("fixture"), 0, SECRET, json!([])),
        "localonly-cccccccc": record("localonly-cccccccc", Some("localonly"), 0, "not-required", json!([]))
    }}));
    let app = fixture.app();
    let (status, body) = call(&app, Method::GET, "/api/providers", None, false).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"], json!("admin_required"));

    let (status, body) = call(&app, Method::GET, "/api/providers", None, true).await;
    assert_eq!(status, StatusCode::OK);
    assert_no_secret(&body);
    let providers = body["providers"].as_array().unwrap();
    assert_eq!(providers.len(), 2);
    assert_eq!(providers[0]["api_key_masked"], json!("sk-fi...6789"));
    assert_eq!(providers[1]["api_key_masked"], Value::Null);
    assert_eq!(providers[0]["requested_id"], json!("fixture"));
    assert_eq!(providers[0]["has_custom_headers"], json!(false));
}

#[tokio::test]
async fn connect_discovery_persists_limits_and_rejects_duplicates() {
    let fixture = Fixture::new();
    let upstream = upstream(vec![(StatusCode::OK, discovery_body())]).await;
    let mut entry = entry("fixture", "api_key", json!({}));
    entry["base_url"] = json!(upstream.base_url);
    fixture.catalog(json!([entry]));
    let app = fixture.app();

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/connect",
        Some(json!({"catalog_id":"fixture","api_key":SECRET})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["success"], json!(true));
    assert_eq!(body["models_count"], json!(2));
    assert_no_secret(&body);
    let connection_id = body["provider"]["id"].as_str().unwrap().to_owned();
    assert!(connection_id.starts_with("fixture-"));
    assert_eq!(connection_id.len(), "fixture-".len() + 8);
    assert_eq!(body["provider"]["name"], json!("Fixture fixture"));
    assert_eq!(body["provider"]["display_index"], json!(0));
    assert_eq!(body["provider"]["requested_id"], json!("fixture"));
    assert_eq!(body["provider"]["base_name"], json!("Fixture fixture"));
    assert_eq!(body["provider"]["api_key_masked"], json!("sk-fi...6789"));

    {
        let seen = upstream.seen.lock().unwrap();
        assert_eq!(seen.len(), 1);
        assert_eq!(
            header(&seen[0], "authorization"),
            Some(format!("Bearer {SECRET}").as_str())
        );
        assert_eq!(header(&seen[0], "accept"), Some("application/json"));
    }

    let stored = fixture.read_providers();
    assert_eq!(stored["version"], json!(2));
    let saved = &stored["providers"][&connection_id];
    assert_eq!(saved["api_key"], json!(SECRET));
    assert_eq!(saved["base_url"], json!(upstream.base_url));
    assert_eq!(saved["models"][0]["id"], json!("chat-model"));
    assert_eq!(saved["models"][0]["context_length"], json!(1000000));
    assert_eq!(saved["models"][0]["default_context_window"], json!(128000));
    assert_eq!(saved["models"][0]["max_context_window"], json!(1000000));
    assert_eq!(saved["models"][0]["max_input_tokens"], json!(900000));
    assert_eq!(saved["models"][0]["max_output_tokens"], json!(64000));
    // Explicit upstream context_length is recorded as the explicit ceiling.
    assert_eq!(saved["models"][1]["context_length"], json!(256000));
    assert_eq!(saved["models"][1]["max_context_window"], json!(256000));
    assert!(
        saved["models"].as_array().unwrap().iter().all(|model| {
            let id = model["id"].as_str().unwrap();
            id != "text-embedding-3" && id != "gpt-image-1" && id != "whisper-1"
        }),
        "non-chat models are filtered"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(fixture.data.join("providers.json"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600, "credentials stay private");
    }

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/connect",
        Some(json!({"catalog_id":"fixture","api_key":SECRET})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], json!("duplicate_connection"));
}

#[tokio::test]
async fn connect_keeps_the_credential_when_discovery_fails() {
    let fixture = Fixture::new();
    let upstream = upstream(vec![(
        StatusCode::INTERNAL_SERVER_ERROR,
        json!({"error":"boom"}),
    )])
    .await;
    fixture.catalog(json!([{
        "id":"fixture", "name":"Fixture", "base_url":upstream.base_url, "auth":"api_key"
    }]));
    let (status, body) = call(
        &fixture.app(),
        Method::POST,
        "/api/providers/connect",
        Some(json!({"catalog_id":"fixture","api_key":SECRET})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["success"], json!(true));
    assert_eq!(body["models_count"], json!(0));
    assert_eq!(body["models_error"], json!("http_500"));
    let id = body["provider"]["id"].as_str().unwrap();
    assert_eq!(
        fixture.read_providers()["providers"][id]["models_error"],
        json!("http_500")
    );
}

#[tokio::test]
async fn static_models_fallback_is_used_only_when_online_discovery_fails() {
    let fixture = Fixture::new();
    let failing = upstream(vec![(StatusCode::NOT_FOUND, json!({"error":"no list"}))]).await;
    fixture.catalog(json!([{
        "id":"plan", "name":"Plan", "base_url":failing.base_url, "auth":"api_key",
        "static_models":["plan-a","plan-b"]
    }]));
    let (status, body) = call(
        &fixture.app(),
        Method::POST,
        "/api/providers/connect",
        Some(json!({"catalog_id":"plan","api_key":SECRET})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["models_count"], json!(2));
    assert_eq!(body["models_error"], Value::Null);
    assert_eq!(body["provider"]["models"][0]["id"], json!("plan-a"));
}

#[tokio::test]
async fn connect_custom_provider_uses_an_unreusable_internal_id() {
    let fixture = Fixture::new();
    let upstream = upstream(vec![(StatusCode::OK, discovery_body())]).await;
    let app = fixture.app();
    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/connect",
        Some(json!({
            "provider_id":"my-provider",
            "name":"My Provider",
            "base_url":upstream.base_url,
            "api_key":SECRET,
            "headers":{"X-Custom":"v1"," X-Trimmed ":" v2 "}
        })),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let connection_id = body["provider"]["id"].as_str().unwrap().to_owned();
    assert!(
        connection_id.starts_with("custom-my-provider-"),
        "{connection_id}"
    );
    assert_eq!(body["provider"]["catalog_id"], Value::Null);
    assert_eq!(body["provider"]["requested_id"], json!("my-provider"));
    assert_eq!(body["provider"]["base_name"], json!("My Provider"));
    assert_eq!(body["provider"]["has_custom_headers"], json!(true));
    assert_no_secret(&body);

    {
        let seen = upstream.seen.lock().unwrap();
        assert_eq!(header(&seen[0], "x-custom"), Some("v1"));
        // HTTP header parsing trims optional surrounding whitespace.
        assert_eq!(header(&seen[0], "x-trimmed"), Some("v2"));
        assert_eq!(
            header(&seen[0], "authorization"),
            Some(format!("Bearer {SECRET}").as_str())
        );
    }

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/connect",
        Some(json!({
            "provider_id":"my-provider","name":"Other","base_url":upstream.base_url,"api_key":"sk-other-key-12345678"
        })),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], json!("provider_id_exists"));

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/connect",
        Some(json!({
            "provider_id":"local-provider","name":"Local","base_url":upstream.base_url
        })),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    // The public provider summary never carries an auth discriminator; a local
    // placeholder key must not surface as a mask either.
    assert_eq!(body["provider"]["api_key_masked"], Value::Null);
    let stored = fixture.read_providers();
    assert_eq!(
        stored["providers"][body["provider"]["id"].as_str().unwrap()]["api_key"],
        json!("not-required")
    );
}

#[tokio::test]
async fn connect_validation_matches_python_error_codes() {
    let fixture = Fixture::new();
    fixture.catalog(json!([
        entry("fixture", "api_key", json!({})),
        entry("codex", "codex_oauth", json!({}))
    ]));
    let app = fixture.app();
    for (payload, error) in [
        (json!({"catalog_id":"missing"}), "unknown_catalog_id"),
        (
            json!({"catalog_id":"codex","api_key":SECRET}),
            "codex_oauth_use_codex_api",
        ),
        (json!({"catalog_id":"fixture"}), "api_key_required"),
        (
            json!({"provider_id":"Bad Id","base_url":"https://x.invalid","api_key":SECRET}),
            "invalid_provider_id",
        ),
        (
            json!({"provider_id":"ok","base_url":"ftp://x.invalid","api_key":SECRET}),
            "invalid_base_url",
        ),
    ] {
        let (status, body) = call(
            &app,
            Method::POST,
            "/api/providers/connect",
            Some(payload),
            true,
        )
        .await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(body["error"], json!(error));
    }
}

#[tokio::test]
async fn v1_store_is_migrated_in_place_without_reusing_ids() {
    let fixture = Fixture::new();
    fixture.catalog(json!([entry("fixture", "api_key", json!({}))]));
    let path = fixture.providers(json!({"version":1,"providers":{
        "fixture":{"catalog_id":"fixture","name":"Fixture","base_url":"https://upstream.invalid/v1","api_key":SECRET,"models":[{"id":"old-model"}],"connected_at":"2026-01-01T00:00:00.000000Z"},
        "legacycustom":{"name":"Legacy Custom","base_url":"https://upstream.invalid/v1","api_key":"sk-legacy-key-12345678","models":[]}
    }}));
    let (status, body) = call(&fixture.app(), Method::GET, "/api/providers", None, true).await;
    assert_eq!(status, StatusCode::OK);
    let providers = body["providers"].as_array().unwrap();
    let migrated = providers.iter().find(|row| row["id"] == "fixture").unwrap();
    assert_eq!(migrated["display_index"], json!(0));
    assert_eq!(migrated["name"], json!("Fixture fixture"));
    assert_eq!(migrated["base_name"], json!("Fixture fixture"));
    assert_eq!(migrated["requested_id"], json!("fixture"));
    let custom = providers
        .iter()
        .find(|row| row["id"] == "legacycustom")
        .unwrap();
    assert_eq!(custom["display_index"], json!(0));
    assert_eq!(custom["requested_id"], json!("legacycustom"));
    assert_eq!(custom["base_name"], json!("Legacy Custom"));

    let raw: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    assert_eq!(raw["version"], json!(2));
    assert_eq!(raw["providers"]["fixture"]["display_index"], json!(0));
}

#[tokio::test]
async fn refresh_updates_models_and_keeps_the_previous_list_on_failure() {
    let fixture = Fixture::new();
    let upstream = upstream(vec![
        (
            StatusCode::OK,
            json!({"data":[{"id":"new-model","max_output_tokens":4096}]}),
        ),
        (StatusCode::INTERNAL_SERVER_ERROR, json!({"error":"boom"})),
    ])
    .await;
    fixture.providers(json!({"version":2,"providers":{
        "fixture-aaaaaaaa": {
            "catalog_id":"fixture","requested_id":"fixture","base_name":"Fixture","display_index":0,
            "name":"Fixture","base_url":upstream.base_url,"api_key":SECRET,"headers":{},"enabled":true,
            "connected_at":"2026-11-24T00:00:00.000000Z",
            "models":[{"id":"old-model"}],"models_fetched_at":"2026-11-24T00:00:00.000000Z","models_error":null
        }
    }}));
    let app = fixture.app();
    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/fixture-aaaaaaaa/refresh",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["success"], json!(true));
    assert_eq!(body["models_count"], json!(1));
    assert_eq!(body["provider"]["models"][0]["id"], json!("new-model"));
    assert_no_secret(&body);

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/fixture-aaaaaaaa/refresh",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{body}");
    assert_eq!(body["success"], json!(false));
    assert_eq!(body["models_error"], json!("http_500"));
    assert_eq!(
        body["provider"]["models"][0]["id"],
        json!("new-model"),
        "old list is retained"
    );
    assert_eq!(body["models_count"], json!(1));

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/missing/refresh",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], json!("provider_not_found"));
}

#[tokio::test]
async fn refresh_all_covers_enabled_connections_and_is_throttled() {
    let fixture = Fixture::new();
    let upstream = upstream(vec![(StatusCode::OK, json!({"data":[{"id":"one"}]}))]).await;
    fixture.providers(json!({"version":2,"providers":{
        "fixture-aaaaaaaa": record("fixture-aaaaaaaa", Some("fixture"), 0, SECRET, json!([{"id":"old"}])),
        "fixture-bbbbbbbb": record("fixture-bbbbbbbb", Some("fixture"), 1, "sk-second-key-12345678", json!([])),
        "fixture-cccccccc": {
            "catalog_id":"fixture","requested_id":"fixture","base_name":"Fixture","display_index":2,
            "name":"Fixture-2","base_url":upstream.base_url,"api_key":"sk-third-key-12345678","headers":{},
            "enabled":false,"connected_at":"2026-11-24T00:00:00.000000Z","models":[],"models_error":null
        }
    }}));
    let mut raw = fixture.read_providers();
    raw["providers"]["fixture-aaaaaaaa"]["base_url"] = json!(upstream.base_url);
    raw["providers"]["fixture-bbbbbbbb"]["base_url"] = json!(upstream.base_url);
    fixture.providers(raw);
    let app = fixture.app();

    let (status, body) = call(&app, Method::POST, "/api/providers/refresh-all", None, true).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["success"], json!(true));
    assert_eq!(body["skipped"], json!(false));
    assert_eq!(body["count"], json!(2), "disabled connections are skipped");
    assert_eq!(body["failed"], json!(0));
    assert_eq!(body["results"][0]["kind"], json!("provider"));
    assert_eq!(body["results"][0]["models_count"], json!(1));

    let (status, body) = call(&app, Method::POST, "/api/providers/refresh-all", None, true).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["skipped"], json!(true));
    assert_eq!(body["reason"], json!("throttled"));
    assert!(body["last_refresh_at"].is_number());
    assert!(body["next_refresh_at"].as_f64().unwrap() > body["last_refresh_at"].as_f64().unwrap());
}

#[tokio::test]
async fn disconnect_removes_only_that_connection_and_keeps_indices() {
    let fixture = Fixture::new();
    fixture.providers(json!({"version":2,"providers":{
        "fixture-aaaaaaaa": record("fixture-aaaaaaaa", Some("fixture"), 0, SECRET, json!([{"id":"a"}])),
        "fixture-bbbbbbbb": record("fixture-bbbbbbbb", Some("fixture"), 1, "sk-second-key-12345678", json!([{"id":"b"}]))
    }}));
    let app = fixture.app();
    let (status, body) = call(
        &app,
        Method::DELETE,
        "/api/providers/fixture-aaaaaaaa",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["success"], json!(true));
    let (status, body) = call(
        &app,
        Method::DELETE,
        "/api/providers/fixture-aaaaaaaa",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], json!("provider_not_found"));

    let (_, body) = call(&app, Method::GET, "/api/providers", None, true).await;
    let providers = body["providers"].as_array().unwrap();
    assert_eq!(providers.len(), 1);
    assert_eq!(providers[0]["id"], json!("fixture-bbbbbbbb"));
    assert_eq!(
        providers[0]["display_index"],
        json!(1),
        "survivors are not renumbered"
    );
}

#[tokio::test]
async fn discovery_parser_matches_the_shared_limit_fixture() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../test/2026-11-24_Rust后端/catalog_model_limits.json");
    let fixture: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    let parsed = super::discovery::parse_models_payload(&fixture["discovery"]);
    let expected = fixture["stored_models"].as_array().unwrap();
    for (index, expected) in expected.iter().take(5).enumerate() {
        assert_eq!(&parsed[index], expected, "limit fixture row {index}");
    }
    assert_eq!(parsed.len(), 5);
}

#[tokio::test]
async fn positive_limits_reject_booleans_negatives_and_fractions() {
    assert_eq!(
        super::discovery::positive_limit(&json!(128000)),
        Some(128000)
    );
    assert_eq!(
        super::discovery::positive_limit(&json!("65536")),
        Some(65536)
    );
    assert_eq!(
        super::discovery::positive_limit(&json!(128000.0)),
        Some(128000)
    );
    assert_eq!(super::discovery::positive_limit(&json!(true)), None);
    assert_eq!(super::discovery::positive_limit(&json!(-1)), None);
    assert_eq!(super::discovery::positive_limit(&json!(1.5)), None);
    assert_eq!(super::discovery::positive_limit(&json!("abc")), None);
}

#[tokio::test]
async fn upstream_errors_use_python_compatible_codes() {
    let fixture = Fixture::new();
    let unauthorized = upstream(vec![(StatusCode::UNAUTHORIZED, json!({}))]).await;
    fixture.catalog(json!([{"id":"fixture","name":"Fixture","base_url":unauthorized.base_url,"auth":"api_key"}]));
    let (_, body) = call(
        &fixture.app(),
        Method::POST,
        "/api/providers/connect",
        Some(json!({"catalog_id":"fixture","api_key":SECRET})),
        true,
    )
    .await;
    assert_eq!(body["models_error"], json!("auth_failed (http_401)"));

    let empty = upstream(vec![(
        StatusCode::OK,
        json!({"data":[{"id":"text-embedding-3"}]}),
    )])
    .await;
    let fixture = Fixture::new();
    fixture.catalog(
        json!([{"id":"fixture","name":"Fixture","base_url":empty.base_url,"auth":"api_key"}]),
    );
    let (_, body) = call(
        &fixture.app(),
        Method::POST,
        "/api/providers/connect",
        Some(json!({"catalog_id":"fixture","api_key":SECRET})),
        true,
    )
    .await;
    assert_eq!(body["models_error"], json!("empty_models"));

    let bad = upstream(vec![(StatusCode::OK, json!({"unexpected":true}))]).await;
    let fixture = Fixture::new();
    fixture.catalog(
        json!([{"id":"fixture","name":"Fixture","base_url":bad.base_url,"auth":"api_key"}]),
    );
    let (_, body) = call(
        &fixture.app(),
        Method::POST,
        "/api/providers/connect",
        Some(json!({"catalog_id":"fixture","api_key":SECRET})),
        true,
    )
    .await;
    assert_eq!(body["models_error"], json!("empty_models"));
}

#[tokio::test]
async fn custom_models_crud_writes_the_deployment_copy_privately() {
    let fixture = Fixture::new();
    let seed = json!({"models":[{
        "model_name":"seed-model","url":"https://seed.invalid/v1","apikey":"fixture-seed-key","model_id":"seed-upstream"
    }]});
    fixture.write_json(&fixture.repo.join("custom_models.json"), seed);
    let app = fixture.app();

    let (status, body) = call(&app, Method::GET, "/api/custom-models", None, true).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["models"].as_array().unwrap().len(), 1);
    assert_eq!(
        body["source_path"].as_str().unwrap(),
        fixture
            .repo
            .join("custom_models.json")
            .to_string_lossy()
            .as_ref()
    );

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/custom-models",
        Some(json!({
            "model_name":"new-model","display_name":"New","url":"https://new.invalid/v1",
            "apikey":"fixture-new-key","model_id":"new-upstream","unknown_field":"drop-me"
        })),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["model"]["model_name"], json!("new-model"));
    assert!(body["model"].get("unknown_field").is_none());

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/custom-models",
        Some(json!({"model_name":"new-model","url":"https://new.invalid/v1","apikey":"k","model_id":"m"})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], json!("model_name_exists"));

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/custom-models",
        Some(
            json!({"model_name":"bad name","url":"https://x.invalid","apikey":"k","model_id":"m"}),
        ),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], json!("model_name_invalid"));

    let (status, body) = call(
        &app,
        Method::PUT,
        "/api/custom-models/new-model",
        Some(json!({"model_name":"renamed","url":"https://new.invalid/v1","apikey":"fixture-new-key","model_id":"new-upstream"})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["model"]["model_name"], json!("renamed"));
    let (status, body) = call(
        &app,
        Method::PUT,
        "/api/custom-models/seed-model",
        Some(json!({"model_name":"renamed","url":"https://seed.invalid/v1","apikey":"k","model_id":"m"})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], json!("model_name_exists"));
    let (status, _) = call(
        &app,
        Method::PUT,
        "/api/custom-models/missing",
        Some(json!({"model_name":"missing","url":"https://x.invalid","apikey":"k","model_id":"m"})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);

    let (status, body) = call(
        &app,
        Method::DELETE,
        "/api/custom-models/seed-model",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["success"], json!(true));
    let (status, body) = call(
        &app,
        Method::DELETE,
        "/api/custom-models/seed-model",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], json!("model_not_found"));

    let deployed: Value =
        serde_json::from_slice(&fs::read(fixture.deploy.join("custom_models.json")).unwrap())
            .unwrap();
    assert_eq!(deployed["models"].as_array().unwrap().len(), 1);
    assert_eq!(deployed["models"][0]["model_name"], json!("renamed"));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(fixture.deploy.join("custom_models.json"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }
}

#[tokio::test]
async fn refresh_all_does_not_require_csrf_when_mounted_behind_protect_api() {
    // The raw router only enforces the Host session extension; CSRF/origin
    // validation is supplied by WebAuth::protect_api at the mount site.
    let fixture = Fixture::new();
    let (status, _) = call(
        &fixture.app(),
        Method::POST,
        "/api/providers/refresh-all",
        Some(json!({})),
        false,
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
}

fn modelsdev_stamp(seconds_ago: i64) -> String {
    let now = time::OffsetDateTime::now_utc() - time::Duration::seconds(seconds_ago);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second()
    )
}

fn read_modelsdev_cache(fixture: &Fixture) -> Value {
    serde_json::from_slice(&fs::read(fixture.data.join("modelsdev_cache.json")).unwrap()).unwrap()
}

#[tokio::test]
async fn connect_refreshes_modelsdev_cache_with_python_v3_slim_contract() {
    let fixture = Fixture::new();
    let upstream = upstream(vec![(StatusCode::OK, discovery_body())]).await;
    let modelsdev = modelsdev_upstream(vec![(
        StatusCode::OK,
        json!({
            "fixture-gateway": {
                "npm": "@ai-sdk/openai",
                "models": {
                    "m1": {
                        "provider": {"npm": "@ai-sdk/anthropic"},
                        "reasoning": false,
                        "modalities": {"input": ["text", "image"]},
                        "limit": {"context": "200000", "input": 150000.0, "output": -1},
                        "cost": {"input": 1, "cache_read": 0.5, "output": 2, "cache_write": 9, "bad": "x"}
                    },
                    "m2": {"limit": {"context": true, "output": 32000}, "cost": {}},
                    "m3": {"reasoning": true, "modalities": {"input": []}}
                }
            },
            "not-in-catalog": {"npm": "@ai-sdk/google", "models": {"x": {}}}
        }),
    )])
    .await;
    let mut catalog_entry = entry(
        "gateway",
        "api_key",
        json!({"protocol_source": "models.dev", "modelsdev_key": "fixture-gateway"}),
    );
    catalog_entry["base_url"] = json!(upstream.base_url);
    fixture.catalog(json!([catalog_entry]));
    let app = fixture.app_with_modelsdev(&modelsdev.url);

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/connect",
        Some(json!({"catalog_id": "gateway", "api_key": SECRET})),
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let connection_id = body["provider"]["id"].as_str().unwrap().to_owned();
    assert_eq!(modelsdev.hits(), 1, "connect refreshes a missing cache");

    let cache = read_modelsdev_cache(&fixture);
    assert_eq!(cache["version"], json!(3));
    assert_eq!(cache["source"], json!("https://models.dev/api.json"));
    let fetched_at = cache["fetched_at"].as_str().unwrap();
    assert_eq!(fetched_at.len(), 20);
    assert!(fetched_at.ends_with('Z'));
    let providers = cache["providers"].as_object().unwrap();
    assert_eq!(
        providers.len(),
        1,
        "only catalog modelsdev_key entries are kept"
    );
    assert!(providers.get("not-in-catalog").is_none());
    let gateway = &providers["fixture-gateway"];
    assert_eq!(gateway["npm"], json!("@ai-sdk/openai"));
    let models = gateway["models"].as_object().unwrap();
    assert_eq!(models.len(), 3);
    assert_eq!(models["m1"]["npm"], json!("@ai-sdk/anthropic"));
    assert_eq!(models["m1"]["reasoning"], json!(false));
    assert_eq!(models["m1"]["modalities_input"], json!(["text", "image"]));
    assert_eq!(models["m1"]["context"], json!(200000));
    assert_eq!(models["m1"]["max_input"], json!(150000));
    assert!(models["m1"].get("max_output").is_none());
    assert_eq!(
        models["m1"]["cost"],
        json!({"input": 1.0, "cache_read": 0.5, "output": 2.0})
    );
    assert!(
        models["m2"].get("context").is_none(),
        "bool is not a token count"
    );
    assert_eq!(models["m2"]["max_output"], json!(32000));
    assert_eq!(models["m2"]["cost"], json!({}));
    assert_eq!(models["m3"]["reasoning"], json!(true));
    assert_eq!(models["m3"]["modalities_input"], json!([]));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(fixture.data.join("modelsdev_cache.json"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }

    let (status, body) = call(
        &app,
        Method::POST,
        &format!("/api/providers/{connection_id}/refresh"),
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(modelsdev.hits(), 1, "the fresh v3 cache is reused");
    assert_eq!(
        read_modelsdev_cache(&fixture)["fetched_at"],
        json!(fetched_at)
    );
}

#[tokio::test]
async fn modelsdev_refresh_keeps_the_old_cache_on_fetch_or_payload_failure() {
    let fixture = Fixture::new();
    let upstream = upstream(vec![(StatusCode::OK, json!({"data":[{"id":"one"}]}))]).await;
    let old = json!({
        "version": 3,
        "source": "https://models.dev/api.json",
        "fetched_at": "2000-01-01T00:00:00Z",
        "providers": {"sentinel": {"npm": null, "models": {"old": {}}}}
    });
    fixture.write_json(&fixture.data.join("modelsdev_cache.json"), old.clone());
    fixture.catalog(json!([entry(
        "gateway",
        "api_key",
        json!({"modelsdev_key": "fixture-gateway"})
    )]));
    let mut record = record(
        "gateway-aaaaaaaa",
        Some("gateway"),
        0,
        SECRET,
        json!([{"id":"old"}]),
    );
    record["base_url"] = json!(upstream.base_url);
    fixture.providers(json!({"version":2,"providers":{"gateway-aaaaaaaa":record}}));
    let modelsdev = modelsdev_upstream(vec![
        (StatusCode::INTERNAL_SERVER_ERROR, json!({"error":"boom"})),
        (StatusCode::OK, json!([])),
        (
            StatusCode::OK,
            json!({"fixture-gateway":{"npm":null,"models":{"m":{"limit":{"context":123}}}}}),
        ),
    ])
    .await;
    let app = fixture.app_with_modelsdev(&modelsdev.url);

    for (expected_hits, message) in [(1, "http failure"), (2, "non-object payload")] {
        let (status, body) = call(
            &app,
            Method::POST,
            "/api/providers/gateway-aaaaaaaa/refresh",
            None,
            true,
        )
        .await;
        assert_eq!(status, StatusCode::OK, "{message}: {body}");
        assert_eq!(modelsdev.hits(), expected_hits);
        assert_eq!(
            read_modelsdev_cache(&fixture),
            old,
            "{message} must preserve the previous cache"
        );
    }

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/gateway-aaaaaaaa/refresh",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(modelsdev.hits(), 3);
    let cache = read_modelsdev_cache(&fixture);
    assert!(cache["providers"].get("fixture-gateway").is_some());
    assert!(cache["providers"].get("sentinel").is_none());
    assert_eq!(
        cache["providers"]["fixture-gateway"]["models"]["m"]["context"],
        json!(123)
    );
    assert_eq!(cache["version"], json!(3));
}

#[tokio::test]
async fn modelsdev_refresh_skips_a_fresh_v3_cache_without_rewriting_it() {
    let fixture = Fixture::new();
    let upstream = upstream(vec![(StatusCode::OK, json!({"data":[{"id":"one"}]}))]).await;
    let fresh = json!({
        "version": 3,
        "source": "https://models.dev/api.json",
        "fetched_at": modelsdev_stamp(60),
        "providers": {"sentinel": {"npm": "@ai-sdk/openai", "models": {}}}
    });
    fixture.write_json(&fixture.data.join("modelsdev_cache.json"), fresh.clone());
    fixture.catalog(json!([entry(
        "gateway",
        "api_key",
        json!({"modelsdev_key": "fixture-gateway"})
    )]));
    let mut record = record(
        "gateway-aaaaaaaa",
        Some("gateway"),
        0,
        SECRET,
        json!([{"id":"old"}]),
    );
    record["base_url"] = json!(upstream.base_url);
    fixture.providers(json!({"version":2,"providers":{"gateway-aaaaaaaa":record}}));
    let modelsdev = modelsdev_upstream(vec![(
        StatusCode::OK,
        json!({"fixture-gateway":{"npm":null,"models":{"new":{}}}}),
    )])
    .await;
    let app = fixture.app_with_modelsdev(&modelsdev.url);

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/providers/gateway-aaaaaaaa/refresh",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(modelsdev.hits(), 0, "a six-hour-fresh cache is not fetched");
    assert_eq!(read_modelsdev_cache(&fixture), fresh);
}

#[derive(Clone)]
struct CodexReply {
    status: StatusCode,
    body: Value,
    etag: Option<String>,
}

struct CodexUpstream {
    models_url: String,
    token_url: String,
    npm_url: String,
    model_requests: Arc<Mutex<Vec<(HeaderMap, HashMap<String, String>)>>>,
    token_requests: Arc<Mutex<Vec<(HeaderMap, String)>>>,
    task: JoinHandle<()>,
}

impl Drop for CodexUpstream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

fn next_reply<T: Clone>(queue: &Mutex<VecDeque<T>>) -> T {
    let mut queue = queue.lock().unwrap();
    if queue.len() > 1 {
        queue.pop_front().unwrap()
    } else {
        queue.front().cloned().expect("a mock response is queued")
    }
}

async fn codex_upstream(
    models: Vec<CodexReply>,
    tokens: Vec<(StatusCode, Value)>,
    npm: (StatusCode, Value),
) -> CodexUpstream {
    let models = Arc::new(Mutex::new(models.into()));
    let tokens = Arc::new(Mutex::new(tokens.into()));
    let npm = Arc::new(Mutex::new(vec![npm].into()));
    let model_requests = Arc::new(Mutex::new(Vec::new()));
    let token_requests = Arc::new(Mutex::new(Vec::new()));
    let app = Router::new()
        .route(
            "/models",
            axum::routing::get({
                let models = models.clone();
                let seen = model_requests.clone();
                move |headers: HeaderMap,
                      axum::extract::Query(query): axum::extract::Query<
                    HashMap<String, String>,
                >| {
                    let models = models.clone();
                    let seen = seen.clone();
                    async move {
                        seen.lock().unwrap().push((headers, query));
                        let reply = next_reply(&models);
                        let mut response =
                            (reply.status, Json(reply.body)).into_response();
                        if let Some(etag) = reply.etag {
                            response.headers_mut().insert(
                                axum::http::header::ETAG,
                                axum::http::HeaderValue::from_str(&etag).unwrap(),
                            );
                        }
                        response
                    }
                }
            }),
        )
        .route(
            "/oauth/token",
            axum::routing::post({
                let tokens = tokens.clone();
                let seen = token_requests.clone();
                move |headers: HeaderMap, body: Bytes| {
                    let tokens = tokens.clone();
                    let seen = seen.clone();
                    async move {
                        let body = String::from_utf8_lossy(&body).into_owned();
                        seen.lock().unwrap().push((headers, body));
                        // Widen the race window so a missing per-account lock
                        // would produce two concurrent token rotations.
                        tokio::time::sleep(Duration::from_millis(50)).await;
                        let (status, body) = next_reply(&tokens);
                        (status, Json(body))
                    }
                }
            }),
        )
        .route(
            "/npm/latest",
            axum::routing::get({
                let npm = npm.clone();
                move || {
                    let npm = npm.clone();
                    async move {
                        let (status, body) = next_reply(&npm);
                        (status, Json(body))
                    }
                }
            }),
        );
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    CodexUpstream {
        models_url: format!("http://{address}/models"),
        token_url: format!("http://{address}/oauth/token"),
        npm_url: format!("http://{address}/npm/latest"),
        model_requests,
        token_requests,
        task,
    }
}

fn codex_models_body() -> Value {
    json!({"models": [
        {
            "slug": "gpt-5",
            "display_name": "GPT-5",
            "visibility": "list",
            "supported_in_api": true,
            "context_window": 128000,
            "max_context_window": 256000,
            "input_modalities": ["text", "image"]
        },
        {
            "slug": "internal-review",
            "visibility": "hide",
            "supported_in_api": true
        }
    ]})
}

fn synthetic_jwt(exp: i64) -> String {
    let payload = json!({
        "exp": exp,
        "https://api.openai.com/auth": {"chatgpt_account_id": "chatgpt-account-fixture"}
    })
    .to_string();
    format!("header.{}.signature", URL_SAFE_NO_PAD.encode(payload))
}

fn codex_credential_path(fixture: &Fixture, account_id: &str) -> PathBuf {
    if account_id == "openai-codex" {
        fixture.data.join("codex_auth.json")
    } else {
        fixture
            .data
            .join("codex_accounts")
            .join(format!("{account_id}.json"))
    }
}

fn write_codex_credential(
    fixture: &Fixture,
    account_id: &str,
    access_token: &str,
    refresh_token: &str,
    account: &str,
) {
    fixture.write_json(
        &codex_credential_path(fixture, account_id),
        json!({
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": null,
            "tokens": {
                "id_token": "",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "account_id": account
            },
            "last_refresh": "2026-11-24T00:00:00.000000Z"
        }),
    );
}

fn read_codex_cache(fixture: &Fixture, account_id: &str) -> Value {
    let path = if account_id == "openai-codex" {
        fixture.data.join("codex_models.json")
    } else {
        fixture
            .data
            .join("codex_models")
            .join(format!("{account_id}.json"))
    };
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

#[tokio::test]
async fn secondary_account_refresh_uses_saved_global_version_and_its_own_etag() {
    let fixture = Fixture::new();
    let id = "openai-codex-secondary";
    write_codex_credential(&fixture, id, "fixture-access", "fixture-refresh", "fixture-account");
    fixture.write_json(&fixture.data.join("codex_models").join(format!("{id}.json")),
        json!({"client_version": "1.0.0", "etag": "old-version-etag", "models": []}));
    let upstream = codex_upstream(
        vec![CodexReply { status: StatusCode::OK, body: codex_models_body(), etag: None }],
        vec![], (StatusCode::SERVICE_UNAVAILABLE, Value::Null),
    ).await;
    let app = fixture.app_with_codex(&upstream.models_url, &upstream.token_url, &upstream.npm_url);
    let (status, _) = call(&app, Method::POST, "/api/codex/settings",
        Some(json!({"client_version": " 9.8.7 "})), true).await;
    assert_eq!(status, StatusCode::OK);
    let (status, result) = call(&app, Method::POST,
        &format!("/api/codex/models/refresh?account_id={id}"), None, true).await;
    assert_eq!(status, StatusCode::OK, "{result}");
    assert_eq!(result["client_version"], "9.8.7");
    let requests = upstream.model_requests.lock().unwrap();
    assert_eq!(requests.len(), 1);
    let (headers, query) = &requests[0];
    assert_eq!(query.get("client_version").map(String::as_str), Some("9.8.7"));
    assert_eq!(header(headers, "if-none-match"), None);
    assert_eq!(read_codex_cache(&fixture, id)["client_version"], "9.8.7");
}

#[tokio::test]
async fn codex_refresh_fetches_models_with_oauth_headers_and_persists_cache() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "synthetic-refresh",
        "chatgpt-account-fixture",
    );
    let upstream = codex_upstream(
        vec![CodexReply {
            status: StatusCode::OK,
            body: codex_models_body(),
            etag: Some("etag-fixture-1".into()),
        }],
        vec![],
        (StatusCode::OK, json!({"version": "1.2.3"})),
    )
    .await;
    let app = fixture.app_with_codex(&upstream.models_url, &upstream.token_url, &upstream.npm_url);

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/codex/models/refresh?account_id=openai-codex",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["success"], json!(true));
    assert_eq!(body["account_id"], json!("openai-codex"));
    assert_eq!(body["refreshed"], json!(true));
    assert_eq!(body["count"], json!(2));
    assert_eq!(body["models_count"], json!(2));
    assert_eq!(body["client_version"], json!("1.2.3"));
    assert_eq!(body["version_source"], json!("npm_latest"));
    assert!(!body.to_string().contains("synthetic-access"));
    assert!(!body.to_string().contains("synthetic-refresh"));

    let seen = upstream.model_requests.lock().unwrap();
    assert_eq!(seen.len(), 1);
    let (headers, query) = &seen[0];
    assert_eq!(
        header(headers, "authorization"),
        Some("Bearer synthetic-access")
    );
    assert_eq!(
        header(headers, "chatgpt-account-id"),
        Some("chatgpt-account-fixture")
    );
    assert_eq!(
        header(headers, "openai-beta"),
        Some("responses=experimental")
    );
    assert_eq!(header(headers, "originator"), Some("codex_cli_rs"));
    assert_eq!(header(headers, "accept"), Some("application/json"));
    assert_eq!(
        query.get("client_version").map(String::as_str),
        Some("1.2.3")
    );

    let cache = read_codex_cache(&fixture, "openai-codex");
    assert_eq!(cache["models"].as_array().unwrap().len(), 2);
    assert_eq!(cache["etag"], json!("etag-fixture-1"));
    assert_eq!(cache["client_version"], json!("1.2.3"));
    assert_eq!(cache["source"], json!("online"));
    assert!(cache["fetched_at"].as_str().unwrap().ends_with('Z'));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(fixture.data.join("codex_models.json"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }
}

#[tokio::test]
async fn codex_refresh_uses_etag_and_keeps_global_cache_keys_on_304() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "synthetic-refresh",
        "chatgpt-account-fixture",
    );
    fixture.write_json(
        &fixture.data.join("codex_models.json"),
        json!({
            "models": codex_models_body()["models"],
            "etag": "etag-fixture-1",
            "client_version": "1.2.3",
            "proxy": "http://proxy.invalid:7890",
            "fetched_at": "2026-11-24T00:00:00.000000Z",
            "source": "online"
        }),
    );
    let upstream = codex_upstream(
        vec![CodexReply {
            status: StatusCode::NOT_MODIFIED,
            body: Value::Null,
            etag: None,
        }],
        vec![],
        (StatusCode::OK, json!({"version": "1.2.3"})),
    )
    .await;
    let app = fixture.app_with_codex(&upstream.models_url, &upstream.token_url, &upstream.npm_url);

    let (status, body) = call(&app, Method::POST, "/api/codex/models/refresh", None, true).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["refreshed"], json!(true));
    assert_eq!(body["not_modified"], json!(true));
    assert_eq!(body["count"], json!(2));
    assert_eq!(body["models_count"], json!(2));
    assert_eq!(body["version_source"], json!("cache"));
    {
        let seen = upstream.model_requests.lock().unwrap();
        assert_eq!(seen.len(), 1);
        assert_eq!(header(&seen[0].0, "if-none-match"), Some("etag-fixture-1"));
    }
    let cache = read_codex_cache(&fixture, "openai-codex");
    assert_eq!(cache["proxy"], json!("http://proxy.invalid:7890"));
    assert_eq!(cache["source"], json!("online"));
    assert_eq!(cache["models"].as_array().unwrap().len(), 2);
    assert_ne!(cache["fetched_at"], json!("2026-11-24T00:00:00.000000Z"));
}

#[tokio::test]
async fn codex_refresh_rotates_expired_access_and_refresh_tokens() {
    let fixture = Fixture::new();
    let expired = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() - 3600);
    let fresh = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    write_codex_credential(
        &fixture,
        "openai-codex",
        &expired,
        "old-refresh-token",
        "chatgpt-account-fixture",
    );
    let path = codex_credential_path(&fixture, "openai-codex");
    let mut credential: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    credential["custom_root"] = json!("keep-me");
    fixture.write_json(&path, credential);
    let upstream = codex_upstream(
        vec![CodexReply {
            status: StatusCode::OK,
            body: codex_models_body(),
            etag: Some("etag-fixture-2".into()),
        }],
        vec![(
            StatusCode::OK,
            json!({
                "access_token": fresh,
                "refresh_token": "new-refresh-token",
                "id_token": ""
            }),
        )],
        (StatusCode::OK, json!({"version": "1.2.3"})),
    )
    .await;
    let app = fixture.app_with_codex(&upstream.models_url, &upstream.token_url, &upstream.npm_url);

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/codex/models/refresh?account_id=openai-codex",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["success"], json!(true));

    {
        let tokens = upstream.token_requests.lock().unwrap();
        assert_eq!(tokens.len(), 1);
        let (headers, form) = &tokens[0];
        assert_eq!(
            header(headers, "content-type"),
            Some("application/x-www-form-urlencoded")
        );
        let fields: HashMap<&str, &str> = form
            .split('&')
            .filter_map(|pair| pair.split_once('='))
            .collect();
        assert_eq!(fields.get("grant_type"), Some(&"refresh_token"));
        assert_eq!(fields.get("refresh_token"), Some(&"old-refresh-token"));
        assert_eq!(
            fields.get("client_id"),
            Some(&"app_EMoamEEZ73f0CkXaXp7hrann")
        );
    }
    {
        let seen = upstream.model_requests.lock().unwrap();
        let (headers, _) = &seen[0];
        assert_eq!(
            header(headers, "authorization"),
            Some(format!("Bearer {fresh}").as_str())
        );
    }

    let credential: Value =
        serde_json::from_slice(&fs::read(codex_credential_path(&fixture, "openai-codex")).unwrap())
            .unwrap();
    assert_eq!(credential["auth_mode"], json!("chatgpt"));
    assert_eq!(credential["custom_root"], json!("keep-me"));
    assert_eq!(credential["tokens"]["access_token"], json!(fresh));
    assert_eq!(
        credential["tokens"]["refresh_token"],
        json!("new-refresh-token")
    );
    assert_ne!(
        credential["last_refresh"],
        json!("2026-11-24T00:00:00.000000Z")
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(codex_credential_path(&fixture, "openai-codex"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }
}

#[tokio::test]
async fn codex_refresh_reports_failures_and_rejects_unknown_accounts() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "synthetic-refresh",
        "chatgpt-account-fixture",
    );
    fixture.write_json(
        &fixture.data.join("codex_models.json"),
        json!({
            "models": codex_models_body()["models"],
            "etag": "etag-old",
            "client_version": "1.2.3",
            "fetched_at": "2026-11-24T00:00:00.000000Z",
            "source": "online"
        }),
    );
    let upstream = codex_upstream(
        vec![CodexReply {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            body: json!({"error": "boom"}),
            etag: None,
        }],
        vec![],
        (StatusCode::OK, json!({"version": "1.2.3"})),
    )
    .await;
    let app = fixture.app_with_codex(&upstream.models_url, &upstream.token_url, &upstream.npm_url);

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/codex/models/refresh?account_id=openai-codex",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["success"], json!(true));
    assert_eq!(body["refreshed"], json!(false));
    assert_eq!(body["error"], json!("http_500"));
    assert_eq!(body["fallback"], json!("cache"));
    assert_eq!(body["models_count"], json!(2));
    assert_eq!(body["count"], Value::Null);
    let cache = read_codex_cache(&fixture, "openai-codex");
    assert_eq!(cache["etag"], json!("etag-old"));
    assert_eq!(cache["fetched_at"], json!("2026-11-24T00:00:00.000000Z"));

    let (status, body) = call(
        &app,
        Method::POST,
        "/api/codex/models/refresh?account_id=missing-account",
        None,
        true,
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["success"], json!(false));
    assert_eq!(body["error"], json!("account_not_found"));
}

#[tokio::test]
async fn refresh_all_appends_ready_codex_accounts_and_reports_each_failure() {
    let fixture = Fixture::new();
    let provider_upstream = upstream(vec![(StatusCode::OK, json!({"data":[{"id":"one"}]}))]).await;
    let mut enabled = record(
        "fixture-aaaaaaaa",
        Some("fixture"),
        0,
        SECRET,
        json!([{"id":"old"}]),
    );
    enabled["base_url"] = json!(provider_upstream.base_url);
    let mut disabled = record(
        "fixture-bbbbbbbb",
        Some("fixture"),
        1,
        "sk-second-key-12345678",
        json!([]),
    );
    disabled["base_url"] = json!(provider_upstream.base_url);
    disabled["enabled"] = json!(false);
    fixture.catalog(json!([entry("fixture", "api_key", json!({}))]));
    fixture.providers(json!({"version":2,"providers":{
        "fixture-aaaaaaaa": enabled,
        "fixture-bbbbbbbb": disabled
    }}));
    write_codex_credential(
        &fixture,
        "openai-codex",
        "legacy-access",
        "legacy-refresh",
        "chatgpt-account-legacy",
    );
    write_codex_credential(
        &fixture,
        "openai-codex-aaaaaaaa",
        "second-access",
        "second-refresh",
        "chatgpt-account-second",
    );
    fixture.write_json(
        &fixture
            .data
            .join("codex_accounts")
            .join("openai-codex-bbbbbbbb.json"),
        json!({"tokens":{"access_token":"disconnected-access","refresh_token":""}}),
    );
    fixture.write_json(
        &fixture
            .data
            .join("codex_models")
            .join("openai-codex-aaaaaaaa.json"),
        json!({"models": [{"slug": "old-model"}], "client_version": "1.2.3"}),
    );
    let codex = codex_upstream(
        vec![
            CodexReply {
                status: StatusCode::OK,
                body: codex_models_body(),
                etag: Some("etag-legacy".into()),
            },
            CodexReply {
                status: StatusCode::INTERNAL_SERVER_ERROR,
                body: json!({"error": "boom"}),
                etag: None,
            },
        ],
        vec![],
        (StatusCode::OK, json!({"version": "1.2.3"})),
    )
    .await;
    let app = fixture.app_with_codex(&codex.models_url, &codex.token_url, &codex.npm_url);

    let (status, body) = call(&app, Method::POST, "/api/providers/refresh-all", None, true).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["success"], json!(true));
    assert_eq!(body["skipped"], json!(false));
    assert_eq!(
        body["count"],
        json!(3),
        "disabled provider/non-ready account skipped"
    );
    assert_eq!(body["failed"], json!(1));
    let results = body["results"].as_array().unwrap();
    assert_eq!(results[0]["kind"], json!("provider"));
    assert_eq!(results[0]["success"], json!(true));
    assert_eq!(results[1]["kind"], json!("codex"));
    assert_eq!(results[1]["id"], json!("openai-codex"));
    assert_eq!(results[1]["success"], json!(true));
    assert_eq!(results[1]["models_count"], json!(2));
    assert_eq!(results[1]["error"], json!(""));
    assert_eq!(results[2]["kind"], json!("codex"));
    assert_eq!(results[2]["id"], json!("openai-codex-aaaaaaaa"));
    assert_eq!(results[2]["success"], json!(false));
    assert_eq!(results[2]["models_count"], Value::Null);
    assert_eq!(results[2]["error"], json!("http_500"));
    assert_eq!(
        read_codex_cache(&fixture, "openai-codex-aaaaaaaa")["models"][0]["slug"],
        json!("old-model"),
        "failed account keeps its previous model list"
    );
    assert_eq!(codex.model_requests.lock().unwrap().len(), 2);
}

async fn inject_test_session(
    mut request: axum::extract::Request,
    next: axum::middleware::Next,
) -> Response {
    request
        .extensions_mut()
        .insert(crate::web_auth::BrowserSession::synthetic());
    next.run(request).await
}

struct HttpServer {
    base: String,
    task: JoinHandle<()>,
}

impl Drop for HttpServer {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn serve_http(router: Router) -> HttpServer {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    HttpServer {
        base: format!("http://{address}"),
        task,
    }
}

async fn http_post(server: &HttpServer, path: &str, body: Option<Value>) -> (StatusCode, Value) {
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let mut request = client.post(format!("{}{}", server.base, path));
    if let Some(body) = body {
        request = request.json(&body);
    }
    let response = request.send().await.unwrap();
    let status = response.status();
    let body = response.json().await.unwrap_or(Value::Null);
    (status, body)
}

#[tokio::test]
async fn concurrent_refresh_for_one_account_rotates_expired_tokens_once() {
    let fixture = Fixture::new();
    let expired = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() - 3600);
    let fresh = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    write_codex_credential(
        &fixture,
        "openai-codex",
        &expired,
        "old-refresh-token",
        "chatgpt-account-fixture",
    );
    let upstream = codex_upstream(
        vec![CodexReply {
            status: StatusCode::OK,
            body: codex_models_body(),
            etag: Some("etag-concurrent".into()),
        }],
        vec![(
            StatusCode::OK,
            json!({
                "access_token": fresh,
                "refresh_token": "new-refresh-token",
                "id_token": ""
            }),
        )],
        (StatusCode::OK, json!({"version": "1.2.3"})),
    )
    .await;
    let app = fixture.app_with_codex(&upstream.models_url, &upstream.token_url, &upstream.npm_url);

    let first = call(
        &app,
        Method::POST,
        "/api/codex/models/refresh?account_id=openai-codex",
        None,
        true,
    );
    let second = call(
        &app,
        Method::POST,
        "/api/codex/models/refresh?account_id=openai-codex",
        None,
        true,
    );
    let (first, second) = tokio::join!(first, second);
    assert_eq!(first.0, StatusCode::OK, "{}", first.1);
    assert_eq!(second.0, StatusCode::OK, "{}", second.1);
    assert_eq!(first.1["success"], json!(true));
    assert_eq!(second.1["success"], json!(true));
    assert_eq!(
        upstream.token_requests.lock().unwrap().len(),
        1,
        "the per-account lock must rotate the refresh token once"
    );
    assert!(
        upstream.model_requests.lock().unwrap().len() >= 1,
        "both refreshes may re-fetch models after the shared rotation"
    );
    let credential: Value =
        serde_json::from_slice(&fs::read(codex_credential_path(&fixture, "openai-codex")).unwrap())
            .unwrap();
    assert_eq!(
        credential["tokens"]["refresh_token"],
        json!("new-refresh-token")
    );
    assert_eq!(credential["tokens"]["access_token"], json!(fresh));
}

#[tokio::test]
async fn concurrent_refresh_updates_each_account_cache_without_losing_unknown_fields() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "legacy-access",
        "legacy-refresh",
        "chatgpt-account-legacy",
    );
    write_codex_credential(
        &fixture,
        "openai-codex-aaaaaaaa",
        "second-access",
        "second-refresh",
        "chatgpt-account-second",
    );
    fixture.write_json(
        &fixture.data.join("codex_models.json"),
        json!({
            "models": [{"slug": "legacy-old"}],
            "proxy": "http://legacy-proxy.invalid:7890",
            "custom_meta": {"keep": "legacy"}
        }),
    );
    fixture.write_json(
        &fixture
            .data
            .join("codex_models")
            .join("openai-codex-aaaaaaaa.json"),
        json!({
            "models": [{"slug": "second-old"}],
            "proxy": "http://second-proxy.invalid:7890",
            "custom_meta": {"keep": "second"}
        }),
    );
    let upstream = codex_upstream(
        vec![CodexReply {
            status: StatusCode::OK,
            body: codex_models_body(),
            etag: Some("etag-multi".into()),
        }],
        vec![],
        (StatusCode::OK, json!({"version": "1.2.3"})),
    )
    .await;
    let app = fixture.app_with_codex(&upstream.models_url, &upstream.token_url, &upstream.npm_url);

    let legacy = call(
        &app,
        Method::POST,
        "/api/codex/models/refresh?account_id=openai-codex",
        None,
        true,
    );
    let second = call(
        &app,
        Method::POST,
        "/api/codex/models/refresh?account_id=openai-codex-aaaaaaaa",
        None,
        true,
    );
    let (legacy, second) = tokio::join!(legacy, second);
    assert_eq!(legacy.0, StatusCode::OK, "{}", legacy.1);
    assert_eq!(second.0, StatusCode::OK, "{}", second.1);
    assert_eq!(upstream.model_requests.lock().unwrap().len(), 2);

    let legacy_cache = read_codex_cache(&fixture, "openai-codex");
    assert_eq!(legacy_cache["models"].as_array().unwrap().len(), 2);
    assert_eq!(
        legacy_cache["proxy"],
        json!("http://legacy-proxy.invalid:7890")
    );
    assert_eq!(legacy_cache["custom_meta"], json!({"keep": "legacy"}));
    let second_cache = read_codex_cache(&fixture, "openai-codex-aaaaaaaa");
    assert_eq!(second_cache["models"].as_array().unwrap().len(), 2);
    assert_eq!(
        second_cache["proxy"],
        json!("http://second-proxy.invalid:7890")
    );
    assert_eq!(second_cache["custom_meta"], json!({"keep": "second"}));

    for path in [
        fixture.data.join("codex_models.json"),
        fixture
            .data
            .join("codex_models")
            .join("openai-codex-aaaaaaaa.json"),
    ] {
        let names: Vec<String> = fs::read_dir(path.parent().unwrap())
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert!(
            !names.iter().any(|name| name.ends_with(".tmp")),
            "atomic write left a temporary file: {names:?}"
        );
    }
}

#[tokio::test]
async fn production_http_web_and_headless_route_surfaces_match_python() {
    let fixture = Fixture::new();
    let provider_upstream = upstream(vec![(StatusCode::OK, json!({"data":[{"id":"one"}]}))]).await;
    let mut enabled = record(
        "fixture-aaaaaaaa",
        Some("fixture"),
        0,
        SECRET,
        json!([{"id":"old"}]),
    );
    enabled["base_url"] = json!(provider_upstream.base_url);
    fixture.catalog(json!([entry("fixture", "api_key", json!({}))]));
    fixture.providers(json!({"version":2,"providers":{"fixture-aaaaaaaa":enabled}}));
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "synthetic-refresh",
        "chatgpt-account-fixture",
    );
    let codex = codex_upstream(
        vec![CodexReply {
            status: StatusCode::OK,
            body: codex_models_body(),
            etag: Some("etag-http".into()),
        }],
        vec![],
        (StatusCode::OK, json!({"version": "1.2.3"})),
    )
    .await;

    let web = serve_http(fixture.web_router_with_codex(
        &codex.models_url,
        &codex.token_url,
        &codex.npm_url,
    ))
    .await;
    let headless = serve_http(fixture.headless_router_with_codex(
        &codex.models_url,
        &codex.token_url,
        &codex.npm_url,
    ))
    .await;

    let (status, body) = http_post(
        &web,
        "/api/codex/models/refresh?account_id=openai-codex",
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["success"], json!(true));
    assert_eq!(body["models_count"], json!(2));

    let (status, body) = http_post(
        &headless,
        "/api/codex/models/refresh?account_id=openai-codex",
        None,
    )
    .await;
    assert_eq!(
        status,
        StatusCode::NOT_FOUND,
        "Python headless does not register codex_auth_bp: {body}"
    );

    for (name, server) in [("web", &web), ("headless", &headless)] {
        let (status, body) = http_post(server, "/api/providers/refresh-all", None).await;
        assert_eq!(status, StatusCode::OK, "{name}: {body}");
        assert_eq!(body["success"], json!(true), "{name}");
        assert_eq!(body["count"], json!(2), "{name}: provider + codex");
        assert_eq!(body["failed"], json!(0), "{name}");
        let results = body["results"].as_array().unwrap();
        assert_eq!(results[0]["kind"], json!("provider"), "{name}");
        assert_eq!(results[1]["kind"], json!("codex"), "{name}");
        assert_eq!(results[1]["id"], json!("openai-codex"), "{name}");
        assert_eq!(results[1]["success"], json!(true), "{name}");
        assert_eq!(results[1]["models_count"], json!(2), "{name}");
    }
}

struct UsageUpstream {
    url: String,
    headers: Arc<Mutex<Vec<HeaderMap>>>,
    task: JoinHandle<()>,
}

struct ResponsesUpstream {
    url: String,
    requests: Arc<Mutex<Vec<(HeaderMap, Value)>>>,
    task: JoinHandle<()>,
}

impl Drop for ResponsesUpstream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

/// Codex Responses upstream with a status/body queue. The final reply repeats.
async fn responses_upstream(replies: Vec<(StatusCode, Value)>, delay: Duration) -> ResponsesUpstream {
    let replies = Arc::new(Mutex::new(replies.into()));
    let requests = Arc::new(Mutex::new(Vec::new()));
    let app = Router::new().route(
        "/backend-api/codex/responses",
        axum::routing::post({
            let replies = replies.clone();
            let seen = requests.clone();
            move |headers: HeaderMap, Json(body): Json<Value>| {
                let replies = replies.clone();
                let seen = seen.clone();
                async move {
                    seen.lock().unwrap().push((headers, body));
                    tokio::time::sleep(delay).await;
                    let (status, body) = next_reply(&replies);
                    (status, Json(body))
                }
            }
        }),
    );
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    ResponsesUpstream {
        url: format!("http://{address}/backend-api/codex/responses"),
        requests,
        task,
    }
}

impl Drop for UsageUpstream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn usage_upstream(body: Value, delay: Duration) -> UsageUpstream {
    usage_upstream_with_status(StatusCode::OK, body, delay).await
}

async fn usage_upstream_with_status(
    status: StatusCode,
    body: Value,
    delay: Duration,
) -> UsageUpstream {
    let headers = Arc::new(Mutex::new(Vec::new()));
    let app = Router::new().route(
        "/usage",
        axum::routing::get({
            let body = body.clone();
            let seen = headers.clone();
            move |headers: HeaderMap| {
                let body = body.clone();
                let seen = seen.clone();
                async move {
                    seen.lock().unwrap().push(headers);
                    tokio::time::sleep(delay).await;
                    (status, Json(body))
                }
            }
        }),
    );
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    UsageUpstream {
        url: format!("http://{address}/usage"),
        headers,
        task,
    }
}

/// Sends the response headers immediately and the body later, so a test can
/// mutate the credential between `send()` resolving and `json()` completing.
async fn slow_body_usage_upstream(body: Value, delay: Duration) -> UsageUpstream {
    let headers = Arc::new(Mutex::new(Vec::new()));
    let app = Router::new().route(
        "/usage",
        axum::routing::get({
            let body = body.clone();
            let seen = headers.clone();
            move |headers: HeaderMap| {
                let body = body.clone();
                let seen = seen.clone();
                async move {
                    seen.lock().unwrap().push(headers);
                    let stream = futures_util::stream::once(async move {
                        tokio::time::sleep(delay).await;
                        Ok::<Bytes, std::convert::Infallible>(Bytes::from(body.to_string()))
                    });
                    Response::builder()
                        .header(CONTENT_TYPE, "application/json")
                        .body(Body::from_stream(stream))
                        .unwrap()
                }
            }
        }),
    );
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    UsageUpstream {
        url: format!("http://{address}/usage"),
        headers,
        task,
    }
}

fn crosspet_config(fixture: &Fixture, token_url: &str, usage_url: &str) -> ProviderConfig {
    fixture
        .config_with_codex(
            None,
            Some((
                "http://127.0.0.1:1/models",
                token_url,
                "http://127.0.0.1:1/npm",
            )),
        )
        .with_crosspet_usage_url(usage_url)
}

#[test]
fn crosspet_account_respects_primary_missing_and_corrupt_credentials() {
    let fixture = Fixture::new();
    let config = crosspet_config(
        &fixture,
        "http://127.0.0.1:1/token",
        "http://127.0.0.1:1/usage",
    );
    assert!(config.crosspet_account().unwrap().is_none());

    // Primary is registered but its credential is missing: never fall back to
    // the valid secondary account.
    fixture.write_json(
        &fixture.data.join("codex_accounts.json"),
        json!({"version":1,"accounts":{"openai-codex":{
            "identity_hash":"legacy-hash","display_hash":"aaaaaa",
            "created_at":"2026-11-24T00:00:00.000000Z","legacy":true
        }}}),
    );
    write_codex_credential(
        &fixture,
        "openai-codex-aaaaaaaa",
        "secondary-access",
        "secondary-refresh",
        "chatgpt-account-secondary",
    );
    assert!(config.crosspet_account().unwrap().is_none());

    write_codex_credential(
        &fixture,
        "openai-codex",
        "legacy-access",
        "legacy-refresh",
        "chatgpt-account-legacy",
    );
    let identity = config.crosspet_account().unwrap().unwrap();
    assert!(identity.clone() == identity);

    let path = codex_credential_path(&fixture, "openai-codex");
    fixture.write_json(&path, json!({"tokens":"broken"}));
    assert!(
        config
            .crosspet_account()
            .err()
            .unwrap()
            .starts_with("credential_invalid")
    );
    fs::write(&path, b"{not-json").unwrap();
    assert!(
        config
            .crosspet_account()
            .err()
            .unwrap()
            .starts_with("credential_invalid")
    );
    fixture.write_json(&path, json!({"tokens": []}));
    assert!(
        config.crosspet_account().is_err(),
        "tokens must be an object"
    );
    fixture.write_json(&path, json!({"tokens": {}}));
    assert!(
        config.crosspet_account().unwrap().is_none(),
        "a registered primary with empty tokens is unavailable, not a fallback"
    );
    fixture.write_json(
        &path,
        json!({"tokens": {"access_token": "", "refresh_token": ""}}),
    );
    assert!(config.crosspet_account().unwrap().is_none());
    fixture.write_json(
        &path,
        json!({"tokens": {"access_token": "only-access", "refresh_token": ""}}),
    );
    assert!(
        config.crosspet_account().unwrap().is_none(),
        "one token is disconnected, not connected"
    );

    write_codex_credential(
        &fixture,
        "openai-codex",
        "legacy-access",
        "legacy-refresh",
        "chatgpt-account-legacy",
    );
    let before = config.crosspet_account().unwrap().unwrap();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "legacy-access",
        "legacy-refresh-rotated",
        "chatgpt-account-legacy",
    );
    let after = config.crosspet_account().unwrap().unwrap();
    assert!(
        before != after,
        "credential version must participate in identity"
    );
}

#[test]
fn crosspet_account_reports_a_corrupt_registry_without_falling_back() {
    let fixture = Fixture::new();
    let config = crosspet_config(
        &fixture,
        "http://127.0.0.1:1/token",
        "http://127.0.0.1:1/usage",
    );
    write_codex_credential(
        &fixture,
        "openai-codex",
        "legacy-access",
        "legacy-refresh",
        "chatgpt-account-legacy",
    );
    fixture.write_json(
        &fixture.data.join("codex_accounts.json"),
        json!({"version": 1, "accounts": []}),
    );
    assert!(
        config
            .crosspet_account()
            .err()
            .unwrap()
            .starts_with("account_registry_error"),
        "a corrupt registry must not silently use the legacy file"
    );
    fs::write(fixture.data.join("codex_accounts.json"), b"{broken").unwrap();
    assert!(
        config
            .crosspet_account()
            .err()
            .unwrap()
            .starts_with("account_registry_error")
    );
    fixture.write_json(
        &fixture.data.join("codex_accounts.json"),
        json!({"version":1,"accounts":{"openai-codex-secondary":{
            "identity_hash":"secondary","display_hash":"bbbbbb",
            "created_at":"2026-11-25T00:00:00.000000Z","legacy":false
        }}}),
    );
    fs::write(codex_credential_path(&fixture, "openai-codex"), b"{broken").unwrap();
    assert!(
        config
            .crosspet_account()
            .err()
            .unwrap()
            .starts_with("credential_invalid"),
        "a corrupt legacy file must not fall back to a registered secondary"
    );
}

#[tokio::test]
async fn crosspet_usage_sends_oauth_headers_and_returns_the_same_identity() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "synthetic-refresh",
        "chatgpt-account-fixture",
    );
    let usage = usage_upstream(
        json!({"plan_type":"plus","usage":{"used":1}}),
        Duration::ZERO,
    )
    .await;
    let config = crosspet_config(&fixture, "http://127.0.0.1:1/token", &usage.url);
    let identity = config.crosspet_account().unwrap().unwrap();

    let (returned, usage_result) = config.crosspet_usage_detailed(&identity).await.unwrap();
    let body = usage_result.unwrap();
    assert!(returned == identity);
    assert_eq!(body["plan_type"], json!("plus"));
    let seen = usage.headers.lock().unwrap();
    assert_eq!(seen.len(), 1);
    assert_eq!(
        header(&seen[0], "authorization"),
        Some("Bearer synthetic-access")
    );
    assert_eq!(
        header(&seen[0], "chatgpt-account-id"),
        Some("chatgpt-account-fixture")
    );
    assert_eq!(header(&seen[0], "originator"), Some("codex_cli_rs"));
    assert_eq!(header(&seen[0], "accept"), Some("application/json"));
    assert!(header(&seen[0], "openai-beta").is_none());
}

#[tokio::test]
async fn concurrent_crosspet_usage_rotates_an_expired_token_once() {
    let fixture = Fixture::new();
    let expired = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() - 3600);
    let fresh = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    write_codex_credential(
        &fixture,
        "openai-codex",
        &expired,
        "old-refresh-token",
        "chatgpt-account-fixture",
    );
    let token = codex_upstream(
        vec![],
        vec![(
            StatusCode::OK,
            json!({"access_token": fresh, "refresh_token": "new-refresh-token", "id_token": ""}),
        )],
        (StatusCode::OK, json!({"version":"1.2.3"})),
    )
    .await;
    let usage = usage_upstream(json!({"plan_type":"plus"}), Duration::ZERO).await;
    let config = crosspet_config(&fixture, &token.token_url, &usage.url);
    let identity = config.crosspet_account().unwrap().unwrap();

    let (first, second) = tokio::join!(
        config.crosspet_usage(&identity),
        config.crosspet_usage(&identity)
    );
    let (first_identity, first_body) = first.unwrap();
    let (second_identity, second_body) = second.unwrap();
    assert_eq!(first_body["plan_type"], json!("plus"));
    assert_eq!(second_body["plan_type"], json!("plus"));
    assert!(first_identity == second_identity);
    assert!(first_identity != identity);
    assert_eq!(token.token_requests.lock().unwrap().len(), 1);
    assert_eq!(usage.headers.lock().unwrap().len(), 2);
    let credential: Value =
        serde_json::from_slice(&fs::read(codex_credential_path(&fixture, "openai-codex")).unwrap())
            .unwrap();
    assert_eq!(
        credential["tokens"]["refresh_token"],
        json!("new-refresh-token")
    );
}

#[tokio::test]
async fn crosspet_usage_rejects_an_account_removed_while_the_request_is_in_flight() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "synthetic-refresh",
        "chatgpt-account-fixture",
    );
    let usage = usage_upstream(json!({"plan_type":"plus"}), Duration::from_millis(150)).await;
    let config = crosspet_config(&fixture, "http://127.0.0.1:1/token", &usage.url);
    let identity = config.crosspet_account().unwrap().unwrap();
    let path = codex_credential_path(&fixture, "openai-codex");

    let request = config.crosspet_usage(&identity);
    let remove = async move {
        tokio::time::sleep(Duration::from_millis(30)).await;
        fs::remove_file(path).unwrap();
    };
    let (result, ()) = tokio::join!(request, remove);
    let error = match result {
        Err(error) => error,
        Ok(_) => panic!("expected an outer identity error"),
    };
    assert_eq!(
        error, "account_changed",
        "stale usage must not be attributed"
    );
}

#[tokio::test]
async fn crosspet_usage_rechecks_identity_after_the_body_finishes() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "synthetic-refresh",
        "chatgpt-account-fixture",
    );
    let usage = slow_body_usage_upstream(
        json!({"plan_type":"plus"}),
        Duration::from_millis(250),
    )
    .await;
    let config = crosspet_config(&fixture, "http://127.0.0.1:1/token", &usage.url);
    let identity = config.crosspet_account().unwrap().unwrap();

    let request = config.crosspet_usage(&identity);
    let replace = async {
        tokio::time::sleep(Duration::from_millis(60)).await;
        write_codex_credential(
            &fixture,
            "openai-codex",
            "rotated-access-token",
            "rotated-refresh-token",
            "chatgpt-account-fixture",
        );
    };
    let (result, ()) = tokio::join!(request, replace);
    let error = match result {
        Err(error) => error,
        Ok(_) => panic!("expected the body-time credential check to fail"),
    };
    assert_eq!(
        error, "credential_changed",
        "a slow response body must not attribute an old account's usage"
    );
    assert_eq!(usage.headers.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn crosspet_usage_returns_the_rotated_identity_when_usage_http_fails() {
    let fixture = Fixture::new();
    let expired = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() - 3600);
    let fresh = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    write_codex_credential(
        &fixture,
        "openai-codex",
        &expired,
        "old-refresh-token",
        "chatgpt-account-fixture",
    );
    let token = codex_upstream(
        vec![],
        vec![(
            StatusCode::OK,
            json!({"access_token": fresh, "refresh_token": "new-refresh-token", "id_token": ""}),
        )],
        (StatusCode::OK, json!({"version":"1.2.3"})),
    )
    .await;
    let usage = usage_upstream_with_status(
        StatusCode::INTERNAL_SERVER_ERROR,
        json!({"error":"boom"}),
        Duration::ZERO,
    )
    .await;
    let config = crosspet_config(&fixture, &token.token_url, &usage.url);
    let identity = config.crosspet_account().unwrap().unwrap();

    let (returned, usage_result) = config.crosspet_usage_detailed(&identity).await.unwrap();
    assert!(
        returned != identity,
        "the token rotation changed the identity"
    );
    assert_eq!(usage_result.unwrap_err(), "http_500");
    assert_eq!(token.token_requests.lock().unwrap().len(), 1);
}


#[tokio::test]
async fn codex_responses_transport_uses_the_explicit_connection_and_python_headers() {
    let fixture = Fixture::new();
    let future = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    write_codex_credential(
        &fixture,
        "openai-codex-aaaaaaaa",
        &future,
        "secondary-refresh",
        "chatgpt-account-secondary",
    );
    let token = codex_upstream(
        vec![],
        vec![(StatusCode::OK, json!({}))],
        (StatusCode::OK, json!({})),
    )
    .await;
    let responses = responses_upstream(
        vec![(StatusCode::OK, json!({"ok":true}))],
        Duration::ZERO,
    )
    .await;
    let config = fixture
        .config_with_codex(
            None,
            Some((&token.models_url, &token.token_url, "")),
        )
        .with_codex_responses_url(&responses.url);
    let transport = config.codex_transport("openai-codex-aaaaaaaa");
    let authorization = transport.authorize().await.unwrap();
    assert_eq!(authorization.fingerprint().len(), 64);
    assert!(
        authorization
            .fingerprint()
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    );
    let original_fingerprint = authorization.fingerprint().to_owned();
    let body = json!({"model":"gpt-5","input":[{"role":"user","content":"hi"}]});
    let response = transport
        .post_responses(authorization, "session-fixture", &body)
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.json::<Value>().await.unwrap(), json!({"ok":true}));

    let requests = responses.requests.lock().unwrap();
    assert_eq!(requests.len(), 1);
    let (headers, sent) = &requests[0];
    assert_eq!(sent, &body);
    assert_eq!(
        header(headers, "authorization"),
        Some(format!("Bearer {future}").as_str()).as_deref()
    );
    assert_eq!(
        header(headers, "chatgpt-account-id"),
        Some("chatgpt-account-secondary")
    );
    assert_eq!(header(headers, "content-type"), Some("application/json"));
    assert_eq!(header(headers, "accept"), Some("text/event-stream"));
    assert_eq!(header(headers, "openai-beta"), Some("responses=experimental"));
    assert_eq!(header(headers, "originator"), Some("codex_cli_rs"));
    assert_eq!(header(headers, "session_id"), Some("session-fixture"));
    drop(requests);

    // Same subscription across token rotation keeps the persisted 64-hex
    // reasoning scope; a different subscription behind the same connection
    // changes it.
    let rotated = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 7200);
    write_codex_credential(
        &fixture,
        "openai-codex-aaaaaaaa",
        &rotated,
        "secondary-refresh-2",
        "chatgpt-account-secondary",
    );
    let same = transport.authorize().await.unwrap();
    assert_eq!(same.fingerprint(), original_fingerprint.as_str());
    write_codex_credential(
        &fixture,
        "openai-codex-aaaaaaaa",
        &rotated,
        "secondary-refresh-3",
        "chatgpt-account-other",
    );
    let other = transport.authorize().await.unwrap();
    assert_ne!(other.fingerprint(), original_fingerprint.as_str());
}

#[tokio::test]
async fn codex_responses_401_rotates_once_and_replays_the_identical_body() {
    let fixture = Fixture::new();
    let current = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    let rotated = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 7200);
    write_codex_credential(
        &fixture,
        "openai-codex",
        &current,
        "refresh-1",
        "chatgpt-account-fixture",
    );
    let token = codex_upstream(
        vec![],
        vec![(
            StatusCode::OK,
            json!({"access_token": rotated, "refresh_token": "refresh-2", "id_token": ""}),
        )],
        (StatusCode::OK, json!({})),
    )
    .await;
    let responses = responses_upstream(
        vec![
            (StatusCode::UNAUTHORIZED, json!({"error":"expired"})),
            (StatusCode::OK, json!({"ok":true})),
        ],
        Duration::ZERO,
    )
    .await;
    let config = fixture
        .config_with_codex(None, Some((&token.models_url, &token.token_url, "")))
        .with_codex_responses_url(&responses.url);
    let transport = config.codex_transport("openai-codex");
    let authorization = transport.authorize().await.unwrap();
    let body = json!({"model":"gpt-5","input":[]});
    let response = transport
        .post_responses(authorization, "session-retry", &body)
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    let requests = responses.requests.lock().unwrap();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].1, body);
    assert_eq!(requests[1].1, body);
    assert_eq!(
        header(&requests[0].0, "authorization"),
        Some(format!("Bearer {current}").as_str()).as_deref()
    );
    assert_eq!(
        header(&requests[1].0, "authorization"),
        Some(format!("Bearer {rotated}").as_str()).as_deref()
    );
    assert_eq!(
        header(&requests[1].0, "chatgpt-account-id"),
        Some("chatgpt-account-fixture")
    );
    assert_eq!(token.token_requests.lock().unwrap().len(), 1);
    let form = &token.token_requests.lock().unwrap()[0].1;
    assert!(form.contains("refresh_token=refresh-1"), "{form}");
}

#[tokio::test]
async fn codex_responses_401_never_falls_back_to_another_account() {
    let fixture = Fixture::new();
    let future = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    write_codex_credential(
        &fixture,
        "openai-codex",
        &future,
        "primary-refresh",
        "chatgpt-account-primary",
    );
    write_codex_credential(
        &fixture,
        "openai-codex-aaaaaaaa",
        &future,
        "secondary-refresh",
        "chatgpt-account-secondary",
    );
    let token = codex_upstream(
        vec![],
        vec![(StatusCode::INTERNAL_SERVER_ERROR, json!({"error":"boom"}))],
        (StatusCode::OK, json!({})),
    )
    .await;
    let responses = responses_upstream(
        vec![(StatusCode::UNAUTHORIZED, json!({"error":"expired"}))],
        Duration::ZERO,
    )
    .await;
    let config = fixture
        .config_with_codex(None, Some((&token.models_url, &token.token_url, "")))
        .with_codex_responses_url(&responses.url);
    let transport = config.codex_transport("openai-codex-aaaaaaaa");
    let authorization = transport.authorize().await.unwrap();
    let error = transport
        .post_responses(authorization, "session-fallback", &json!({"model":"gpt-5"}))
        .await
        .unwrap_err();
    assert!(error.starts_with("auth_refresh_failed"), "{error}");
    assert_eq!(responses.requests.lock().unwrap().len(), 1);
    let form = &token.token_requests.lock().unwrap()[0].1;
    assert!(form.contains("refresh_token=secondary-refresh"), "{form}");
    assert!(!form.contains("primary-refresh"), "{form}");
}

#[tokio::test]
async fn codex_responses_rejects_a_subscription_swap_under_the_same_connection() {
    let fixture = Fixture::new();
    let future = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    write_codex_credential(
        &fixture,
        "openai-codex",
        &future,
        "refresh-a",
        "chatgpt-account-a",
    );
    let config = crosspet_config(
        &fixture,
        "http://127.0.0.1:1/token",
        "http://127.0.0.1:1/usage",
    );
    let transport = config.codex_transport("openai-codex");
    let authorization = transport.authorize().await.unwrap();
    let before_swap = config.crosspet_account().unwrap().unwrap();
    write_codex_credential(
        &fixture,
        "openai-codex",
        &future,
        "refresh-b",
        "chatgpt-account-b",
    );
    let error = match transport.refresh_after_unauthorized(&authorization).await {
        Err(error) => error,
        Ok(_) => panic!("expected the subscription swap to be rejected"),
    };
    assert_eq!(error, "account_changed");
    let after_swap = config.crosspet_account().unwrap().unwrap();
    assert!(before_swap != after_swap);
}

#[tokio::test]
async fn codex_responses_detects_account_removal_after_the_response() {
    let fixture = Fixture::new();
    let future = synthetic_jwt(time::OffsetDateTime::now_utc().unix_timestamp() + 3600);
    write_codex_credential(
        &fixture,
        "openai-codex",
        &future,
        "refresh-1",
        "chatgpt-account-fixture",
    );
    let responses = responses_upstream(
        vec![(StatusCode::OK, json!({"ok":true}))],
        Duration::from_millis(150),
    )
    .await;
    let config = crosspet_config(
        &fixture,
        "http://127.0.0.1:1/token",
        "http://127.0.0.1:1/usage",
    )
    .with_codex_responses_url(&responses.url);
    let transport = config.codex_transport("openai-codex");
    let authorization = transport.authorize().await.unwrap();
    let path = codex_credential_path(&fixture, "openai-codex");
    let body = json!({"model":"gpt-5"});
    let request = transport.post_responses(authorization, "session-remove", &body);
    let remove = async move {
        tokio::time::sleep(Duration::from_millis(30)).await;
        fs::remove_file(path).unwrap();
    };
    let (result, ()) = tokio::join!(request, remove);
    assert_eq!(result.unwrap_err(), "account_changed");
}


#[tokio::test]
async fn crosspet_usage_rejects_a_subscription_swap_under_one_connection() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "refresh-a",
        "chatgpt-account-a",
    );
    let config = crosspet_config(
        &fixture,
        "http://127.0.0.1:1/token",
        "http://127.0.0.1:1/usage",
    );
    let before = config.crosspet_account().unwrap().unwrap();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "refresh-b",
        "chatgpt-account-b",
    );
    let after = config.crosspet_account().unwrap().unwrap();
    assert!(before != after, "subscription digest must separate accounts");
    let error = match config.crosspet_usage_detailed(&before).await {
        Err(error) => error,
        Ok(_) => panic!("expected the subscription swap to be rejected"),
    };
    assert_eq!(error, "account_changed");
}

#[tokio::test]
async fn crosspet_usage_rejects_a_rewrite_without_a_remote_identity() {
    let fixture = Fixture::new();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "refresh-a",
        "",
    );
    let config = crosspet_config(
        &fixture,
        "http://127.0.0.1:1/token",
        "http://127.0.0.1:1/usage",
    );
    let before = config.crosspet_account().unwrap().unwrap();
    write_codex_credential(
        &fixture,
        "openai-codex",
        "synthetic-access",
        "refresh-b-longer",
        "",
    );
    let error = match config.crosspet_usage_detailed(&before).await {
        Err(error) => error,
        Ok(_) => panic!("expected a no-identity rewrite to be rejected"),
    };
    assert_eq!(error, "account_changed");
}


#[test]
fn codex_accounts_public_payload_matches_python_store_and_flask() {
    let fixture = Fixture::new();
    fixture.write_json(
        &fixture.data.join("codex_accounts.json"),
        json!({"version":1,"accounts":{
            "openai-codex":{
                "display_hash":"aaaaaa",
                "created_at":"2026-11-24T00:00:00Z",
                "last_login_at":"2026-11-24T01:00:00Z",
                "legacy":true
            },
            "openai-codex-second":{
                "display_hash":"bbbbbb",
                "created_at":"2026-11-23T00:00:00Z",
                "last_login_at":"2026-11-24T02:00:00Z",
                "legacy":false
            },
            "openai-codex-third":{
                "display_hash":"cccccc",
                "created_at":"2026-11-25T00:00:00Z",
                "legacy":false
            }
        }}),
    );
    fixture.write_json(
        &fixture.data.join("codex_auth.json"),
        json!({"tokens":{
            "access_token":"legacy-access",
            "refresh_token":"legacy-refresh"
        }}),
    );
    fixture.write_json(
        &fixture.data.join("codex_accounts/openai-codex-second.json"),
        json!({"tokens":{
            "access_token":"second-access",
            "refresh_token":""
        }}),
    );
    fixture.write_json(
        &fixture.data.join("codex_models.json"),
        json!({
            "source":"online",
            "fetched_at":"2026-11-24T00:00:00Z",
            "models":[
                {"slug":"gpt-5","visibility":"list","supported_in_api":true},
                {"slug":"hidden","visibility":"hide","supported_in_api":true},
                {"slug":"gpt-5","visibility":"list","supported_in_api":true}
            ]
        }),
    );
    fixture.write_json(
        &fixture.data.join("codex_models/openai-codex-second.json"),
        json!({
            "source":"cache",
            "models":[{"slug":"gpt-second","visibility":"list","supported_in_api":true}]
        }),
    );

    let (accounts, primary) = super::codex::public_accounts(&fixture.data);
    assert_eq!(primary.as_deref(), Some("openai-codex"));
    let rust = json!({
        "success": true,
        "accounts": accounts,
        "primary_account_id": primary,
    });
    assert_eq!(
        rust["accounts"].as_array().unwrap().iter().map(|a| a["id"].as_str().unwrap()).collect::<Vec<_>>(),
        vec!["openai-codex", "openai-codex-second", "openai-codex-third"],
        "legacy first, then created_at"
    );
    assert_eq!(rust["accounts"][0]["is_primary"], json!(true));
    assert_eq!(rust["accounts"][1]["state"], json!("disconnected"));
    assert_eq!(rust["accounts"][2]["state"], json!("credential_missing"));
    assert_eq!(
        rust["accounts"][2]["models"],
        json!({"model_count":0,"total_models":0}),
        "missing credential short-circuits the cache summary"
    );
    assert_eq!(
        rust["accounts"][0]["models"],
        json!({
            "source":"online",
            "fetched_at":"2026-11-24T00:00:00Z",
            "model_count":1,
            "total_models":3,
            "visible_models":["openai-codex/gpt-5"]
        })
    );
    assert_eq!(
        rust["accounts"][1]["models"],
        json!({
            "source":"cache",
            "fetched_at":null,
            "model_count":1,
            "total_models":1,
            "visible_models":["openai-codex-second/gpt-second"]
        }),
        "disconnected accounts with a present credential still expose cache stats"
    );

    let python = std::env::var_os("ASTRION_CODEX_TEST_PYTHON")
        .or_else(|| std::env::var_os("ASTRION_LEGACY_TEST_PYTHON"))
        .unwrap_or_else(|| "python3".into());
    let script = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../test/2026-11-24_Rust后端/codex_accounts_fixture.py");
    let output = std::process::Command::new(python)
        .arg("-B")
        .arg(script.with_file_name("with_legacy_source.py"))
        .arg(script)
        .arg("--data-dir")
        .arg(&fixture.data)
        .env("DATA_DIR", &fixture.data)
        .output()
        .expect("run the Python account_store/Flask fixture");
    assert!(
        output.status.success(),
        "Python fixture failed:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let python_payload: Value = serde_json::from_slice(&output.stdout)
        .expect("Python fixture returned JSON");
    assert_eq!(rust, python_payload);
}

#[test]
fn codex_accounts_skip_index_ids_that_are_not_safe_path_components() {
    let fixture = Fixture::new();
    fixture.write_json(
        &fixture.data.join("codex_accounts.json"),
        json!({"version":1,"accounts":{
            "../escape":{"display_hash":"eeeeee","created_at":"2026-11-24T00:00:00Z","legacy":false},
            "bad/id":{"display_hash":"ffffff","created_at":"2026-11-24T00:00:00Z","legacy":false},
            "openai-codex":{"display_hash":"aaaaaa","created_at":"2026-11-24T00:00:00Z","legacy":true}
        }}),
    );
    fixture.write_json(
        &fixture.data.join("codex_auth.json"),
        json!({"tokens":{"access_token":"legacy","refresh_token":"refresh"}}),
    );
    let (accounts, primary) = super::codex::public_accounts(&fixture.data);
    assert_eq!(primary.as_deref(), Some("openai-codex"));
    assert_eq!(accounts.len(), 1);
    assert_eq!(accounts[0]["id"], json!("openai-codex"));
    assert!(super::codex::resolve_account(&fixture.data, "../escape").is_none());
}


#[tokio::test]
async fn codex_accounts_route_is_web_only_and_matches_the_public_projection() {
    let fixture = Fixture::new();
    fixture.write_json(
        &fixture.data.join("codex_accounts.json"),
        json!({"version":1,"accounts":{
            "openai-codex":{
                "display_hash":"aaaaaa",
                "created_at":"2026-11-24T00:00:00Z",
                "last_login_at":"2026-11-24T01:00:00Z",
                "legacy":true
            },
            "openai-codex-second":{
                "display_hash":"bbbbbb",
                "created_at":"2026-11-25T00:00:00Z",
                "legacy":false
            }
        }}),
    );
    fixture.write_json(
        &fixture.data.join("codex_auth.json"),
        json!({"tokens":{"access_token":"legacy-access","refresh_token":"legacy-refresh"}}),
    );
    fixture.write_json(
        &fixture.data.join("codex_accounts/openai-codex-second.json"),
        json!({"tokens":{"access_token":"second-access","refresh_token":"second-refresh"}}),
    );
    fixture.write_json(
        &fixture.data.join("codex_models.json"),
        json!({
            "source":"online",
            "fetched_at":"2026-11-24T00:00:00Z",
            "models":[{"slug":"gpt-5","visibility":"list","supported_in_api":true}]
        }),
    );

    let web = fixture.web_router_with_codex(
        "http://127.0.0.1:1/models",
        "http://127.0.0.1:1/token",
        "",
    );
    let (status, body) = call(&web, Method::GET, "/api/codex/accounts", None, true).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let (accounts, primary) = super::codex::public_accounts(&fixture.data);
    assert_eq!(
        body,
        json!({
            "success": true,
            "accounts": accounts,
            "primary_account_id": primary,
        })
    );
    assert_eq!(body["primary_account_id"], json!("openai-codex"));
    assert_eq!(body["accounts"][0]["is_primary"], json!(true));

    let unauthenticated = codex_router(fixture.config_with_codex(
        None,
        Some(("http://127.0.0.1:1/models", "http://127.0.0.1:1/token", "")),
    ));
    let (status, _) = call(
        &unauthenticated,
        Method::GET,
        "/api/codex/accounts",
        None,
        false,
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);

    let (status, _) = call(&web, Method::HEAD, "/api/codex/accounts", None, true).await;
    assert_eq!(status, StatusCode::OK, "GET routes answer HEAD");

    let headless = fixture.headless_router_with_codex(
        "http://127.0.0.1:1/models",
        "http://127.0.0.1:1/token",
        "",
    );
    let (status, _) = call(&headless, Method::GET, "/api/codex/accounts", None, true).await;
    assert_eq!(
        status,
        StatusCode::NOT_FOUND,
        "Python headless does not register codex_auth_bp"
    );
}
