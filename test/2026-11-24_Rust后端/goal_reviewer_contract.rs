use super::{GoalReviewer, ReviewMode, ReviewRequest, Verdict};
use crate::{catalog::CatalogSource, goals::Goals};
use axum::{
    Json, Router,
    extract::State,
    http::{HeaderMap, header},
    response::IntoResponse,
    routing::post,
};
use serde_json::{Value, json};
use std::{
    cell::RefCell,
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
};
use uuid::Uuid;

struct Temp(PathBuf);
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[tokio::test]
async fn goal_review_replays_reminder_and_settles_each_round_from_catalog_profile() {
    let temp = Temp(std::env::temp_dir().join(format!("astrion-goal-review-{}", Uuid::new_v4())));
    let config = temp.0.join("config");
    let data = temp.0.join("data");
    fs::create_dir_all(&config).unwrap();
    fs::create_dir_all(&data).unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    fs::write(
        config.join("custom_models.json"),
        json!([{
            "model_name":"goal-review-fixture","model_id":"goal-upstream","url":base,
            "apikey":"goal-test-key","context_window":1,"max_output_tokens":256,
            "extra_parameter":{"temperature":0.25},
        }])
        .to_string(),
    )
    .unwrap();
    fs::write(
        data.join("personalization.json"),
        json!({
            "review_agents":{"goal_review":{"model":"goal-review-fixture","max_rounds":2}}
        })
        .to_string(),
    )
    .unwrap();
    let calls = Arc::new(Mutex::new(Vec::new()));
    let upstream = Router::new()
        .route("/chat/completions", post(review_response))
        .with_state(calls.clone());
    let server = tokio::spawn(async move { axum::serve(listener, upstream).await.unwrap() });
    let reviewer = GoalReviewer::new(
        Goals::new(&data),
        CatalogSource::with_paths(&temp.0, &config, &data),
        &config,
    );
    let conversation_id = "conv_goal_review";
    let progress = RefCell::new(Vec::new());
    let result = reviewer
        .evaluate(
            ReviewRequest {
                conversation_id,
                workspace: &temp.0,
                mode: ReviewMode::Readonly,
                payload: "Fixture goal to inspect",
            },
            |kind, data| {
                assert_eq!(kind, "goal_review_progress");
                assert_eq!(data["conversation_id"], conversation_id);
                progress
                    .borrow_mut()
                    .push(data["progress"]["round"].clone());
                Ok(())
            },
        )
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(result, Verdict::Done(reason) if reason == "Fixture passed"));
    assert_eq!(progress.into_inner(), [json!(1), json!(2)]);
    let calls = calls.lock().unwrap();
    assert_eq!(calls.len(), 2);
    for call in calls.iter() {
        assert_eq!(call["model"], "goal-upstream");
        assert_eq!(call["temperature"], 0.25);
        assert_eq!(call["max_tokens"], 1);
        assert_eq!(call["messages"][1]["content"], "Fixture goal to inspect");
        let tools = call["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["function"]["name"], "report_goal_status");
    }
    assert_eq!(
        calls[1]["messages"][2]["content"],
        "A verdict without a tool call"
    );
    assert!(
        calls[1]["messages"][3]["content"]
            .as_str()
            .unwrap()
            .contains("report_goal_status")
    );
    let ledger: Value = serde_json::from_slice(
        &fs::read(
            data.join("api_costs")
                .join(format!("{conversation_id}.json")),
        )
        .unwrap(),
    )
    .unwrap();
    let actor = &ledger["actors"]["review_agent:goal_review"];
    assert_eq!(actor["requests"], 2);
    assert_eq!(actor["input_tokens"], 10);
    assert_eq!(actor["output_tokens"], 4);
    server.abort();
}

async fn review_response(
    State(calls): State<Arc<Mutex<Vec<Value>>>>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    assert_eq!(headers[header::AUTHORIZATION], "Bearer goal-test-key");
    let mut calls = calls.lock().unwrap();
    calls.push(body);
    let (delta, finish) = match calls.len() {
        1 => (
            json!({"role":"assistant","content":"A verdict without a tool call"}),
            "stop",
        ),
        2 => (
            json!({"role":"assistant","tool_calls":[{
                "index":0,"id":"fixture-verdict","type":"function",
                "function":{"name":"report_goal_status","arguments":"{\"status\":\"done\",\"message\":\"Fixture passed\"}"},
            }]}),
            "tool_calls",
        ),
        round => panic!("Unexpected review round {round}"),
    };
    let first = json!({"choices":[{"index":0,"delta":delta,"finish_reason":null}]});
    let last = json!({"choices":[{"index":0,"delta":{},"finish_reason":finish}]});
    let usage =
        json!({"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}});
    (
        [(header::CONTENT_TYPE, "text/event-stream")],
        format!("data: {first}\n\ndata: {last}\n\ndata: {usage}\n\ndata: [DONE]\n\n"),
    )
}
