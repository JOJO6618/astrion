use std::{
    collections::BTreeMap,
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use axum::{
    Router,
    body::{Body, to_bytes},
    extract::ConnectInfo,
    http::{HeaderMap, Method, Request, StatusCode, header},
};
use serde_json::{Value, json};
use tower::ServiceExt;
use uuid::Uuid;

use super::{
    Knowledge,
    browser::{
        BrowserError, BrowserKind, BrowserMode, BrowserObservation, BrowserPending,
        BrowserPreferences, BrowserRuntime, BrowserScope,
    },
    global_memory::{self, GlobalMemory, GlobalMemoryAccess, MemoryKind},
    web,
};
use crate::{
    runtime::Runtime,
    web_auth::{WebAuth, WebAuthConfig},
};

struct Fixture {
    root: PathBuf,
    data: PathBuf,
    workspace: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("astrion-knowledge-rest-{}", Uuid::new_v4()));
        let data = root.join("existing-data");
        let workspace = root.join("current-workspace");
        fs::create_dir_all(&data).unwrap();
        fs::create_dir(&workspace).unwrap();
        Self {
            root,
            data,
            workspace,
        }
    }

    fn auth(&self) -> WebAuth {
        let mut config = WebAuthConfig::new(&self.data, 8092);
        config.desktop = true;
        config.workspace_ready = true;
        WebAuth::new(config).unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

fn write(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, contents).unwrap();
}

#[test]
fn global_memory_consumes_and_rewrites_legacy_numbered_markdown() {
    let fixture = Fixture::new();
    let main_path = fixture.data.join("memory.md");
    let tasks_path = fixture.data.join("task_memory.md");
    let memory = GlobalMemory::open(&fixture.data).unwrap();
    assert!(memory.entries(MemoryKind::Main).unwrap().is_empty());
    assert!(!main_path.exists());
    write(
        &main_path,
        "# 主记忆\r\n 42. 已有条目  \r\n5.\r\nordinary Markdown\r\n",
    );
    write(&tasks_path, "1. existing task\n");
    assert_eq!(memory.entries(MemoryKind::Main).unwrap(), ["已有条目", ""]);
    assert_eq!(memory.entries(MemoryKind::Task).unwrap(), ["existing task"]);
    assert!(global_memory::definition(GlobalMemoryAccess::Read).is_none());
    assert_eq!(
        global_memory::definition(GlobalMemoryAccess::Manage).unwrap()["function"]["name"],
        "update_memory"
    );

    let append = json!({"operation": "append", "content": "  新增  ", "conversation_id": "conv_untrusted", "memory_type": "task"});
    let denied = memory.execute(&append, GlobalMemoryAccess::Read, Some("conv_trusted"));
    assert_eq!(denied["error_code"], "permission_denied");
    assert_eq!(memory.entries(MemoryKind::Main).unwrap(), ["已有条目", ""]);
    let added = memory.execute(&append, GlobalMemoryAccess::Manage, Some("conv_trusted"));
    assert_eq!(
        added,
        json!({"success": true, "operation": "append", "memory_type": "main", "count": 3})
    );
    let stored = fs::read_to_string(&main_path).unwrap();
    assert!(stored.contains("][conv_trusted] 新增\n"));
    assert!(!stored.contains("conv_untrusted"));
    let replacement = memory.execute(
        &json!({"operation": "replace", "index": 1, "content": "[已有前缀] 更新"}),
        GlobalMemoryAccess::Manage,
        Some("conv_trusted"),
    );
    assert_eq!(replacement["index"], 1);
    let invalid = memory.execute(
        &json!({"operation": "delete", "index": 4}),
        GlobalMemoryAccess::Manage,
        None,
    );
    assert_eq!(invalid["success"], false);
    let deleted = memory.execute(
        &json!({"operation": "delete", "index": 2}),
        GlobalMemoryAccess::Manage,
        None,
    );
    assert_eq!(
        deleted,
        json!({"success": true, "operation": "delete", "memory_type": "main", "index": 2, "count": 2})
    );
    assert!(
        fs::read_to_string(&main_path)
            .unwrap()
            .starts_with("1. [已有前缀] 更新\n2. [")
    );
    assert_eq!(
        fs::read_to_string(tasks_path).unwrap(),
        "1. existing task\n"
    );
}

