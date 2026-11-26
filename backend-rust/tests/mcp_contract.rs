use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use astrion_headless::{
    mcp::{LIST_TOOL, Registry, Runtime, RuntimeScope, model_content, router_with_registry},
    workspace_catalog::{RegisteredWorkspace, manage::WorkspaceRegistry},
};
use axum::{
    Json, Router,
    body::{Body, to_bytes},
    extract::State,
    http::{HeaderMap, Request, StatusCode},
    response::{IntoResponse, Response},
    routing::post,
};
use serde_json::{Value, json};
use tower::ServiceExt;
use uuid::Uuid;

struct Fixture {
    root: PathBuf,
    data: PathBuf,
    workspace: RegisteredWorkspace,
    registry: Registry,
    app: Router,
}

impl Fixture {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!("mcp-test-{}", Uuid::new_v4()));
        let data = root.join("data");
        let project = root.join("work");
        fs::create_dir_all(&data).unwrap();
        fs::create_dir_all(&project).unwrap();
        let workspace = RegisteredWorkspace {
            id: "work".into(),
            label: "Local fixture".into(),
            path: project.canonicalize().unwrap(),
        };
        let workspace_config = root.join("workspaces.json");
        fs::write(&workspace_config, json!({"workspaces": [{"workspace_id": workspace.id, "label": workspace.label, "path": workspace.path}],
            "default_workspace_id": workspace.id}).to_string()).unwrap();
        let registry = Registry::new(data.join("mcp_servers.json"), true);
        let app = router_with_registry(
            data.clone(),
            WorkspaceRegistry::new(workspace_config, root.clone()),
            registry.clone(),
        );
        Self {
            root,
            data,
            workspace,
            registry,
            app,
        }
    }

    async fn request(&self, method: &str, path: &str, body: Value, expected: StatusCode) -> Value {
        let request = Request::builder()
            .method(method)
            .uri(format!("/api/mcp{path}"))
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap();
        let response = self.app.clone().oneshot(request).await.unwrap();
        let status = response.status();
        let bytes = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
        assert_eq!(
            status,
            expected,
            "{} {} returned {}",
            method,
            path,
            String::from_utf8_lossy(&bytes)
        );
        serde_json::from_slice(&bytes).unwrap()
    }

    fn main(&self, name: &str) -> Runtime {
        Runtime::new(
            self.registry.clone(),
            RuntimeScope::main(&self.workspace, &self.data, name).unwrap(),
        )
    }

    fn child(&self, name: &str, cwd: &Path) -> Runtime {
        Runtime::new(
            self.registry.clone(),
            RuntimeScope::child(&self.workspace, &self.data, "conversation", name, cwd).unwrap(),
        )
    }

    async fn create_stdio(&self) -> Value {
        let python = std::env::var("ASTRION_MCP_TEST_PYTHON")
            .unwrap_or_else(|_| if cfg!(windows) { "python" } else { "python3" }.into());
        let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/mcp_mock.py");
        self.request("POST", "/servers", json!({"id": "local", "transport": "stdio", "command": python, "args": ["-B", script, "@playwright/mcp"],
            "env": {"PLAYWRIGHT_MCP_CONFIG":"", "PLAYWRIGHT_MCP_ISOLATED":"0", "PLAYWRIGHT_MCP_USER_DATA_DIR":"",
                "PLAYWRIGHT_MCP_CDP_ENDPOINT":"", "PLAYWRIGHT_MCP_ENDPOINT":"", "PLAYWRIGHT_MCP_EXTENSION":"0"},
            "exclude_tools": ["hidden"], "tools_cache": [{"name":"forged-cache"}]}), StatusCode::OK).await
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[tokio::test]
async fn management_and_stdio_calls_obey_role_lists_and_scope() {
    let fixture = Fixture::new();
    let created = fixture.create_stdio().await;
    assert_eq!(created["data"]["tools_cache_count"], 0);
    fs::write(
        fixture.data.join("personalization.json"),
        r#"{"disabled_tool_categories":["mcp"]}"#,
    )
    .unwrap();
    let initial = fixture
        .request("GET", "/settings", Value::Null, StatusCode::OK)
        .await;
    assert_eq!(initial["data"]["defaults"], json!({"main": [], "sub": []}));
    assert_eq!(initial["data"]["default_workspace_id"], "work");
    fixture
        .request(
            "PUT",
            "/access/main",
            json!({"server_ids": ["local", "unknown", "local"]}),
            StatusCode::OK,
        )
        .await;
    fixture
        .request(
            "PUT",
            "/access/sub",
            json!({"server_ids": ["local"]}),
            StatusCode::OK,
        )
        .await;
    fixture
        .request(
            "PUT",
            "/access/sub/work",
            json!({"inherit":false,"server_ids":[]}),
            StatusCode::OK,
        )
        .await;
    fixture
        .request(
            "PUT",
            "/access/main/missing",
            json!({"inherit":false,"server_ids":[]}),
            StatusCode::NOT_FOUND,
        )
        .await;
    fixture
        .request(
            "PUT",
            "/access/unknown",
            json!({"server_ids":["local"]}),
            StatusCode::NOT_FOUND,
        )
        .await;
    fixture
        .request(
            "PUT",
            "/access/main",
            json!({"server_ids":[false]}),
            StatusCode::BAD_REQUEST,
        )
        .await;
    fixture
        .request("POST", "/sync", json!({"id":"local"}), StatusCode::OK)
        .await;
    assert!(!fixture.data.join("mcp_profiles").exists());
    let settings = fixture
        .request("GET", "/settings", Value::Null, StatusCode::OK)
        .await;
    assert_eq!(settings["data"]["servers"][0]["tools_cache_count"], 2);
    assert!(settings["data"]["servers"][0].get("env").is_none());
    assert_eq!(settings["data"]["defaults"]["main"], json!(["local"]));
    let detail = fixture
        .request("GET", "/servers/local", Value::Null, StatusCode::OK)
        .await;
    assert!(detail["data"].get("tools_cache").is_none());
    assert!(detail["data"]["env"].is_object());

    let main = fixture.main("conversation");
    let cwd = fixture.workspace.path.join("child");
    fs::create_dir(&cwd).unwrap();
    let child = fixture.child("child-a", &cwd);
    let other_child = fixture.child("child-b", &cwd);
    let snapshot = main.prepare_tools().await;
    let alias = "mcp__local__echo_synthetic";
    assert!(snapshot.allows(alias) && snapshot.allows(LIST_TOOL));
    assert!(!snapshot.allows("mcp__local__hidden"));
    assert!(!child.prepare_tools().await.allows(alias));
    fixture
        .request(
            "PUT",
            "/access/sub/work",
            json!({"inherit":true,"server_ids":[]}),
            StatusCode::OK,
        )
        .await;
    let child_snapshot = child.prepare_tools().await;
    assert!(child_snapshot.allows(alias));
    let listed = main.execute(&snapshot, LIST_TOOL, json!({})).await;
    assert_eq!(listed["servers"][0]["tool_aliases"], json!([alias]));
    let called = main
        .execute(
            &snapshot,
            alias,
            json!({"text":"本地消息", "intent":"synthetic"}),
        )
        .await;
    assert_eq!(called["success"], true, "{called}");
    assert_eq!(
        called["message"].as_str().unwrap().lines().next(),
        Some("本地消息")
    );
    assert_eq!(called["structured_content"]["calls"], 1);
    assert_eq!(model_content(&called, true).as_array().unwrap().len(), 3);
    assert_eq!(model_content(&called, false).as_array().unwrap().len(), 2);
    let called_again = main
        .execute(&snapshot, alias, json!({"text":"reuse"}))
        .await;
    assert_eq!(called_again["structured_content"]["calls"], 2);
    let child_call = child
        .execute(&child_snapshot, alias, json!({"text":"child"}))
        .await;
    assert_eq!(
        Path::new(child_call["structured_content"]["cwd"].as_str().unwrap())
            .canonicalize()
            .unwrap(),
        cwd.canonicalize().unwrap()
    );
    let other_snapshot = other_child.prepare_tools().await;
    let other_call = other_child
        .execute(&other_snapshot, alias, json!({"text":"sibling"}))
        .await;
    let main_profile = profile(&called);
    let first_profile = profile(&child_call);
    let second_profile = profile(&other_call);
    assert_ne!(main_profile, first_profile);
    assert_ne!(first_profile, second_profile);
    assert_ne!(main_profile, second_profile);
    assert!(Path::new(main_profile).is_dir());
    assert!(Path::new(first_profile).is_dir());
    assert!(Path::new(second_profile).is_dir());
    let reopened = fixture.child("child-a", &cwd);
    let reopened_snapshot = reopened.prepare_tools().await;
    let reopened_call = reopened
        .execute(&reopened_snapshot, alias, json!({"text":"same child"}))
        .await;
    assert_eq!(profile(&reopened_call), first_profile);
    reopened.close().await;

    main.set_category_overrides(BTreeMap::from([("mcp".into(), false)]))
        .await;
    assert_eq!(
        main.execute(&snapshot, alias, json!({"text":"denied"}))
            .await["success"],
        false
    );
    assert!(child.prepare_tools().await.allows(alias));
    main.set_category_overrides(BTreeMap::new()).await;
    fixture
        .request(
            "PUT",
            "/servers/local",
            json!({"enabled":false, "id":"forged"}),
            StatusCode::OK,
        )
        .await;
    assert_eq!(
        child
            .execute(&child_snapshot, alias, json!({"text":"disabled"}))
            .await["success"],
        false
    );
    fixture
        .request(
            "POST",
            "/sync",
            json!({"id":"local"}),
            StatusCode::BAD_GATEWAY,
        )
        .await;
    fixture
        .request("DELETE", "/servers/local", Value::Null, StatusCode::OK)
        .await;
    fixture
        .request("GET", "/servers/local", Value::Null, StatusCode::NOT_FOUND)
        .await;
    let deleted = fixture
        .request("GET", "/settings", Value::Null, StatusCode::OK)
        .await;
    assert_eq!(deleted["data"]["defaults"], json!({"main": [], "sub": []}));
    assert_eq!(
        deleted["data"]["workspaces"][0]["access"]["sub"],
        json!({"inherit":true,"server_ids":[]})
    );
    main.close().await;
    child.close().await;
    other_child.close().await;
}

fn profile(call: &Value) -> &str {
    let args = call["structured_content"]["launch"]
        .as_array()
        .unwrap_or_else(|| panic!("{call}"));
    assert_eq!(args[1], "--user-data-dir");
    args[2].as_str().unwrap()
}

#[derive(Clone, Default)]
struct HttpState(Arc<Mutex<HttpCounters>>);

#[derive(Default)]
struct HttpCounters {
    next_session: u32,
    initialized: Vec<String>,
    closed: Vec<String>,
}

async fn http_mcp(
    State(state): State<HttpState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    let method = body["method"].as_str().unwrap();
    let result = |value| json!({"jsonrpc":"2.0", "id":body["id"], "result": value});
    assert_eq!(headers["x-test-only"], "synthetic");
    assert_eq!(headers["mcp-method"], method);
    let mut state = state.0.lock().unwrap();
    if method == "initialize" {
        assert_eq!(body["params"]["protocolVersion"], "2025-06-18");
        state.next_session += 1;
        return (
            [("mcp-session-id", format!("local-{}", state.next_session))],
            Json(result(json!({"protocolVersion":"2025-03-26"}))),
        )
            .into_response();
    }
    let session = headers["mcp-session-id"].to_str().unwrap();
    assert_eq!(headers["mcp-protocol-version"], "2025-03-26");
    if method == "notifications/initialized" {
        state.initialized.push(session.to_owned());
        return StatusCode::ACCEPTED.into_response();
    }
    assert!(state.initialized.iter().any(|id| id == session));
    match method {
        "tools/list" => Json(result(
            json!({"tools":[{"name":"echo", "inputSchema":{"type":"object"}}]}),
        ))
        .into_response(),
        "tools/call" if session == "local-2" => StatusCode::NOT_FOUND.into_response(),
        "tools/call" => {
            assert_eq!(headers["mcp-name"], "echo");
            let value = result(
                json!({"content":[{"type":"text","text":body["params"]["arguments"]["text"]}],"structuredContent":{"session":session}}),
            );
            (
                [("content-type", "text/event-stream")],
                format!("event: message\r\ndata: {value}\r\n\r\n"),
            )
                .into_response()
        }
        _ => StatusCode::BAD_REQUEST.into_response(),
    }
}

async fn close_http(State(state): State<HttpState>, headers: HeaderMap) -> StatusCode {
    state
        .0
        .lock()
        .unwrap()
        .closed
        .push(headers["mcp-session-id"].to_str().unwrap().into());
    StatusCode::NO_CONTENT
}

#[tokio::test]
async fn streamable_http_negotiates_session_sse_calls_and_recovers_expiry() {
    let fixture = Fixture::new();
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))
        .await
        .unwrap();
    let url = format!("http://{}/mcp", listener.local_addr().unwrap());
    let state = HttpState::default();
    let server = Router::new()
        .route("/mcp", post(http_mcp).delete(close_http))
        .with_state(state.clone());
    let serving = tokio::spawn(async move { axum::serve(listener, server).await.unwrap() });
    let created = fixture
        .request(
            "POST",
            "/servers",
            json!({"id":"http_test","transport":"http", "url":url,
        "headers":{"x-test-only":"synthetic"}}),
            StatusCode::OK,
        )
        .await;
    assert_eq!(created["data"]["transport"], "streamable_http");
    fixture
        .request("POST", "/sync", json!({}), StatusCode::OK)
        .await;
    let main = fixture.main("http-conversation");
    let tools = main.prepare_tools().await;
    let called = main
        .execute(
            &tools,
            "mcp__http_test__echo",
            json!({"text":"loopback only"}),
        )
        .await;
    assert_eq!(called["success"], true, "{called}");
    assert_eq!(called["message"], "loopback only");
    assert_eq!(called["structured_content"]["session"], "local-3");
    main.close().await;
    assert_eq!(state.0.lock().unwrap().closed, ["local-1", "local-3"]);
    serving.abort();
}
