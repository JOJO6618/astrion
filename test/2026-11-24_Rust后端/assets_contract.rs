use super::{TerminalPolicy, WebAssets, personalization::Personalization};
use crate::{
    catalog::CatalogSource,
    web_auth::{BrowserSession, WebAuth, WebAuthConfig},
    workspace_catalog::RegisteredWorkspace,
};
use axum::{
    Router,
    extract::Request,
    http::{Method, StatusCode, header},
    middleware::{self, Next},
    response::Response,
};
use reqwest::redirect::Policy;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{net::TcpListener, task::JoinHandle};
use uuid::Uuid;

const TEMPLATES: &[&str] = &[
    "index.html",
    "login.html",
    "terminal.html",
    "chrome.html",
    "browser-window.html",
    "quick.html",
    "admin_policy/index.html",
    "custom_tools/index.html",
    "custom_tools/guide.html",
    "voice_test.html",
    "security.js",
    "icons/browser.svg",
];

struct Fixture {
    directory: PathBuf,
    root: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let directory = repo
            .join("test/2026-11-24_Rust后端")
            .join(format!("assets_fixture_{}", Uuid::new_v4()));
        let root = directory.join("static");
        let fixture = Self { directory, root };
        for name in TEMPLATES {
            let destination = fixture.root.join(name);
            fs::create_dir_all(destination.parent().unwrap()).unwrap();
            fs::copy(repo.join("static").join(name), destination).unwrap();
        }
        fixture.build();
        fixture
    }

    fn build(&self) {
        let mut manifest = serde_json::Map::new();
        for name in [
            "main",
            "login",
            "chrome",
            "browserWindow",
            "quick",
            "adminPolicy",
            "adminCustomTools",
            "adminCustomToolsGuide",
        ] {
            let script = format!("assets/{name}-hAsH_01-.js");
            let style = format!("assets/{name}-zAsH-01_.css");
            self.write(&format!("dist/{script}"), b"export {};\n");
            self.write(&format!("dist/{style}"), b"body { margin: 0; }\n");
            manifest.insert(
                format!("src/{name}.ts"),
                json!({"file": script, "name": name, "isEntry": true, "css": [style]}),
            );
        }
        self.write("dist/assets/SecondaryGate-Xzy_01-2.css", b"body {}\n");
        manifest.insert(
            "_SecondaryGate.js".to_owned(),
            json!({"file": "assets/SecondaryGate-Xzy_01-2.js", "css": ["assets/SecondaryGate-Xzy_01-2.css"]}),
        );
        self.write_json("dist/.vite/manifest.json", Value::Object(manifest));
        self.write(
            "dist/style-a123456789.css",
            b"@import url('/static/dist/assets/main-zAsH-01_.css');\n",
        );
        self.write(
            "dist/browser-window-b123456789.css",
            b"@import url('/static/dist/assets/browserWindow-zAsH-01_.css');\n",
        );
        self.write_json(
            "dist/.vite/entry-styles.json",
            json!({ "assemblies": {
            "/static/style.css": "style-a123456789.css",
            "/static/browser-window.css": "browser-window-b123456789.css"
        } }),
        );
    }

    fn write(&self, path: &str, contents: impl AsRef<[u8]>) {
        let file = self.root.join(path);
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(file, contents).unwrap();
    }

    fn write_json(&self, path: &str, value: Value) {
        self.write(path, serde_json::to_vec(&value).unwrap());
    }

    fn manifest(&self) -> Value {
        serde_json::from_slice(&fs::read(self.root.join("dist/.vite/manifest.json")).unwrap())
            .unwrap()
    }

    async fn serve(&self) -> Server {
        let assets = WebAssets::new(&self.root).unwrap();
        // Login consumes the same resolver as the authenticated page router.
        // The WebAuth integration owns its cookie validation and redirect behavior.
        let router = assets
            .router()
            .merge(Router::new().route("/login", assets.login()))
            .layer(middleware::from_fn(test_identity));
        Server::start(router).await
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.directory);
    }
}

