use super::*;
use axum::{
    Json, Router,
    body::Body,
    extract::State,
    http::{Request, StatusCode},
    response::IntoResponse,
    routing::post,
};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use tower::ServiceExt;

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let root =
            std::env::temp_dir().join(format!("astrion-workflow-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        Self(root)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn builtin() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../workflows")
}
fn registry(root: &Path) -> WorkflowRegistry {
    WorkflowRegistry::new(builtin(), root.join("workflows")).unwrap()
}
fn fixture() -> Value {
    serde_json::from_str(include_str!("workflow_fixture.json")).unwrap()
}
fn state(root: &Path, name: &str) -> Value {
    serde_json::from_slice(
        &fs::read(root.join("workflow_states").join(name).join("state.json")).unwrap(),
    )
    .unwrap()
}
fn test_definition(mode: &str) -> Value {
    json!({"name":"payload-name-is-ignored", "description":"on", "reviewMode":mode, "endConditions":"yes", "maxStageRounds":2, "body":"  # 流程约定  ", "source":"builtin",
    "nodes":[
        {"id":"start", "kind":"start", "next":"draft", "position":{"x":12.5,"y":3.5}},
        {"id":"draft", "kind":"stage", "name":"撰写", "goal":"整理", "instructions":"保留证据", "next":"check"},
        {"id":"check", "kind":"review", "name":"审核材料", "prompt":"验证数据", "next":"merge", "rejectTo":"draft", "maxRejects":2},
        {"id":"merge", "kind":"branch", "next":[{"target":"choice"}]},
        {"id":"choice", "kind":"branch", "name":"决策", "next":[{"target":"finish","condition":"归档"},{"target":"draft","condition":"返工"}]},
        {"id":"finish", "kind":"end", "name":"结束"}
    ]})
}

