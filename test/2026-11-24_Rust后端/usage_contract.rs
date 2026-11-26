use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc, Barrier,
        atomic::{AtomicUsize, Ordering},
    },
};

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use jiff::Timestamp;
use serde_json::{Value, json};
use tower::ServiceExt;
use uuid::Uuid;

use super::*;

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("astrion-usage-test-{}", Uuid::new_v4()));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn path(&self, path: &str) -> PathBuf {
        self.0.join(path)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn put(path: &Path, value: Value) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
}

fn read(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

fn price() -> PriceSnapshot {
    serde_json::from_value(json!({
        "model_key": "provider/m", "provider_id": "provider",
        "rates": {"input": 0.12345678901234567890, "output": 2, "cache_read": 0.01},
        "source": "synthetic", "fetched_at": "2026-11-24T12:00:00Z",
    }))
    .unwrap()
}

#[test]
fn prices_use_exact_provider_and_freeze_provenance() {
    let fixture = Fixture::new();
    let source = PricingSource {
        cache_path: fixture.path("modelsdev_cache.json"),
        bundled_snapshot_path: fixture.path("modelsdev_snapshot.json"),
    };
    let registry = |input| {
        json!({
            "version": 2, "source": "https://example.test/pricing", "fetched_at": "2026-11-22T10:00:00Z",
            "providers": {
                "openai": {"models": {"exact/m": {"cost": {"input": input, "cache_read": 0, "output": 12}}}},
                "other": {"models": {"missing": {"cost": {"input": 50, "output": 20}}}},
            }
        })
    };
    put(&source.bundled_snapshot_path, registry(3));
    let snapshot = source.capture(
        "openai-codex/account/exact/m",
        Some("account"),
        Some("openai"),
        "exact/m",
    );
    put(&source.cache_path, registry(30));
    let entry = calculate_cost(
        Some(
            &json!({"input_tokens": 7, "input_tokens_details": {"cached_tokens": 2}, "output_tokens": 5}),
        ),
        snapshot,
    );
    assert_eq!(entry.cost_usd.as_deref(), Some("0.000075"));
    assert_eq!(entry.price.rates.input.unwrap().as_u64(), Some(3));
    assert_eq!(
        entry.price.source.as_deref(),
        Some("https://example.test/pricing")
    );
    assert_eq!(
        entry.price.fetched_at.as_deref(),
        Some("2026-11-22T10:00:00Z")
    );
    assert_eq!(entry.price.provider_id.as_deref(), Some("account"));
    let current = source.capture(
        "openai-codex/account/exact/m",
        Some("account"),
        Some("openai"),
        "exact/m",
    );
    assert_eq!(current.rates.input.unwrap().as_u64(), Some(30));
    for snapshot in [
        source.capture("a/missing", Some("a"), Some("openai"), "missing"),
        source.capture("custom", None, None, "exact/m"),
    ] {
        let entry = calculate_cost(Some(&json!({"input_tokens": 1})), snapshot);
        assert_eq!(entry.status, Status::Unpriced);
        assert_eq!(entry.cost_usd, None);
    }
}

#[test]
fn missing_usage_and_missing_rate_are_distinct_from_explicit_zero() {
    let mut price = price();
    price.rates.cache_read = None;
    assert_eq!(calculate_cost(None, price.clone()).cost_usd, None);
    assert_eq!(
        calculate_cost(Some(&json!({"duration_ms": 20})), price.clone()).cost_usd,
        None
    );
    assert_eq!(
        calculate_cost(Some(&json!({"input_tokens": 0})), price.clone())
            .cost_usd
            .as_deref(),
        Some("0")
    );
    let entry = calculate_cost(
        Some(
            &json!({"input_tokens": 6, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 2, "output_tokens": 1}),
        ),
        price,
    );
    assert_eq!(entry.status, Status::Unpriced);
    assert_eq!(
        (
            entry.tokens.input_tokens,
            entry.tokens.cached_input_tokens,
            entry.tokens.output_tokens
        ),
        (12, 4, 1)
    );

    let high_precision =
        serde_json::from_str::<Value>(r#"{"input":0.1234567890123456789012345}"#).unwrap();
    let price = PriceSnapshot {
        rates: serde_json::from_value(high_precision).unwrap(),
        ..PriceSnapshot::default()
    };
    let entry = calculate_cost(Some(&json!({"prompt_tokens": 3})), price);
    assert_eq!(
        entry.cost_usd.as_deref(),
        Some("0.0000003703703670370370367037035")
    );
}

#[test]
fn ledger_commits_once_under_parallel_writers_and_retains_historical_baseline() {
    let fixture = Fixture::new();
    let ledger = Ledger::new(&fixture.0);
    let price = PriceSnapshot {
        rates: serde_json::from_value(json!({"input": 0.1, "output": 0.2})).unwrap(),
        ..price()
    };
    let entry = calculate_cost(
        Some(&json!({"prompt_tokens": 1, "completion_tokens": 3})),
        price,
    );
    let barrier = Arc::new(Barrier::new(8));
    let winners = AtomicUsize::new(0);
    std::thread::scope(|threads| {
        for index in 0..8 {
            let barrier = barrier.clone();
            let (ledger, entry, winners) = (&ledger, &entry, &winners);
            threads.spawn(move || {
                let mut main = Actor::new("main", "Main", "main");
                main.baseline.input_tokens = 10;
                main.baseline.total_tokens = 10;
                main.historical_unpriced = true;
                let timing = RequestTiming {
                    duration_ms: Some(900),
                    ttft_ms: Some(200),
                    speed_sample: Some(SpeedSample::default()),
                };
                barrier.wait();
                if ledger
                    .record_request("conv_shared", "same-uuid", &main, entry, &timing)
                    .unwrap()
                {
                    winners.fetch_add(1, Ordering::Relaxed);
                }
                assert!(
                    ledger
                        .record_request(
                            "conv_shared",
                            &format!("req-{index}"),
                            &main,
                            entry,
                            &timing
                        )
                        .unwrap()
                );
            });
        }
    });
    let stats = ledger
        .actor_statistics("conv_shared", "main")
        .unwrap()
        .unwrap();
    assert_eq!(winners.load(Ordering::Relaxed), 1);
    assert_eq!(stats["requests"], 9);
    assert_eq!(stats["input_tokens"], 19);
    assert_eq!(stats["total_tokens"], 46);
    assert_eq!(stats["cost_usd"], "0.0000063");
    let on_disk = read(&fixture.path("api_costs/conv_shared.json"));
    assert_eq!(on_disk["version"], 1);
    assert_eq!(on_disk["requests"].as_object().unwrap().len(), 9);
    assert_eq!(on_disk["requests"]["same-uuid"]["speed_sample"], json!({}));
    let sub = SubAgent {
        task_id: "archived",
        display_name: None,
        summary: Some("Older task"),
        cost_tracking_version: false,
    };
    let summary = ledger.summarize("conv_shared", 0, &[sub]).unwrap();
    assert_eq!(summary["total_usd"], "0.0000063");
    assert_eq!(summary["has_priced"], true);
    assert_eq!(summary["partial"], true);
    assert_eq!(summary["actors"][1]["name"], "Older task");
    assert!(ledger.actor_statistics("../elsewhere", "main").is_err());

    let now = Timestamp::now();
    let config = UsageConfig {
        shared_agent_data_dir: Some(fixture.0.clone()),
        ..UsageConfig::default()
    };
    let report = config.report(&ReportQuery::default(), now).unwrap();
    assert_eq!(report["overview"]["requests"], 9);
    assert_eq!(report["overview"]["total_tokens"], 36);
    assert_eq!(report["ranges"]["all"]["models"][0]["speed_tokens"], 0);
    assert_eq!(report["ranges"]["all"]["models"][0]["ttft_n"], 9);
}

#[test]
fn updates_existing_actor_without_repricing_historical_requests() {
    let fixture = Fixture::new();
    let path = fixture.path("api_costs/conv_existing.json");
    put(
        &path,
        json!({
            "version": 1, "metadata": {"retained": true},
            "actors": {
                "main": {"id":"main", "name":"Original", "kind":"main", "cost_usd":"3e-5", "requests":2,
                    "priced_requests":1, "unpriced_requests":0, "subscription_requests":1, "local_requests":0,
                    "input_tokens":110, "output_tokens":9, "total_tokens":119, "historical_unpriced":true}
            },
            "requests": {"paid": {"status":"priced"}, "subscription": {"status":"subscription", "cost_usd":null}}
        }),
    );
    let ledger = Ledger::new(&fixture.0);
    let entry = calculate_cost(None, price());
    let mut actor = Actor::new("main", "Renamed", "main");
    actor.baseline.input_tokens = 100_000;
    assert!(
        ledger
            .record_request(
                "conv_existing",
                "missing-usage",
                &actor,
                &entry,
                &RequestTiming::default()
            )
            .unwrap()
    );
    let on_disk = read(&path);
    assert_eq!(on_disk["metadata"], json!({"retained":true}));
    assert_eq!(
        on_disk["requests"]["subscription"]["status"],
        "subscription"
    );
    assert_eq!(on_disk["requests"]["missing-usage"]["status"], "unpriced");
    assert!(on_disk["requests"]["missing-usage"]["cost_usd"].is_null());
    let stats = ledger
        .actor_statistics("conv_existing", "main")
        .unwrap()
        .unwrap();
    assert_eq!(stats["input_tokens"], 110);
    assert_eq!(stats["cost_usd"], "0.00003");
    assert_eq!(stats["name"], "Renamed");
    assert_eq!(stats["requests"], 3);
    assert_eq!(stats["subscription_requests"], 1);
    assert_eq!(stats["unpriced_requests"], 1);
}

fn request(time: &str, actor: &str, model: &str, cost: &str) -> Value {
    json!({"recorded_at": time, "actor_id": actor, "price": {"model_key": model}, "status": "priced", "cost_usd": cost,
           "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10, "duration_ms": 200, "ttft_ms": 50})
}

fn legacy(requests: Value) -> Value {
    json!({"version": 1, "actors": {"main": {"kind": "main"}, "review_agent:goal_review": {"kind": "review_agent"}}, "requests": requests})
}

#[test]
fn report_preserves_sources_ranges_timezone_and_unpriced_usage() {
    let fixture = Fixture::new();
    let agent = fixture.path("agent");
    let ref_a = WorkspaceRef {
        workspace_id: "a".into(),
        label: "Research".into(),
        data_dir: agent.clone(),
        conversations_dir: fixture.path("a"),
    };
    let ref_b = WorkspaceRef {
        workspace_id: "b".into(),
        label: "Draft".into(),
        data_dir: agent.clone(),
        conversations_dir: fixture.path("b"),
    };
    put(&ref_a.conversations_dir.join("conv_a.json"), json!({}));
    put(&ref_b.conversations_dir.join("conv_a.json"), json!({}));
    let mut missing = request(
        "2026-11-02T05:00:00+00:00",
        "review_agent:goal_review",
        "b/model",
        "0.5",
    );
    missing["status"] = json!("unpriced");
    let mut sample = request("2026-11-02T05:25:00+00:00", "main", "a/model", "0.02");
    sample["speed_sample"] = json!({"output_tokens": 5, "duration_ms": 10});
    let mut corrupt = request("2026-11-02T05:25:00+00:00", "main", "a/model", "NaN");
    corrupt["speed_sample"] = json!({});
    put(
        &agent.join("api_costs/conv_a.json"),
        legacy(json!({
            "old": request("2024-11-01T20:00:00", "main", "a/model", "0.1"),
            "dst": request("2026-11-01T04:00:00Z", "main", "a/model", "0.002"),
            "fallback": request("2026-11-02T04:59:59Z", "main", "a/model", "0.003"),
            "current": sample,
            "invalid_cost": corrupt,
            "future": request("2026-11-06T04:00:00Z", "main", "a/model", "4"),
        })),
    );
    put(
        &agent.join("api_costs/conv_gone.json"),
        legacy(json!({"missing": missing})),
    );
    let chat = fixture.path("chat");
    put(
        &chat.join("api_costs/conv_a.json"),
        legacy(json!({"first": request("2026-11-01T10:00:00Z", "main", "b/model", "0") })),
    ); // Same ID, separate root.
    put(
        &fixture.path("exchange_rates.json"),
        json!({"usd_cny": 7.5, "rate_date": "2026-10-30", "fetched_at": "2026-11-01T00:00:00Z"}),
    );
    let config = UsageConfig {
        workspaces: vec![ref_a, ref_b],
        shared_agent_data_dir: Some(agent),
        chat_root: Some(chat),
        exchange_rate_dir: Some(fixture.0.clone()),
    };
    let now = "2026-11-02T12:00:00Z".parse().unwrap();
    let mut query = ReportQuery {
        tz: Some("America/New_York".into()),
        tz_offset: Some("0".into()),
        ..ReportQuery::default()
    };
    let report = config.report(&query, now).unwrap();
    assert_eq!(report["today"], "2026-11-02");
    assert_eq!(report["window_start"], "2025-10-28");
    assert_eq!(report["first_date"], "2024-11-01");
    assert_eq!(report["timezone"], "America/New_York");
    assert_eq!(report["overview"]["requests"], 7);
    assert_eq!(report["overview"]["cost_usd"], "0.125");
    assert_eq!(report["overview"]["unpriced_requests"], 2);
    assert_eq!(report["overview"]["unpriced_tokens"], 220);
    assert_eq!(report["overview"]["current_streak"], 2);
    assert_eq!(report["overview"]["longest_streak"], 2);
    assert_eq!(
        report["overview"]["best_day"],
        json!({"date": "2026-11-01", "tokens": 330})
    );
    assert_eq!(report["days"][0]["date"], "2026-11-01");
    assert_eq!(report["days"][1]["date"], "2026-11-02");
    assert_eq!(
        report["prior"],
        json!({"tokens":110,"work_tokens":70,"cost_usd":"0.1","requests":1})
    );
    assert_eq!(report["ranges"]["7"]["punch"][6][0], 110);
    assert_eq!(report["ranges"]["7"]["punch"][6][23], 110);
    assert_eq!(report["ranges"]["7"]["models"][0]["speed_tokens"], 35);
    assert_eq!(report["ranges"]["7"]["models"][0]["speed_ms"], 610);
    assert_eq!(report["ranges"]["7"]["models"][0]["ttft_n"], 5);
    assert_eq!(report["ranges"]["7"]["kinds"]["review_agent"], 110);
    assert_eq!(
        report["ranges"]["7"]["modes"],
        json!({"agent":550,"chat":110})
    );
    assert_eq!(report["sources"].as_array().unwrap().len(), 4);
    assert_eq!(report["sources"][1]["has_data"], false);
    assert_eq!(report["sources"][2]["id"], "ws:__unassigned__");
    assert_eq!(report["exchange_rate"]["stale"], true);
    assert_eq!(report["exchange_rate"]["usd_cny"], json!(7.5));
    query.model_group = Some("provider".into());
    let report = config.report(&query, now).unwrap();
    assert_eq!(
        report["ranges"]["all"]["models"].as_array().unwrap().len(),
        2
    );
    assert_eq!(report["ranges"]["all"]["models"][0]["model_key"], "a/model");
    query.source = Some("chat".into());
    query.tz = Some("../invalid".into());
    query.tz_offset = Some("-330".into());
    let report = config.report(&query, now).unwrap();
    assert_eq!(report["timezone"], "UTC-05:30");
    assert_eq!(report["overview"]["requests"], 1);
    assert_eq!(report["overview"]["priced_requests"], 1);
}

#[test]
fn compatible_cache_reuses_signatures_and_retries_unreadable_ledgers() {
    let fixture = Fixture::new();
    let dir = fixture.path("api_costs");
    let ledger = dir.join("conv_cached.json");
    let payload =
        legacy(json!({"first": request("2026-11-01T10:00:00Z", "main", "a/model", "0.4")}));
    put(&ledger, payload.clone());
    fs::write(dir.join("conv_unreadable.json"), b"partial JSON").unwrap();
    let config = UsageConfig {
        shared_agent_data_dir: Some(fixture.0.clone()),
        ..UsageConfig::default()
    };
    let now = "2026-11-02T12:00:00Z".parse().unwrap();
    let report = || config.report(&ReportQuery::default(), now).unwrap();
    assert_eq!(report()["overview"]["requests"], 1);
    let path = fixture.path("usage_stats_rollup.json");
    let mut cache = read(&path);
    assert_eq!(cache["version"], 1);
    assert_eq!(cache["files"].as_object().unwrap().len(), 1);
    let buckets = cache["files"]["conv_cached"]["buckets"]
        .as_object_mut()
        .unwrap();
    let bucket = buckets.values_mut().next().unwrap();
    assert_eq!(
        *bucket,
        json!([100, 40, 10, "0.4", 1, 1, 0, 0, 10, 200, 50, 1])
    );
    bucket[3] = json!("4e-1"); // Python Decimal can use exponent notation.
    bucket[9] = json!(33);
    put(&path, cache);
    assert_eq!(report()["ranges"]["7"]["models"][0]["speed_ms"], 33);
    put(&dir.join("conv_unreadable.json"), payload.clone());
    let mut changed = payload;
    changed["requests"]["second"] = request("2026-11-01T11:00:00Z", "main", "a/model", "0.6");
    put(&ledger, changed);
    assert_eq!(report()["overview"]["requests"], 3);
    fs::remove_file(ledger).unwrap();
    assert_eq!(report()["overview"]["requests"], 1);
    assert_eq!(read(&path)["files"].as_object().unwrap().len(), 1);
}

#[tokio::test]
async fn router_provides_usage_endpoints_and_unknown_source_error() {
    let app = router(UsageConfig::default());
    for (path, status, expected) in [
        (
            "/api/usage",
            StatusCode::OK,
            json!({"success":true,"applicable":false,"data":null}),
        ),
        (
            "/api/usage-stats?source=unknown",
            StatusCode::BAD_REQUEST,
            json!({"success":false,"error":"unknown source"}),
        ),
    ] {
        let response = app
            .clone()
            .oneshot(Request::builder().uri(path).body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(response.status(), status);
        let body: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
                .unwrap();
        assert_eq!(body, expected);
    }
    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/usage-stats?source=chat&tz=UTC")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap()).unwrap();
    assert_eq!(body["data"]["selected"], "chat");
    assert_eq!(body["data"]["exchange_rate"], json!({"stale":true}));
}

#[tokio::test]
async fn host_router_reloads_trusted_workspace_ownership_after_catalog_changes() {
    let fixture = Fixture::new();
    let data = fixture.path("host/data");
    put(
        &data.join("exchange_rates.json"),
        json!({"usd_cny":7.5,"fetched_at":Timestamp::now().to_string()}),
    );
    let catalog = fixture.path("config/host_workspaces.json");
    let project = fixture.path("missing-project");
    let alpha = json!({"workspace_id":"alpha", "path":project, "label":"Alpha"});
    let beta = json!({"workspace_id":"beta", "path":project, "label":"Beta"});
    put(
        &catalog,
        json!({"workspaces":[alpha, beta, {"workspace_id":"../unlisted", "path":project}]}),
    );
    let recorded_at = Timestamp::now()
        .checked_sub(jiff::Span::new().hours(24))
        .unwrap()
        .to_string();
    for id in ["alpha", "beta", "unlisted"] {
        let conversation = format!("conv_{id}.json");
        put(
            &data.join("conversations").join(id).join(&conversation),
            json!({}),
        );
        put(
            &data.join("api_costs").join(conversation),
            legacy(json!({
                "req": request(&recorded_at, "main", "a/model", "0.401"),
            })),
        );
    }
    let app = host_router(&data, &catalog);
    let get = |uri: &'static str| {
        let app = app.clone();
        async move {
            let response = app
                .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
                .await
                .unwrap();
            let code = response.status();
            let body: Value =
                serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
                    .unwrap();
            (code, body)
        }
    };
    let (status, body) = get("/api/usage-stats?source=all").await;
    assert_eq!(status, StatusCode::OK);
    let sources = body["data"]["sources"].as_array().unwrap();
    assert_eq!(
        sources
            .iter()
            .filter_map(|source| source["id"].as_str())
            .collect::<Vec<_>>(),
        ["ws:alpha", "ws:beta", "ws:__unassigned__", "chat"]
    );
    assert_eq!(body["data"]["overview"]["requests"], 3);
    assert_eq!(body["data"]["overview"]["cost_usd"], "1.203");
    assert!(!project.exists());
    put(&catalog, json!({"workspaces":[beta]}));
    let (status, body) = get("/api/usage-stats?source=ws%3Aalpha").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "unknown source");
    let (status, body) = get("/api/usage-stats?source=ws%3A__unassigned__").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["overview"]["requests"], 2);
    fs::write(&catalog, "bad JSON").unwrap();
    let (status, body) = get("/api/usage-stats").await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(
        body,
        json!({"success":false,"error":"usage stats unavailable"})
    );
}