async fn test_identity(mut request: Request, next: Next) -> Response {
    if request.headers().contains_key("x-test-session") {
        request.extensions_mut().insert(BrowserSession::synthetic());
    }
    let policy = match request
        .headers()
        .get("x-test-terminal-policy")
        .and_then(|value| value.to_str().ok())
    {
        Some("allow") => Some(TerminalPolicy::Allowed),
        Some("block") => Some(TerminalPolicy::Blocked),
        _ => None,
    };
    if let Some(policy) = policy {
        request.extensions_mut().insert(policy);
    }
    next.run(request).await
}

struct Server {
    origin: String,
    client: reqwest::Client,
    worker: JoinHandle<()>,
}

impl Server {
    async fn start(router: Router) -> Self {
        Self::listen(TcpListener::bind("127.0.0.1:0").await.unwrap(), router)
    }

    fn listen(listener: TcpListener, router: Router) -> Self {
        let origin = format!("http://{}", listener.local_addr().unwrap());
        let worker = tokio::spawn(async move {
            axum::serve(
                listener,
                router.into_make_service_with_connect_info::<SocketAddr>(),
            )
            .await
            .unwrap()
        });
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .timeout(Duration::from_secs(5))
            .build()
            .unwrap();
        Self {
            origin,
            client,
            worker,
        }
    }

    fn request(&self, method: Method, path: &str) -> reqwest::RequestBuilder {
        self.client
            .request(method, format!("{}{path}", self.origin))
    }

    async fn get(&self, path: &str, authenticated: bool) -> reqwest::Response {
        let request = self.request(Method::GET, path);
        let request = if authenticated {
            request.header("x-test-session", "verified")
        } else {
            request
        };
        request.send().await.unwrap()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.worker.abort();
    }
}

async fn html(response: reqwest::Response) -> String {
    assert_eq!(response.status(), StatusCode::OK, "{}", response.url());
    assert_eq!(
        response.headers()[header::CONTENT_TYPE],
        "text/html; charset=utf-8"
    );
    assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
    assert_eq!(
        response.headers()[header::X_CONTENT_TYPE_OPTIONS],
        "nosniff"
    );
    assert_eq!(response.headers()[header::X_FRAME_OPTIONS], "SAMEORIGIN");
    response.text().await.unwrap()
}

#[tokio::test]
async fn real_templates_use_vite_entries_styles_and_assemblies() {
    let fixture = Fixture::new();
    let server = fixture.serve().await;
    for (path, entry, authenticated) in [
        ("/login", "login", false),
        ("/chrome", "chrome", false),
        ("/browser-window", "browserWindow", false),
        ("/static/quick.html", "quick", false),
        ("/new", "main", true),
        ("/admin/policy", "adminPolicy", true),
        ("/admin/custom-tools", "adminCustomTools", true),
        (
            "/static/custom_tools/guide.html",
            "adminCustomToolsGuide",
            false,
        ),
    ] {
        let page = html(server.get(path, authenticated).await).await;
        assert!(
            page.contains(&format!("/static/dist/assets/{entry}-hAsH_01-.js")),
            "{path}"
        );
        assert!(
            !page.contains(&format!("/static/dist/assets/{entry}.js")),
            "{path}"
        );
        if matches!(path, "/admin/policy" | "/admin/custom-tools") {
            assert!(page.contains("/static/dist/assets/SecondaryGate-Xzy_01-2.css"));
        }
    }
    let login = html(server.get("/login", false).await).await;
    assert!(login.contains("/static/dist/assets/login-zAsH-01_.css"));
    assert!(login.contains("/static/security.js"));
    let main = html(server.get("/new", true).await).await;
    assert!(main.contains("/static/dist/style-a123456789.css"));
    assert!(!main.contains("/static/style.css"));
    let browser = html(server.get("/browser-window", false).await).await;
    assert!(browser.contains("/static/dist/browser-window-b123456789.css"));
    assert!(!browser.contains("/static/browser-window.css"));
    let static_html = html(server.get("/static/voice_test.html", false).await).await;
    assert_eq!(
        static_html,
        fs::read_to_string(fixture.root.join("voice_test.html")).unwrap()
    );
}