#[cfg(unix)]
#[test]
fn linked_global_memory_cannot_read_or_overwrite_outside_documents() {
    use std::os::unix::fs::symlink;

    let fixture = Fixture::new();
    let outside = fixture.root.join("private.md");
    write(&outside, "1. synthetic external document\n");
    let name = fixture.data.join("memory.md");
    symlink(&outside, &name).unwrap();
    let memory = GlobalMemory::open(&fixture.data).unwrap();
    assert!(memory.entries(MemoryKind::Main).is_err());
    assert_eq!(
        memory.execute(
            &json!({"operation": "append", "content": "blocked"}),
            GlobalMemoryAccess::Manage,
            None
        )["success"],
        false
    );
    assert_eq!(
        fs::read_to_string(&outside).unwrap(),
        "1. synthetic external document\n"
    );
    assert!(fs::symlink_metadata(name).unwrap().is_symlink());
}

#[derive(Clone, Default)]
struct Session {
    cookie: String,
    csrf: String,
}

type Headers<'a> = &'a [(&'a str, &'a str)];

async fn call(
    app: &Router,
    method: Method,
    path: &str,
    headers: Headers<'_>,
    body: Option<Value>,
) -> (StatusCode, HeaderMap, Value) {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header(header::HOST, "127.0.0.1:8092");
    for (name, value) in headers {
        request = request.header(*name, *value);
    }
    let request = if let Some(body) = body {
        request
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(body.to_string()))
    } else {
        request.body(Body::empty())
    }
    .unwrap();
    let mut request = request;
    let peer: SocketAddr = "127.0.0.1:53000".parse().unwrap();
    request.extensions_mut().insert(ConnectInfo(peer));
    let response = app.clone().oneshot(request).await.unwrap();
    let (parts, body) = response.into_parts();
    let body = to_bytes(body, 1024 * 1024).await.unwrap();
    (
        parts.status,
        parts.headers,
        serde_json::from_slice(&body).unwrap_or_default(),
    )
}

