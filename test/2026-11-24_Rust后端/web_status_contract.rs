use super::*;
use crate::runtime::ModelConfig;
use crate::web_auth::{WebAuth, WebAuthConfig};
use axum::body::{Body, Bytes, to_bytes};
use axum::extract::{ConnectInfo, Request};
use axum::http::{Method, header};
use futures_util::{StreamExt, stream};
use std::fs;
use std::net::SocketAddr;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio::sync::oneshot;
use tower::ServiceExt;
use uuid::Uuid;

const WORKSPACE: &str = "status-workspace";
const ID: &str = "conv_20240905_112233_019";
const HOST: &str = "127.0.0.1:18185";
const PORT: u16 = 18185;
const EMPTY_JSON: &str = "{}";

struct Fixture {
    root: PathBuf,
    runtime: Runtime,
    app: Router,
}

impl Fixture {
    fn new(messages: Vec<Value>, pending_permission: Option<&str>) -> Self {
        Self::with_routes(messages, pending_permission, router)
    }

    fn with_routes(
        messages: Vec<Value>,
        pending_permission: Option<&str>,
        routes: impl FnOnce(WebStatusConfig) -> Router,
    ) -> Self {
        let root = std::env::temp_dir().join(format!("astrion-web-status-{}", Uuid::new_v4()));
        let project = root.join("project");
        fs::create_dir_all(&project).unwrap();
        fs::write(project.join("index.html"), "synthetic HTML").unwrap();
        let data = root.join("data");
        let conversations = data.join("conversations").join(WORKSPACE);
        fs::create_dir_all(&conversations).unwrap();
        let record = json!({
            "id": ID, "title": "Existing Host chat", "created_at":"2024-09-05T11:22:33.019", "updated_at":"2024-09-05T11:22:33.019",
            "messages": messages,
            "metadata": {
                "title_locked": true,
                "work_mode": "ask", "permission_mode": "approval", "execution_mode": "direct", "network_permission": "full",
                "run_mode": "deep", "thinking_mode": true, "reasoning_effort": "high", "model_key": "test-model", "chatgpt_fast_mode": true,
                "context_baseline_tokens": 900, "compress_limit_tokens": 12000, "compress_limit_source": "custom",
                "pending_permission_mode": pending_permission,
                "edited_files": [{"path":"index.html"}, {"path":"../data/personalization.json"}],
                "preview_targets": [{"type":"file", "path":"index.html"}, {"type":"server", "url":"http://0.0.0.0:43210/index.html"}, {"type":"server", "url":"https://external.invalid/"}],
                "runtime_message_queue": {"pending":[{"id":"saved","text":"saved draft"}],"guidance":["saved guidance"],"paused":true},
                "frozen_system_prompt": "synthetic-private-policy",
            },
            "token_statistics": {"current_context_tokens":1234}, "todo_list":[{"text":"remember"}],
        });
        crate::conversation_db::Database::open(&conversations).unwrap().save(&record,&json!({"title":"Existing Host chat"})).unwrap();
        fs::write(data.join("personalization.json"), serde_json::to_vec(&json!({
            "default_model": "test-model", "default_run_mode": "fast", "default_reasoning_effort": "low",
            "default_work_mode":"plan", "default_permission_mode":"approval", "git_worktree_enabled":true,
            "browser_tools_default":true, "tavily_api_key":"synthetic-private-key"
        })).unwrap()).unwrap();
        fs::write(
            data.join("memory.md"),
            "# Memory\n\n1. First\n2. Second\nplain\n",
        )
        .unwrap();
        fs::write(data.join("admin_policy.json"), serde_json::to_vec(&json!({
            "updated_at":"synthetic-version",
            "global":{"ui_blocks":{"block_upload":true,"block_realtime_terminal":true,"unknown":true}},
            "roles":{"admin":{"ui_blocks":{"block_upload":false},"forced_category_states":{"toolbox":true}}},
            "users":{"host":{"ui_blocks":{"block_focus_panel":true}}}
        })).unwrap()).unwrap();
        let runtime = Runtime::open(&data).unwrap();
        runtime.bind_workspace(WORKSPACE, &project).unwrap();
        let catalog = CatalogSource::with_paths(&root, &root.join("config"), &data);
        let auth = WebAuth::new(WebAuthConfig::new(&data, PORT)).unwrap();
        let routes = routes(WebStatusConfig {
            runtime: runtime.clone(),
            catalog,
            data_dir: data,
            workspace_id: WORKSPACE.to_owned(),
            project_path: project,
            version: "test-version".to_owned(),
        });
        let app = auth.router().merge(auth.protect_api(routes));
        Self { root, runtime, app }
    }

