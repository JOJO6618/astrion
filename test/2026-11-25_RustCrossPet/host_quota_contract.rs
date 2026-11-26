use super::*;
use astrion_headless::crosspet::CrossPet;
use axum::{
    Json, Router,
    body::Body,
    http::{HeaderMap, Request, StatusCode},
    routing::get,
};
use serde_json::{Value, json};
use std::{fs, path::PathBuf, sync::Arc, time::Duration};
use tokio::{
    net::TcpListener,
    sync::{mpsc, oneshot},
    time::{sleep, timeout},
};
use tower::ServiceExt;
use uuid::Uuid;

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("astrion-host-quota-{}", Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        Self(root.canonicalize().unwrap())
    }

    fn snapshot(&self) -> Option<Value> {
        serde_json::from_slice(&fs::read(self.0.join("output/astrion-state.json")).ok()?).ok()
    }

    async fn quota_at(&self, status: &str) -> Value {
        self.quota_matching(status, |_| true).await
    }

    async fn quota_matching(&self, status: &str, matches: impl Fn(&Value) -> bool) -> Value {
        timeout(Duration::from_secs(8), async {
            loop {
                if let Some(snapshot) = self.snapshot() {
                    let quota = &snapshot["quota"]["gpt"];
                    if quota["status"] == status && matches(quota) {
                        return quota.clone();
                    }
                }
                sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap_or_else(|_| panic!("timed out waiting for quota status {status}"))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn write_token(path: &std::path::Path, access: &str, account: &str) {
    let pending = path.with_extension("pending");
    let credential = json!({"auth_mode": "chatgpt", "tokens": {
        "access_token": access, "refresh_token": "synthetic-refresh", "account_id": account
    }});
    fs::write(&pending, credential.to_string()).unwrap();
    fs::rename(pending, path).unwrap();
}

async fn switch(service: &CrossPet, enabled: bool) {
    let request = Request::post("/api/crosspet")
        .header("content-type", "application/json")
        .body(Body::from(json!({"enabled": enabled}).to_string()))
        .unwrap();
    assert_eq!(
        service.router().oneshot(request).await.unwrap().status(),
        StatusCode::OK
    );
}

fn usage(used: u64) -> Value {
    json!({"plan_type":"private-synthetic-plan", "account_id":"private-upstream-account",
    "credits": {"balance":900}, "rate_limit":{"primary_window": {
        "used_percent":used, "limit_window_seconds":18_030, "reset_at":4_000_000_001_u64,
        "private": "private-upstream-sample"
    }}})
}

type UsageRequest = (HeaderMap, oneshot::Sender<Value>);

async fn next_request(
    requests: &mut mpsc::Receiver<UsageRequest>,
    access: &str,
    account: &str,
) -> oneshot::Sender<Value> {
    let (headers, reply) = timeout(Duration::from_secs(8), requests.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(headers["authorization"], format!("Bearer {access}"));
    assert_eq!(headers["chatgpt-account-id"], account);
    assert_eq!(headers["originator"], "codex_cli_rs");
    assert_eq!(headers["accept"], "application/json");
    assert!(!headers.contains_key("openai-beta"));
    reply
}

#[tokio::test(flavor = "multi_thread")]
async fn host_crosspet_uses_local_oauth_and_publishes_only_current_numeric_quota() {
    let scratch = Scratch::new();
    let paths = HostPaths {
        deploy: scratch.0.join("deploy"),
        logs: scratch.0.join("logs"),
        resources: scratch.0.join("resources"),
        host_libraries: scratch.0.join("host"),
        workspace_config: scratch.0.join("deploy/host_workspaces.json"),
    };
    let data = paths.host_libraries.join("data");
    let runtime = Runtime::open(&data).unwrap();
    let catalog = CatalogSource::with_paths(&paths.resources, &paths.deploy, &data);
    let navigation = Navigation::new(&data);
    let children = SubAgents::new(&data, runtime.clone());
    let config = CrossPetConfig::with_paths(
        paths.deploy.join("crosspet.json"),
        scratch.0.join("integration.json"),
        scratch.0.join("output"),
    );
    // A legacy installation can have credentials without an account registry.
    let credential = data.join("codex_auth.json");
    let account_index = data.join("codex_accounts.json");
    write_token(&credential, "synthetic-access-a", "synthetic-account-a");

    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let (sent, mut requests) = mpsc::channel::<UsageRequest>(8);
    let upstream = tokio::spawn(async move {
        let app = Router::new().route(
            "/usage",
            get(move |headers: HeaderMap| {
                let sent = sent.clone();
                async move {
                    let (reply, result) = oneshot::channel();
                    sent.send((headers, reply)).await.unwrap();
                    Json(result.await.unwrap())
                }
            }),
        );
        axum::serve(listener, app).await.unwrap();
    });
    let providers = ProviderConfig::host(&paths.resources, &paths.deploy, &data)
        .with_client(reqwest::Client::builder().no_proxy().build().unwrap())
        .with_crosspet_usage_url(format!("{endpoint}/usage"))
        .with_codex_endpoints(
            format!("{endpoint}/models"),
            format!("{endpoint}/token"),
            format!("{endpoint}/npm"),
        );
    let observer = HostCrossPetRuntime::new(
        runtime,
        catalog,
        navigation,
        children,
        paths,
        providers,
        config.clone(),
    );
    let service = CrossPet::start(config, Arc::new(observer)).unwrap();
    assert!(
        timeout(Duration::from_millis(2_150), requests.recv())
            .await
            .is_err()
    );
    assert!(scratch.snapshot().is_none());

    switch(&service, true).await;
    let first = next_request(&mut requests, "synthetic-access-a", "synthetic-account-a").await;
    let checking = scratch.quota_at("checking").await;
    assert!(checking["at"].is_null());
    first.send(usage(24)).unwrap();
    let live = scratch.quota_at("live").await;
    assert_eq!(live["epoch"], checking["epoch"]);
    assert_ne!(live["epoch"], Value::Null);
    assert!(live["at"].as_f64().unwrap() > 0.0);
    assert_eq!(
        live["rateLimits"],
        json!({"primary": {
            "usedPercent":24.0, "windowDurationMins":300.5, "resetsAt":4_000_000_001.0
        }})
    );
    let packet = scratch.snapshot().unwrap().to_string();
    assert!(!packet.contains("synthetic"));
    assert!(!packet.contains("credits"));
    assert!(!account_index.exists());

    fs::write(&credential, b"{malformed-synthetic-credential").unwrap();
    let stale = scratch.quota_at("stale").await;
    assert_eq!(stale["epoch"], live["epoch"]);
    assert_eq!(stale["at"], live["at"]);
    assert_eq!(stale["rateLimits"], live["rateLimits"]);
    assert!(requests.try_recv().is_err());

    write_token(&credential, "synthetic-access-b", "synthetic-account-b");
    let replaced = next_request(&mut requests, "synthetic-access-b", "synthetic-account-b").await;
    let replacement = scratch.quota_at("checking").await;
    assert_ne!(replacement["epoch"], live["epoch"]);
    assert!(replacement["rateLimits"].is_null());
    switch(&service, false).await;
    write_token(&credential, "synthetic-access-c", "synthetic-account-c");
    let disabled = scratch.snapshot().unwrap();
    replaced.send(usage(99)).unwrap();
    assert!(
        timeout(Duration::from_millis(2_150), requests.recv())
            .await
            .is_err()
    );
    assert_eq!(scratch.snapshot().unwrap(), disabled);

    switch(&service, true).await;
    let next = next_request(&mut requests, "synthetic-access-c", "synthetic-account-c").await;
    scratch
        .quota_matching("checking", |quota| quota["epoch"] != replacement["epoch"])
        .await;
    next.send(usage(51)).unwrap();
    let live = scratch.quota_at("live").await;
    assert_eq!(live["rateLimits"]["primary"]["usedPercent"], json!(51.0));

    fs::remove_file(credential).unwrap();
    let disconnected = scratch.quota_at("unavailable").await;
    assert_eq!(
        disconnected,
        json!({"connected":false,"status":"unavailable"})
    );
    assert!(!account_index.exists());
    assert!(requests.try_recv().is_err());
    service.shutdown();
    upstream.abort();
}