#[test]
fn real_python_definitions_and_snapshot_match() {
    let temp = Temp::new();
    let registry = registry(&temp.0);
    let expected = fixture();
    assert_eq!(registry.list_response().unwrap(), expected["list"]);
    for (name, definition) in expected["definitions"].as_object().unwrap() {
        assert_eq!(
            registry.get_response(name).unwrap(),
            json!({"workflow":definition}),
            "{name}"
        );
        registry.load(name).unwrap().validate().unwrap();
    }
    let dir = temp.0.join("workflow_states/conv_fixture");
    fs::create_dir_all(&dir).unwrap();
    fs::write(
        dir.join("WORKFLOW.md"),
        expected["state_markdown"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(dir.join("state.json"), expected["state"].to_string()).unwrap();
    let states = WorkflowStates::new(&temp.0);
    assert_eq!(
        states.snapshot("conv_fixture").unwrap(),
        expected["snapshot"]
    );
    assert_eq!(
        states.poll_notices("conv_fixture").unwrap(),
        expected["state"]["pending_notices"]
            .as_array()
            .unwrap()
            .clone()
    );
    assert_eq!(
        state(&temp.0, "conv_fixture")["history"],
        expected["state"]["history"]
    );
}

#[test]
fn put_is_atomic_and_user_copy_shadows_builtin() {
    let temp = Temp::new();
    let registry = registry(&temp.0);
    let name = "research-report";
    let original = registry.read_markdown(name).unwrap();
    assert_eq!(registry.delete_response(name).unwrap_err().status, 400);
    assert_eq!(registry.get_response("absent").unwrap_err().status, 404);
    let definition = test_definition("readonly");
    assert_eq!(
        registry
            .put_response(name, json!({"workflow":definition}))
            .unwrap(),
        json!({"ok":true})
    );
    assert!(
        registry
            .read_markdown(name)
            .unwrap()
            .contains("\"max_stage_rounds\": 2")
    );
    let saved = registry.get_response(name).unwrap();
    assert_eq!(saved["workflow"]["name"], name);
    assert_eq!(saved["workflow"]["source"], "user");
    assert_eq!(saved["workflow"]["description"], "on");
    assert_eq!(saved["workflow"]["endConditions"], "yes");
    assert_eq!(
        saved["workflow"]["nodes"][0]["position"]["x"].as_f64(),
        Some(12.0)
    );
    assert_eq!(
        saved["workflow"]["nodes"][0]["position"]["y"].as_f64(),
        Some(4.0)
    );
    assert_eq!(saved["workflow"]["updatedAt"].as_str().unwrap().len(), 16);
    let mut invalid = definition.clone();
    invalid["nodes"][2]["rejectTo"] = json!("start");
    assert_eq!(
        registry
            .put_response(name, json!({"workflow":invalid}))
            .unwrap_err()
            .status,
        400
    );
    assert_eq!(registry.get_response(name).unwrap(), saved);
    assert_eq!(
        registry
            .put_response("../outside", json!({"workflow":definition}))
            .unwrap_err()
            .status,
        400
    );
    assert_eq!(registry.delete_response(name).unwrap(), json!({"ok":true}));
    assert_eq!(registry.read_markdown(name).unwrap(), original);
    fs::create_dir_all(temp.0.join("workflows/corrupt")).unwrap();
    fs::write(temp.0.join("workflows/corrupt/WORKFLOW.md"), "invalid file").unwrap();
    assert_eq!(registry.get_response("corrupt").unwrap_err().status, 400);
    assert_eq!(
        registry.list_response().unwrap()["workflows"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["name"] == "corrupt")
            .unwrap()["nodeCount"],
        0
    );
}

#[cfg(unix)]
#[test]
fn symlinks_and_builtin_overlap_cannot_cross_boundaries() {
    use std::os::unix::fs::symlink;
    let temp = Temp::new();
    let registry = registry(&temp.0);
    let user = temp.0.join("workflows");
    fs::create_dir(&user).unwrap();
    let outside = temp.0.join("workflows-outside");
    fs::create_dir(&outside).unwrap();
    let file = outside.join("WORKFLOW.md");
    fs::write(&file, "private").unwrap();
    symlink(&outside, user.join("escape")).unwrap();
    assert_eq!(registry.get_response("escape").unwrap_err().status, 400);
    assert_eq!(
        registry
            .put_response("escape", json!({"workflow":test_definition("readonly")}))
            .unwrap_err()
            .status,
        400
    );
    assert_eq!(registry.delete_response("escape").unwrap_err().status, 400);
    fs::create_dir(user.join("linked-file")).unwrap();
    symlink(&file, user.join("linked-file/WORKFLOW.md")).unwrap();
    assert_eq!(
        registry.get_response("linked-file").unwrap_err().status,
        400
    );
    assert_eq!(
        registry
            .put_response(
                "linked-file",
                json!({"workflow":test_definition("readonly")})
            )
            .unwrap_err()
            .status,
        400
    );
    assert_eq!(fs::read_to_string(&file).unwrap(), "private");
    assert!(WorkflowRegistry::new(&outside, outside.join("child")).is_err());
    symlink(&outside, temp.0.join("workflow_states")).unwrap();
    let s = WorkflowStates::new(&temp.0);
    assert_eq!(s.snapshot("conv_valid").unwrap_err().status, 400);
    assert_eq!(s.snapshot("../private").unwrap_err().status, 400);
}

#[test]
fn state_advances_through_gate_and_branch_using_frozen_definition() {
    let temp = Temp::new();
    let registry = registry(&temp.0);
    let cid = "conv_rounds";
    let mut definition = test_definition("readonly");
    registry
        .put_response("case", json!({"workflow":definition}))
        .unwrap();
    let states = WorkflowStates::new(&temp.0);
    let blocked = states.activate(&registry, cid, "case", 1, |_| {
        Err(WorkflowError::new(409, "busy"))
    });
    assert_eq!(blocked.unwrap_err().status, 409);
    assert_eq!(states.snapshot(cid).unwrap(), json!({"active":false}));
    let activation = states
        .activate(&registry, cid, "case", 1, |activation| {
            assert!(activation.prompt.contains("# 流程约定"));
            Ok(())
        })
        .unwrap();
    assert_eq!(activation.snapshot["current"]["name"], "撰写");
    assert_eq!(activation.snapshot["next"], "审核材料");
    assert!(
        states
            .activate(&registry, cid, "case", 99, |_| panic!(
                "duplicate admission"
            ))
            .unwrap()
            .already
    );
    assert!(states.tick_stage_round(cid).unwrap().is_none());
    assert!(
        states
            .tick_stage_round(cid)
            .unwrap()
            .unwrap()
            .contains("达到单步轮数上限")
    );
    assert!(states.tick_stage_round(cid).unwrap().is_none());
    definition["nodes"][3]["next"][0]["target"] = json!("finish");
    registry
        .put_response("case", json!({"workflow":definition}))
        .unwrap();
    let history = vec![
        json!({"role":"tool","content":"should not be audited"}),
        json!({"role":"assistant","tool_calls":[{"function":{"name":"read_file","arguments":"{\"path\":\"notes.md\"}"}}]}),
        json!({"role":"tool","content":"   measured\n result  "}),
    ];
    let Advance::Review(request) = states.report_stage(cid, "指标已核对", &history).unwrap()
    else {
        panic!("review required")
    };
    assert_eq!(request.mode, ReviewMode::Readonly);
    assert!(request.payload.contains("read_file：notes.md"));
    assert!(request.payload.contains("measured result"));
    assert!(!request.payload.contains("should not be audited"));
    assert_eq!(state(&temp.0, cid)["history"], json!([]));
    let Advance::Progress(rejected) = states
        .resolve_review(*request, ReviewDecision::Reject("缺少出处".into()))
        .unwrap()
    else {
        panic!("rejection should return to draft")
    };
    assert_eq!(rejected.event["event"], "workflow_rejected");
    assert_eq!(rejected.snapshot["history"], json!([]));
    assert_eq!(rejected.snapshot["current"]["rounds"], 0);
    let Advance::Review(request) = states.report_stage(cid, "补齐指标出处", &history).unwrap()
    else {
        panic!("review required")
    };
    assert!(request.payload.contains("第 1 次驳回：缺少出处"));
    let Advance::Progress(advanced) = states
        .resolve_review(*request, ReviewDecision::Pass("证据齐全".into()))
        .unwrap()
    else {
        panic!("must advance to branch")
    };
    assert_eq!(advanced.snapshot["history"][0]["name"], "撰写");
    assert_eq!(advanced.snapshot["current"]["name"], "决策");
    assert!(advanced.snapshot["next"].is_null());
    assert_eq!(
        states
            .choose_branch(cid, "check", &history)
            .unwrap_err()
            .status,
        400
    );
    let Advance::Progress(completed) = states.choose_branch(cid, "finish", &history).unwrap()
    else {
        panic!("must complete")
    };
    assert_eq!(completed.event["event"], "workflow_completed");
    assert_eq!(completed.event["status"], "completed");
    assert_eq!(completed.snapshot, json!({"active":false}));
    assert_eq!(state(&temp.0, cid)["exit_reason"], "completed");
}

#[test]
fn rejection_limit_and_user_exit_are_soft_and_stale_review_cannot_resume() {
    let temp = Temp::new();
    let cid = "conv_soft";
    let registry = registry(&temp.0);
    registry
        .put_response("case", json!({"workflow":test_definition("active")}))
        .unwrap();
    let states = WorkflowStates::new(&temp.0);
    states
        .activate(&registry, cid, "case", 0, |_| Ok(()))
        .unwrap();
    for exit in [false, true] {
        let Advance::Review(request) = states.report_stage(cid, "done", &[]).unwrap() else {
            panic!("review required")
        };
        let Advance::Progress(progress) = states
            .resolve_review(*request, ReviewDecision::Unavailable("上游超时".into()))
            .unwrap()
        else {
            panic!("must stop review")
        };
        assert_eq!(progress.snapshot["active"], !exit);
    }
    assert_eq!(state(&temp.0, cid)["exit_reason"], "max_rejects");
    states
        .activate(&registry, cid, "case", 0, |_| Ok(()))
        .unwrap();
    let Advance::Review(request) = states.report_stage(cid, "done", &[]).unwrap() else {
        panic!("review required")
    };
    assert_eq!(
        states.deactivate_by_user(cid).unwrap()["snapshot"],
        json!({"active":false})
    );
    assert_eq!(
        states
            .resolve_review(*request, ReviewDecision::Pass("ignored".into()))
            .unwrap_err()
            .status,
        409
    );
    let notices = states.poll_notices(cid).unwrap();
    assert_eq!(notices.len(), 1);
    assert_eq!(notices[0]["type"], "deactivated_by_user");
    assert!(states.poll_notices(cid).unwrap().is_empty());
    states.restore_notices(cid, notices.clone()).unwrap();
    assert_eq!(states.poll_notices(cid).unwrap(), notices);
    assert_eq!(state(&temp.0, cid)["exit_reason"], "user");
}

#[tokio::test]
async fn readonly_review_needs_a_real_report_tool_from_upstream() {
    let temp = Temp::new();
    let registry = registry(&temp.0);
    registry
        .put_response("case", json!({"workflow":test_definition("readonly")}))
        .unwrap();
    let states = WorkflowStates::new(&temp.0);
    let cid = "conv_review";
    states
        .activate(&registry, cid, "case", 0, |_| Ok(()))
        .unwrap();
    let Advance::Review(request) = states.report_stage(cid, "主模型汇报", &[]).unwrap() else {
        panic!("review required")
    };
    let requests = Arc::new(Mutex::new(Vec::new()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/chat/completions", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/chat/completions", post(mock_review))
        .with_state(Arc::clone(&requests));
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let selected = crate::catalog::SelectedModel {
        key: "review-key".into(),
        model_id: "review-provider-id".into(),
        codex: None,
        endpoint,
        api_key: Some("workflow-test-key".into()),
        api_protocol: "chat_completions".into(),
        headers: serde_json::Map::from_iter([("x-review-fixture".into(), json!("workflow"))]),
        extra_params: serde_json::Map::from_iter([("temperature".into(), json!(0.25))]),
        max_output_tokens: json!(256),
        context_window: Some(1),
        chat_default_max_output_tokens: 32_768,
        price_identity: Default::default(),
        prompt_description: String::new(),
        prompt_multimodal: "text",
    };
    let mut progress = Vec::new();
    let verdict = run_model_review(
        &request,
        &selected,
        2,
        &temp.0,
        None,
        |event| {
            progress.push(event);
            Ok(())
        },
        (
            || std::future::ready(()),
            |(), _, _| std::future::ready(Ok(())),
        ),
    )
    .await
    .unwrap();
    assert!(matches!(verdict, ReviewDecision::Pass(ref message) if message == "已验证"));
    assert_eq!(progress[0]["progress"]["stage"], "start");
    assert_eq!(progress.len(), 3);
    let calls = requests.lock().unwrap();
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0]["model"], "review-provider-id");
    assert!(
        calls
            .iter()
            .all(|call| call["max_tokens"] == 1 && call["temperature"] == 0.25)
    );
    assert_eq!(calls[0]["tools"].as_array().unwrap().len(), 1);
    assert_eq!(
        calls[0]["tools"][0]["function"]["name"],
        "report_workflow_review"
    );
    assert!(
        calls[0]["messages"][1]["content"]
            .as_str()
            .unwrap()
            .contains("主模型汇报")
    );
    assert!(
        calls[1]["messages"][3]["content"]
            .as_str()
            .unwrap()
            .contains("report_workflow_review")
    );
    drop(calls);
    assert_eq!(state(&temp.0, cid)["history"], json!([]));
    let Advance::Progress(out) = states.resolve_review(*request, verdict).unwrap() else {
        panic!("branch should be next")
    };
    assert_eq!(out.snapshot["current"]["name"], "决策");
    server.abort();
}

async fn mock_review(
    State(requests): State<Arc<Mutex<Vec<Value>>>>,
    headers: axum::http::HeaderMap,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    assert_eq!(headers["authorization"], "Bearer workflow-test-key");
    assert_eq!(headers["x-review-fixture"], "workflow");
    let mut requests = requests.lock().unwrap();
    requests.push(body);
    let (delta, finish) = if requests.len() == 1 {
        (json!({"role":"assistant", "content":"pass"}), "stop")
    } else {
        (
            json!({"role":"assistant", "tool_calls":[{"index":0, "id":"review-call", "type":"function", "function":{
                "name":"report_workflow_review", "arguments":"{\"decision\":\"pass\",\"message\":\"已验证\"}"
            }}]}),
            "tool_calls",
        )
    };
    let first = json!({"choices":[{"index":0, "delta":delta, "finish_reason":null}]});
    let last = json!({"choices":[{"index":0, "delta":{}, "finish_reason":finish}]});
    (
        [("content-type", "text/event-stream")],
        format!("data: {first}\n\ndata: {last}\n\ndata: [DONE]\n\n"),
    )
}

#[tokio::test]
async fn http_requires_a_browser_and_rejects_cross_workspace_status() {
    let temp = Temp::new();
    let runtime = crate::runtime::Runtime::open(&temp.0).unwrap();
    let registry = registry(&temp.0);
    let router = browser_router(registry, runtime, "trusted".into());
    let denied = router
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/workflows/research-report")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(denied.status(), StatusCode::UNAUTHORIZED);
    let wrong_workspace = router
        .oneshot(
            Request::builder()
                .uri("/api/workflow/status?workspace_id=other&conversation_id=conv_owned")
                .extension(crate::web_auth::BrowserSession::synthetic())
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(wrong_workspace.status(), StatusCode::NOT_FOUND);
}

#[derive(Default)]
struct Evidence(Mutex<Vec<(PathBuf, String)>>);
impl ReviewEvidence for Evidence {
    fn run<'a>(
        &'a self,
        workspace: &'a Path,
        command: &'a str,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Value> + Send + 'a>> {
        self.0
            .lock()
            .unwrap()
            .push((workspace.into(), command.into()));
        Box::pin(std::future::ready(
            json!({"success":true,"stdout":"evidence.txt has 3 verified rows"}),
        ))
    }
}

#[tokio::test]
async fn active_review_waits_for_a_trusted_read_only_executor() {
    let temp = Temp::new();
    let registry = registry(&temp.0);
    registry
        .put_response("case", json!({"workflow":test_definition("active")}))
        .unwrap();
    let cid = "conv_active_review";
    let states = WorkflowStates::new(&temp.0);
    states
        .activate(&registry, cid, "case", 0, |_| Ok(()))
        .unwrap();
    let Advance::Review(request) = states.report_stage(cid, "verified", &[]).unwrap() else {
        panic!("expected review")
    };
    let received = Arc::new(Mutex::new(Vec::new()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/chat/completions", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/chat/completions", post(mock_evidence_review))
        .with_state(Arc::clone(&received));
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let profile = crate::catalog::SelectedModel {
        key: "audit".into(),
        model_id: "audit".into(),
        codex: None,
        endpoint,
        api_key: Some("workflow-test-key".into()),
        api_protocol: "chat_completions".into(),
        headers: Default::default(),
        extra_params: Default::default(),
        max_output_tokens: Value::Null,
        context_window: None,
        chat_default_max_output_tokens: 32_768,
        price_identity: Default::default(),
        prompt_description: String::new(),
        prompt_multimodal: "text",
    };
    let result = run_model_review(
        &request,
        &profile,
        2,
        &temp.0,
        None,
        |_| Ok(()),
        (
            || std::future::ready(()),
            |(), _, _| std::future::ready(Ok(())),
        ),
    )
    .await;
    assert_eq!(result.unwrap_err().status, 503);
    assert!(received.lock().unwrap().is_empty());
    assert_eq!(state(&temp.0, cid)["history"], json!([]));
    let evidence = Evidence::default();
    let verdict = run_model_review(
        &request,
        &profile,
        2,
        &temp.0,
        Some(&evidence),
        |_| Ok(()),
        (
            || std::future::ready(()),
            |(), _, _| std::future::ready(Ok(())),
        ),
    )
    .await
    .unwrap();
    assert!(matches!(verdict, ReviewDecision::Pass(_)));
    assert_eq!(
        evidence.0.lock().unwrap().as_slice(),
        &[(temp.0.clone(), "cat evidence.txt".to_owned())]
    );
    let seen = received.lock().unwrap();
    assert_eq!(seen.len(), 2);
    assert_eq!(seen[0]["tools"][0]["function"]["name"], "run_command");
    assert_eq!(seen[1]["messages"][3]["tool_call_id"], "evidence");
    assert!(
        seen[1]["messages"][3]["content"]
            .as_str()
            .unwrap()
            .contains("3 verified rows")
    );
    server.abort();
}

async fn mock_evidence_review(
    State(requests): State<Arc<Mutex<Vec<Value>>>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    let mut requests = requests.lock().unwrap();
    requests.push(body);
    if requests.len() == 1 {
        tool_stream(
            "evidence",
            "run_command",
            json!({"command":"cat evidence.txt"}),
        )
    } else {
        tool_stream(
            "decision",
            "report_workflow_review",
            json!({"decision":"pass","message":"核对了证据"}),
        )
    }
}

fn tool_stream(
    id: &str,
    name: &str,
    arguments: Value,
) -> ([(&'static str, &'static str); 1], String) {
    stream(
        json!({"role":"assistant", "tool_calls":[{"index":0, "id":id, "type":"function", "function":{"name":name,"arguments":arguments.to_string()}}]}),
        "tool_calls",
    )
}

fn stream(delta: Value, finish: &str) -> ([(&'static str, &'static str); 1], String) {
    let first = json!({"choices":[{"index":0,"delta":delta,"finish_reason":null}]});
    let usage = json!({"choices":[], "usage":{"prompt_tokens":20,"completion_tokens":10,"total_tokens":30}});
    let last = json!({"choices":[{"index":0,"delta":{},"finish_reason":finish}]});
    (
        [("content-type", "text/event-stream")],
        format!("data: {first}\n\ndata: {last}\n\ndata: {usage}\n\ndata: [DONE]\n\n"),
    )
}

#[derive(Default)]
struct FlowMock {
    requests: Mutex<Vec<Value>>,
    first_main: tokio::sync::Notify,
}

async fn mock_full_workflow(
    State(mock): State<Arc<FlowMock>>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    let model = body["model"].as_str().unwrap_or_default().to_owned();
    let count = {
        let mut requests = mock.requests.lock().unwrap();
        requests.push(body);
        requests
            .iter()
            .filter(|request| request["model"] == model)
            .count()
    };
    match (model.as_str(), count) {
        ("audit-upstream", _) => tool_stream(
            "audit-done",
            "report_workflow_review",
            json!({"decision":"pass","message":"完成核查"}),
        ),
        ("main-upstream", 1) => {
            mock.first_main.notified().await;
            tool_stream(
                "stage",
                "report_workflow_stage",
                json!({"summary":"主模型阶段证据"}),
            )
        }
        ("main-upstream", 2) => tool_stream(
            "branch",
            "choose_workflow_branch",
            json!({"target_node_id":"finish"}),
        ),
        _ => stream(json!({"role":"assistant","content":"工作流完成。"}), "stop"),
    }
}

fn browser_request(
    method: &str,
    path: &str,
    workspace: Option<&str>,
    body: Value,
) -> Request<Body> {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json")
        .extension(crate::web_auth::BrowserSession::synthetic());
    if let Some(workspace) = workspace {
        request =
            request.extension(crate::approval::ApprovalPrincipal::new("host", workspace).unwrap());
    }
    request.body(Body::from(body.to_string())).unwrap()
}

async fn response_data(response: axum::response::Response) -> (StatusCode, Value) {
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), 256_000)
        .await
        .unwrap();
    let data = serde_json::from_slice(&bytes)
        .unwrap_or_else(|_| panic!("expected JSON, got {}", String::from_utf8_lossy(&bytes)));
    (status, data)
}

#[tokio::test]
async fn scoped_http_activation_runs_main_review_and_branch_and_records_the_actor() {
    use crate::{
        catalog::CatalogSource,
        runtime::{ModelConfig, Runtime, WorkflowRuntime},
    };
    let temp = Temp::new();
    let registry = registry(&temp.0);
    let data = temp.0.join("data");
    let config = temp.0.join("config");
    let main = temp.0.join("main");
    let other = temp.0.join("other");
    for path in [&data, &config, &main, &other] {
        fs::create_dir(path).unwrap();
    }
    let mock = Arc::new(FlowMock::default());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    fs::write(config.join("custom_models.json"), json!([{"model_name":"workflow-review", "model_id":"audit-upstream", "url":base, "apikey":"isolated-fixture-key"}]).to_string()).unwrap();
    fs::write(
        data.join("personalization.json"),
        json!({"review_agents":{"workflow_review":{"model":"workflow-review", "max_rounds":2}}})
            .to_string(),
    )
    .unwrap();
    let upstream = Router::new()
        .route("/chat/completions", post(mock_full_workflow))
        .with_state(mock.clone());
    let server = tokio::spawn(async move { axum::serve(listener, upstream).await.unwrap() });
    let models = CatalogSource::with_paths(&temp.0, &config, &data);
    let fixed = ModelConfig::fixed(
        format!("{base}/chat/completions"),
        None,
        "main-upstream".into(),
    );
    let runtime = Runtime::open(&data)
        .unwrap()
        .with_workflow(WorkflowRuntime::new(
            registry.clone(),
            &data,
            models,
            Some(fixed.clone()),
        ));
    runtime.bind_workspace("main", &main).unwrap();
    runtime.bind_workspace("other", &other).unwrap();
    let router = browser_scoped_router(registry, runtime.clone());
    let put = browser_request(
        "PUT",
        "/api/workflows/case",
        None,
        json!({"workflow":test_definition("readonly")}),
    );
    assert_eq!(
        router.clone().oneshot(put).await.unwrap().status(),
        StatusCode::OK
    );
    let activate = browser_request(
        "POST",
        "/api/workflow/activate",
        Some("main"),
        json!({"name":"case"}),
    );
    let (code, activation) = response_data(router.clone().oneshot(activate).await.unwrap()).await;
    assert_eq!(code, StatusCode::OK, "{activation}");
    let cid = activation["conversation_id"].as_str().unwrap();
    let task_id = activation["task_id"].as_str().unwrap();
    let status_url = format!("/api/workflow/status?conversation_id={cid}");
    let (code, active) = response_data(
        router
            .clone()
            .oneshot(browser_request(
                "GET",
                &status_url,
                Some("main"),
                Value::Null,
            ))
            .await
            .unwrap(),
    )
    .await;
    assert_eq!(code, StatusCode::OK, "{active}");
    assert_eq!(active["snapshot"]["active"], true);
    let wrong_scope = browser_request("GET", &status_url, Some("other"), Value::Null);
    assert_eq!(
        router.clone().oneshot(wrong_scope).await.unwrap().status(),
        StatusCode::NOT_FOUND
    );
    let busy = browser_request(
        "POST",
        "/api/workflow/activate",
        Some("main"),
        json!({"name":"case","conversation_id":cid}),
    );
    assert_eq!(
        router.clone().oneshot(busy).await.unwrap().status(),
        StatusCode::CONFLICT
    );
    mock.first_main.notify_one();
    let task = tokio::time::timeout(std::time::Duration::from_secs(5), async {
        loop {
            let snapshot = runtime.poll_task("main", task_id, 0).unwrap();
            if snapshot["data"]["status"] != "running" {
                break snapshot;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(task["data"]["status"], "succeeded", "{task}");
    assert_eq!(task["data"]["message_source"], "workflow");
    let events = task["data"]["events"].as_array().unwrap();
    assert!(
        events
            .iter()
            .any(|event| event["type"] == "workflow_review_progress")
    );
    assert!(
        events
            .iter()
            .any(|event| event["data"]["event"] == "workflow_completed")
    );
    assert!(
        events
            .iter()
            .any(|event| event["type"] == "user_message"
                && event["data"]["workflow_activate"] == true)
    );
    let history = runtime.session_history("main", cid).unwrap();
    assert_eq!(
        history["conversation"]["messages"][0]["metadata"]["message_source"],
        "workflow"
    );
    assert_eq!(state(&data, cid)["exit_reason"], "completed");
    let ledger: Value = serde_json::from_slice(
        &fs::read(data.join("api_costs").join(format!("{cid}.json"))).unwrap(),
    )
    .unwrap();
    assert_eq!(
        ledger["actors"]["review_agent:workflow_review"]["requests"],
        1
    );
    assert_eq!(
        ledger["actors"]["review_agent:workflow_review"]["input_tokens"],
        20
    );
    assert_eq!(ledger["actors"]["main"]["requests"], 3);
    let received = mock.requests.lock().unwrap();
    let review = received
        .iter()
        .find(|request| request["model"] == "audit-upstream")
        .unwrap();
    assert!(
        review["messages"][1]["content"]
            .as_str()
            .unwrap()
            .contains("主模型阶段证据")
    );
    assert_eq!(review["tools"].as_array().unwrap().len(), 1);
    server.abort();
}

#[tokio::test]
async fn subscription_review_reuses_the_workflow_session_with_fast_disabled() {
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    use std::cell::RefCell;

    let temp = Temp::new();
    let registry = registry(&temp.0);
    registry
        .put_response("case", json!({"workflow":test_definition("active")}))
        .unwrap();
    let conversation = "conv_subscription_review";
    let states = WorkflowStates::new(&temp.0);
    states
        .activate(&registry, conversation, "case", 0, |_| Ok(()))
        .unwrap();
    let Advance::Review(request) = states
        .report_stage(conversation, "Subscription review evidence", &[])
        .unwrap()
    else {
        panic!("expected workflow review");
    };

    let subscription = "review-fixture-account";
    let claims = URL_SAFE_NO_PAD.encode(
        json!({
            "sub":"fixture-reviewer", "exp":4_102_444_800_u64,
            "https://api.openai.com/auth":{"chatgpt_account_id":subscription},
        })
        .to_string(),
    );
    let access_token = format!("header.{claims}.signature");
    fs::write(
        temp.0.join("codex_auth.json"),
        json!({"tokens":{
            "access_token":access_token,"refresh_token":"fixture-refresh","account_id":subscription,
        }})
        .to_string(),
    )
    .unwrap();
    fs::write(temp.0.join("codex_models.json"), json!({"models":[{
        "slug":"review-subscription","visibility":"list","supported_in_api":true,
        "base_instructions":"Fixture reviewer instructions", "service_tiers":[{"id":"priority"}],
        "context_window":1,
    }]}).to_string()).unwrap();

    let requests = Arc::new(Mutex::new(Vec::new()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let app = Router::new()
        .route("/responses", post(mock_subscription_review))
        .with_state(requests.clone());
    let providers = crate::providers::ProviderConfig::new(&temp.0, &temp.0, &temp.0)
        .with_client(reqwest::Client::builder().no_proxy().build().unwrap())
        .with_codex_responses_url(format!(
            "http://{}/responses",
            listener.local_addr().unwrap()
        ));
    let catalog = crate::catalog::CatalogSource::with_paths(&temp.0, &temp.0, &temp.0)
        .with_providers(providers);
    let selected = catalog
        .load()
        .select(Some("openai-codex/review-subscription"), true)
        .unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let settled = RefCell::new(Vec::new());
    let evidence = Evidence::default();
    let result = run_model_review(
        &request,
        &selected,
        2,
        &temp.0,
        Some(&evidence),
        |_| Ok(()),
        (
            || std::future::ready(()),
            |(), usage, _| {
                settled.borrow_mut().push(usage.unwrap());
                std::future::ready(Ok(()))
            },
        ),
    )
    .await
    .unwrap();
    assert!(
        matches!(&result, ReviewDecision::Pass(message) if message == "Subscription verified"),
        "{result:?}"
    );
    assert_eq!(
        evidence.0.lock().unwrap().as_slice(),
        &[(temp.0.clone(), "cat evidence.txt".to_owned())]
    );
    let settled = settled.into_inner();
    assert_eq!(settled.len(), 2);
    assert!(
        settled
            .iter()
            .all(|usage| usage["prompt_tokens"] == 5 && usage["completion_tokens"] == 2)
    );
    let unavailable = tokio::time::timeout(
        std::time::Duration::from_secs(2),
        run_model_review(
            &request,
            &selected,
            2,
            &temp.0,
            Some(&evidence),
            |_| Ok(()),
            (
                || std::future::ready(()),
                |(), _, _| -> std::future::Ready<Result<()>> {
                    panic!("quota rejection must not settle a successful request")
                },
            ),
        ),
    )
    .await
    .expect("quota exhaustion must not sleep before retrying")
    .unwrap();
    assert!(
        matches!(unavailable, ReviewDecision::Unavailable(error) if error.contains("subscription usage limit"))
    );
    let requests = requests.lock().unwrap();
    assert_eq!(requests.len(), 3);
    let session = &requests[0].0["session_id"];
    assert_eq!(&requests[1].0["session_id"], session);
    assert_ne!(&requests[2].0["session_id"], session);
    for (headers, body) in requests.iter() {
        let session = headers["session_id"].to_str().unwrap();
        uuid::Uuid::parse_str(session).unwrap();
        assert_eq!(headers["authorization"], format!("Bearer {access_token}"));
        assert_eq!(headers["chatgpt-account-id"], subscription);
        assert_eq!(body["model"], "review-subscription");
        assert_eq!(body["prompt_cache_key"], session);
        assert_eq!(body["max_output_tokens"], 1);
        assert!(body.get("service_tier").is_none());
        assert_eq!(body["tools"][0]["name"], "run_command");
        assert_eq!(body["tools"][1]["name"], "report_workflow_review");
        assert!(
            body["input"]
                .to_string()
                .contains("Subscription review evidence")
        );
    }
    let replay = requests[1].1["input"].as_array().unwrap();
    let tool = replay
        .iter()
        .position(|item| item["type"] == "function_call")
        .unwrap();
    assert_eq!(replay[tool]["name"], "run_command");
    assert_eq!(replay[tool]["call_id"], "workflow-subscription-evidence");
    assert_eq!(replay[tool + 1]["type"], "function_call_output");
    assert_eq!(
        replay[tool + 1]["call_id"],
        "workflow-subscription-evidence"
    );
    let output =
        serde_json::from_str::<Value>(replay[tool + 1]["output"].as_str().unwrap()).unwrap();
    assert_eq!(output["stdout"], "evidence.txt has 3 verified rows");
    server.abort();
}

async fn mock_subscription_review(
    State(requests): State<Arc<Mutex<Vec<(axum::http::HeaderMap, Value)>>>>,
    headers: axum::http::HeaderMap,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    let mut requests = requests.lock().unwrap();
    requests.push((headers, body));
    let (id, name, arguments) = match requests.len() {
        1 => (
            "workflow-subscription-evidence",
            "run_command",
            json!({"command":"cat evidence.txt"}),
        ),
        2 => (
            "workflow-subscription-result",
            "report_workflow_review",
            json!({"decision":"pass","message":"Subscription verified"}),
        ),
        3 => {
            return (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({"error":{"message":"quota exhausted"}})),
            )
                .into_response();
        }
        calls => panic!("Unexpected number of subscription calls {calls}"),
    };
    let mut item = json!({"type":"function_call","id":format!("item-{id}"),"call_id":id,"name":name,"arguments":""});
    let added = json!({"type":"response.output_item.added","item":item});
    item["arguments"] = json!(arguments.to_string());
    let completed_item = json!({"type":"response.output_item.done","item":item});
    let done = json!({"type":"response.completed","response":{
        "output":[],"usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7},
    }});
    (
        [("content-type", "text/event-stream")],
        format!("data: {added}\n\ndata: {completed_item}\n\ndata: {done}\n\n"),
    )
        .into_response()
}