#[tokio::test]
async fn known_entry_routes_gate_sessions_and_effective_terminal_policy() {
    let fixture = Fixture::new();
    let server = fixture.serve().await;
    let protected_index = [
        "/new",
        "/chat",
        "/chat/new",
        "/chat/conv_20261124_120001_123",
        "/20261124_120001_123",
        "/conv_20261124_120001_123",
        "/settings",
        "/settings/tools/mcp",
        "/git",
        "/schedules",
        "/workflows",
        "/workflow/group/Example%20workflow",
        "/static/index.html",
    ];
    for path in protected_index.into_iter().chain([
        "/",
        "/terminal",
        "/admin/policy",
        "/admin/custom-tools",
        "/static/terminal.html",
        "/static/admin_policy/index.html",
        "/static/custom_tools/index.html",
    ]) {
        let response = server.get(path, false).await;
        assert_eq!(response.status(), StatusCode::FOUND, "{path}");
        assert_eq!(response.headers()[header::LOCATION], "/login", "{path}");
        assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
    }
    for path in protected_index {
        let page = html(server.get(path, true).await).await;
        assert!(
            page.contains("/static/dist/assets/main-hAsH_01-.js"),
            "{path}"
        );
    }
    let root = server.get("/", true).await;
    assert_eq!(root.status(), StatusCode::FOUND);
    assert_eq!(root.headers()[header::LOCATION], "/new");
    for path in ["/terminal", "/static/terminal.html"] {
        assert_eq!(
            server.get(path, true).await.status(),
            StatusCode::SERVICE_UNAVAILABLE
        );
        let blocked = server
            .request(Method::GET, path)
            .header("x-test-session", "verified")
            .header("x-test-terminal-policy", "block")
            .send()
            .await
            .unwrap();
        assert_eq!(blocked.status(), StatusCode::FORBIDDEN);
        let allowed = server
            .request(Method::GET, path)
            .header("x-test-session", "verified")
            .header("x-test-terminal-policy", "allow")
            .send()
            .await
            .unwrap();
        assert_eq!(
            html(allowed).await,
            fs::read_to_string(fixture.root.join("terminal.html")).unwrap()
        );
    }
    for path in [
        "/unknown",
        "/chat/no-conversation",
        "/chat/conv_20261124_120001_12",
        "/api/not-present",
    ] {
        assert_eq!(
            server.get(path, false).await.status(),
            StatusCode::NOT_FOUND,
            "{path}"
        );
        assert_eq!(
            server.get(path, true).await.status(),
            StatusCode::NOT_FOUND,
            "{path}"
        );
    }
    let head = server
        .request(Method::HEAD, "/chrome")
        .send()
        .await
        .unwrap();
    assert_eq!(head.status(), StatusCode::OK);
    assert_eq!(head.headers()[header::CACHE_CONTROL], "no-store");
    assert!(head.bytes().await.unwrap().is_empty());
    let options = server
        .request(Method::OPTIONS, "/chat/new")
        .send()
        .await
        .unwrap();
    assert_eq!(options.headers()[header::ALLOW], "GET, HEAD, OPTIONS");
}

