use super::*;
use crate::interactions::Kind;
use std::process::Command;

async fn mock_model(
    response: impl Fn(Value) -> String + Clone + Send + Sync + 'static,
) -> (String, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "http://{}/v1/chat/completions",
        listener.local_addr().unwrap()
    );
    let routes = Router::new().route(
        "/v1/chat/completions",
        post(move |Json(body): Json<Value>| {
            let response = response.clone();
            async move {
                (
                    [(header::CONTENT_TYPE, "text/event-stream")],
                    response(body),
                )
            }
        }),
    );
    let server = tokio::spawn(async move { axum::serve(listener, routes).await.unwrap() });
    (endpoint, server)
}

fn answer_response() -> String {
    format!(
        "data: {}\n\ndata: [DONE]\n\n",
        json!({"choices":[{"index":0,"delta":{"content":"fixture answer"},"finish_reason":"stop"}]}),
    )
}

fn question_response() -> String {
    let answer = json!({"choices":[{"index":0,"delta":{"tool_calls":[{
        "index":0,"id":"call_schedule_question","type":"function",
        "function":{"name":"ask_user","arguments":"{\"question\":\"Continue?\"}"}
    }]},"finish_reason":"tool_calls"}]});
    format!("data: {answer}\n\ndata: [DONE]\n\n")
}

async fn wait_until(
    service: &ScheduleService,
    owner: &crate::schedules::ScheduleOwner,
    state: &str,
) -> Value {
    for _ in 0..100 {
        owner.tick(Timestamp::now()).unwrap();
        let result = service.runs("native").unwrap();
        if let Some(row) = result
            .as_array()
            .unwrap()
            .first()
            .filter(|row| row["state"] == state)
        {
            return row.clone();
        }
        tokio::time::sleep(Duration::from_millis(15)).await;
    }
    panic!(
        "expected {state}, found {:?}",
        service.runs("native").unwrap()
    );
}

#[tokio::test]
async fn custom_plan_delivers_hidden_notice_and_preserves_real_readonly_mode() {
    let (endpoint, server) = mock_model(|body| {
        let messages = body["messages"].as_array().unwrap();
        let suffix = &messages[messages.len() - 2..];
        assert_eq!(suffix[0]["content"], "schedule fixture");
        assert_eq!(suffix[1]["content"], ASK_USER_NOTICE);
        answer_response()
    })
    .await;
    let data = Data::new();
    let (service, runtime, _) = data.setup(endpoint);
    let host = HostPrincipal::new(Some(WORKSPACE.into()));
    let mut plan = definition();
    plan["configuration"] = json!({"mode":"custom","work_mode":"plan","network_enabled":false});
    service.submit(&host, &plan, &Value::Null, false).unwrap();
    service
        .manage("native", "run", None, None, Some("custom"))
        .unwrap();
    let owner = service.acquire_owner().unwrap();
    let result = wait_until(&service, &owner, "success").await;
    let id = result["conversation_id"].as_str().unwrap();
    let metadata = runtime.web_metadata(WORKSPACE, id).unwrap();
    assert_eq!(metadata["work_mode"], "plan");
    assert_eq!(metadata["permission_mode"], "readonly");
    assert_eq!(metadata["execution_mode"], "sandbox");
    assert_eq!(metadata["network_permission"], "none");
    assert_eq!(metadata["title"], "Fixture");
    assert_eq!(metadata["schedule_id"], "native");
    assert_eq!(metadata["schedule_run_id"], result["id"]);
    assert_eq!(metadata["skip_auto_title_generation"], true);
    assert_eq!(result["summary"], "fixture answer");
    drop(runtime);
    server.abort();
}

