use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
};

use axum::{
    Router,
    body::{Body, Bytes},
    extract::State,
    http::StatusCode,
    response::Response,
    routing::post,
};
use jiff::Timestamp;
use serde_json::json;
use tokio::{
    net::TcpListener,
    time::{sleep, timeout},
};

use super::*;
use crate::{
    catalog::CatalogSource,
    runtime::{ModelConfig, Runtime},
    usage::test_support::{Scratch, put, read},
};

#[tokio::test]
async fn stable_attempt_id_and_responses_speed_keep_tokens_and_charges_separate() {
    let fixture = Scratch::new();
    let billing = Billing::new(fixture.root());
    let identity = PriceIdentity::default();
    let scope = BillingScope::main(
        "conv_retries".into(),
        &json!({"total_input_tokens":9, "total_output_tokens":1}),
    );
    let lookup = || ModelPrice {
        model_key: "fixture/model",
        model_id: "model",
        identity: &identity,
        responses: true,
    };
    let mut failed = billing.begin(scope.clone(), lookup()).await;
    failed.observe_text("fragment before disconnect");
    drop(failed);
    assert!(!fixture.path("api_costs").exists());
    let attempt = billing.begin(scope, lookup()).await;
    let usage = json!({"prompt_tokens":6, "completion_tokens":4, "completion_tokens_details":{"reasoning_tokens":2}});
    assert!(attempt.settle_success(Some(&usage), Some(9)).await.unwrap());
    assert!(!attempt.settle_success(Some(&usage), Some(9)).await.unwrap());
    let ledger = read(&fixture.path("api_costs/conv_retries.json"));
    assert_eq!(ledger["actors"]["main"]["input_tokens"], 15);
    assert_eq!(ledger["actors"]["main"]["historical_unpriced"], true);
    let requests = ledger["requests"].as_object().unwrap();
    assert_eq!(requests.len(), 1);
    let record = requests.values().next().unwrap();
    assert_eq!(record["status"], "unpriced");
    assert_eq!(record["output_tokens"], 4);
    assert_eq!(
        record["speed_sample"],
        json!({"output_tokens":2,"duration_ms":9})
    );
    assert!(requests.contains_key(attempt.request_id()));
    let review = billing
        .begin(BillingScope::review("conv_retries".into()), lookup())
        .await;
    assert!(review.settle_success(None, None).await.unwrap());
    let ledger = read(&fixture.path("api_costs/conv_retries.json"));
    assert_eq!(ledger["actors"]["main"]["requests"], 1);
    assert_eq!(
        ledger["actors"]["review_agent:auto_approval"]["kind"],
        "review_agent"
    );
    assert_eq!(
        ledger["actors"]["review_agent:auto_approval"]["name"],
        "auto_approval"
    );
}