#[tokio::test]
async fn static_files_observe_visibility_boundaries_and_revalidate() {
    let fixture = Fixture::new();
    fixture.write(".private", b"hidden");
    fixture.write("icons/.private", b"nested hidden");
    fixture.write("admin_dashboard/dashboard.js", b"private dashboard");
    fixture.write("assets/sample.mjs", b"export {};\n");
    let server = fixture.serve().await;
    for path in [
        "/static/.private",
        "/static/icons/.private",
        "/static/dist/.vite/manifest.json",
        "/static/dist/%2Evite/entry-styles.json",
        "/static/icons",
        "/static/icons/",
        "/static//security.js",
        "/static/%252e%252e/terminal.html",
        "/static/icons%2f%2e%2e%2fsecurity.js",
        "/static/icons%5c..%5csecurity.js",
        "/static/security.js%3astream",
        "/static/security.js%2e",
    ] {
        assert_eq!(
            server.get(path, true).await.status(),
            StatusCode::NOT_FOUND,
            "{path}"
        );
    }
    let private_path = "/static/admin_dashboard/dashboard.js";
    assert_eq!(
        server.get(private_path, false).await.status(),
        StatusCode::NOT_FOUND
    );
    let private = server.get(private_path, true).await;
    assert_eq!(private.headers()[header::CACHE_CONTROL], "no-store");
    assert_eq!(private.text().await.unwrap(), "private dashboard");
    let path = "/static/assets/sample.mjs";
    let first = server.get(path, false).await;
    assert_eq!(first.status(), StatusCode::OK);
    assert_eq!(
        first.headers()[header::CONTENT_TYPE],
        "text/javascript; charset=utf-8"
    );
    assert_eq!(first.headers()[header::CACHE_CONTROL], "no-cache");
    let modified = first.headers()[header::LAST_MODIFIED].clone();
    let same_date = server
        .request(Method::GET, path)
        .header(header::IF_MODIFIED_SINCE, &modified)
        .send()
        .await
        .unwrap();
    assert_eq!(same_date.status(), StatusCode::NOT_MODIFIED);
    let overridden = server
        .request(Method::GET, path)
        .header(header::IF_MODIFIED_SINCE, &modified)
        .header(header::IF_NONE_MATCH, "\"different-version\"")
        .send()
        .await
        .unwrap();
    assert_eq!(overridden.status(), StatusCode::OK);
    let etag = first.headers()[header::ETAG].to_str().unwrap().to_owned();
    let unchanged = server
        .request(Method::GET, path)
        .header(header::IF_NONE_MATCH, format!("W/{etag}"))
        .send()
        .await
        .unwrap();
    assert_eq!(unchanged.status(), StatusCode::NOT_MODIFIED);
    assert_eq!(unchanged.headers()[header::ETAG], etag);
    assert!(unchanged.bytes().await.unwrap().is_empty());
    fixture.write("assets/sample.mjs", b"import {};\n");
    let changed = server
        .request(Method::GET, path)
        .header(header::IF_NONE_MATCH, &etag)
        .send()
        .await
        .unwrap();
    assert_eq!(changed.status(), StatusCode::OK);
    assert_ne!(changed.headers()[header::ETAG], etag);
    assert_eq!(
        server
            .get("/static/icons/browser.svg", false)
            .await
            .headers()[header::CONTENT_TYPE],
        "image/svg+xml"
    );
}

#[cfg(unix)]
#[tokio::test]
async fn static_symlink_aliases_are_inaccessible_even_to_the_host() {
    use std::os::unix::fs::symlink;
    let fixture = Fixture::new();
    fixture.write(".private", b"hidden");
    fixture.write("admin_dashboard/dashboard.js", b"admin");
    let outside = fixture.directory.join("outside.txt");
    fs::write(&outside, "outside").unwrap();
    symlink(&outside, fixture.root.join("outside.txt")).unwrap();
    symlink(".private", fixture.root.join("hidden.txt")).unwrap();
    symlink("terminal.html", fixture.root.join("terminal-alias.html")).unwrap();
    symlink("admin_dashboard", fixture.root.join("dashboard-alias")).unwrap();
    let server = fixture.serve().await;
    for path in [
        "/static/outside.txt",
        "/static/hidden.txt",
        "/static/terminal-alias.html",
        "/static/dashboard-alias/dashboard.js",
    ] {
        assert_eq!(
            server.get(path, false).await.status(),
            StatusCode::NOT_FOUND,
            "{path}"
        );
        assert_eq!(
            server.get(path, true).await.status(),
            StatusCode::NOT_FOUND,
            "{path}"
        );
    }
}

#[tokio::test]
async fn manifest_and_assembly_changes_reload_per_static_root() {
    let fixture = Fixture::new();
    let other = Fixture::new();
    let server = fixture.serve().await;
    let other_server = other.serve().await;
    assert!(
        html(server.get("/new", true).await)
            .await
            .contains("style-a123456789.css")
    );
    let mut manifest = fixture.manifest();
    manifest["src/main.ts"]["file"] = json!("assets/main-new-build-id.js");
    fixture.write(
        "dist/assets/main-new-build-id.js",
        b"export const second = 2;\n",
    );
    fixture.write_json("dist/.vite/manifest.json", manifest);
    fixture.write("dist/style-rebuilt-000001.css", b"body { margin: 1px; }\n");
    fixture.write_json(
        "dist/.vite/entry-styles.json",
        json!({ "assemblies": {
        "/static/style.css": "style-rebuilt-000001.css"
    } }),
    );
    let updated = html(server.get("/new", true).await).await;
    assert!(updated.contains("main-new-build-id.js"));
    assert!(updated.contains("style-rebuilt-000001.css"));
    let old = html(other_server.get("/new", true).await).await;
    assert!(old.contains("main-hAsH_01-.js"));
    assert!(old.contains("style-a123456789.css"));
}