#[tokio::test]
async fn scheduled_question_can_be_answered_or_expire_through_owner_timeout() {
    let (endpoint, server) = mock_model(|body| {
        let messages = body["messages"].as_array().unwrap();
        assert!(!messages.iter().any(|row| row["content"] == ASK_USER_NOTICE));
        if messages.iter().any(|row| row["role"] == "tool") {
            answer_response()
        } else {
            question_response()
        }
    })
    .await;
    let data = Data::new();
    let (service, runtime, _) = data.setup(endpoint);
    let host = HostPrincipal::new(Some(WORKSPACE.into()));
    let mut plan = definition();
    plan["configuration"] = json!({"mode":"custom","work_mode":"execute","ask_user":true});
    plan["policies"] = json!({"approval_timeout_seconds":1});
    service.submit(&host, &plan, &Value::Null, false).unwrap();
    service
        .manage("native", "run", None, None, Some("question"))
        .unwrap();
    let owner = service.acquire_owner().unwrap();
    let waiting = wait_until(&service, &owner, "waiting").await;
    let id = waiting["conversation_id"].as_str().unwrap();
    let principal = ApprovalPrincipal::new("host", WORKSPACE).unwrap();
    let pending = runtime
        .pending_interactions(&principal, Kind::Question, Some(id))
        .unwrap();
    assert_eq!(pending.len(), 1);
    let question = pending[0]["question_id"].as_str().unwrap();
    let answer = serde_json::from_value(json!({"text":"Continue"})).unwrap();
    runtime
        .answer_user_question(&principal, question, &answer)
        .unwrap();
    let succeeded = wait_until(&service, &owner, "success").await;
    assert_eq!(succeeded["summary"], "fixture answer");

    service
        .manage("native", "run", None, None, Some("timeout"))
        .unwrap();
    let following = wait_until(&service, &owner, "waiting").await;
    let id = following["conversation_id"].as_str().unwrap();
    let later = Timestamp::now()
        .checked_add(Duration::from_secs(3))
        .unwrap();
    owner.tick(later).unwrap();
    for _ in 0..100 {
        tokio::time::sleep(Duration::from_millis(10)).await;
        owner.tick(later).unwrap();
        if service
            .runs("native")
            .unwrap()
            .as_array()
            .unwrap()
            .iter()
            .any(|row| {
                row["id"] == following["id"]
                    && row["state"] == "failed"
                    && row["reason"] == "approval_timeout"
            })
        {
            assert!(
                runtime
                    .pending_interactions(&principal, Kind::Question, Some(id))
                    .unwrap()
                    .is_empty()
            );
            server.abort();
            return;
        }
    }
    panic!(
        "timeout did not stop the scheduled task: {:?}",
        service.runs("native").unwrap()
    );
}