#[tokio::test]
async fn catalog_model_settles_success_and_retry_without_charging_a_failed_attempt() {
    let fixture = Scratch::new();
    let config = fixture.path("config");
    let data = fixture.path("data");
    put(
        &config.join("providers_catalog.json"),
        json!({"providers":[{"id":"fixture-provider", "modelsdev_key":"fixture-md"}]}),
    );
    put(
        &data.join("modelsdev_cache.json"),
        json!({
            "version":3,"source":"synthetic registry","fetched_at":Timestamp::now().strftime("%Y-%m-%dT%H:%M:%SZ").to_string(),
            "providers":{"fixture-md":{"models":{"model":{"cost":{"input":2.5,"cache_read":0.1,"output":5}}}},
                         "different-md":{"models":{"model":{"cost":{"input":99}}}}}
        }),
    );
    let successful = concat!(
        "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"OK\"},\"finish_reason\":\"stop\"}]}\n\n",
        "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":6,\"completion_tokens\":4,\"prompt_tokens_details\":{\"cached_tokens\":2}}}\n\n",
        "data: [DONE]\n\n",
    );
    let missing = concat!(
        "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Still a completed response\"},\"finish_reason\":\"stop\"}]}\n\n",
        "data: [DONE]\n\n",
    );
    let interrupted = concat!(
        "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Partial before disconnect\"}}]}\n\n",
        "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":60,\"completion_tokens\":40}}\n\n",
    );
    let responses = Arc::new(Mutex::new(VecDeque::from([
        (
            StatusCode::BAD_GATEWAY,
            "{\"error\":\"synthetic gateway failure\"}",
        ),
        (StatusCode::OK, interrupted),
        (StatusCode::OK, successful),
        (StatusCode::OK, missing),
    ])));
    type FixtureResponses = Arc<Mutex<VecDeque<(StatusCode, &'static str)>>>;
    async fn upstream(State(responses): State<FixtureResponses>, _: Bytes) -> Response {
        let (status, body) = responses
            .lock()
            .unwrap()
            .pop_front()
            .expect("unexpected model retry");
        let content_type = if status == StatusCode::OK {
            "text/event-stream"
        } else {
            "application/json"
        };
        Response::builder()
            .status(status)
            .header("content-type", content_type)
            .body(Body::from(body))
            .unwrap()
    }
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base_url = format!("http://{}/v1", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/v1/chat/completions", post(upstream))
        .with_state(responses.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    put(
        &data.join("providers.json"),
        json!({"providers":{"account":{"catalog_id":"fixture-provider", "api_key":"synthetic-key", "base_url":base_url, "models":[{"id":"model"}]}}}),
    );
    let catalog = CatalogSource::with_paths(fixture.root(), &config, &data).load();
    let selected = catalog.select(Some("account/model"), false).unwrap();
    assert_eq!(
        selected.price_identity.provider_id.as_deref(),
        Some("account")
    );
    assert_eq!(
        selected.price_identity.modelsdev_key.as_deref(),
        Some("fixture-md")
    );
    let model = ModelConfig::from_catalog(selected).unwrap();
    let runtime = Runtime::open(&data).unwrap().with_pricing(&config);
    runtime.bind_workspace("fixture", fixture.root()).unwrap();
    let first = runtime
        .create_task(
            "fixture",
            &json!({"message":"first attempt"}),
            model.clone(),
        )
        .unwrap();
    let conversation = first["data"]["conversation_id"].as_str().unwrap();
    let path = data.join("api_costs").join(format!("{conversation}.json"));
    assert_eq!(terminal_status(&runtime, &first).await, "failed");
    assert!(!path.exists());
    let disconnected = runtime
        .create_task(
            "fixture",
            &json!({"message":"interrupted stream","conversation_id":conversation}),
            model.clone(),
        )
        .unwrap();
    assert_eq!(terminal_status(&runtime, &disconnected).await, "failed");
    assert!(!path.exists());
    for text in ["retry", "missing usage"] {
        let task = runtime
            .create_task(
                "fixture",
                &json!({"message":text,"conversation_id":conversation}),
                model.clone(),
            )
            .unwrap();
        assert_eq!(terminal_status(&runtime, &task).await, "succeeded");
    }
    let ledger = read(&path);
    assert_eq!(ledger["actors"]["main"]["requests"], 2);
    assert_eq!(ledger["actors"]["main"]["priced_requests"], 1);
    assert_eq!(ledger["actors"]["main"]["unpriced_requests"], 1);
    assert_eq!(ledger["actors"]["main"]["cost_usd"], "0.0000302");
    assert_eq!(ledger["actors"]["main"]["total_tokens"], 10);
    let requests = ledger["requests"].as_object().unwrap();
    assert!(requests.keys().all(|id| Uuid::parse_str(id).is_ok()));
    let records: Vec<_> = requests.values().collect();
    assert_eq!(records[0]["price"]["source"], "synthetic registry");
    assert_eq!(records[0]["price"]["provider_id"], "account");
    assert_eq!(records[0]["price"]["model_key"], "account/model");
    assert_eq!(records[0]["price"]["model_id"], "model");
    assert_eq!(records[0]["price"]["modelsdev_key"], "fixture-md");
    assert!(records[0].get("speed_sample").is_none());
    assert_eq!(records[1]["status"], "unpriced");
    assert!(records[1]["cost_usd"].is_null());
    assert!(responses.lock().unwrap().is_empty());
    server.abort();
}

async fn terminal_status(runtime: &Runtime, task: &Value) -> String {
    let id = task["data"]["task_id"].as_str().unwrap();
    timeout(Duration::from_secs(8), async {
        loop {
            let snapshot = runtime.poll_task("fixture", id, 0).unwrap();
            let status = snapshot["data"]["status"].as_str().unwrap();
            if status != "running" {
                if status == "failed" {
                    eprintln!("synthetic task failure: {}", snapshot["data"]["error"]);
                }
                return status.to_owned();
            }
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap()
}