    async fn request(
        &self,
        method: Method,
        uri: &str,
        headers: &[(&str, &str)],
        body: impl Into<Body>,
    ) -> Response {
        let mut request = Request::builder()
            .method(method)
            .uri(uri)
            .header(header::HOST, HOST)
            .extension(ConnectInfo::<SocketAddr>(
                "127.0.0.1:41564".parse().unwrap(),
            ));
        for (name, value) in headers {
            request = request.header(*name, *value);
        }
        self.app
            .clone()
            .oneshot(request.body(body.into()).unwrap())
            .await
            .unwrap()
    }

    async fn login(&self) -> (String, String) {
        let challenge = self
            .request(Method::GET, "/api/csrf-token", &[], Body::empty())
            .await;
        let cookie = cookie_pair(&challenge);
        let token = contract_json(challenge).await["token"]
            .as_str()
            .unwrap()
            .to_owned();
        let login = self
            .request(
                Method::POST,
                "/host-login",
                &[
                    ("cookie", &cookie),
                    ("x-csrf-token", &token),
                    ("content-type", "application/json"),
                ],
                EMPTY_JSON,
            )
            .await;
        assert_eq!(login.status(), StatusCode::OK);
        let cookie = cookie_pair(&login);
        let challenge = self
            .request(
                Method::GET,
                "/api/csrf-token",
                &[("cookie", &cookie)],
                Body::empty(),
            )
            .await;
        let csrf = contract_json(challenge).await["token"]
            .as_str()
            .unwrap()
            .to_owned();
        (cookie, csrf)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn cookie_pair(response: &Response) -> String {
    response.headers()[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned()
}

async fn contract_json(response: Response) -> Value {
    let body = to_bytes(response.into_body(), 16 * 1024 * 1024)
        .await
        .unwrap();
    serde_json::from_slice(&body).unwrap()
}

fn messages(count: usize) -> Vec<Value> {
    (0..count)
        .map(|index| {
            json!({"role":if index % 2 == 0 { "user" } else { "assistant" },
                "message_id": format!("msg_{index}"), "content": format!("Stored message {index}"),
            })
        })
        .collect()
}

#[tokio::test]
async fn authenticated_status_uses_workspace_policy_and_stored_defaults() {
    let fixture = Fixture::new(messages(2), Some("readonly"));
    let rejected = fixture
        .request(
            Method::GET,
            "/api/status",
            &[("authorization", "Bearer synthetic")],
            Body::empty(),
        )
        .await;
    assert_eq!(rejected.status(), StatusCode::UNAUTHORIZED);
    let (cookie, csrf) = fixture.login().await;
    let headers = [("cookie", cookie.as_str())];
    let status = contract_json(
        fixture
            .request(Method::GET, "/api/status", &headers, Body::empty())
            .await,
    )
    .await;
    assert_eq!(
        status["container"],
        json!({"success":true,"mode":"host","workspace_id":WORKSPACE})
    );
    assert_eq!(status["version"], "test-version");
    assert_eq!(status["run_mode"], "fast");
    assert_eq!(status["work_mode"], "plan");
    assert_eq!(status["permission_mode"], "readonly");
    assert_eq!(status["model_key"], "test-model");
    assert_eq!(status["memory"]["main"], 2);
    assert_eq!(
        status["admin_policy"]["ui_blocks"],
        json!({"block_upload":false,"block_realtime_terminal":true,"block_focus_panel":true})
    );
    assert_eq!(status["conversation"]["total_conversations"], 1);
    let response = fixture
        .request(
            Method::GET,
            "/api/workspace-view?workspace_id=another",
            &headers,
            Body::empty(),
        )
        .await;
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let view = contract_json(
        fixture
            .request(Method::GET, "/api/workspace-view", &headers, Body::empty())
            .await,
    )
    .await;
    assert_eq!(view["data"]["workspace"]["workspace_id"], WORKSPACE);
    assert_eq!(view["data"]["draft_owner"], "4740ae6347b0172c01254ff5");
    assert_eq!(view["data"]["active_conversation_id"], Value::Null);

    let response = fixture
        .request(
            Method::POST,
            "/api/new-conversation/settings",
            &headers,
            EMPTY_JSON,
        )
        .await;
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let settings = contract_json(
        fixture
            .request(
                Method::POST,
                "/api/new-conversation/settings",
                &[
                    ("cookie", &cookie),
                    ("x-csrf-token", &csrf),
                    ("x-astrion-workspace-id", WORKSPACE),
                    ("content-type", "application/json"),
                ],
                json!({"workspace_id":WORKSPACE,"previous_conversation_id":ID}).to_string(),
            )
            .await,
    )
    .await;
    let values = &settings["data"]["settings"];
    assert_eq!(values["work_mode"], "plan");
    assert_eq!(values["permission_mode"], "readonly");
    assert_eq!(values["execution_mode"], "sandbox");
    assert_eq!(values["network_permission"], "full");
    assert_eq!(values["chatgpt_fast_mode"], true);
    assert_eq!(values["browser_tools"], true);
    assert_eq!(values["restore_modes"]["pre_plan_execution_mode"], "direct");
    assert!(!settings.to_string().contains("synthetic-private-key"));
}

#[tokio::test]
async fn scoped_settings_use_registered_workspace_and_persist_only_its_conversation() {
    const TARGET: &str = "dynamic-workspace";
    let fixture = Fixture::with_routes(messages(2), None, |config| {
        let path = config.project_path.with_file_name("other-project");
        fs::create_dir_all(&path).unwrap();
        config.runtime.bind_workspace(TARGET, &path).unwrap();
        let legacy_dir = config.data_dir.join("conversations");
        fs::create_dir_all(legacy_dir.join(TARGET)).unwrap();
        let source = crate::conversation_db::Database::open(&legacy_dir.join(WORKSPACE)).unwrap();
        let record = source.read(ID).unwrap().unwrap();
        crate::conversation_db::Database::open(&legacy_dir.join(TARGET)).unwrap().save(&record,&source.summary(ID).unwrap().unwrap()).unwrap();
        let workspace = RegisteredWorkspace {
            id: TARGET.into(),
            label: "Other project".into(),
            path,
        };
        scoped_router(config).layer(Extension(workspace))
    });
    let (cookie, csrf) = fixture.login().await;
    let headers = [
        ("cookie", cookie.as_str()),
        ("x-csrf-token", csrf.as_str()),
        ("content-type", "application/json"),
    ];
    let view = contract_json(
        fixture
            .request(Method::GET, "/api/workspace-view", &headers, Body::empty())
            .await,
    )
    .await;
    assert_eq!(view["data"]["workspace"]["workspace_id"], TARGET);
    assert!(
        view["data"]["workspace"]["project_path"]
            .as_str()
            .unwrap()
            .ends_with("other-project")
    );
    let work_mode = |workspace_id: &str| {
        json!({"workspace_id": workspace_id, "conversation_id": ID, "mode": "plan"}).to_string()
    };
    let rejected = fixture
        .request(
            Method::POST,
            "/api/work-mode",
            &headers,
            work_mode(WORKSPACE),
        )
        .await;
    assert_eq!(rejected.status(), StatusCode::NOT_FOUND);
    let applied = contract_json(
        fixture
            .request(Method::POST, "/api/work-mode", &headers, work_mode(TARGET))
            .await,
    )
    .await;
    assert_eq!(applied["mode"], "plan");
    assert_eq!(applied["permission_mode"], "readonly");
    assert_eq!(applied["execution_mode"], "sandbox");
    let compressed = contract_json(
        fixture
            .request(
                Method::POST,
                "/api/conversation/compress-limit",
                &headers,
                json!({"conversation_id": ID, "tokens": 48_000}).to_string(),
            )
            .await,
    )
    .await;
    assert_eq!(
        compressed["data"],
        json!({"compress_limit_tokens":48000,"compress_limit_source":"custom"})
    );
    let original = fixture.runtime.web_metadata(WORKSPACE, ID).unwrap();
    let target = fixture.runtime.web_metadata(TARGET, ID).unwrap();
    assert_eq!(original["work_mode"], "ask");
    assert_eq!(original["compress_limit_tokens"], 12000);
    assert_eq!(target["work_mode"], "plan");
    assert_eq!(target["compress_limit_tokens"], 48000);
}

#[tokio::test]
async fn conversation_bootstrap_projects_disk_data_and_workspace_scope() {
    let fixture = Fixture::new(messages(2), Some("readonly"));
    let (cookie, _) = fixture.login().await;
    let headers = [("cookie", cookie.as_str())];
    let prefixless = ID.strip_prefix("conv_").unwrap();
    let metadata = contract_json(
        fixture
            .request(
                Method::GET,
                &format!("/api/conversations/{prefixless}/metadata"),
                &headers,
                Body::empty(),
            )
            .await,
    )
    .await;
    assert_eq!(
        metadata["data"],
        json!({"id":ID,"workspace_id":WORKSPACE,"title":"Existing Host chat","title_locked":true,"title_pending":false})
    );
    let response = fixture
        .request(
            Method::GET,
            "/api/conversations/absent/bootstrap",
            &headers,
            Body::empty(),
        )
        .await;
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let response = fixture
        .request(
            Method::GET,
            "/api/conversations/%2E%2E%2Fescape/bootstrap",
            &headers,
            Body::empty(),
        )
        .await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let route = format!("/api/conversations/{ID}/bootstrap");
    let response = fixture
        .request(
            Method::GET,
            &route,
            &[("cookie", &cookie), ("x-astrion-workspace-id", "other")],
            Body::empty(),
        )
        .await;
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let result = contract_json(
        fixture
            .request(Method::GET, &route, &headers, Body::empty())
            .await,
    )
    .await;
    assert!(!result.to_string().contains("synthetic-private-policy"));
    let data = &result["data"];
    assert_eq!(data["messages"].as_array().unwrap().len(), 2);
    assert_eq!(data["meta"]["run_mode"], "thinking");
    assert_eq!(
        data["meta"]["pending_runtime_modes"]["permission_mode"],
        "readonly"
    );
    assert_eq!(data["current_context_tokens"], 1234);
    assert_eq!(data["context_baseline_tokens"], 900);
    assert_eq!(data["compress_limit_source"], "custom");
    assert_eq!(
        data["runtime_queue"]["messages"][1]["id"],
        "b721549d-9067-51b9-941a-b0234b3209d6"
    );
    assert_eq!(data["runtime_queue"]["paused"], true);
    assert_eq!(
        data["edited_files"],
        json!([{"path":"index.html","op":"edit","ts":""}])
    );
    assert_eq!(data["preview_targets"].as_array().unwrap().len(), 2);
    assert_eq!(
        data["preview_targets"][1]["url"],
        "http://127.0.0.1:43210/index.html"
    );
    assert_eq!(data["display"]["next_event_idx"], 0);
    assert_eq!(data["display"]["task"], Value::Null);
    assert_eq!(data["running"]["is_main_running"], false);
}

#[tokio::test]
async fn very_long_active_stream_has_full_projection_and_exact_next_cursor() {
    const HISTORY: usize = 700;
    const CHUNKS: usize = 20_050;
    let fixture = Fixture::new(messages(HISTORY), None);
    let (cookie, _) = fixture.login().await;
    let headers = [("cookie", cookie.as_str())];
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "http://{}/v1/chat/completions",
        listener.local_addr().unwrap()
    );
    let (release, done) = oneshot::channel();
    let done = Arc::new(std::sync::Mutex::new(Some(done)));
    let fake_model = Router::new().route("/v1/chat/completions", post(move || {
        let done = done.lock().unwrap().take().unwrap();
        async move {
            let first_chunk = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"a\"},\"finish_reason\":null}]}\n\n".repeat(CHUNKS);
            let prefix = stream::iter([Ok::<_, std::io::Error>(Bytes::from(first_chunk))]);
            let end = stream::once(async move {
                done.await.unwrap();
                Ok(Bytes::from_static(b"data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"z\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n"))
            });
            ([(header::CONTENT_TYPE, "text/event-stream")], Body::from_stream(prefix.chain(end)))
        }
    }));
    let upstream = tokio::spawn(async move {
        axum::serve(listener, fake_model).await.unwrap();
    });
    let accepted = fixture
        .runtime
        .create_task(
            WORKSPACE,
            &json!({"conversation_id":ID,"message":"active user"}),
            ModelConfig::fixed(endpoint, None, "test-model".into()),
        )
        .unwrap();
    let task_id = accepted["data"]["task_id"].as_str().unwrap();
    let cursor = tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            let poll = fixture
                .runtime
                .poll_task(WORKSPACE, task_id, u64::MAX)
                .unwrap();
            let cursor = poll["data"]["next_offset"].as_u64().unwrap();
            assert_eq!(poll["data"]["status"], "running");
            if cursor >= CHUNKS as u64 + 2 {
                assert!(poll["data"]["window_start"].as_u64().unwrap() > 0);
                break cursor;
            }
            tokio::time::sleep(Duration::from_millis(15)).await;
        }
    })
    .await
    .unwrap();
    let view = contract_json(
        fixture
            .request(Method::GET, "/api/workspace-view", &headers, Body::empty())
            .await,
    )
    .await;
    assert_eq!(view["data"]["active_conversation_id"], ID);
    let route = format!("/api/conversations/{ID}/bootstrap");
    let snapshot = contract_json(
        fixture
            .request(Method::GET, &route, &headers, Body::empty())
            .await,
    )
    .await;
    let data = &snapshot["data"];
    assert_eq!(data["messages"].as_array().unwrap().len(), HISTORY + 1);
    assert_eq!(data["messages"][HISTORY]["content"], "active user");
    assert_eq!(data["display"]["task"]["task_id"], task_id);
    assert_eq!(data["display"]["next_event_idx"], cursor);
    assert_eq!(
        data["display"]["messages"][0]["actions"][0]["content"],
        "a".repeat(CHUNKS)
    );
    assert_eq!(data["display"]["state"]["api_request_pending"], false);
    assert_eq!(
        data["runtime_queue"]["messages"].as_array().unwrap().len(),
        1
    );
    release.send(()).unwrap();
    tokio::time::timeout(Duration::from_secs(6), async {
        loop {
            let poll = fixture
                .runtime
                .poll_task(WORKSPACE, task_id, cursor)
                .unwrap();
            if poll["data"]["status"] == "succeeded" {
                let events = poll["data"]["events"].as_array().unwrap();
                assert_eq!(events[0]["idx"], cursor);
                assert_eq!(events[0]["data"]["content"], "z");
                break;
            }
            tokio::time::sleep(Duration::from_millis(15)).await;
        }
    })
    .await
    .unwrap();
    let snapshot = contract_json(
        fixture
            .request(Method::GET, &route, &headers, Body::empty())
            .await,
    )
    .await;
    let data = &snapshot["data"];
    assert_eq!(data["messages"].as_array().unwrap().len(), HISTORY + 2);
    assert_eq!(
        data["messages"][HISTORY + 1]["content"],
        format!("{}z", "a".repeat(CHUNKS))
    );
    assert_eq!(data["display"]["messages"], json!([]));
    upstream.abort();
}