fn git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .current_dir(root)
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[tokio::test]
async fn only_registered_authorized_worktrees_enter_the_admitted_scope() {
    let (endpoint, server) = mock_model(|_| answer_response()).await;
    let data = Data::new();
    let (service, runtime, _) = data.setup(endpoint);
    let root = data.0.join("project").canonicalize().unwrap();
    git(&root, &["init", "-b", "main"]);
    git(
        &root,
        &[
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
    );
    let inside = root.join("worktree");
    let outside = data.0.join("outside");
    git(
        &root,
        &["worktree", "add", "-b", "inside", inside.to_str().unwrap()],
    );
    git(
        &root,
        &[
            "worktree",
            "add",
            "-b",
            "outside",
            outside.to_str().unwrap(),
        ],
    );

    let host = HostPrincipal::new(Some(WORKSPACE.into()));
    let mut plan = definition();
    plan["configuration"] =
        json!({"mode":"custom","work_mode":"execute","worktree":{"branch_name":"outside"}});
    assert_eq!(
        service
            .validate(&host, &plan, &Value::Null)
            .unwrap_err()
            .status,
        403
    );
    plan["configuration"]["worktree"]["branch_name"] = json!("missing");
    assert_eq!(
        service
            .validate(&host, &plan, &Value::Null)
            .unwrap_err()
            .status,
        400
    );
    plan["configuration"]["worktree"]["branch_name"] = json!("main");
    assert_eq!(
        service
            .validate(&host, &plan, &Value::Null)
            .unwrap_err()
            .status,
        400
    );
    plan["configuration"]["worktree"]["branch_name"] = json!("inside");
    service.submit(&host, &plan, &Value::Null, false).unwrap();
    service
        .manage("native", "run", None, None, Some("inside"))
        .unwrap();
    let owner = service.acquire_owner().unwrap();
    let run = wait_until(&service, &owner, "success").await;
    let id = run["conversation_id"].as_str().unwrap();
    let metadata = runtime.web_metadata(WORKSPACE, id).unwrap();
    assert_eq!(metadata["git_worktree_enabled"], true);
    assert_eq!(metadata["git_worktree"]["branch"], "inside");
    assert_eq!(metadata["git_worktree"]["directory"], json!(inside));
    assert_eq!(metadata["git_worktree"]["revision"], 1);

    plan["target"]["conversation"] = json!({"mode":"existing","id":id});
    plan["revision"] = run["revision"].clone();
    plan["configuration"]["worktree"] = json!("main");
    service.submit(&host, &plan, &Value::Null, true).unwrap();
    service
        .manage("native", "run", None, None, Some("main"))
        .unwrap();
    for _ in 0..100 {
        owner.tick(Timestamp::now()).unwrap();
        if runtime.web_metadata(WORKSPACE, id).unwrap()["git_worktree"]["active"] == false {
            let metadata = runtime.web_metadata(WORKSPACE, id).unwrap();
            assert_eq!(metadata["git_worktree"]["branch"], "main");
            assert_eq!(metadata["git_worktree"]["revision"], 2);
            server.abort();
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("scheduled entry did not restore its main execution root");
}

#[tokio::test]
async fn scheduled_goals_use_existing_goal_lifecycle_and_review_requirements() {
    let (endpoint, server) = mock_model(|body| {
        let messages = body["messages"].as_array().unwrap();
        assert!(!messages.iter().any(|row| row["content"] == ASK_USER_NOTICE));
        answer_response()
    })
    .await;
    let data = Data::new();
    let (service, runtime, _) = data.setup(endpoint);
    let host = HostPrincipal::new(Some(WORKSPACE.into()));
    let mut plan = definition();
    plan["configuration"] =
        json!({"mode":"custom","work_mode":"execute","permission_mode":"approval"});
    plan["goal"] = json!({"mode":"start","max_turns":2});
    service.submit(&host, &plan, &Value::Null, false).unwrap();
    service
        .manage("native", "run", None, None, Some("goal"))
        .unwrap();
    let owner = service.acquire_owner().unwrap();
    let blocked = wait_until(&service, &owner, "failed").await;
    let id = blocked["conversation_id"].as_str().unwrap();
    let metadata = runtime.web_metadata(WORKSPACE, id).unwrap();
    assert_eq!(metadata["work_mode"], "execute");
    assert_eq!(metadata["permission_mode"], "auto_approval");
    let workspace = RegisteredWorkspace {
        id: WORKSPACE.into(),
        label: String::new(),
        path: data.0.join("project").canonicalize().unwrap(),
    };
    let status = runtime.conversation_running_status(&workspace, id).unwrap();
    assert_eq!(status["data"]["goal_progress"]["status"], "blocked");
    assert_eq!(status["data"]["goal_progress"]["max_turns"], 2);
    assert_eq!(blocked["reason"], "未配置目标审核智能体的模型。");

    plan["target"]["conversation"] = json!({"mode":"existing","id":id});
    plan["revision"] = blocked["revision"].clone();
    plan["goal"] = json!({"mode":"continue","max_turns":4});
    service.submit(&host, &plan, &Value::Null, true).unwrap();
    service
        .manage("native", "run", None, None, Some("continue"))
        .unwrap();
    let resumed = wait_until(&service, &owner, "failed").await;
    assert_eq!(resumed["conversation_id"], id);
    assert_ne!(resumed["task_id"], blocked["task_id"]);
    let status = runtime.conversation_running_status(&workspace, id).unwrap();
    assert_eq!(status["data"]["goal_progress"]["max_turns"], 4);
    assert_eq!(status["data"]["goal_progress"]["turn_count"], 2);
    server.abort();
}