#[tokio::test]
async fn incomplete_builds_error_and_existing_legacy_dist_files_still_render() {
    let fixture = Fixture::new();
    let server = fixture.serve().await;
    fs::remove_file(fixture.root.join("dist/assets/login-hAsH_01-.js")).unwrap();
    let response = server.get("/login", false).await;
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
    assert_eq!(
        response.text().await.unwrap(),
        "Web assets are unavailable."
    );
    let mut manifest = fixture.manifest();
    manifest.as_object_mut().unwrap().remove("src/login.ts");
    fixture.write_json("dist/.vite/manifest.json", manifest.clone());
    assert_eq!(
        server.get("/login", false).await.status(),
        StatusCode::INTERNAL_SERVER_ERROR
    );
    fixture.write("dist/assets/login.js", b"export {};\n");
    fixture.write("dist/assets/login.css", b"body {}\n");
    let legacy = html(server.get("/login", false).await).await;
    assert!(legacy.contains("/static/dist/assets/login.js"));
    assert!(legacy.contains("/static/dist/assets/login.css"));
    manifest["src/conflicting.ts"] =
        json!({"file": "assets/other.js", "name": "main", "isEntry": true});
    fixture.write_json("dist/.vite/manifest.json", manifest.clone());
    assert_eq!(
        server.get("/new", true).await.status(),
        StatusCode::INTERNAL_SERVER_ERROR
    );
    manifest
        .as_object_mut()
        .unwrap()
        .remove("src/conflicting.ts");
    manifest["src/main.ts"]["file"] = json!("../security.js");
    fixture.write_json("dist/.vite/manifest.json", manifest);
    assert_eq!(
        server.get("/new", true).await.status(),
        StatusCode::INTERNAL_SERVER_ERROR
    );
    fixture.write("dist/.vite/manifest.json", b"{invalid JSON");
    assert_eq!(
        server.get("/new", true).await.status(),
        StatusCode::INTERNAL_SERVER_ERROR
    );
    fs::remove_file(fixture.root.join("dist/.vite/manifest.json")).unwrap();
    assert_eq!(
        server.get("/new", true).await.status(),
        StatusCode::INTERNAL_SERVER_ERROR
    );
    assert_eq!(server.get("/new", false).await.status(), StatusCode::FOUND);
    fs::remove_file(fixture.root.join("chrome.html")).unwrap();
    assert_eq!(
        server.get("/chrome", false).await.status(),
        StatusCode::NOT_FOUND
    );
}

