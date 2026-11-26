//! Offline HTTP quota contract with genuine Web cookie and CSRF middleware.
use std::{
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

use axum::{
    Extension, Json, Router,
    body::{Body, to_bytes},
    extract::{ConnectInfo, Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::any,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use jiff::Timestamp;
use serde_json::{Value, json};
use tokio::{net::TcpListener, task::JoinHandle};
use tower::ServiceExt;

use super::{Endpoints, ProviderConfig, router_with_endpoints};
use crate::web_auth::{WebAuth, WebAuthConfig};

struct Fixture {
    data: PathBuf,
    app: Router,
    mock: Arc<Mutex<Mock>>,
    task: JoinHandle<()>,
    cookie: String,
    csrf: String,
}

struct Mock {
    calls: Vec<Call>,
    usage: Value,
    usage_status: StatusCode,
    opencode_status: StatusCode,
}

struct Call {
    path: String,
    method: String,
    authority: String,
    remote_account: String,
    codex_beta: bool,
    body: Value,
}

fn store(path: &Path, value: &Value) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
}

fn token(subject: &str) -> String {
    let data = json!({"sub": subject, "exp": Timestamp::now().as_second() + 3600});
    format!("e30.{}.fixture", URL_SAFE_NO_PAD.encode(data.to_string()))
}

async fn upstream(State(mock): State<Arc<Mutex<Mock>>>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let body = to_bytes(body, 100_000).await.unwrap();
    let path = parts.uri.path().to_owned();
    let header = |key| {
        parts
            .headers
            .get(key)
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default()
            .to_owned()
    };
    let mut mock = mock.lock().unwrap();
    mock.calls.push(Call {
        path: path.clone(),
        method: parts.method.to_string(),
        authority: header("authorization"),
        remote_account: header("chatgpt-account-id"),
        codex_beta: parts.headers.contains_key("openai-beta"),
        body: serde_json::from_slice(&body).unwrap_or(Value::Null),
    });
    match path.as_str() {
        "/usage" => (mock.usage_status, Json(mock.usage.clone())),
        "/credits" => (
            StatusCode::OK,
            Json(json!({"credits": [{"id": "credit-one", "status": "available"}]})),
        ),
        "/consume" => (
            StatusCode::OK,
            Json(json!({"credit_id": "credit-one", "status": "redeemed"})),
        ),
        "/opencode" => (
            mock.opencode_status,
            Json(json!({"usage": {
                "monthly": {"percent": 105},
                "rolling": {"percent": 2.5, "resetsAt": "tomorrow", "status": "valid"},
                "weekly": {"percent": 3.5, "resets_at": "next-week"}
            }})),
        ),
        _ => (StatusCode::NOT_FOUND, Json(Value::Null)),
    }
    .into_response()
}

impl Fixture {
    async fn new() -> Self {
        let data =
            std::env::temp_dir().join(format!("rust-quota-fixture-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&data).unwrap();
        store(
            &data.join("codex_auth.json"),
            &json!({"tokens": {
                "access_token": token("legacy"), "refresh_token": "synthetic-refresh", "account_id": "synthetic-remote"
            }}),
        );
        let mock = Arc::new(Mutex::new(Mock {
            calls: Vec::new(),
            usage: json!({"rate_limit": {"primary_window": {
                "used_percent": 20, "limit_window_seconds": 604_800, "reset_at": Timestamp::now().as_second() + 7200
            }, "secondary_window": {
                "used_percent": 80, "limit_window_seconds": 18_000, "reset_at": Timestamp::now().as_second() + 3600
            }}}),
            usage_status: StatusCode::OK,
            opencode_status: StatusCode::OK,
        }));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let router = Router::new()
            .route("/{path}", any(upstream))
            .with_state(mock.clone());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        let config = ProviderConfig::new(data.join("resources"), &data, data.join("deploy"))
            .with_client(
                reqwest::Client::builder()
                    .no_proxy()
                    .redirect(reqwest::redirect::Policy::none())
                    .timeout(Duration::from_secs(2))
                    .build()
                    .unwrap(),
            );
        let endpoints = Endpoints::default()
            .with_usage(format!("{base}/usage"))
            .with_reset_credits(format!("{base}/credits"), format!("{base}/consume"))
            .with_opencode_usage(format!("{base}/opencode"));
        let auth = WebAuth::new(WebAuthConfig::new(&data, 8193)).unwrap();
        let app = auth
            .router()
            .merge(auth.protect_api(router_with_endpoints(config, endpoints)))
            .layer(Extension(ConnectInfo(
                "127.0.0.1:8193".parse::<SocketAddr>().unwrap(),
            )));
        let challenge = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/csrf-token")
                    .header("host", "127.0.0.1:8193")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let first = cookie(&challenge);
        let token = decode(challenge).await["token"]
            .as_str()
            .unwrap()
            .to_owned();
        let login = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/host-login")
                    .header("host", "127.0.0.1:8193")
                    .header("origin", "http://127.0.0.1:8193")
                    .header("cookie", first)
                    .header("x-csrf-token", token)
                    .header("content-type", "application/json")
                    .body(Body::from("{}"))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(login.status(), StatusCode::OK);
        let cookie = cookie(&login);
        let csrf = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/csrf-token")
                    .header("host", "127.0.0.1:8193")
                    .header("cookie", &cookie)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let csrf = decode(csrf).await["token"].as_str().unwrap().to_owned();
        Self {
            data,
            app,
            mock,
            task,
            cookie,
            csrf,
        }
    }

    async fn request(
        &self,
        method: &str,
        path: &str,
        body: Value,
        authenticated: bool,
        csrf: bool,
    ) -> (StatusCode, Value) {
        let mut request = Request::builder()
            .method(method)
            .uri(path)
            .header("host", "127.0.0.1:8193")
            .header("origin", "http://127.0.0.1:8193")
            .header("content-type", "application/json");
        if authenticated {
            request = request.header("cookie", &self.cookie);
        }
        if csrf {
            request = request.header("x-csrf-token", &self.csrf);
        }
        let response = self
            .app
            .clone()
            .oneshot(request.body(Body::from(body.to_string())).unwrap())
            .await
            .unwrap();
        (response.status(), decode(response).await)
    }

    async fn get(&self, path: &str) -> (StatusCode, Value) {
        self.request("GET", path, Value::Null, true, false).await
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.task.abort();
        let _ = fs::remove_dir_all(&self.data);
    }
}

fn cookie(response: &Response) -> String {
    response.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned()
}

async fn decode(response: Response) -> Value {
    serde_json::from_slice(&to_bytes(response.into_body(), 1_000_000).await.unwrap()).unwrap()
}

#[tokio::test]
async fn account_quota_cache_identity_and_explicit_credit_redemption() {
    let fixture = Fixture::new().await;
    let usage = "/api/codex/usage";
    let consume = "/api/codex/reset-credits/consume";
    assert_eq!(
        fixture
            .request("GET", usage, Value::Null, false, false)
            .await
            .0,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        fixture.get(&format!("{usage}?account_id=unknown")).await.0,
        StatusCode::NOT_FOUND
    );
    let first = fixture.get(usage).await;
    assert_eq!(first.0, StatusCode::OK);
    assert_eq!(first.1["account_id"], "openai-codex");
    assert_eq!(first.1["account_hash"].as_str().unwrap().len(), 6);
    assert_eq!(fixture.get(usage).await, first);
    assert_eq!(fixture.mock.lock().unwrap().calls.len(), 1);
    {
        let mock = fixture.mock.lock().unwrap();
        assert_eq!(mock.calls[0].remote_account, "synthetic-remote");
        let credential: Value =
            serde_json::from_slice(&fs::read(fixture.data.join("codex_auth.json")).unwrap())
                .unwrap();
        assert_eq!(
            mock.calls[0].authority,
            format!(
                "Bearer {}",
                credential["tokens"]["access_token"].as_str().unwrap()
            )
        );
        assert!(!mock.calls[0].codex_beta);
    }
    assert_eq!(
        fixture.get(&format!("{usage}?refresh=1")).await.0,
        StatusCode::OK
    );
    let listing = fixture.get("/api/codex/reset-credits").await;
    assert_eq!(listing.1["credits"][0]["id"], "credit-one");
    assert_eq!(
        fixture
            .request(
                "POST",
                consume,
                json!({"credit_id":"credit-one"}),
                true,
                false
            )
            .await
            .0,
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        fixture
            .request("POST", consume, json!({}), true, true)
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    let consumed = fixture
        .request(
            "POST",
            consume,
            json!({"credit_id":"credit-one"}),
            true,
            true,
        )
        .await;
    assert_eq!(consumed.0, StatusCode::OK);
    assert_eq!(consumed.1["account_id"], "openai-codex");
    {
        let mock = fixture.mock.lock().unwrap();
        let call = mock.calls.last().unwrap();
        assert_eq!(
            (call.path.as_str(), call.method.as_str()),
            ("/consume", "POST")
        );
        assert_eq!(call.body["credit_id"], "credit-one");
        assert_eq!(
            uuid::Uuid::parse_str(call.body["redeem_request_id"].as_str().unwrap())
                .unwrap()
                .get_version_num(),
            4
        );
    }
    fixture.mock.lock().unwrap().usage_status = StatusCode::TOO_MANY_REQUESTS;
    assert_eq!(fixture.get(usage).await.1["error"], "http_429");
    fixture.mock.lock().unwrap().usage_status = StatusCode::OK;
    fixture.mock.lock().unwrap().usage = json!({"unexpected": true});
    assert_eq!(
        fixture.get(usage).await.1["error"],
        "invalid_subscription_usage"
    );
}

#[tokio::test]
async fn opencode_selection_normalization_credential_rotation_and_upstream_errors() {
    let fixture = Fixture::new().await;
    let config = fixture.data.join("providers.json");
    let mut providers = json!({"version": 2, "providers": {
        "disabled": {"base_url": "https://opencode.ai/zen/go/v1", "enabled": false, "api_key": "mock"},
        "other": {"base_url": "https://other.invalid/zen/go/v1", "api_key": "mock"},
        "empty": {"base_url": "https://opencode.ai/zen/go/v1", "api_key": ""},
        "go": {"base_url": "https://opencode.ai/zen/go/v1", "name": "Go fixture", "api_key": "synthetic-a"}
    }});
    store(&config, &providers);
    let base = "/api/subscription/opencode-usage";
    for (id, code) in [
        ("missing", "provider_not_found"),
        ("disabled", "provider_disabled"),
        ("other", "not_opencode_provider"),
        ("empty", "credential_missing"),
    ] {
        let reply = fixture.get(&format!("{base}?provider_id={id}")).await;
        assert_eq!(
            reply,
            (
                StatusCode::OK,
                json!({"success": false, "error": code, "provider_id": id})
            )
        );
    }
    let (status, first) = fixture.get(base).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(first["provider_id"], "");
    assert_eq!(first["label"], "Go fixture");
    assert_eq!(
        first["windows"],
        json!([
            {"key": "rolling", "percent": 2, "reset_at": "tomorrow", "status": "valid"},
            {"key": "weekly", "percent": 4, "reset_at": "next-week", "status": ""},
            {"key": "monthly", "percent": 100, "reset_at": "", "status": ""}
        ])
    );
    assert_eq!(fixture.get(base).await.1, first);
    assert_eq!(fixture.mock.lock().unwrap().calls.len(), 1);
    providers["providers"]["go"]["api_key"] = json!("synthetic-b");
    store(&config, &providers);
    assert_eq!(fixture.get(base).await.1["success"], true);
    assert_eq!(
        fixture.mock.lock().unwrap().calls.last().unwrap().authority,
        "Bearer synthetic-b"
    );
    assert!(!first.to_string().contains("synthetic"));
    for (status, code) in [
        (403, "invalid_credential"),
        (404, "not_subscribed"),
        (429, "http_429"),
    ] {
        fixture.mock.lock().unwrap().opencode_status = StatusCode::from_u16(status).unwrap();
        assert_eq!(
            fixture.get(&format!("{base}?refresh=1")).await.1["error"],
            code
        );
    }
}

#[tokio::test]
async fn weekly_tracker_rolls_periods_and_preserves_snapshot_after_error() {
    let fixture = Fixture::new().await;
    let path = "/api/codex/weekly-value";
    let now = Timestamp::now();
    let cost = |model, cost| {
        json!({
            "recorded_at": now.checked_sub(Duration::from_secs(60)).unwrap().to_string(),
            "price": {"model_key": model}, "cost_usd": cost,
        })
    };
    store(
        &fixture.data.join("api_costs/agent.json"),
        &json!({"requests": {
            "shared": cost("codex/GPT-6", "0.5000005"),
            "unrelated": cost("qwen", "2"),
        }}),
    );
    store(
        &fixture.data.join("chat/api_costs/chat.json"),
        &json!({"requests": {
            "shared": cost("codex/GPT-6", "0.5000005"),
            "unique": cost("gpt-fixture", "1.0000000"),
        }}),
    );
    store(
        &fixture
            .data
            .join("legacy-user/api_costs/old-conversation.json"),
        &json!({"requests": {
            "archived-user": cost("gpt-fixture", "0.2000000"),
            "out-of-window": {
                "recorded_at": "2024-01-01T01:00:00+00:00",
                "price": {"model_key": "gpt-fixture"}, "cost_usd": "50.0"
            },
        }}),
    );
    let state = fixture.data.join("weekly_quota.json");
    store(
        &state,
        &json!({"version": 1, "accounts": {"openai-codex": {
            "current": {"period_end":1,"resets_at":1,"used_percent":30}, "history": [],
        }}}),
    );
    assert_eq!(fixture.get(path).await.1["current"]["resets_at"], 1);
    assert_eq!(
        fixture.get(&format!("{path}?refresh=1")).await.0,
        StatusCode::OK
    );
    let current = wait_sample(&fixture, path, |data| {
        data["current"]["resets_at"].as_i64().unwrap_or_default() > 1
    })
    .await;
    assert_eq!(current["history"][0]["period_end"], 1);
    assert_eq!(current["current"]["window_seconds"], 604_800);
    assert_eq!(current["current"]["api_value_usd"], "1.700000");
    assert_eq!(current["current"]["max_value_usd"], "8.500002");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&state).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
    let mut stored: Value = serde_json::from_slice(&fs::read(&state).unwrap()).unwrap();
    stored["last_attempt_at"] = json!("2024-01-01T01:00:00+00:00");
    store(&state, &stored);
    fixture.mock.lock().unwrap().usage = json!({"rate_limit": {}});
    fixture.get(&format!("{path}?refresh=1")).await;
    let failed = wait_sample(&fixture, path, |data| {
        data["last_error"]
            .as_str()
            .is_some_and(|error| error.contains("weekly_window_unavailable"))
    })
    .await;
    assert_eq!(failed["current"], current["current"]);
}

async fn wait_sample(fixture: &Fixture, path: &str, complete: impl Fn(&Value) -> bool) -> Value {
    tokio::time::timeout(Duration::from_secs(4), async {
        loop {
            let (_, data) = fixture.get(path).await;
            if complete(&data) {
                break data;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("weekly sample did not finish")
}
