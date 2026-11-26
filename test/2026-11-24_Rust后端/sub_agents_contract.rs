use super::*;
use axum::{
    Router,
    extract::{Request, State},
    middleware::{self, Next},
    response::{IntoResponse, Response},
};
use reqwest::{Client, header};
use std::{fs, sync::Mutex};
use tokio::{net::TcpListener, task::JoinHandle};
use uuid::Uuid;

use crate::web_auth::{WebAuth, WebAuthConfig};
use crate::conversation_db::Database;

const LIST: &str = "/api/sub_agents";
const TOKEN: &str = "test-gateway-token";

struct Fixture {
    root: PathBuf,
    data: PathBuf,
    runtime: Runtime,
    workspaces: Vec<RegisteredWorkspace>,
    live: Arc<Mutex<Vec<Value>>>,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("astrion-child-agents-{}", Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        let data = root.join("data");
        let runtime = Runtime::open(&data).unwrap();
        let workspaces = ["ws_a", "ws_b"]
            .into_iter()
            .map(|id| {
                let path = root.join(id);
                fs::create_dir(&path).unwrap();
                let path = path.canonicalize().unwrap();
                runtime.bind_workspace(id, &path).unwrap();
                let directory = data.join("conversations").join(id);
                fs::create_dir_all(&directory).unwrap();
                let mut database = Database::open(&directory).unwrap();
                for parent in ["conv_shared", "conv_other"] {
                    database.save(&json!({"id":parent,"messages":[]}), &json!({"id":parent})).unwrap();
                }
                RegisteredWorkspace {
                    id: id.into(),
                    label: id.into(),
                    path,
                }
            })
            .collect();
        Self {
            root,
            data,
            runtime,
            workspaces,
            live: Arc::default(),
        }
    }

    fn record(
        &self,
        id: &str,
        parent: &str,
        workspace: usize,
        status: &str,
        name: &str,
        created: i32,
    ) -> Value {
        json!({"task_id":id,"conversation_id":parent,"workspace_root":self.workspaces[workspace].path,
            "status":status,"name":name,"display_name":"do not project this","summary":"summary","created_at":created,
            "working_dir":self.workspaces[workspace].path,"model_key":"fixture","task_root":"/untrusted",
            "stats_file":"/untrusted/stats.json","progress_file":"/untrusted/progress.jsonl"})
    }

    fn registry(&self, records: &[Value]) -> Vec<u8> {
        let tasks: serde_json::Map<_, _> = records
            .iter()
            .map(|record| {
                (
                    record["task_id"].as_str().unwrap().to_owned(),
                    record.clone(),
                )
            })
            .collect();
        serde_json::to_vec(&json!({"version":1,"tasks":tasks,"states":{}})).unwrap()
    }

    fn write_registry(&self, records: &[Value]) {
        let next = self.data.join("registry.tmp");
        fs::write(&next, self.registry(records)).unwrap();
        fs::rename(next, self.data.join("sub_agent_runtime.json")).unwrap();
    }

    fn task_dir(&self, task: &str) -> PathBuf {
        let directory = self.data.join("sub_agent_tasks").join(task);
        fs::create_dir_all(&directory).unwrap();
        directory
    }

