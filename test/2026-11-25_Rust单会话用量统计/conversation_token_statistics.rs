use std::{fs, path::PathBuf};

use axum::{
    body::{Body, to_bytes},
    http::Request,
};
use jiff::Timestamp;
use serde_json::json;
use tower::ServiceExt;
use uuid::Uuid;

use super::*;
use crate::{conversation_db::Database, sub_agents::migrate_legacy};

const CONVERSATION: &str = "conv_20261125_143300_000";
const UNKNOWN: &str = "conv_20261125_143300_001";

#[test]
fn python_counter_defaults_conversion_and_reset() {
    let legacy = json!({
        "total_input_tokens": " +001_024 ", "total_output_tokens": -3.99,
        "total_cached_input_tokens": true, "cache_exempt_input_tokens": "-2",
        "current_context_tokens": 10.99, "updated_at": "legacy timestamp",
    });
    assert_eq!(
        validated_token_statistics(Some(&legacy)),
        Some(json!({
            "total_input_tokens": 1024, "total_output_tokens": -3,
            "total_tokens": 0, "total_cached_input_tokens": 1,
            "cache_exempt_input_tokens": -2, "current_context_tokens": 10,
            "updated_at": "legacy timestamp",
        }))
    );
    let damaged =
        json!({"total_input_tokens": 99, "total_cached_input_tokens": "1.5", "updated_at": "old"});
    let reset = validated_token_statistics(Some(&damaged)).unwrap();
    for counter in TOKEN_COUNTERS {
        assert_eq!(reset[counter], 0);
    }
    assert!(
        reset["updated_at"]
            .as_str()
            .is_some_and(|value| value.contains('T'))
    );
    assert_eq!(
        validated_token_statistics(Some(&json!({"updated_at": null}))).unwrap()["updated_at"],
        Value::Null
    );
    assert_eq!(
        validated_token_statistics(None).unwrap()["total_input_tokens"],
        0
    );
    assert_eq!(validated_token_statistics(Some(&Value::Null)), None);
    assert_eq!(validated_token_statistics(Some(&json!([]))), None);
}

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-25_Rust单会话用量统计")
            .join(format!("fixture-{}", Uuid::new_v4().simple()));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn write(&self, path: &str, value: &Value) -> PathBuf {
        let file = self.0.join(path);
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(&file, serde_json::to_vec(value).unwrap()).unwrap();
        file
    }

    fn workspace(&self, runtime: &Runtime, id: &str) -> RegisteredWorkspace {
        let project = self.0.join("projects").join(id);
        fs::create_dir_all(&project).unwrap();
        let path = project.canonicalize().unwrap();
        runtime.bind_workspace(id, &path).unwrap();
        RegisteredWorkspace {
            id: id.into(),
            label: id.into(),
            path,
        }
    }

    fn save_conversation(&self, workspace: &str, document: &Value) {
        Database::open(&self.0.join("data/conversations").join(workspace)).unwrap()
            .save(document, &json!({"id":document["id"]})).unwrap();
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

async fn get(app: &Router, workspace: &RegisteredWorkspace, id: &str) -> (StatusCode, Value) {
    let response = app
        .clone()
        .layer(Extension(workspace.clone()))
        .oneshot(
            Request::builder()
                .uri(format!("/api/conversations/{id}/token-statistics"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn endpoint_reads_legacy_counters_and_all_ledger_actors_without_writes() {
    let fixture = Fixture::new();
    let data = fixture.0.join("data");
    let runtime = Runtime::open(&data).unwrap();
    let owner = fixture.workspace(&runtime, "ws-a");
    let other = fixture.workspace(&runtime, "ws-b");
    let original = json!({
        "id":CONVERSATION, "metadata":{"project_path":owner.path}, "messages":[],
        "token_statistics":{"total_input_tokens":"128", "total_output_tokens":16.8, "updated_at":"stored"},
    });
    let file = fixture.write(
        &format!("data/conversations/ws-a/{CONVERSATION}.json"),
        &original,
    );
    fixture.save_conversation("ws-a", &original);
    let index = fixture.write(
        "data/conversations/ws-a/index.json",
        &json!({"future-index-key":{"keep":true}}),
    );
    let ledgers = json!({"version":1,"requests":{},"actors":{
        "sub_tracked":{"id":"sub_tracked","name":"Tracked actor","kind":"sub_agent","cost_usd":"0.01","priced_requests":1},
        "review_agent:goal_review":{"id":"review_agent:goal_review","name":"goal_review","kind":"review_agent","cost_usd":"0.02","priced_requests":1},
        "thinking_summary":{"id":"thinking_summary","name":"Summary","kind":"thinking_summary","cost_usd":"0.03","priced_requests":1},
    }});
    let ledger = fixture.write(&format!("data/api_costs/{CONVERSATION}.json"), &ledgers);
    fixture.write(&format!("data/api_costs/{UNKNOWN}.json"), &ledgers);
    let registry = fixture.write("data/sub_agent_runtime.json", &json!({"version":1,"tasks":{
        "sub_legacy":{"conversation_id":CONVERSATION,"task_id":"sub_legacy","display_name":"","summary":"Historical"},
        "sub_marked":{"conversation_id":CONVERSATION,"task_id":"sub_marked","display_name":"Marked","cost_tracking_version":1},
        "sub_tracked":{"conversation_id":CONVERSATION,"task_id":"sub_tracked","display_name":"Older label"},
        "sub_wrong_workspace":{"conversation_id":CONVERSATION,"task_id":"sub_wrong_workspace","workspace_root":other.path},
        "sub_wrong_parent":{"conversation_id":UNKNOWN,"task_id":"sub_wrong_parent"},
    }}));
    assert_eq!(migrate_legacy(&data).unwrap(), 5);
    let cache = fixture.write("data/exchange_rates.json", &json!({
        "usd_cny":7.1,"rate_date":"2026-11-24","fetched_at":Timestamp::now().to_string(),"source":"fixture",
    }));
    let before = [&file, &index, &ledger, &registry, &cache].map(|path| fs::read(path).unwrap());
    let app = router(runtime, &data);
    let (status, response) = get(&app, &owner, CONVERSATION.trim_start_matches("conv_")).await;
    assert_eq!(status, StatusCode::OK);
    let stats = &response["data"];
    assert_eq!(stats["conversation_id"], CONVERSATION);
    assert_eq!(stats["total_input_tokens"], 128);
    assert_eq!(stats["total_output_tokens"], 16);
    assert_eq!(stats["total_tokens"], 0);
    assert_eq!(stats["updated_at"], "stored");
    let costs = &stats["costs"];
    assert_eq!(costs["currency"], "USD");
    assert_eq!(costs["total_usd"], "0.06");
    assert_eq!(costs["partial"], true);
    assert_eq!(costs["has_priced"], true);
    assert_eq!(costs["exchange_rate"]["usd_cny"], 7.1);
    assert_eq!(costs["exchange_rate"]["stale"], false);
    let actors = costs["actors"].as_array().unwrap();
    assert_eq!(actors.len(), 6);
    assert_eq!(actors[0]["id"], "main");
    assert_eq!(actors[0]["historical_unpriced"], true);
    let find = |id: &str| actors.iter().find(|row| row["id"] == id).unwrap();
    assert_eq!(find("sub_legacy")["name"], "Historical");
    assert_eq!(find("sub_legacy")["historical_unpriced"], true);
    assert_eq!(find("sub_marked")["historical_unpriced"], false);
    assert_eq!(find("sub_tracked")["name"], "Tracked actor");
    assert_eq!(
        get(&app, &other, CONVERSATION).await.0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(get(&app, &owner, UNKNOWN).await.0, StatusCode::NOT_FOUND);
    assert_eq!(
        [&file, &index, &ledger, &registry, &cache].map(|path| fs::read(path).unwrap()),
        before
    );

    let mut duplicate = original;
    duplicate["metadata"]["project_path"] = json!(other.path);
    fixture.save_conversation("ws-b", &duplicate);
    let (status, response) = get(&app, &owner, CONVERSATION).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert!(response.get("data").is_none());
}

#[test]
fn stale_exchange_cache_and_empty_ledger_keep_python_display_values() {
    let fixture = Fixture::new();
    let data = fixture.0.join("data");
    let runtime = Runtime::open(&data).unwrap();
    let workspace = fixture.workspace(&runtime, "ws");
    fixture.save_conversation(
        "ws",
        &json!({
            "id":CONVERSATION,"metadata":{},"messages":[],
        }),
    );
    fixture.write(
        "data/exchange_rates.json",
        &json!({"usd_cny":"7.25","fetched_at":"2001-06-05T10:00:00Z"}),
    );
    let state = Statistics {
        runtime,
        exchange: ExchangeRateRefresh::new(&data),
    };
    let stats = state.read(&workspace, CONVERSATION).unwrap();
    assert_eq!(stats["total_tokens"], 0);
    assert_eq!(
        stats["costs"]["exchange_rate"],
        json!({"usd_cny":"7.25","fetched_at":"2001-06-05T10:00:00Z","stale":true})
    );
    assert_eq!(stats["costs"]["partial"], false);
    assert_eq!(stats["costs"]["total_usd"], "0");
    assert!(!data.join("api_costs").exists());
    assert!(!data.join("sub_agent_runtime.json").exists());

    fixture.save_conversation(
        "ws",
        &json!({
            "id":UNKNOWN,"metadata":{},"messages":[],"token_statistics":null,
        }),
    );
    assert_eq!(state.read(&workspace, UNKNOWN).unwrap_err().status, 404);
}