#[tokio::test]
async fn browser_auth_and_personalization_persist_language_and_split_settings() {
    let fixture = Fixture::new();
    let data = fixture.directory.join("data");
    let global = fixture.directory.join("bundled");
    let private = fixture.directory.join("private");
    fs::create_dir_all(&data).unwrap();
    fs::write(data.join("personalization.json"), br#"{"theme":"dark","tavily_api_key":"synthetic-legacy-key","sub_agent_model":"legacy-low"}"#).unwrap();
    fs::create_dir_all(global.join("test-skill")).unwrap();
    fs::create_dir_all(private.join("test-skill")).unwrap();
    fs::write(
        global.join("test-skill/SKILL.md"),
        "---\nname: Bundled\ndescription: test skill\n---\n",
    )
    .unwrap();
    fs::write(
        private.join("test-skill/SKILL.md"),
        "---\nname: Private\ndescription: custom skill\n---\n",
    )
    .unwrap();
    fs::write(data.join("admin_policy.json"), br#"{"global":{"forced_category_states":{"network":false}},"roles":{"admin":{"ui_blocks":{"block_personal_space":true}}},"users":{"host":{"ui_blocks":{"block_personal_space":false}}}}"#).unwrap();
    let catalog = CatalogSource::with_paths(&fixture.directory, &fixture.directory, &data);
    let sync_calls = Arc::new(Mutex::new(Vec::new()));
    let observed = sync_calls.clone();
    let personalization = Personalization::new(catalog, global, private).with_skill_sync(
        move |workspace, _, _, ids| {
            observed
                .lock()
                .unwrap()
                .push((workspace.id.clone(), ids.to_vec()));
            Ok(())
        },
    );
    let workspace = RegisteredWorkspace {
        id: "registry-selected".into(),
        label: "Selected".into(),
        path: fixture.directory.clone(),
    };
    let settings = personalization.router().layer(middleware::from_fn(
        move |mut request: Request, next: Next| {
            let workspace = workspace.clone();
            async move {
                if request.headers().contains_key("x-test-workspace") {
                    request.extensions_mut().insert(workspace);
                }
                next.run(request).await
            }
        },
    ));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let auth = WebAuth::new(WebAuthConfig::new(
        &data,
        listener.local_addr().unwrap().port(),
    ))
    .unwrap();
    let assets = WebAssets::new(&fixture.root).unwrap().with_host_data(&data);
    let pages = assets
        .router()
        .layer(axum::Extension(TerminalPolicy::Blocked));
    let router = auth
        .router_with_login(assets.login())
        .merge(auth.with_browser_session(pages))
        .merge(auth.protect_api(settings));
    let server = Server::listen(listener, router);
    assert_eq!(
        server.get("/api/personalization", false).await.status(),
        StatusCode::UNAUTHORIZED
    );
    let login = html(server.get("/login", false).await).await;
    assert!(login.contains("/static/dist/assets/login-hAsH_01-.js"));
    let (cookie, token) = sign_in(&server).await;
    let signed_in_login = server
        .request(Method::GET, "/login")
        .header(header::COOKIE, &cookie)
        .send()
        .await
        .unwrap();
    assert_eq!(signed_in_login.status(), StatusCode::FOUND);
    assert_eq!(signed_in_login.headers()[header::LOCATION], "/new");
    let read = || {
        server
            .request(Method::GET, "/api/personalization")
            .header(header::COOKIE, &cookie)
    };
    let write = |value| {
        server
            .request(Method::POST, "/api/personalization")
            .header(header::COOKIE, &cookie)
            .header("x-csrf-token", &token)
            .json(&value)
    };
    let initial = read().send().await.unwrap();
    assert_eq!(initial.status(), StatusCode::OK);
    assert_eq!(initial.headers()[header::CACHE_CONTROL], "no-store");
    let initial: Value = initial.json().await.unwrap();
    assert_eq!(initial["data"]["ui_locale"], "zh-CN");
    assert_eq!(initial["data"]["theme"], "dark");
    assert_eq!(initial["data"]["sub_agent_model_low"], "legacy-low");
    assert_eq!(initial["data"]["tavily_api_key"], "synthetic-legacy-key");
    assert_eq!(initial["skills_catalog"][0]["label"], "Private");
    assert_eq!(initial["skills_catalog"][0]["source"], "private");
    assert_eq!(initial["data"]["enabled_skills"], json!(["test-skill"]));
    assert_eq!(initial["tool_categories"][0]["id"], "network");
    assert_eq!(initial["tool_categories"][0]["locked_state"], false);
    assert_eq!(initial["tool_loading_registry"][0]["key"], "schedules");
    assert_eq!(
        initial["context_compression_settings"]["deep_trigger_tokens"],
        250_000
    );
    let csrf_denied = server
        .request(Method::POST, "/api/personalization")
        .header(header::COOKIE, &cookie)
        .json(&json!({"ui_locale":"en-US"}))
        .send()
        .await
        .unwrap();
    assert_eq!(csrf_denied.status(), StatusCode::FORBIDDEN);
    let updated = write(json!({"ui_locale":"en-US", "enabled_skills":[], "search_provider":" EXA ",
        "exa_api_key":"synthetic-exa", "show_git_status_bar": false, "chat_default_thinking_mode": false,
        "unknown_field":"ignore", "considerations":[" one ", "two"], "recent_conversations_prompt_limit": 100}))
        .send().await.unwrap();
    assert_eq!(updated.status(), StatusCode::OK);
    let updated: Value = updated.json().await.unwrap();
    assert_eq!(updated["data"]["considerations"], "one\ntwo");
    assert_eq!(updated["data"]["recent_conversations_prompt_limit"], 30);
    assert_eq!(updated["data"]["theme"], "dark");
    assert_eq!(updated["data"]["enabled_skills"], json!([]));
    assert_eq!(updated["data"]["chat_default_thinking_mode"], false);
    assert!(updated.get("tool_loading_registry").is_none());
    assert!(sync_calls.lock().unwrap().is_empty());
    let workspace_update = write(json!({"enabled_skills":["test-skill"]}))
        .header("x-test-workspace", "registered")
        .send()
        .await
        .unwrap();
    assert_eq!(workspace_update.status(), StatusCode::OK);
    assert_eq!(
        *sync_calls.lock().unwrap(),
        vec![(
            "registry-selected".to_owned(),
            vec!["test-skill".to_owned()]
        )]
    );
    let payload = |name: &str| -> Value {
        serde_json::from_slice(&fs::read(data.join(name)).unwrap()).unwrap()
    };
    let personal = payload("personalization.json");
    assert_eq!(personal["ui_locale"], "en-US");
    assert!(personal.get("unknown_field").is_none());
    assert!(personal.get("tavily_api_key").is_none());
    assert!(personal.get("search_provider").is_none());
    assert_eq!(payload("service_config.json")["search_provider"], "exa");
    assert_eq!(payload("secrets.json")["exa_api_key"], "synthetic-exa");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(data.join("secrets.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
    let wrong = write(json!({"ui_locale":"fr-FR", "shallow_compress_trigger_tokens":150_000, "deep_compress_trigger_tokens":100_000})).send().await.unwrap();
    assert_eq!(wrong.status(), StatusCode::BAD_REQUEST);
    assert!(
        wrong.json::<Value>().await.unwrap()["error"]
            .as_str()
            .unwrap()
            .starts_with("Deep compression")
    );
    assert_eq!(payload("personalization.json")["ui_locale"], "en-US");
    let terminal = server
        .request(Method::GET, "/terminal")
        .header(header::COOKIE, &cookie)
        .send()
        .await
        .unwrap();
    assert_eq!(terminal.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        terminal.text().await.unwrap(),
        "Realtime terminal has been disabled by the administrator"
    );
    fs::write(
        data.join("admin_policy.json"),
        br#"{"users":{"host":{"ui_blocks":{"block_personal_space":true}}}}"#,
    )
    .unwrap();
    let blocked = read().send().await.unwrap();
    assert_eq!(blocked.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        blocked.json::<Value>().await.unwrap()["error"],
        "Personal space has been disabled by the admin"
    );
    assert_eq!(
        write(json!({"ui_locale":"zh-CN"}))
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::FORBIDDEN
    );
}

async fn sign_in(server: &Server) -> (String, String) {
    let csrf = server.get("/api/csrf-token", false).await;
    let cookie = response_cookie(&csrf);
    let token = csrf.json::<Value>().await.unwrap()["token"]
        .as_str()
        .unwrap()
        .to_owned();
    let login = server
        .request(Method::POST, "/host-login")
        .header(header::COOKIE, cookie)
        .header("x-csrf-token", token)
        .json(&json!({}))
        .send()
        .await
        .unwrap();
    assert_eq!(login.status(), StatusCode::OK);
    let cookie = response_cookie(&login);
    let csrf = server
        .request(Method::GET, "/api/csrf-token")
        .header(header::COOKIE, &cookie)
        .send()
        .await
        .unwrap();
    let token = csrf.json::<Value>().await.unwrap()["token"]
        .as_str()
        .unwrap()
        .to_owned();
    (cookie, token)
}

fn response_cookie(response: &reqwest::Response) -> String {
    response.headers()[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned()
}

// Opt in with a production Vite build and the matching source templates.
#[tokio::test]
#[ignore = "requires a real static/dist build; set ASTRION_TEST_STATIC_ROOT if outside the checkout"]
async fn production_payload_entries_and_all_vite_artifacts_load_over_http() {
    let default_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../static");
    let root = std::env::var_os("ASTRION_TEST_STATIC_ROOT")
        .map(PathBuf::from)
        .unwrap_or(default_root);
    let assets = WebAssets::new(&root).unwrap();
    let server = Server::start(
        assets
            .router()
            .merge(Router::new().route("/login", assets.login()))
            .layer(middleware::from_fn(test_identity)),
    )
    .await;
    let mut referenced = BTreeSet::new();
    for (path, authenticated) in [
        ("/login", false),
        ("/new", true),
        ("/chrome", false),
        ("/browser-window", false),
        ("/static/quick.html", false),
        ("/admin/policy", true),
        ("/admin/custom-tools", true),
        ("/static/custom_tools/guide.html", false),
    ] {
        let page = html(server.get(path, authenticated).await).await;
        assert!(page.contains("/static/dist/assets/"), "{path}");
        for local in page.split('"') {
            if local.starts_with("/static/") && !local.contains(['\n', ' ', '<']) {
                referenced.insert(local.to_owned());
            }
        }
    }
    let mut pending: Vec<_> = referenced.iter().cloned().collect();
    let manifest: Value =
        serde_json::from_slice(&fs::read(root.join("dist/.vite/manifest.json")).unwrap()).unwrap();
    for chunk in manifest.as_object().unwrap().values() {
        for file in
            chunk["file"]
                .as_str()
                .into_iter()
                .chain(["css", "assets"].into_iter().flat_map(|key| {
                    chunk[key]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                }))
        {
            let url = format!("/static/dist/{file}");
            if referenced.insert(url.clone()) {
                pending.push(url);
            }
        }
    }
    assert!(pending.len() > 10);
    while let Some(url) = pending.pop() {
        let response = server.get(&url, false).await;
        assert_eq!(response.status(), StatusCode::OK, "{url}");
        let css_assembly = url.starts_with("/static/dist/style-")
            || url.starts_with("/static/dist/browser-window-");
        if css_assembly {
            assert_eq!(
                response.headers()[header::CONTENT_TYPE],
                "text/css; charset=utf-8"
            );
            let css = response.text().await.unwrap();
            assert!(css.contains("@import url("));
            for reference in css
                .split('\'')
                .filter(|value| value.starts_with("/static/dist/"))
            {
                if referenced.insert(reference.to_owned()) {
                    pending.push(reference.to_owned());
                }
            }
        } else {
            assert!(!response.bytes().await.unwrap().is_empty(), "{url}");
        }
    }
    assert_eq!(
        server
            .get("/static/dist/.vite/manifest.json", false)
            .await
            .status(),
        StatusCode::NOT_FOUND
    );
}

#[test]
fn agent_creation_preferences_freeze_defaults_tool_loading_and_compression() {
    let fixture = Fixture::new();
    let data = fixture.directory.join("data");
    fs::create_dir_all(&data).unwrap();
    let catalog = CatalogSource::with_paths(&fixture.directory, &fixture.directory, &data);
    let preferences = Personalization::new(
        catalog,
        fixture.directory.join("global"),
        fixture.directory.join("private"),
    );
    let defaults = preferences.agent_creation_preferences().unwrap();
    assert_eq!(
        defaults,
        json!({"browser_tools_enabled":false,"git_worktree_enabled":false,
        "compress_limit_tokens":250_000,"compress_limit_source":"snapshot"})
    );
    fs::write(data.join("personalization.json"), br#"{"browser_tools_default":true,"git_worktree_enabled":true,
        "tool_loading_enabled":true, "tool_loading_deferred":["run_command","create_skill","submit_schedule","submit_schedule"],
        "shallow_compress_trigger_tokens":100000,"deep_compress_trigger_tokens":50000}"#).unwrap();
    let selected = preferences.agent_creation_preferences().unwrap();
    assert_eq!(
        selected,
        json!({"browser_tools_enabled":true,"git_worktree_enabled":true,
        "compress_limit_tokens":100_001,"compress_limit_source":"snapshot",
        "tool_loading":{"enabled":true,"deferred_set":["submit_schedule","create_skill"],
        "initial_exposed":[],"loaded":[],"pending":["submit_schedule","create_skill"]}})
    );
    fs::write(
        data.join("personalization.json"),
        br#"{"tool_loading_enabled":true,
        "shallow_compress_trigger_tokens":2000000,"deep_compress_trigger_tokens":2000000}"#,
    )
    .unwrap();
    let maximum = preferences.agent_creation_preferences().unwrap();
    assert!(maximum.get("compress_limit_tokens").is_none());
    assert!(maximum.get("compress_limit_source").is_none());
    assert!(
        maximum["tool_loading"]["deferred_set"]
            .as_array()
            .unwrap()
            .len()
            > 10
    );
    assert_eq!(
        maximum["tool_loading"]["pending"],
        maximum["tool_loading"]["deferred_set"]
    );
    assert_eq!(selected["compress_limit_tokens"], 100_001);
}
