use super::*;

use crate::{
    runtime::ModelConfig,
    web_auth::{WebAuth, WebAuthConfig},
};
use axum::http::{Method, header};
use reqwest::{Client, Response};
use std::{path::Path, sync::Mutex, time::Duration};
use tokio::{net::TcpListener, sync::Notify, task::JoinHandle};
use uuid::Uuid;

const TABS: &str = "/api/conversation-tabs";
const COMMANDS: &str = "/api/conversation-tabs/commands";
const ACTIVITY: &str = "/api/conversation-tabs/activity";

struct Fixture {
    root: PathBuf,
    runtime: Runtime,
    workspaces: Arc<Mutex<Vec<NavigationWorkspace>>>,
    pending: Arc<Mutex<Vec<(String, String, String, String, PendingNavigationKind)>>>,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("astrion-navigation-{}", Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        let runtime = Runtime::open(root.join("data")).unwrap();
        let workspaces = ["ws_a", "ws_b"]
            .into_iter()
            .map(|id| {
                let path = root.join(id);
                fs::create_dir(&path).unwrap();
                runtime.bind_workspace(id, &path).unwrap();
                NavigationWorkspace {
                    id: id.into(),
                    label: format!("Label {id}"),
                    path,
                }
            })
            .collect();
        Self {
            root,
            runtime,
            workspaces: Arc::new(Mutex::new(workspaces)),
            pending: Arc::default(),
        }
    }

    fn data(&self) -> PathBuf {
        self.root.join("data")
    }

    fn state_file(&self) -> PathBuf {
        self.data().join("conversation_tabs.json")
    }

    fn saved(&self) -> Value {
        serde_json::from_slice(&fs::read(self.state_file()).unwrap()).unwrap()
    }

    fn indexed_agent(&self, workspace: &str, id: &str, title: &str) -> PathBuf {
        self.indexed(self.data().join("conversations").join(workspace), id, title)
    }

    fn indexed_chat(&self, id: &str, title: &str) -> PathBuf {
        self.indexed(self.data().join("chat").join("conversations"), id, title)
    }

    fn indexed(&self, directory: PathBuf, id: &str, title: &str) -> PathBuf {
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join(format!("{id}.json"));
        // Deliberately unreadable as a history document. Navigation needs only stat and index.
        fs::write(&path, b"history is not read by desktop navigation").unwrap();
        let mut index = serde_json::from_slice::<Value>(
            &fs::read(directory.join("index.json")).unwrap_or_default(),
        )
        .unwrap_or_else(|_| json!({}));
        index[id] = json!({"title": title});
        write_json(directory.join("index.json"), &index);
        path
    }

