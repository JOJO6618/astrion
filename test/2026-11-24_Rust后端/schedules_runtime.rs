use super::*;

use axum::{Json, Router, http::header, routing::post};
use jiff::Timestamp;
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use tokio::net::TcpListener;
use uuid::Uuid;

use crate::schedules::ScheduleService;

const WORKSPACE: &str = "workspace-fixture";
const MODEL: &str = "fixture-model";

struct Data(PathBuf);

impl Data {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("schedules-runtime-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        Self(root)
    }

    fn setup(&self, endpoint: String) -> (ScheduleService, Runtime, ModelConfig) {
        let project = self.0.join("project");
        let data = self.0.join("data");
        fs::create_dir(&project).unwrap();
        let runtime = Runtime::open(&data).unwrap();
        runtime.bind_workspace(WORKSPACE, &project).unwrap();
        let model = ModelConfig::fixed(endpoint, None, MODEL.into());
        let adapter = RuntimeScheduleAdapter::new(
            runtime.clone(),
            WorkspaceOption {
                id: WORKSPACE.into(),
                label: "Fixture".into(),
            },
            &project,
            CatalogSource::with_paths(&self.0, &self.0, &data),
            Some(model.clone()),
        )
        .unwrap();
        (
            ScheduleService::open(&data, Arc::new(adapter)).unwrap(),
            runtime,
            model,
        )
    }
}

