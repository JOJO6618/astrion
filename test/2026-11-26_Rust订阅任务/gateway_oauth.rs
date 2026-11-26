use super::*;
use astrion_headless::{providers::ProviderConfig, sub_agents::SubAgents};
use axum::{http::HeaderMap, routing::post};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::Value;
use std::{
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{
    sync::mpsc,
    time::{sleep, timeout},
};

struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn write_json(path: &Path, value: Value) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
}

fn account(data: &Path, connection: &str, name: &str, priority: bool) {
    let claims = json!({"sub":format!("subject-{name}"),"exp":4102444800_u64,
        "https://api.openai.com/auth":{"chatgpt_account_id":name}});
    let token = format!(
        "header.{}.signature",
        URL_SAFE_NO_PAD.encode(claims.to_string())
    );
    let (auth, cache) = if connection == "openai-codex" {
        (data.join("codex_auth.json"), data.join("codex_models.json"))
    } else {
        (
            data.join(format!("codex_accounts/{connection}.json")),
            data.join(format!("codex_models/{connection}.json")),
        )
    };
    write_json(
        &auth,
        json!({"auth_mode":"chatgpt", "tokens":{"access_token":token,"id_token":token,"refresh_token":"fake-refresh","account_id":name}}),
    );
    let tiers = if priority {
        json!([{"id":"priority"}])
    } else {
        json!([])
    };
    write_json(
        &cache,
        json!({"models":[{"slug":"shared","visibility":"list","supported_in_api":true,
        "service_tiers":tiers,"base_instructions":format!("Base for {name}"),"default_reasoning_level":"medium"}]}),
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn subscription_tasks_use_the_selected_account_and_preserve_frozen_history() {
    let scratch = Scratch(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-26_Rust订阅任务")
            .join(format!("gateway-{}", uuid::Uuid::new_v4())),
    );
    let dir = &scratch.0;
    let data = dir.join("host/data");
    let project = dir.join("work");
    fs::create_dir_all(&project).unwrap();
    let project = project.canonicalize().unwrap();
    let paths = host_paths::HostPaths {
        deploy: dir.join("config"),
        logs: dir.join("logs"),
        resources: dir.join("resources"),
        host_libraries: dir.join("host"),
        workspace_config: dir.join("config/host_workspaces.json"),
    };
    write_json(
        &paths.workspace_config,
        json!({"default_workspace_id":"sample","workspaces":[{"workspace_id":"sample","label":"sample","path":project}]}),
    );
    let first = "openai-codex";
    let second = "openai-codex-second";
    write_json(
        &data.join("codex_accounts.json"),
        json!({"version":1,"accounts":{(first):{"legacy":true},(second):{}}}),
    );
    account(&data, first, "alpha", true);
    account(&data, second, "beta", false);
    let upstream = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/responses", upstream.local_addr().unwrap());
    let (sent, mut requests) = mpsc::unbounded_channel();
    let mock = Router::new().route("/responses",post(move |headers:HeaderMap, Json(body):Json<Value>| {
        let sent = sent.clone();
        async move {
            sent.send((headers,body)).unwrap();
            ([("content-type","text/event-stream")], concat!(
                "data: {\"type\":\"response.output_item.done\",\"item\":{\"type\":\"reasoning\",\"summary\":[],\"encrypted_content\":\"private-fixture-reasoning\"}}\n\n",
                "data: {\"type\":\"response.output_text.delta\",\"delta\":\"done\"}\n\n",
                "data: {\"type\":\"response.completed\",\"response\":{\"output\":[],\"usage\":{\"input_tokens\":15,\"output_tokens\":2,\"total_tokens\":17}}}\n\n"))
        }
    }));
    let mock_server = tokio::spawn(async move { axum::serve(upstream, mock).await.unwrap() });
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();
    let providers = ProviderConfig::host(&paths.resources, &paths.deploy, &data)
        .with_client(client.clone())
        .without_modelsdev_refresh()
        .with_codex_responses_url(endpoint);
    let catalog =
        CatalogSource::with_paths(&paths.resources, &paths.deploy, &data).with_providers(providers);
    let runtime = Runtime::open(&data).unwrap();
    runtime
        .bind_workspace_with_tools(
            "sample",
            &project,
            ToolSet::for_workspace(&project, &data).unwrap(),
        )
        .unwrap();
    let adapter = RuntimeScheduleAdapter::new(
        runtime.clone(),
        WorkspaceOption {
            id: "sample".into(),
            label: "sample".into(),
        },
        &project,
        catalog.clone(),
        None,
    )
    .unwrap();
    let app = gateway::Gateway {
        schedules: ScheduleService::open(&data, Arc::new(adapter)).unwrap(),
        children: SubAgents::new(&data, runtime.clone()),
        preferences: Personalization::new(
            catalog.clone(),
            dir.join("skills"),
            dir.join("user-skills"),
        ),
        workspace: host_paths::Workspace::resolve(
            &project,
            &paths.workspace_config,
            &paths.resources,
        )
        .unwrap(),
        workflows: WorkflowRegistry::new(dir.join("builtin"), dir.join("user")).unwrap(),
        catalog,
        paths,
        runtime,
        fixed_model: None,
        token: "test-token".into(),
        web: None,
    };
    let app = gateway::router(Arc::new(app)).unwrap();
    let host = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", host.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(
            host,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await
        .unwrap()
    });
    let get = |path: String| {
        client
            .get(format!("{base}{path}"))
            .bearer_auth("test-token")
            .header("X-Astrion-Workspace-Id", "sample")
    };
    let mut conversation = Value::Null;
    let mut frozen = String::new();
    for (index, (connection, owner)) in [(first, "alpha"), (first, "alpha"), (second, "beta")]
        .into_iter()
        .enumerate()
    {
        let task: Value = client.post(format!("{base}/api/tasks")).bearer_auth("test-token").header("X-Astrion-Workspace-Id","sample")
            .json(&json!({"conversation_id":conversation,"model_key":format!("{connection}/shared"),"message":"hello","chatgpt_fast_mode":true}))
            .send().await.unwrap().json().await.unwrap();
        assert_eq!(task["success"], true, "{task}");
        conversation = task["data"]["conversation_id"].clone();
        let id = task["data"]["task_id"].as_str().unwrap();
        timeout(Duration::from_secs(10), async {
            loop {
                let status: Value = get(format!("/api/tasks/{id}"))
                    .send()
                    .await
                    .unwrap()
                    .json()
                    .await
                    .unwrap();
                assert_eq!(status["success"], true);
                if status["data"]["status"] != "running" {
                    assert_eq!(
                        status["data"]["status"], "succeeded",
                        "{}",
                        status["data"]["error"]
                    );
                    break;
                }
                sleep(Duration::from_millis(15)).await;
            }
        })
        .await
        .unwrap();
        let (headers, body) = timeout(Duration::from_secs(1), requests.recv())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(headers["chatgpt-account-id"], owner);
        assert_eq!(headers["session_id"], conversation.as_str().unwrap());
        assert_eq!(body["prompt_cache_key"], conversation);
        assert_eq!(body["model"], "shared");
        assert_eq!(
            body["service_tier"].as_str(),
            (index < 2).then_some("priority")
        );
        assert_eq!(body["input"][0]["role"], "developer");
        let prefix = body["input"][0]["content"][0]["text"].as_str().unwrap();
        if index == 0 {
            assert!(prefix.starts_with("Base for alpha\n\n"));
            frozen = prefix.to_owned();
        }
        assert_eq!(prefix, frozen);
        assert_eq!(
            body["input"]
                .to_string()
                .contains("private-fixture-reasoning"),
            index == 1
        );
        let history: Value = get(format!(
            "/api/runtime/sessions/{}/history",
            conversation.as_str().unwrap()
        ))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
        assert_eq!(history["success"], true);
        assert!(!history.to_string().contains("private-fixture-reasoning"));
    }
    server.abort();
    mock_server.abort();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn chat_and_schedule_use_the_created_conversation_on_the_subscription_upstream() {
    let scratch = Scratch(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-26_Rust订阅任务")
            .join(format!("schedule-{}", uuid::Uuid::new_v4())),
    );
    let dir = &scratch.0;
    let data = dir.join("host/data");
    let project = dir.join("work");
    fs::create_dir_all(&project).unwrap();
    let project = project.canonicalize().unwrap();
    let paths = host_paths::HostPaths {
        deploy: dir.join("config"),
        logs: dir.join("logs"),
        resources: dir.join("resources"),
        host_libraries: dir.join("host"),
        workspace_config: dir.join("config/host_workspaces.json"),
    };
    write_json(
        &paths.workspace_config,
        json!({"default_workspace_id":"sample","workspaces":[{"workspace_id":"sample","label":"sample","path":project}]}),
    );
    let connection = "openai-codex";
    write_json(
        &data.join("codex_accounts.json"),
        json!({"version":1,"accounts":{(connection):{"legacy":true}}}),
    );
    account(&data, connection, "alpha", true);
    write_json(
        &data.join("personalization.json"),
        json!({"default_run_mode":"thinking"}),
    );
    let upstream = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/responses", upstream.local_addr().unwrap());
    let (sent, mut requests) = mpsc::unbounded_channel();
    let mock = Router::new().route("/responses", post(move |headers: HeaderMap, Json(body): Json<Value>| {
        let sent = sent.clone();
        async move {
            sent.send((headers, body)).unwrap();
            ([("content-type","text/event-stream")], concat!(
                "data: {\"type\":\"response.output_text.delta\",\"delta\":\"done\"}\n\n",
                "data: {\"type\":\"response.completed\",\"response\":{\"output\":[],\"usage\":{\"input_tokens\":9,\"output_tokens\":1,\"total_tokens\":10}}}\n\n"))
        }
    }));
    let mock_server = tokio::spawn(async move { axum::serve(upstream, mock).await.unwrap() });
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap();
    let providers = ProviderConfig::host(&paths.resources, &paths.deploy, &data)
        .with_client(client.clone())
        .without_modelsdev_refresh()
        .with_codex_responses_url(endpoint);
    let catalog =
        CatalogSource::with_paths(&paths.resources, &paths.deploy, &data).with_providers(providers);
    let runtime = Runtime::open(&data).unwrap();
    runtime
        .bind_workspace_with_tools(
            "sample",
            &project,
            ToolSet::for_workspace(&project, &data).unwrap(),
        )
        .unwrap();
    let preferences =
        Personalization::new(catalog.clone(), dir.join("skills"), dir.join("user-skills"));
    let adapter = RuntimeScheduleAdapter::new(
        runtime.clone(),
        WorkspaceOption {
            id: "sample".into(),
            label: "sample".into(),
        },
        &project,
        catalog.clone(),
        None,
    )
    .unwrap();
    let schedules = ScheduleService::open(&data, Arc::new(adapter)).unwrap();
    let host = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut config = WebAuthConfig::new(&data, host.local_addr().unwrap().port());
    config.workspace_ready = true;
    let auth = WebAuth::new(config).unwrap();
    let gateway = Arc::new(gateway::Gateway {
        schedules: schedules.clone(),
        children: SubAgents::new(&data, runtime.clone()),
        preferences: preferences.clone(),
        workspace: host_paths::Workspace::resolve(
            &project,
            &paths.workspace_config,
            &paths.resources,
        )
        .unwrap(),
        workflows: WorkflowRegistry::new(dir.join("builtin"), dir.join("user")).unwrap(),
        catalog: catalog.clone(),
        paths: paths.clone(),
        runtime: runtime.clone(),
        fixed_model: None,
        token: "test-token".into(),
        web: Some(auth.clone()),
    });
    fs::create_dir_all(paths.resources.join("static")).unwrap();
    let assets = WebAssets::new(paths.resources.join("static"))
        .unwrap()
        .with_host_data(&data);
    let web = host_web::routes(
        &auth,
        &gateway,
        &data,
        &paths,
        &assets,
        &astrion_headless::navigation::Navigation::new(&data),
    )
    .unwrap();
    let app = gateway::router(gateway).unwrap().merge(web);
    let base = format!("http://{}", host.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(
            host,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await
        .unwrap()
    });
    let challenge = client
        .get(format!("{base}/api/csrf-token"))
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap();
    let cookie = |reply: &reqwest::Response| {
        reply.headers()["set-cookie"]
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .to_owned()
    };
    let guest = cookie(&challenge);
    let challenge = challenge.json::<Value>().await.unwrap();
    let signed_in = client
        .post(format!("{base}/host-login"))
        .header("origin", &base)
        .header("cookie", guest)
        .header("x-csrf-token", challenge["token"].as_str().unwrap())
        .json(&json!({}))
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap();
    let session = cookie(&signed_in);
    let challenge = client
        .get(format!("{base}/api/csrf-token"))
        .header("cookie", &session)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    let post = |path: &str, body: Value| {
        client
            .post(format!("{base}{path}"))
            .header("cookie", &session)
            .header("origin", &base)
            .header("x-csrf-token", challenge["token"].as_str().unwrap())
            .header("X-Astrion-Workspace-Id", "sample")
            .json(&body)
    };

    let sent_chat: Value = post(
        "/api/chat/tasks",
        json!({
            "operation_id":"subscription-chat",
            "message":"chat fixture",
            "model_key":format!("{connection}/shared"),
            "thinking_mode":true,
            "settings":{"chatgpt_fast_mode":true,"reasoning_effort":"high"},
        }),
    )
    .send()
    .await
    .unwrap()
    .json()
    .await
    .unwrap();
    assert_eq!(sent_chat["success"], true, "{sent_chat}");
    let chat_id = sent_chat["data"]["task_id"].as_str().unwrap();
    let chat_conversation = sent_chat["data"]["conversation_id"]
        .as_str()
        .unwrap()
        .to_owned();
    timeout(Duration::from_secs(10), async {
        loop {
            let status: Value = client
                .get(format!("{base}/api/chat/tasks/{chat_id}"))
                .header("cookie", &session)
                .send()
                .await
                .unwrap()
                .json()
                .await
                .unwrap();
            assert_eq!(status["success"], true, "{status}");
            if status["data"]["status"] != "running" {
                assert_eq!(
                    status["data"]["status"], "succeeded",
                    "{}",
                    status["data"]["error"]
                );
                break;
            }
            sleep(Duration::from_millis(15)).await;
        }
    })
    .await
    .unwrap();
    let (headers, body) = timeout(Duration::from_secs(2), requests.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(headers["chatgpt-account-id"], "alpha");
    assert_eq!(headers["session_id"], chat_conversation);
    assert_eq!(body["prompt_cache_key"], chat_conversation);
    assert_eq!(body["service_tier"], "priority");
    assert_eq!(
        body["input"].as_array().unwrap().last().unwrap()["content"][0]["text"],
        "chat fixture"
    );

    let created: Value = post(
        "/api/runtime/sessions",
        json!({
            "model_key":format!("{connection}/shared"),
            "thinking_mode":true,
            "chatgpt_fast_mode":true,
        }),
    )
    .send()
    .await
    .unwrap()
    .json()
    .await
    .unwrap();
    assert_eq!(created["success"], true, "{created}");
    let conversation = created["conversation_id"].as_str().unwrap().to_owned();
    let run_plan = |plan: Value| {
        let submit = post("/api/schedules", json!({"definition":plan}));
        let run = post(
            &format!("/api/schedules/{}/actions", plan["id"].as_str().unwrap()),
            json!({"action":"run","request_id":"manual"}),
        );
        async move {
            for command in [submit, run] {
                let result: Value = command.send().await.unwrap().json().await.unwrap();
                assert_eq!(result["success"], true, "{result}");
            }
        }
    };
    let plan = json!({
        "schema_version":1,"id":"subscription-schedule","name":"Subscription",
        "message":"schedule fixture","enabled":false,
        "schedule":{"type":"daily","timezone":"UTC","times":["09:00"]},
        "target":{"workspace_id":"sample","conversation":{"mode":"existing","id":conversation}},
        "configuration":{"mode":"inherit"},"goal":{"mode":"off"},
    });
    run_plan(plan).await;
    schedules
        .acquire_owner()
        .unwrap()
        .tick(jiff::Timestamp::now())
        .unwrap();
    let (headers, body) = timeout(Duration::from_secs(10), requests.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(headers["chatgpt-account-id"], "alpha");
    assert_eq!(headers["session_id"], conversation);
    assert_eq!(body["prompt_cache_key"], conversation);
    assert_eq!(body["service_tier"], "priority");
    assert_eq!(
        body["input"].as_array().unwrap().last().unwrap()["content"][0]["text"],
        "schedule fixture"
    );

    let new_plan = json!({
        "schema_version":1,"id":"subscription-schedule-new","name":"Subscription new",
        "message":"new schedule fixture","enabled":false,
        "schedule":{"type":"daily","timezone":"UTC","times":["09:00"]},
        "target":{"workspace_id":"sample","conversation":{"mode":"new"}},
        "configuration":{"mode":"inherit"},"goal":{"mode":"off"},
    });
    run_plan(new_plan).await;
    schedules
        .acquire_owner()
        .unwrap()
        .tick(jiff::Timestamp::now())
        .unwrap();
    let (headers, body) = timeout(Duration::from_secs(10), requests.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(headers["chatgpt-account-id"], "alpha");
    assert_eq!(body["service_tier"], Value::Null);
    let generated = headers["session_id"].to_str().unwrap();
    assert!(!generated.is_empty());
    assert_eq!(body["prompt_cache_key"], generated);
    assert_eq!(
        body["input"].as_array().unwrap().last().unwrap()["content"][0]["text"],
        "new schedule fixture"
    );

    server.abort();
    mock_server.abort();
}