    async fn site(&self) -> Site {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let auth = WebAuth::new(WebAuthConfig::new(self.data(), addr.port())).unwrap();
        let navigation = Navigation::new(self.data());
        let workspaces = self.workspaces.clone();
        let pending = self.pending.clone();
        let protected = navigation.host_router_with_pending(
            self.runtime.clone(),
            move || Ok(workspaces.lock().unwrap().clone()),
            move || {
                pending
                    .lock()
                    .unwrap()
                    .iter()
                    .map(
                        |(task, workspace, id, request, kind)| PendingNavigationRequest {
                            task_id: task.clone(),
                            workspace_id: workspace.clone(),
                            conversation_id: id.clone(),
                            request_id: request.clone(),
                            kind: *kind,
                        },
                    )
                    .collect()
            },
        );
        let app = auth.router().merge(auth.protect_api(protected));
        let server = tokio::spawn(async move {
            axum::serve(
                listener,
                app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
            )
            .await
            .unwrap();
        });
        let mut site = Site {
            client: Client::builder().no_proxy().build().unwrap(),
            origin: format!("http://{addr}"),
            cookie: String::new(),
            csrf: String::new(),
            server,
            navigation,
        };
        let challenge = site
            .client
            .get(format!("{}/api/csrf-token", site.origin))
            .send()
            .await
            .unwrap();
        let cookie = cookie(&challenge);
        let token = value(challenge, 200).await;
        let login = site
            .client
            .post(format!("{}/host-login", site.origin))
            .header(header::ORIGIN, &site.origin)
            .header(header::COOKIE, cookie)
            .header("x-csrf-token", token["token"].as_str().unwrap())
            .json(&json!({"password":""}))
            .send()
            .await
            .unwrap();
        assert_eq!(login.status().as_u16(), 200);
        site.cookie = self::cookie(&login);
        let challenge = site.get("/api/csrf-token").await;
        site.csrf = value(challenge, 200).await["token"]
            .as_str()
            .unwrap()
            .into();
        site
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

struct Site {
    client: Client,
    origin: String,
    cookie: String,
    csrf: String,
    server: JoinHandle<()>,
    navigation: Navigation,
}

impl Drop for Site {
    fn drop(&mut self) {
        self.server.abort();
    }
}

impl Site {
    fn request(&self, method: Method, route: &str) -> reqwest::RequestBuilder {
        self.client
            .request(method, format!("{}{route}", self.origin))
            .header(header::COOKIE, &self.cookie)
            .header(header::ORIGIN, &self.origin)
    }

    async fn get(&self, route: &str) -> Response {
        self.request(Method::GET, route).send().await.unwrap()
    }

    async fn snapshot(&self) -> Value {
        value(self.get(TABS).await, 200).await
    }

    async fn send(&self, id: &str, action: &str, payload: &Value) -> Response {
        self.request(Method::POST, COMMANDS)
            .header("x-csrf-token", &self.csrf)
            .json(&json!({"command_id": id, "action": action, "payload": payload}))
            .send()
            .await
            .unwrap()
    }

    async fn command(&self, action: &str, payload: Value) -> Value {
        value(
            self.send(&Uuid::new_v4().to_string(), action, &payload)
                .await,
            200,
        )
        .await
    }

    async fn conflict(&self, action: &str, payload: Value) -> Value {
        let body = value(
            self.send(&Uuid::new_v4().to_string(), action, &payload)
                .await,
            409,
        )
        .await;
        assert_eq!(body["code"], "navigation_superseded");
        body
    }
}

fn cookie(response: &Response) -> String {
    response.headers()[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .into()
}

async fn value(response: Response, expected: u16) -> Value {
    let status = response.status().as_u16();
    let body = response.text().await.unwrap();
    assert_eq!(status, expected, "{body}");
    serde_json::from_str(&body).unwrap()
}

fn write_json(path: impl AsRef<Path>, document: &Value) {
    fs::write(path, serde_json::to_vec(document).unwrap()).unwrap();
}

fn tab<'a>(state: &'a Value, key: &str) -> &'a Value {
    state["tabs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tab| tab["key"] == key)
        .unwrap()
}

fn keys(state: &Value, main_only: bool) -> Vec<&str> {
    state["tabs"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|tab| !main_only || tab["placement"] == "main")
        .map(|tab| tab["key"].as_str().unwrap())
        .collect()
}

#[tokio::test]
async fn host_login_csrf_and_no_workspace_match_frontend_contract() {
    let fixture = Fixture::new();
    fixture.workspaces.lock().unwrap().clear();
    let site = fixture.site().await;
    let denied = site
        .client
        .get(format!("{}{TABS}", site.origin))
        .send()
        .await
        .unwrap();
    value(denied, 401).await;
    let denied = site
        .request(Method::POST, COMMANDS)
        .json(&json!({"action":"create"}))
        .send()
        .await
        .unwrap();
    value(denied, 403).await;
    assert!(!fixture.state_file().exists());
    let expected = json!({"version":3,"revision":1,"tabs":[],"active_key":"","workspace_id":"","renderers":{},"success":true});
    assert_eq!(site.snapshot().await, expected);
    assert_eq!(site.snapshot().await, expected);
    let saved = fs::read(fixture.state_file()).unwrap();
    for (action, payload) in [
        ("create", json!({})),
        ("workspace", json!({"workspace_id":"ws_a"})),
    ] {
        assert_eq!(
            value(site.send("missing", action, &payload).await, 400).await["code"],
            "no_workspace"
        );
    }
    assert_eq!(fs::read(fixture.state_file()).unwrap(), saved);
    assert_eq!(
        value(site.get(ACTIVITY).await, 200).await,
        json!({"success":true,"data":[]})
    );
    let chat = site
        .command(
            "create",
            json!({"key":"chat_new","conversation_kind":"chat","workspace_id":""}),
        )
        .await;
    assert_eq!(tab(&chat["state"], "chat_new")["workspace_id"], "");
    for action in ["deleted", "relocated", "commit", "resume"] {
        value(site.send("no", action, &json!({})).await, 400).await;
    }
    let malformed = site
        .request(Method::POST, COMMANDS)
        .header("x-csrf-token", &site.csrf)
        .header(header::CONTENT_TYPE, "application/json")
        .body("{")
        .send()
        .await
        .unwrap();
    assert_eq!(value(malformed, 400).await["success"], false);
}

#[tokio::test]
async fn python_receipts_and_v2_migrate_once_without_touching_other_users() {
    let fixture = Fixture::new();
    let legacy = json!({"version":2,"revision":7,"tabs":[{
        "key":"tab_A","kind":"new","conversation_id":"","workspace_id":"ws_a",
        "workspace_label":"Label ws_a","title":"旧标签","error":""
    }],"active_key":"","resume_key":"tab_A","workspace_id":"ws_a","renderer_id":"stale",
    "navigation":{"id":"n","key":"tab_A","page":"","status":"pending","error":""},
    "receipts":[{"id":"python","fingerprint":"bb8147706236a20e4f55e165c753fb5565e0436780532f00e9ef3c59e6ee62fb",
    "result":{"key":"tab_A"}}],"closed_keys":["tab_closed"]});
    let other = json!({"version":99,"private":"keep"});
    write_json(fixture.state_file(), &json!({"host":legacy,"other":other}));
    let site = fixture.site().await;
    let initial = site.snapshot().await;
    assert_eq!(initial["revision"], 8);
    assert_eq!(initial["active_key"], "tab_A");
    assert_eq!(tab(&initial, "tab_A")["placement"], "main");
    assert_eq!(initial["renderers"], json!({}));
    for field in [
        "receipts",
        "closed_keys",
        "navigation",
        "resume_key",
        "renderer_id",
    ] {
        assert!(initial.get(field).is_none());
    }
    let original = fs::read(fixture.state_file()).unwrap();
    let body = json!({"title":"旧标签","key":"tab_A","ignored":[1e-7,{"first":"测试","second":true}],"workspace_id":"ws_a"});
    let replay = value(site.send("python", "create", &body).await, 200).await;
    assert_eq!(replay["result"], json!({"key":"tab_A"}));
    assert_eq!(replay["state"]["revision"], 8);
    assert_eq!(
        value(site.send("python", "create", &json!({})).await, 409).await["code"],
        "navigation_superseded"
    );
    site.conflict("create", json!({"key":"tab_closed","workspace_id":"ws_a"}))
        .await;
    assert_eq!(fs::read(fixture.state_file()).unwrap(), original);
    assert_eq!(fixture.saved()["other"], other);
    assert_eq!(
        fixture.saved()["host"]["receipts"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}

#[tokio::test]
async fn v1_migration_and_corrupt_states_do_not_overwrite_disk() {
    let fixture = Fixture::new();
    fixture.indexed_agent("ws_a", "conv_legacy", "Indexed");
    write_json(
        fixture.state_file(),
        &json!({"host":{"tabs":[
        {"kind":"conv","key":"legacy-first","workspace_id":"ws_a","conversation_id":"legacy"},
        {"kind":"conv","key":"legacy-second","workspace_id":"ws_a","conversation_id":"conv_legacy"},
        {"kind":"new","key":"legacy-new","workspace_id":"ws_a"}
    ],"active_key":"legacy-second"}}),
    );
    let site = fixture.site().await;
    let state = site.snapshot().await;
    assert_eq!(state["revision"], 1);
    assert_eq!(keys(&state, true).len(), 2);
    assert_eq!(state["active_key"], state["tabs"][0]["key"]);
    assert_eq!(state["tabs"][0]["title"], "Indexed");
    let valid = fixture.saved();
    for (path, invalid) in [
        ("version", json!(4)),
        ("revision", json!(-1)),
        ("active_key", json!("closed")),
        ("renderers", json!({"tab":"r","invalid":9})),
        ("tabs", json!({})),
    ] {
        let mut corrupt = valid.clone();
        corrupt["host"][path] = invalid;
        write_json(fixture.state_file(), &corrupt);
        let bytes = fs::read(fixture.state_file()).unwrap();
        value(site.get(TABS).await, 503).await;
        value(site.send("x", "create", &json!({})).await, 503).await;
        assert_eq!(fs::read(fixture.state_file()).unwrap(), bytes);
    }
    fs::write(fixture.state_file(), b"{broken").unwrap();
    value(site.get(TABS).await, 503).await;
    assert_eq!(fs::read(fixture.state_file()).unwrap(), b"{broken");
}

#[tokio::test]
async fn commands_preserve_active_order_and_enforce_renderer_ownership() {
    let fixture = Fixture::new();
    fixture.indexed_agent("ws_a", "conv_a", "Title A");
    let second = fixture.indexed_agent("ws_b", "conv_b", "Title B");
    fixture.indexed_chat("conv_a", "Chat A");
    let site = fixture.site().await;
    let denied = site
        .send(
            "wrong-scope",
            "open",
            &json!({"workspace_id":"ws_b","conversation_id":"a"}),
        )
        .await;
    assert_eq!(value(denied, 409).await["code"], "navigation_superseded");
    let a = site
        .command(
            "open",
            json!({"workspace_id":"ws_a","conversation_id":"a","key":"a"}),
        )
        .await;
    assert_eq!(tab(&a["state"], "a")["title"], "Title A");
    assert_eq!(
        site.command(
            "open",
            json!({"workspace_id":"ws_a","conversation_id":"conv_a","key":"unused"})
        )
        .await["result"]["key"],
        "a"
    );
    let chat = site
        .command(
            "open",
            json!({"conversation_kind":"chat","conversation_id":"a","key":"chat"}),
        )
        .await;
    assert_eq!(tab(&chat["state"], "chat")["title"], "Chat A");
    assert_eq!(chat["state"]["workspace_id"], "ws_a");
    site.command(
        "create",
        json!({"key":"blank","workspace_id":"ws_a","conversation_id":"ignored"}),
    )
    .await;
    site.command("attach", json!({"key":"blank","renderer_id":"previous"}))
        .await;
    site.command("detach", json!({"key":"blank"})).await;
    let rebound = site
        .command("workspace", json!({"key":"blank","workspace_id":"ws_b"}))
        .await;
    assert_eq!(rebound["result"], json!({"key":"blank","rebound":true}));
    assert_eq!(tab(&rebound["state"], "blank")["workspace_id"], "ws_b");
    site.command("attach", json!({"key":"blank","renderer_id":"current"}))
        .await;
    site.conflict(
        "bind",
        json!({"key":"blank","renderer_id":"previous","workspace_id":"ws_b","conversation_id":"b"}),
    )
    .await;
    site.conflict(
        "bind",
        json!({"key":"blank","renderer_id":"current","workspace_id":"ws_a","conversation_id":"b"}),
    )
    .await;
    site.conflict("fail", json!({"key":"blank","renderer_id":"previous"}))
        .await;
    let bind = site.command("bind", json!({"key":"blank","renderer_id":"current","workspace_id":"ws_b","conversation_id":"b"})).await;
    assert_eq!(bind["result"]["bound"], true);
    assert_eq!(tab(&bind["state"], "blank")["placement"], "detached");
    let opened = site
        .command("open", json!({"workspace_id":"ws_b","conversation_id":"b"}))
        .await;
    assert_eq!(opened["result"], json!({"key":"blank","focus":"detached"}));
    assert_eq!(opened["state"]["active_key"], "chat");
    site.conflict("activate", json!({"key":"blank"})).await;
    site.conflict("reorder", json!({"key":"a","before_key":"blank"}))
        .await;
    site.command("dock", json!({"key":"blank"})).await;
    let reordered = site
        .command("reorder", json!({"key":"blank","before_key":"a"}))
        .await;
    assert_eq!(keys(&reordered["state"], true), ["blank", "a", "chat"]);
    let failed = site
        .command(
            "fail",
            json!({"key":"blank","renderer_id":"current","error":"broken"}),
        )
        .await;
    assert_eq!(tab(&failed["state"], "blank")["error"], "broken");
    site.command("attach", json!({"key":"blank","renderer_id":"current2"}))
        .await;
    fs::remove_file(second).unwrap();
    let removed = site
        .command("fail", json!({"key":"blank","renderer_id":"current2"}))
        .await;
    assert_eq!(removed["result"]["removed"], true);
    assert!(removed["state"]["renderers"].get("blank").is_none());
    let late = site.command("bind", json!({"key":"blank","renderer_id":"current2","workspace_id":"ws_b","conversation_id":"b"})).await;
    assert_eq!(late["result"]["bound"], false);
    site.conflict("create", json!({"key":"blank","workspace_id":"ws_a"}))
        .await;
    site.command("create", json!({"key":"blank2","workspace_id":"ws_a"}))
        .await;
    site.command("detach", json!({"key":"blank2"})).await;
    assert_eq!(
        site.command("dock", json!({"key":"blank2"})).await["result"]["removed"],
        true
    );
}

#[tokio::test]
async fn pages_focus_close_and_server_hooks_keep_keys_and_placement() {
    let fixture = Fixture::new();
    let source = fixture.indexed_agent("ws_a", "conv_a", "First");
    fixture.indexed_agent("ws_a", "conv_b", "Second");
    let site = fixture.site().await;
    site.command(
        "open",
        json!({"key":"a","workspace_id":"ws_a","conversation_id":"a"}),
    )
    .await;
    site.command(
        "open",
        json!({"key":"b","workspace_id":"ws_a","conversation_id":"b"}),
    )
    .await;
    site.command("activate", json!({"key":"a"})).await;
    let settings = site
        .command("page", json!({"page":"/settings/models"}))
        .await;
    let key = settings["result"]["key"].as_str().unwrap();
    let updated = site
        .command("page", json!({"page":"/settings/general"}))
        .await;
    assert_eq!(updated["result"]["key"], key);
    assert_eq!(tab(&updated["state"], key)["opener_key"], "a");
    assert!(
        tab(&updated["state"], key)
            .get("conversation_kind")
            .is_none()
    );
    value(
        site.send("detach-page", "detach", &json!({"key":key}))
            .await,
        400,
    )
    .await;
    assert_eq!(
        site.command("close", json!({"key":key})).await["state"]["active_key"],
        "a"
    );
    let detached = site.command("detach", json!({"key":"a"})).await;
    assert_eq!(detached["state"]["active_key"], "b");
    let bytes = fs::read(fixture.state_file()).unwrap();
    let revision = detached["state"]["revision"].clone();
    assert_eq!(
        site.command("focus", json!({"key":"a"})).await["state"]["revision"],
        revision
    );
    assert_eq!(fs::read(fixture.state_file()).unwrap(), bytes);
    assert_eq!(
        site.navigation.focused_key(HOST_USER).unwrap().as_deref(),
        Some("a")
    );
    site.command("attach", json!({"key":"a","renderer_id":"r"}))
        .await;
    let target = fixture.data().join("conversations/ws_b/conv_a.json");
    fs::create_dir_all(target.parent().unwrap()).unwrap();
    fs::rename(source, &target).unwrap();
    site.navigation
        .relocated(HOST_USER, "ws_a", "ws_b", "conv_a", "Label ws_b")
        .unwrap();
    let moved = site.snapshot().await;
    assert_eq!(keys(&moved, false), ["a", "b"]);
    assert_eq!(tab(&moved, "a")["workspace_id"], "ws_b");
    assert_eq!(tab(&moved, "a")["placement"], "detached");
    assert!(moved["renderers"]["a"].is_null());
    value(site.send("late-fail", "fail", &json!({"key":"a","renderer_id":"r","error":"stale"})).await, 409).await;
    value(site.send("late-attach", "attach", &json!({"key":"a","renderer_id":"r","workspace_id":"ws_a"})).await, 409).await;
    let reattached = site.command("attach", json!({"key":"a","renderer_id":"r-new","workspace_id":"ws_b"})).await;
    assert_eq!(reattached["state"]["renderers"]["a"], "r-new");
    fs::remove_file(target).unwrap();
    site.navigation
        .deleted(HOST_USER, "ws_b", "conv_a")
        .unwrap();
    assert_eq!(
        site.navigation.focused_key(HOST_USER).unwrap().as_deref(),
        Some("b")
    );
    assert_eq!(keys(&site.snapshot().await, false), ["b"]);
    for route in [
        "/workflows",
        "/schedules",
        "/git?workspace_id=ws_a",
        "/git?workspace_id=ws_b",
    ] {
        site.command("page", json!({"page":route})).await;
    }
    let first_git = site
        .command("page", json!({"page":"/git?workspace_id=ws_%61&other=1"}))
        .await;
    assert_eq!(keys(&first_git["state"], true).len(), 5);
    value(site.send("git", "page", &json!({"page":"/git"})).await, 400).await;
    value(
        site.send(
            "invalid",
            "page",
            &json!({"page":"//another-host/settings"}),
        )
        .await,
        400,
    )
    .await;
}

#[tokio::test]
async fn reconciliation_retains_missing_workspaces_and_removes_only_confirmed_deletions() {
    let fixture = Fixture::new();
    let source = fixture.indexed_agent("ws_a", "conv_needed", "Title");
    let site = fixture.site().await;
    site.command(
        "open",
        json!({"key":"a","workspace_id":"ws_a","conversation_id":"needed"}),
    )
    .await;
    site.command("page", json!({"page":"/git?workspace_id=ws_a"}))
        .await;
    // Zero-workspace startup must preserve the saved tabs and mark them unavailable.
    let previous = fixture
        .workspaces
        .lock()
        .unwrap()
        .drain(..)
        .collect::<Vec<_>>();
    let unavailable = site.snapshot().await;
    assert_eq!(keys(&unavailable, false).len(), 2);
    assert!(
        unavailable["tabs"]
            .as_array()
            .unwrap()
            .iter()
            .all(|tab| tab["error"] == "workspace_unavailable")
    );
    assert_eq!(site.snapshot().await["revision"], unavailable["revision"]);
    *fixture.workspaces.lock().unwrap() = previous;
    let restarted = fixture.site().await;
    fs::remove_file(source.with_file_name("index.json")).unwrap();
    let recovered = restarted.snapshot().await;
    assert_eq!(tab(&recovered, "a")["error"], "");
    assert!(!source.with_file_name("index.json").exists());
    fs::remove_file(source).unwrap();
    assert!(keys(&restarted.snapshot().await, false).contains(&"a"));
    let next = fixture.site().await.snapshot().await;
    assert!(!keys(&next, false).contains(&"a"));
    assert_eq!(
        next["revision"].as_u64(),
        recovered["revision"].as_u64().map(|value| value + 1)
    );
}

#[tokio::test]
async fn concurrent_commands_are_serialized_and_retries_report_latest_revision() {
    let fixture = Fixture::new();
    let site = Arc::new(fixture.site().await);
    let mut issued = Vec::new();
    for index in 0..20 {
        let site = site.clone();
        issued.push(tokio::spawn(async move {
            let key = format!("tab_{index}");
            let body = value(
                site.send(&key, "create", &json!({"key":key,"workspace_id":"ws_a"}))
                    .await,
                200,
            )
            .await;
            body["state"]["revision"].as_u64().unwrap()
        }));
    }
    let mut revisions = BTreeSet::new();
    for request in issued {
        revisions.insert(request.await.unwrap());
    }
    assert_eq!(revisions, (1..=20).collect());
    let retry = value(
        site.send(
            "tab_0",
            "create",
            &json!({"workspace_id":"ws_a","key":"tab_0"}),
        )
        .await,
        200,
    )
    .await;
    assert_eq!(retry["result"]["key"], "tab_0");
    assert_eq!(retry["state"]["revision"], 20);
    assert_eq!(keys(&retry["state"], false).len(), 20);
}

#[tokio::test]
async fn real_runtime_tasks_power_workspace_open_activity_and_waiting_projection() {
    let fixture = Fixture::new();
    let site = fixture.site().await;
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let release = Arc::new(Notify::new());
    let stream = {
        let release = release.clone();
        Router::new().route("/v1/chat/completions", post(move || {
            let release = release.clone();
            async move {
                release.notified().await;
                ([("content-type","text/event-stream")], concat!(
                    "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"finished\"},\"finish_reason\":null}]}\n\n",
                    "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
                    "data: [DONE]\n\n"
                ))
            }
        }))
    };
    let mock = tokio::spawn(async move {
        axum::serve(listener, stream).await.unwrap();
    });
    let model = ModelConfig::fixed(format!("http://{address}/v1/chat/completions"), None, "fixture".into());
    let started = fixture
        .runtime
        .create_task("ws_a", &json!({"message":"Actual run"}), model)
        .unwrap();
    let run = started["data"]["task_id"].as_str().unwrap();
    let id = started["data"]["conversation_id"].as_str().unwrap();
    *fixture.pending.lock().unwrap() = vec![
        (
            run.into(),
            "ws_a".into(),
            id.into(),
            "prompt".into(),
            PendingNavigationKind::Question,
        ),
        (
            run.into(),
            "ws_b".into(),
            id.into(),
            "wrong-workspace".into(),
            PendingNavigationKind::Approval,
        ),
        (
            "unknown-task".into(),
            "ws_a".into(),
            id.into(),
            "wrong-task".into(),
            PendingNavigationKind::Approval,
        ),
    ];
    let activity = value(site.get(ACTIVITY).await, 200).await;
    assert_eq!(activity["data"].as_array().unwrap().len(), 1);
    assert_eq!(activity["data"][0]["run_id"], run);
    assert_eq!(
        activity["data"][0]["pending"],
        json!({"question":["prompt"]})
    );
    let opened = site
        .command("workspace", json!({"workspace_id":"ws_a"}))
        .await;
    let key = opened["result"]["key"].as_str().unwrap();
    let tab = tab(&opened["state"], key);
    assert_eq!(tab["conversation_id"], id);
    assert_eq!(tab["running"], true);
    assert_eq!(tab["awaiting_user_answer"], true);
    assert_eq!(tab["awaiting_human_approval"], false);
    release.notify_one();
    let completed = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let activity = value(site.get(ACTIVITY).await, 200).await;
            if activity["data"][0]["running"] == false {
                break activity;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    mock.abort();
    assert_eq!(completed["data"][0]["run_status"], "succeeded");
    assert_eq!(completed["data"][0]["pending"], json!({}));
    let state = site.snapshot().await;
    assert_eq!(state["revision"], opened["state"]["revision"]);
    assert_eq!(state["tabs"][0]["running"], false);
    assert_eq!(state["tabs"][0]["awaiting_user_answer"], false);
}