impl Drop for Data {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn definition() -> Value {
    json!({"schema_version":1,"id":"native","name":"Fixture","message":"schedule fixture",
        "target":{"workspace_id":WORKSPACE},"schedule":{"type":"daily","timezone":"UTC","times":["09:00"]}})
}

#[tokio::test]
async fn explicit_tick_dispatches_real_runtime_once_against_local_model_fixture() {
    let data = Data::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let mock_calls = calls.clone();
    let model = Router::new().route("/v1/chat/completions", post(move |Json(body): Json<Value>| {
        let calls = mock_calls.clone();
        async move {
            assert_eq!(body["model"], MODEL);
            assert_eq!(body["messages"].as_array().unwrap().last().unwrap()["content"], "schedule fixture");
            calls.fetch_add(1, Ordering::SeqCst);
            ([(header::CONTENT_TYPE,"text/event-stream")], concat!(
                "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Fixture response\"},\"finish_reason\":null}]}\n\n",
                "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
                "data: [DONE]\n\n"))
        }
    }));
    let server = tokio::spawn(async move { axum::serve(listener, model).await.unwrap() });
    let (service, runtime, _) = data.setup(format!("http://{address}/v1/chat/completions"));
    let host = HostPrincipal::new(Some(WORKSPACE.into()));
    service
        .submit(&host, &definition(), &Value::Null, false)
        .unwrap();
    let run = service
        .manage("native", "run", None, None, Some("stable"))
        .unwrap();
    assert_eq!(run["state"], "queued");
    assert!(run["task_id"].is_null());
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    let owner = service.acquire_owner().unwrap();
    owner.tick(Timestamp::now()).unwrap();
    let admitted = service.runs("native").unwrap();
    let admitted = &admitted[0];
    assert_eq!(admitted["state"], "running");
    let task = runtime
        .poll_task(WORKSPACE, admitted["task_id"].as_str().unwrap(), 0)
        .unwrap();
    assert_eq!(task["data"]["message_source"], "schedule");
    assert_eq!(
        service
            .manage("native", "run", None, None, Some("stable"))
            .unwrap()["id"],
        run["id"]
    );
    let finished = tokio::time::timeout(Duration::from_secs(6), async {
        loop {
            owner.tick(Timestamp::now()).unwrap();
            let run = service.runs("native").unwrap()[0].clone();
            if run["state"] != "running" {
                break run;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(finished["state"], "success", "{finished}");
    assert_eq!(finished["summary"], "Fixture response");
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    server.abort();
}

#[tokio::test]
async fn oauth_settings_inherit_existing_metadata_without_yaml_fast_override() {
    let data = Data::new();
    let (_, runtime, _) = data.setup("http://127.0.0.1:9/v1/chat/completions".into());
    let host_data = data.0.join("data");
    let model_key = "openai-codex/shared";
    fs::write(
        host_data.join("codex_auth.json"),
        r#"{"tokens":{"access_token":"fixture","refresh_token":"fixture"}}"#,
    )
    .unwrap();
    fs::write(
        host_data.join("codex_models.json"),
        r#"{"models":[{"slug":"shared","visibility":"list","supported_in_api":true}]}"#,
    )
    .unwrap();
    fs::write(
        host_data.join("personalization.json"),
        r#"{"default_run_mode":"thinking"}"#,
    )
    .unwrap();
    let adapter = RuntimeScheduleAdapter::new(
        runtime.clone(),
        WorkspaceOption {
            id: WORKSPACE.into(),
            label: "Fixture".into(),
        },
        data.0.join("project"),
        CatalogSource::with_paths(&data.0, &data.0, &host_data),
        None,
    )
    .unwrap();
    let conversation = runtime
        .create_session(
            WORKSPACE,
            &json!({
                "model_key":model_key,
                "thinking_mode":true,
                "reasoning_effort":"high",
                "chatgpt_fast_mode":true,
            }),
        )
        .unwrap();
    let id = conversation["conversation_id"].as_str().unwrap();
    let host = HostPrincipal::new(Some(WORKSPACE.into()));
    let principal = host.to_record();
    let snapshot = |plan: &Value| {
        let (normalized, _) =
            crate::schedules::schema::normalize(plan, &json!(WORKSPACE), Timestamp::now(), None)
                .unwrap();
        adapter.resolve(&normalized, &principal).unwrap().1
    };

    let mut plan = definition();
    plan["target"]["conversation"] = json!({"mode":"existing","id":id});
    plan["configuration"] = json!({"mode":"inherit"});
    let inherited = snapshot(&plan);
    assert_eq!(inherited["reasoning_effort"], "high");
    assert_eq!(inherited["chatgpt_fast_mode"], true);

    // Python custom configuration has no Fast override: the target session value
    // is inherited while the allowed fields merge normally.
    plan["configuration"] = json!({"mode":"custom","reasoning_effort":"low"});
    let custom = snapshot(&plan);
    assert_eq!(custom["reasoning_effort"], "low");
    assert_eq!(custom["chatgpt_fast_mode"], true);

    // New target conversations always start with Fast disabled.
    let mut plan = definition();
    plan["id"] = json!("oauth-fast");
    plan["configuration"] = json!({"mode":"custom"});
    let fresh = snapshot(&plan);
    assert_eq!(fresh["model_key"], model_key);
    assert_eq!(fresh["chatgpt_fast_mode"], false);
}

#[tokio::test]
async fn existing_conversation_admission_respects_runtime_gate_and_revalidation() {
    let data = Data::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "http://{}/v1/chat/completions",
        listener.local_addr().unwrap()
    );
    let (service, runtime, model) = data.setup(endpoint);
    let conversation = runtime
        .create_session(WORKSPACE, &json!({"model_key":MODEL}))
        .unwrap();
    let id = conversation["conversation_id"].as_str().unwrap();
    let app_task = runtime
        .create_task(
            WORKSPACE,
            &json!({"message":"foreground","conversation_id":id}),
            model,
        )
        .unwrap();
    let mut plan = definition();
    plan["target"]["conversation"] = json!({"mode":"existing","id":id});
    let host = HostPrincipal::new(Some(WORKSPACE.into()));
    service.submit(&host, &plan, &Value::Null, false).unwrap();
    service
        .manage("native", "run", None, None, Some("busy"))
        .unwrap();
    let owner = service.acquire_owner().unwrap();
    owner.tick(Timestamp::now()).unwrap();
    let skipped = &service.runs("native").unwrap()[0];
    assert_eq!(skipped["state"], "skipped");
    assert_eq!(skipped["reason"], "conversation_gate_busy");
    assert!(skipped["task_id"].is_null());
    runtime
        .cancel_task(WORKSPACE, app_task["data"]["task_id"].as_str().unwrap())
        .unwrap();
    plan["target"]["workspace_id"] = json!("other-workspace");
    assert_eq!(
        service
            .validate(&host, &plan, &Value::Null)
            .unwrap_err()
            .status,
        503
    );
    plan["target"]["workspace_id"] = json!(WORKSPACE);
    plan["goal"]["mode"] = json!("continue");
    service.validate(&host, &plan, &Value::Null).unwrap();
    fs::write(
        data.0.join("data/admin_policy.json"),
        json!({"global":{"disabled_models":[MODEL]}}).to_string(),
    )
    .unwrap();
    let mut plan = definition();
    plan["id"] = json!("admin_disabled");
    assert_eq!(
        service
            .validate(&host, &plan, &Value::Null)
            .unwrap_err()
            .status,
        403
    );
}

#[tokio::test]
async fn registered_workspaces_share_runtime_but_keep_separate_tools_and_pending_plans() {
    use super::super::clock::instant;
    use crate::tools::ToolSet;

    let data = Data::new();
    let roots = data.0.join("projects");
    let runtime_data = data.0.join("runtime-data");
    let first = roots.join("one");
    let second = roots.join("two");
    for (root, content) in [(&first, "only-one"), (&second, "only-two")] {
        fs::create_dir_all(root).unwrap();
        fs::write(root.join("marker.txt"), content).unwrap();
    }
    let registry = data.0.join("host_workspaces.json");
    fs::write(
        &registry,
        json!({"default_workspace_id":"first", "workspaces":[
            {"workspace_id":"first","path":"one"},{"workspace_id":"second","path":"two"}
        ]})
        .to_string(),
    )
    .unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let mock_calls = calls.clone();
    let model = Router::new().route("/v1/chat/completions", post(move |Json(body): Json<Value>| {
        let calls = mock_calls.clone();
        async move {
            calls.fetch_add(1, Ordering::SeqCst);
            let messages = body["messages"].as_array().unwrap();
            let last = messages.last().unwrap();
            let (finish_reason, delta) = if last["role"] == "tool" {
                let instruction = messages.iter().find(|message| message["role"] == "user").unwrap()["content"].as_str().unwrap();
                let tool_text = last["content"].as_str().unwrap();
                assert!(tool_text.contains(instruction), "{instruction}: {tool_text}");
                assert!(!tool_text.contains(if instruction == "only-one" { "only-two" } else { "only-one" }));
                ("stop", json!({"content":format!("Read {instruction}")}))
            } else {
                ("tool_calls", json!({"tool_calls":[{"index":0,"id":"read_marker","type":"function","function":{"name":"read_file",
                    "arguments":json!({"path":"marker.txt","type":"read"}).to_string()}}]}))
            };
            let chunk = json!({"choices":[{"index":0,"delta":delta,"finish_reason":finish_reason}]});
            ([(header::CONTENT_TYPE,"text/event-stream")], format!("data: {chunk}\n\ndata: [DONE]\n\n"))
        }
    }));
    let server = tokio::spawn(async move { axum::serve(listener, model).await.unwrap() });
    let runtime = Runtime::open(&runtime_data).unwrap();
    let bind = |id, root: &Path| {
        runtime
            .bind_workspace_with_tools(
                id,
                root,
                ToolSet::for_workspace(root, &runtime_data).unwrap(),
            )
            .unwrap()
    };
    bind("first", &first);
    bind("second", &second);
    let model = ModelConfig::fixed(
        format!("http://{address}/v1/chat/completions"),
        None,
        MODEL.into(),
    );
    let adapter = RuntimeScheduleAdapter::new(
        runtime.clone(),
        WorkspaceOption {
            id: "".into(),
            label: "unregistered startup".into(),
        },
        &roots,
        CatalogSource::with_paths(&data.0, &data.0, &runtime_data),
        Some(model),
    )
    .unwrap()
    .with_registry(&registry, &roots);
    let service = ScheduleService::open(&runtime_data, Arc::new(adapter)).unwrap();
    let host = HostPrincipal::new(None);
    let options = service.options(&host).unwrap();
    assert_eq!(options["workspaces"].as_array().unwrap().len(), 2);
    let anchor = super::super::clock::iso(Timestamp::now());
    for (id, message) in [("first", "only-one"), ("second", "only-two")] {
        let mut plan = definition();
        plan["id"] = json!(id);
        plan["target"]["workspace_id"] = json!(id);
        plan["message"] = json!(message);
        plan["schedule"] =
            json!({"type":"interval","timezone":"UTC","every":1,"unit":"minutes","anchor":anchor});
        plan["enabled"] = json!(true);
        service.submit(&host, &plan, &Value::Null, false).unwrap();
    }
    let due = service.detail("second").unwrap()["next_at"]
        .as_str()
        .unwrap()
        .to_owned();
    let at = instant(&due).unwrap();
    let owner = service.acquire_owner().unwrap();
    fs::remove_file(&registry).unwrap();
    owner.tick(at).unwrap();
    assert!(
        service
            .runs("first")
            .unwrap()
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert!(
        service
            .runs("second")
            .unwrap()
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(service.options(&host).unwrap()["workspaces"], json!([]));
    assert_eq!(service.detail("second").unwrap()["next_at"], due);

    fs::write(
        &registry,
        json!({"workspaces":[{"workspace_id":"first","path":"one"}]}).to_string(),
    )
    .unwrap();
    runtime.remove_idle_workspace("second", || Ok(())).unwrap();
    owner.tick(at).unwrap();
    assert_eq!(service.runs("first").unwrap().as_array().unwrap().len(), 1);
    assert!(
        service
            .runs("second")
            .unwrap()
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(service.detail("second").unwrap()["next_at"], due);
    assert!(runtime.bound_workspace("second").unwrap().is_none());
    fs::write(&registry, json!({"workspaces":[{"workspace_id":"first","path":"one"},{"workspace_id":"second","path":"two"}]}).to_string()).unwrap();
    owner.tick(at).unwrap();
    assert_eq!(
        runtime.bound_workspace("second").unwrap(),
        Some(second.canonicalize().unwrap())
    );
    let runs = tokio::time::timeout(Duration::from_secs(6), async {
        loop {
            owner.tick(at).unwrap();
            let a = service.runs("first").unwrap()[0].clone();
            let b = service.runs("second").unwrap()[0].clone();
            if a["state"] != "running" && b["state"] != "running" {
                break (a, b);
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(runs.0["state"], "success", "{}", runs.0);
    assert_eq!(runs.1["state"], "success", "{}", runs.1);
    assert_eq!(runs.0["summary"], "Read only-one");
    assert_eq!(runs.1["summary"], "Read only-two");
    assert_eq!(calls.load(Ordering::SeqCst), 4);
    assert!(
        runtime
            .poll_task("first", runs.1["task_id"].as_str().unwrap(), 0)
            .is_err()
    );
    server.abort();
}

#[path = "../2026-11-27_Rust定时执行/admission.rs"]
mod native_admission;