    async fn site(&self) -> Website {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let auth = WebAuth::new(WebAuthConfig::new(&self.data, address.port())).unwrap();
        let live = self.live.clone();
        let readers = SubAgents::new(&self.data, self.runtime.clone())
            .with_live_records(move |_, _| Ok(live.lock().unwrap().clone()));
        let list = readers
            .list_router()
            .route_layer(middleware::from_fn_with_state(
                self.workspaces.clone(),
                scope,
            ))
            .route_layer(middleware::from_fn_with_state(auth.clone(), both));
        let browser = readers
            .browser_router()
            .route_layer(middleware::from_fn_with_state(
                self.workspaces.clone(),
                scope,
            ));
        let app = Router::new()
            .merge(auth.router())
            .merge(list)
            .merge(auth.protect_api(browser));
        let task = tokio::spawn(async move {
            axum::serve(
                listener,
                app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
            )
            .await
            .unwrap();
        });
        let mut site = Website {
            client: Client::builder().no_proxy().build().unwrap(),
            origin: format!("http://{address}"),
            cookie: String::new(),
            task,
        };
        let challenge = site
            .client
            .get(format!("{}/api/csrf-token", site.origin))
            .send()
            .await
            .unwrap();
        let first_cookie = cookie(&challenge);
        let challenge: Value = challenge.json().await.unwrap();
        let login = site
            .client
            .post(format!("{}/host-login", site.origin))
            .header(header::ORIGIN, &site.origin)
            .header(header::COOKIE, first_cookie)
            .header("x-csrf-token", challenge["token"].as_str().unwrap())
            .json(&json!({"password":""}))
            .send()
            .await
            .unwrap();
        assert_eq!(login.status(), 200);
        site.cookie = cookie(&login);
        site
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

async fn both(State(auth): State<WebAuth>, request: Request, next: Next) -> Response {
    let bearer = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok());
    match bearer {
        Some(value) if value.strip_prefix("Bearer ") == Some(TOKEN) => next.run(request).await,
        Some(_) => (
            axum::http::StatusCode::UNAUTHORIZED,
            axum::Json(json!({"success":false})),
        )
            .into_response(),
        None => auth.protect_request(request, next).await,
    }
}

async fn scope(
    State(workspaces): State<Vec<RegisteredWorkspace>>,
    mut request: Request,
    next: Next,
) -> Response {
    let requested = request
        .headers()
        .get("x-astrion-workspace-id")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("ws_a");
    if let Some(workspace) = workspaces.into_iter().find(|entry| entry.id == requested) {
        request.extensions_mut().insert(workspace);
    }
    next.run(request).await
}

struct Website {
    client: Client,
    origin: String,
    cookie: String,
    task: JoinHandle<()>,
}
impl Drop for Website {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Website {
    fn request(&self, path: &str) -> reqwest::RequestBuilder {
        self.client.get(format!("{}{path}", self.origin))
    }
    fn browser(&self, path: &str) -> reqwest::RequestBuilder {
        self.request(path).header(header::COOKIE, &self.cookie)
    }
    async fn get(&self, path: &str) -> Value {
        value(self.browser(path), 200).await
    }
}

fn cookie(response: &reqwest::Response) -> String {
    response.headers()[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .into()
}

async fn value(request: reqwest::RequestBuilder, expected: u16) -> Value {
    let response = request.send().await.unwrap();
    let status = response.status().as_u16();
    let text = response.text().await.unwrap();
    assert_eq!(status, expected, "{text}");
    serde_json::from_str(&text).unwrap()
}

#[tokio::test]
async fn list_supports_both_auth_paths_and_checks_workspace_and_parent() {
    let fixture = Fixture::new();
    let a = fixture.record("sub_a", "conv_shared", 0, "running", "A", 1);
    let b = fixture.record("sub_b", "conv_shared", 1, "idle", "B", 2);
    let other = fixture.record("sub_other", "conv_other", 0, "terminated", "Other", 3);
    let orphan = fixture.record("sub_orphan", "conv_unavailable", 0, "idle", "Orphan", 4);
    fixture.write_registry(&[a, b, other, orphan]);
    let stats = fixture.task_dir("sub_a").join("stats.json");
    fs::write(stats, br#"{"current_context_tokens":42,"api_calls":7}"#).unwrap();
    let saved = fs::read(fixture.data.join("sub_agent_runtime.json")).unwrap();
    assert_eq!(legacy_pending(&fixture.data).unwrap(), 4);
    assert_eq!(migrate_legacy(&fixture.data).unwrap(), 4);
    assert_eq!(legacy_pending(&fixture.data).unwrap(), 0);
    let site = fixture.site().await;
    let path = format!("{LIST}?conversation_id=shared");
    value(site.request(&path), 401).await;
    value(site.request(&path).bearer_auth("bad"), 401).await;
    let browser = site.get(&path).await;
    let bearer = value(site.request(&path).bearer_auth(TOKEN), 200).await;
    assert_eq!(browser, bearer);
    let rows = browser["data"].as_array().unwrap();
    assert_eq!(rows.len(), 1);
    let row = &rows[0];
    assert_eq!(row["task_id"], "sub_a");
    assert_eq!(row["status"], "stopped");
    assert_eq!(row["display_name"], "A");
    assert_eq!(row["conversation_id"], "conv_shared");
    assert_eq!(row["current_context_tokens"], 42);
    assert!(row.get("progress_file").is_none());
    assert!(row.get("workspace_root").is_none());
    let b = value(
        site.browser(&path).header("x-astrion-workspace-id", "ws_b"),
        200,
    )
    .await;
    assert_eq!(b["data"][0]["task_id"], "sub_b");
    value(
        site.browser(&path)
            .header("x-astrion-workspace-id", "unknown"),
        404,
    )
    .await;
    value(
        site.browser(&format!("{LIST}?conversation_id=unavailable")),
        404,
    )
    .await;
    assert_eq!(site.get(LIST).await["data"], json!([]));
    assert_eq!(
        fs::read(fixture.data.join("sub_agent_runtime.json")).unwrap(),
        saved
    );
}

#[tokio::test]
async fn activity_requires_browser_scope_and_reads_only_canonical_task_log() {
    let fixture = Fixture::new();
    let task = fixture.record("sub_a", "conv_shared", 0, "idle", "A", 1);
    fixture.write_registry(&[task]);
    let progress = fixture.task_dir("sub_a").join("progress.jsonl");
    fs::write(
        &progress,
        b"{\"id\":1}\n{\"id\":2}\n{\"id\":3}\n{\"partial\":",
    )
    .unwrap();
    assert_eq!(migrate_legacy(&fixture.data).unwrap(), 1);
    let site = fixture.site().await;
    let route = format!("{LIST}/sub_a/activity");
    let path = format!("{route}?conversation_id=conv_shared");
    value(site.request(&path).bearer_auth(TOKEN), 401).await;
    value(site.browser(&route), 404).await;
    value(
        site.browser(&format!("{route}?conversation_id=conv_other")),
        404,
    )
    .await;
    value(
        site.browser(&path).header("x-astrion-workspace-id", "ws_b"),
        404,
    )
    .await;
    value(site.browser(&format!("{path}&offset=-1")), 400).await;
    value(site.browser(&format!("{path}&before=4&offset=0")), 400).await;
    let tail = site.get(&format!("{path}&limit=1")).await;
    let page = &tail["data"];
    assert_eq!(page["task_id"], "sub_a");
    assert_eq!(page["status"], "idle");
    assert_eq!(page["entries"], json!([{"id":3}]));
    assert_eq!(page["start_offset"], 18);
    assert_eq!(page["next_offset"], 27);
    assert_eq!(page["has_more_before"], true);
    assert_eq!(page["partial_tail"], true);
    let previous = site.get(&format!("{path}&before=18&limit=1")).await;
    assert_eq!(previous["data"]["entries"], json!([{"id":2}]));
    let after = site.get(&format!("{path}&offset=0&limit=2")).await;
    assert_eq!(after["data"]["entries"], json!([{"id":1},{"id":2}]));
    assert_eq!(after["data"]["has_more_after"], true);
    let reset = site.get(&format!("{path}&offset=999&limit=1")).await;
    assert_eq!(reset["data"]["reset"], true);
    assert_eq!(reset["data"]["entries"], json!([{"id":3}]));
    fs::remove_file(progress).unwrap();
    let registry = Registry::new(fixture.data.clone());
    registry.append_activity("sub_a", &json!({"id":4})).unwrap();
    let continued = site.get(&format!("{path}&offset=27")).await;
    assert_eq!(continued["data"]["reset"], false);
    assert_eq!(continued["data"]["entries"], json!([{"id":4}]));
}

#[tokio::test]
async fn migration_is_explicit_and_live_scope_overrides_sqlite() {
    let fixture = Fixture::new();
    let site = fixture.site().await;
    let path = format!("{LIST}?conversation_id=shared");
    assert_eq!(site.get(&path).await["data"], json!([]));
    let task = fixture.record("sub_a", "conv_shared", 0, "running", "A", 1);
    fixture.write_registry(&[task]);
    assert_eq!(site.get(&path).await["data"], json!([]));
    assert_eq!(migrate_legacy(&fixture.data).unwrap(), 1);
    assert_eq!(site.get(&path).await["data"][0]["name"], "A");
    let next = fixture.record("sub_a", "conv_shared", 0, "running", "Z", 1);
    let registry = Registry::new(fixture.data.clone());
    let mut document = registry.document().unwrap();
    document["tasks"]["sub_a"] = next;
    registry.store(&document).unwrap();
    assert_eq!(migrate_legacy(&fixture.data).unwrap(), 0);
    assert_eq!(site.get(&path).await["data"][0]["name"], "Z");
    let live = fixture.record("sub_a", "conv_shared", 0, "running", "Live", 1);
    let bad_live = fixture.record("sub_other", "conv_shared", 1, "running", "Foreign", 2);
    *fixture.live.lock().unwrap() = vec![live, bad_live];
    let result = site.get(&path).await;
    assert_eq!(result["data"].as_array().unwrap().len(), 1);
    assert_eq!(result["data"][0]["name"], "Live");
    assert_eq!(result["data"][0]["status"], "running");
    fixture.live.lock().unwrap().clear();
    fs::write(fixture.data.join("sub_agent_runtime.json"), b"{broken").unwrap();
    assert!(migrate_legacy(&fixture.data).is_err());
    assert_eq!(site.get(&path).await["data"][0]["name"], "Z");
    fs::remove_file(fixture.data.join("sub_agent_runtime.json")).unwrap();
    assert_eq!(site.get(&path).await["data"][0]["name"], "Z");
    fixture
        .runtime
        .remove_idle_workspace("ws_a", || Ok(()))
        .unwrap();
    fixture
        .runtime
        .bind_workspace("ws_a", &fixture.workspaces[1].path)
        .unwrap();
    value(site.browser(&path), 404).await;
}

#[cfg(unix)]
#[tokio::test]
async fn migration_rejects_linked_progress_and_stats() {
    use std::os::unix::fs::symlink;
    let fixture = Fixture::new();
    fixture.write_registry(&[fixture.record("sub_a", "conv_shared", 0, "idle", "A", 1)]);
    let directory = fixture.task_dir("sub_a");
    let outside = fixture.root.join("other.json");
    fs::write(
        &outside,
        b"{\"current_context_tokens\":999,\"secret\":\"foreign\"}\n",
    )
    .unwrap();
    symlink(&outside, directory.join("progress.jsonl")).unwrap();
    fs::hard_link(&outside, directory.join("stats.json")).unwrap();
    assert!(migrate_legacy(&fixture.data).is_err());
    assert_eq!(legacy_pending(&fixture.data).unwrap(), 1);
    let site = fixture.site().await;
    let listing = site.get(&format!("{LIST}?conversation_id=shared")).await;
    assert_eq!(listing["data"], json!([]));
    let body = value(
        site.browser(&format!("{LIST}/sub_a/activity?conversation_id=shared")),
        404,
    )
    .await;
    assert!(!body.to_string().contains("foreign"));
    fs::remove_file(directory.join("progress.jsonl")).unwrap();
    fs::remove_file(directory.join("stats.json")).unwrap();
    assert_eq!(migrate_legacy(&fixture.data).unwrap(), 1);
    assert_eq!(site.get(&format!("{LIST}?conversation_id=shared")).await["data"][0]["current_context_tokens"], 0);
}