fn cookie(headers: &HeaderMap) -> String {
    headers[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned()
}

async fn login(app: &Router) -> Session {
    let (status, headers, body) = call(app, Method::GET, "/api/csrf-token", &[], None).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let draft = cookie(&headers);
    let csrf = body["token"].as_str().unwrap();
    let (status, headers, body) = call(
        app,
        Method::POST,
        "/host-login",
        &[
            ("cookie", &draft),
            ("x-csrf-token", csrf),
            ("origin", "http://127.0.0.1:8092"),
        ],
        Some(json!({})),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let cookie = cookie(&headers);
    let (status, _, body) = call(
        app,
        Method::GET,
        "/api/csrf-token",
        &[("cookie", &cookie)],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    Session {
        csrf: body["token"].as_str().unwrap().into(),
        cookie,
    }
}

#[tokio::test]
async fn legacy_read_routes_allow_only_authenticated_bound_workspace_resources() {
    let fixture = Fixture::new();
    write(&fixture.data.join("memory.md"), "1. global memory\n");
    write(&fixture.data.join("task_memory.md"), "7. task memory\n");
    write(
        &fixture.workspace.join(".astrion/skills/Write/SKILL.md"),
        "---\nname: '写作'\ndescription: 'Write drafts'\n---\nInstructions\n",
    );
    write(
        &fixture.workspace.join(".agents/skills/local/SKILL.md"),
        "Other skills list\n",
    );
    let another = fixture.root.join("another");
    write(
        &another.join(".astrion/skills/read/SKILL.md"),
        "Other workspace\n",
    );
    let auth = fixture.auth();
    let default = fixture.workspace.clone();
    let scope = axum::middleware::from_fn(move |mut req: axum::extract::Request, next: axum::middleware::Next| {
        let selected = match web::workspace_header(req.headers()) {
            Some("" | "default") => Some(("default", default.clone())),
            Some("other") => Some(("other", another.clone())),
            _ => None,
        };
        async move {
            let Some((id, path)) = selected else {
                return web::failure(StatusCode::NOT_FOUND, "工作区不存在");
            };
            req.extensions_mut().insert(crate::workspace_catalog::RegisteredWorkspace {
                id: id.into(), label: id.into(), path,
            });
            next.run(req).await
        }
    });
    let routes = web::scoped_router(fixture.data.clone()).route_layer(scope);
    let app = auth.router().merge(auth.protect_api(routes));
    for path in ["/api/memory", "/api/skills"] {
        assert_eq!(
            call(&app, Method::GET, path, &[], None).await.0,
            StatusCode::UNAUTHORIZED
        );
    }
    let session = login(&app).await;
    let headers = [("cookie", session.cookie.as_str())];
    assert_eq!(
        call(&app, Method::GET, "/api/memory", &headers, None)
            .await
            .2,
        json!({"success": true, "type": "main", "entries": ["global memory"]})
    );
    assert_eq!(
        call(&app, Method::GET, "/api/memory?type=task", &headers, None)
            .await
            .2,
        json!({"success": true, "type": "task", "entries": ["task memory"]})
    );
    assert_eq!(
        call(
            &app,
            Method::GET,
            "/api/memory?type=project",
            &headers,
            None
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        call(&app, Method::GET, "/api/skills", &headers, None)
            .await
            .2,
        json!({"success": true, "skills": [
            {"name": "写作", "description": "Write drafts", "path": ".astrion/skills/Write/SKILL.md"}
        ]})
    );
    let other = [
        ("cookie", session.cookie.as_str()),
        ("x-astrion-workspace-id", "other"),
    ];
    assert_eq!(
        call(&app, Method::GET, "/api/skills", &other, None).await.2["skills"][0]["path"],
        ".astrion/skills/read/SKILL.md"
    );
    let unknown = [
        ("cookie", session.cookie.as_str()),
        ("x-astrion-workspace-id", "../../unknown"),
    ];
    assert_eq!(
        call(&app, Method::GET, "/api/skills", &unknown, None)
            .await
            .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(&app, Method::POST, "/api/memory", &[("cookie", &session.cookie), ("x-csrf-token", &session.csrf)], Some(json!({})))
            .await
            .0,
        StatusCode::METHOD_NOT_ALLOWED
    );
}

#[tokio::test]
async fn browser_preferences_read_existing_conversation_and_site_files_and_guard_updates() {
    let fixture = Fixture::new();
    let runtime = Runtime::open(&fixture.data).unwrap();
    runtime
        .bind_workspace("default", &fixture.workspace)
        .unwrap();
    let created = runtime.create_session("default", &json!({})).unwrap();
    let conversation = created["conversation_id"].as_str().unwrap();
    runtime
        .update_idle_web_metadata("default", conversation, |meta| {
            meta.insert("browser_tools_enabled".into(), json!(true));
            meta.insert("pending_browser_tools".into(), json!("off"));
        })
        .unwrap();
    let sites_file = fixture.data.join("browser_site_permissions.json");
    write(
        &sites_file,
        r#"{"version":1,"sites":{"z.test":{"granted_at":2,"legacy":"retain"},"a.test":{"granted_at":1},"bad/path":{}}}"#,
    );
    let auth = fixture.auth();
    let prefs =
        BrowserPreferences::for_runtime(&fixture.data, "default", Vec::new(), runtime).unwrap();
    let app = auth.router().merge(prefs.router(&auth));
    assert_eq!(
        call(&app, Method::GET, "/api/browser-tools", &[], None)
            .await
            .0,
        StatusCode::UNAUTHORIZED
    );
    let session = login(&app).await;
    let headers = [("cookie", session.cookie.as_str())];
    let route = format!(
        "/api/browser-tools?conversation_id={}",
        conversation.trim_start_matches("conv_")
    );
    assert_eq!(
        call(&app, Method::GET, &route, &headers, None).await.2,
        json!({"success": true, "supported": false, "enabled": true, "pending": "off"})
    );
    assert_eq!(
        call(
            &app,
            Method::GET,
            "/api/browser-tools?conversation_id=missing",
            &headers,
            None
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    let (status, _, result) = call(
        &app,
        Method::GET,
        "/api/browser-site-permissions",
        &headers,
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        result,
        json!({"success": true, "supported": false, "sites": [{"site": "a.test", "granted_at": 1}, {"site": "z.test", "granted_at": 2}]})
    );
    let revoke = json!({"site": "a.test"});
    assert_eq!(
        call(
            &app,
            Method::DELETE,
            "/api/browser-site-permissions",
            &headers,
            Some(revoke.clone())
        )
        .await
        .0,
        StatusCode::FORBIDDEN
    );
    let writing = [
        ("cookie", session.cookie.as_str()),
        ("x-csrf-token", session.csrf.as_str()),
        ("origin", "http://127.0.0.1:8092"),
    ];
    assert_eq!(
        call(
            &app,
            Method::POST,
            &route,
            &writing,
            Some(json!({"enabled": false}))
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        call(
            &app,
            Method::DELETE,
            "/api/browser-site-permissions",
            &writing,
            Some(revoke)
        )
        .await
        .2,
        json!({"success": true, "removed": true, "sites": [{"site": "z.test", "granted_at": 2}]})
    );
    let persisted: Value = serde_json::from_slice(&fs::read(sites_file).unwrap()).unwrap();
    assert_eq!(
        persisted,
        json!({"version": 1, "sites": {"z.test": {"granted_at": 2, "legacy": "retain"}}})
    );
    assert_eq!(
        call(
            &app,
            Method::DELETE,
            "/api/browser-site-permissions",
            &writing,
            Some(json!({"site": "http://bad/path"}))
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
}

#[derive(Default)]
struct NativeFixture {
    changed: Mutex<Vec<(String, String, bool)>>,
}

impl BrowserRuntime for NativeFixture {
    fn supported(&self) -> bool {
        true
    }
    fn mode(&self, _workspace: &str, _conversation: &str) -> Result<BrowserMode, BrowserError> {
        Ok(BrowserMode::default())
    }
    fn observe(
        &self,
        owners: &[BrowserScope],
    ) -> Result<BTreeMap<BrowserScope, BrowserObservation>, BrowserError> {
        Ok(owners
            .iter()
            .map(|scope| {
                (
                    scope.clone(),
                    BrowserObservation {
                        exists: Some(scope.2 != "conv_deleted"),
                        open: scope.2 == "conv_reopened",
                        running: scope.2 == "conv_running",
                    },
                )
            })
            .collect())
    }
    fn change_mode(
        &self,
        workspace: &str,
        conversation: &str,
        enabled: bool,
    ) -> Result<Option<BrowserPending>, BrowserError> {
        self.changed
            .lock()
            .unwrap()
            .push((workspace.into(), conversation.into(), enabled));
        Ok(Some(if enabled {
            BrowserPending::On
        } else {
            BrowserPending::Off
        }))
    }
}

#[tokio::test]
async fn retention_releases_closed_and_excess_idle_groups_while_native_toggle_reports_pending() {
    let fixture = Fixture::new();
    let native = Arc::new(NativeFixture::default());
    let browser =
        BrowserPreferences::with_backend(&fixture.data, "default", Vec::new(), native.clone())
            .unwrap();
    let auth = fixture.auth();
    let app = auth.router().merge(browser.router(&auth));
    let session = login(&app).await;
    let rows = [
        ("closed", 1, true),
        ("running", 0, true),
        ("deleted", 2, false),
        ("reopened", 9, true),
        ("recent", 8, false),
        ("old", 3, false),
    ];
    let owners: Vec<Value> = rows.into_iter().map(|(id, visit, closed)| json!({"scope": ["agent", "default", format!("conv_{id}")], "visit": visit, "closed": closed})).collect();
    let state = json!({"owners": owners}).to_string();
    let headers = [
        ("cookie", session.cookie.as_str()),
        ("x-astrion-browser-state", state.as_str()),
    ];
    let (_, _, result) = call(&app, Method::GET, "/api/browser-retention", &headers, None).await;
    let scope = |name| {
        serde_json::to_string(&BrowserScope(
            BrowserKind::Agent,
            "default".into(),
            format!("conv_{name}"),
        ))
        .unwrap()
    };
    assert_eq!(
        result,
        json!({"success": true, "username": "host", "deleted": [scope("deleted")], "release": [scope("closed"), scope("old")]})
    );
    let invalid = [
        ("cookie", session.cookie.as_str()),
        (
            "x-astrion-browser-state",
            r#"{"owners":[{"scope":["agent","default","x"],"visit":true,"closed":false}]}"#,
        ),
    ];
    assert_eq!(
        call(&app, Method::GET, "/api/browser-retention", &invalid, None)
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    let writing = [
        ("cookie", session.cookie.as_str()),
        ("x-csrf-token", session.csrf.as_str()),
        ("origin", "http://127.0.0.1:8092"),
    ];
    let (_, _, result) = call(
        &app,
        Method::POST,
        "/api/browser-tools",
        &writing,
        Some(json!({"conversation_id": "running", "enabled": false})),
    )
    .await;
    assert_eq!(
        result,
        json!({"success": true, "enabled": false, "pending": "off", "release_failed": true})
    );
    assert_eq!(
        native.changed.lock().unwrap().as_slice(),
        &[("default".into(), "conv_running".into(), false)]
    );
}
