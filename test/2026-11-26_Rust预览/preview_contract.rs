use super::*;
use axum::{
    Extension, Router,
    body::{Body, to_bytes},
    extract::Request,
    http::{Method, StatusCode, header},
    response::IntoResponse,
    routing::any,
};
use std::{
    fs,
    sync::atomic::{AtomicBool, AtomicUsize, Ordering},
};
use tower::ServiceExt;
use uuid::Uuid;

const FIRST: &str = "conv_first";
const SECOND: &str = "conv_second";
const LEGACY_TOKEN: &str = "original-python-preview-token-451";

struct Fixture {
    root: PathBuf,
    data: PathBuf,
    workspace: RegisteredWorkspace,
    runtime: Runtime,
    present: Arc<AtomicBool>,
}

impl Fixture {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-26_Rust预览")
            .join(format!(".fixture-{}", Uuid::new_v4()));
        let project = root.join("workspace");
        let data = root.join("data");
        fs::create_dir_all(project.join("page & assets")).unwrap();
        fs::write(
            project.join("page & assets/index.html"),
            "<head data-kind=\"fixture\"></head><img src=\"logo.png\">",
        )
        .unwrap();
        fs::write(project.join("page & assets/logo.png"), [1, 2, 3]).unwrap();
        fs::write(project.join(".env"), "synthetic-secret").unwrap();
        fs::write(
            root.join("outside.html"),
            "outside the authorized fixture workspace",
        )
        .unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(root.join("outside.html"), project.join("outside-link.html"))
            .unwrap();
        let runtime = Runtime::open(&data).unwrap();
        let workspace = RegisteredWorkspace {
            id: "preview-workspace".into(),
            label: "Preview fixture".into(),
            path: project.canonicalize().unwrap(),
        };
        runtime
            .bind_workspace(&workspace.id, &workspace.path)
            .unwrap();
        Self {
            root,
            data,
            runtime,
            workspace,
            present: Arc::new(AtomicBool::new(true)),
        }
    }

    fn document(&self, id: &str, token: Option<&str>, targets: Value) {
        let doc = crate::legacy::LegacyDocument::from_value(json!({
            "id": id, "title": "Stored preview fixture", "created_at": "2026-11-01T12:00:00",
            "updated_at": "2026-11-01T12:00:00", "messages": [], "token_statistics": {},
            "metadata": {"project_path": self.workspace.path, "preview_targets": targets, "preview_token": token},
        })).unwrap();
        self.runtime.store().unwrap().persist(&self.workspace.id, doc, true).unwrap();
    }

    fn service(&self) -> Preview {
        let workspace = self.workspace.clone();
        let present = self.present.clone();
        Preview::new(self.runtime.clone(), move |id| {
            Ok((id == workspace.id && present.load(Ordering::SeqCst)).then(|| workspace.clone()))
        })
        .unwrap()
    }

    fn web(&self, service: &Preview) -> Router {
        service.router().layer(Extension(self.workspace.clone()))
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

async fn web_request(
    router: &Router,
    method: Method,
    path: &str,
    host: &str,
    body: &str,
) -> axum::response::Response {
    let request = Request::builder()
        .method(method)
        .uri(path)
        .header(header::HOST, host)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(body.to_owned()))
        .unwrap();
    router.clone().oneshot(request).await.unwrap()
}

async fn json_response(response: axum::response::Response) -> Value {
    serde_json::from_slice(&to_bytes(response.into_body(), 64 * 1024).await.unwrap()).unwrap()
}

fn browser() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .unwrap()
}

fn host_header(host: &str) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(header::HOST, host.parse().unwrap());
    headers
}

#[tokio::test]
async fn published_tokens_are_persisted_bound_to_conversations_and_served_on_a_different_origin() {
    let fixture = Fixture::new();
    fixture.document(
        FIRST,
        Some(LEGACY_TOKEN),
        json!([
            {"type":"file","path":"page & assets/index.html"},
            {"type":"file","path":"missing.html"},
            {"type":"server","url":"http://0.0.0.0:12345/page**（fixture）"},
            {"type":"server","url":"https://outside.invalid:12345/"},
        ]),
    );
    fixture.document(SECOND, None, json!([]));
    let service = fixture.service();
    let web = fixture.web(&service);
    let response = web_request(
        &web,
        Method::GET,
        "/api/preview/targets?conversation_id=first",
        "localhost:8091",
        "",
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
    let response = json_response(response).await;
    assert_eq!(response["preview_token"], LEGACY_TOKEN);
    assert_eq!(response["preview_targets"].as_array().unwrap().len(), 2);
    assert_eq!(
        response["preview_targets"][1]["url"],
        "http://127.0.0.1:12345/page"
    );
    let base = response["preview_base"].as_str().unwrap();
    assert!(base.starts_with("http://127.0.0.1:"));
    assert_ne!(base, "http://127.0.0.1:8091");
    let (second_base, second_token) = service
        .runtime_for(&host_header("[::1]:8091"), &fixture.workspace, SECOND)
        .await;
    assert_eq!(second_base.as_deref(), Some(base));
    let second_token = second_token.unwrap();
    assert_ne!(second_token, LEGACY_TOKEN);
    assert_eq!(
        fixture
            .runtime
            .web_metadata(&fixture.workspace.id, SECOND)
            .unwrap()["preview_token"],
        second_token
    );
    let (_, reread) = fixture
        .service()
        .runtime_for(&host_header("localhost:8091"), &fixture.workspace, SECOND)
        .await;
    assert_eq!(reread.as_deref(), Some(second_token.as_str()));

    let browser = browser();
    let file = format!("{base}/{LEGACY_TOKEN}/file/page%20%26%20assets/index.html");
    let response = browser.get(&file).send().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()[header::REFERRER_POLICY], "no-referrer");
    assert_eq!(
        response.headers()[header::CONTENT_TYPE],
        "text/html; charset=utf-8"
    );
    assert!(response.text().await.unwrap().contains(&format!(
        "<head data-kind=\"fixture\"><base href=\"/{LEGACY_TOKEN}/file/page%20%26%20assets/\">"
    )));
    let response = browser
        .get(format!(
            "{base}/{LEGACY_TOKEN}/file/page%20%26%20assets/logo.png"
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(response.headers()[header::CONTENT_TYPE], "image/png");
    assert_eq!(response.bytes().await.unwrap().as_ref(), &[1, 2, 3]);
    for wrong_host in ["redirect.invalid:8091", "127.0.0.1", "localhost:1"] {
        let response = browser
            .get(&file)
            .header(header::HOST, wrong_host)
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    }
    let port = base.rsplit_once(':').unwrap().1;
    assert_eq!(
        browser
            .get(&file)
            .header(header::HOST, format!("LOCALHOST:{port}"))
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
    let unknown = browser
        .get(format!(
            "{base}/unknown-token-can-be-long/file/page%20%26%20assets/index.html"
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(unknown.status(), StatusCode::NOT_FOUND);
    for path in [".env", "%2E%2E%2Foutside.html", "outside-link.html"] {
        let response = browser
            .get(format!("{base}/{LEGACY_TOKEN}/file/{path}"))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{path}");
    }
    assert_eq!(
        browser
            .get(format!("{base}/api/preview/targets"))
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::NOT_FOUND
    );
    fixture.present.store(false, Ordering::SeqCst);
    assert_eq!(
        browser.get(&file).send().await.unwrap().status(),
        StatusCode::NOT_FOUND
    );
}

async fn loopback(app: Router) -> (u16, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    (address.port(), task)
}

#[tokio::test]
async fn proxy_forwards_methods_and_rewrites_approved_urls_then_revokes_a_removed_port() {
    let fixture = Fixture::new();
    let denied_calls = Arc::new(AtomicUsize::new(0));
    let seen = denied_calls.clone();
    let (denied_port, denied_task) = loopback(Router::new().fallback(move || async move {
        seen.fetch_add(1, Ordering::SeqCst);
        "unregistered server"
    }))
    .await;
    let upstream = Router::new()
        .route("/", any(|| async { "upstream root" }))
        .route("/page", any(|| async {
            ([
                ("content-type", "text/html"), ("x-frame-options", "DENY"),
                ("content-security-policy", "default-src 'none'"), ("set-cookie", "fixture=foreign"),
                ("access-control-allow-origin", "*"),
            ], "<head></head><img src='/logo.png'><a href=\"//outside.invalid/\">outside</a><form action = '/form'>")
        }))
        .route("/redirect", any(move || async move {
            (StatusCode::TEMPORARY_REDIRECT, [(header::LOCATION, format!("http://127.0.0.1:{denied_port}/stop"))])
        }))
        .route("/redirect-local", any(|| async { (StatusCode::FOUND, [(header::LOCATION, "/page?value=%26")]) }))
        .route("/echo/{*path}", any(|request: Request| async move {
            let (parts, body) = request.into_parts();
            let body = to_bytes(body, 64 * 1024).await.unwrap();
            let headers: Vec<_> = parts.headers.keys().map(|name| name.as_str()).collect();
            (StatusCode::ACCEPTED, axum::Json(json!({
                "method": parts.method.as_str(), "uri": parts.uri.to_string(),
                "body": String::from_utf8_lossy(&body), "headers": headers,
            }))).into_response()
        }));
    let (port, upstream_task) = loopback(upstream).await;
    fixture.document(
        FIRST,
        Some(LEGACY_TOKEN),
        json!([{"type":"server","url":format!("http://localhost:{port}/page")}]),
    );
    fixture.document(
        SECOND,
        None,
        json!([{"type":"server","url":format!("http://localhost:{denied_port}/")}]),
    );
    let service = fixture.service();
    let web = fixture.web(&service);
    let (base, _) = service
        .runtime_for(&host_header("localhost:8091"), &fixture.workspace, FIRST)
        .await;
    let base = base.unwrap();
    let prefix = format!("/{LEGACY_TOKEN}/proxy/{port}");
    let browser = browser();

    let response = browser
        .get(format!("{base}{prefix}/"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.text().await.unwrap(), "upstream root");
    let page = browser
        .get(format!("{base}{prefix}/page"))
        .send()
        .await
        .unwrap();
    for header in [
        "x-frame-options",
        "content-security-policy",
        "set-cookie",
        "access-control-allow-origin",
    ] {
        assert!(!page.headers().contains_key(header), "{header}");
    }
    let page = page.text().await.unwrap();
    assert!(page.contains(&format!("<head><base href=\"{prefix}/\">")));
    assert!(page.contains(&format!("src='{prefix}/logo.png'")));
    assert!(page.contains(&format!("action = '{prefix}/form'")));
    assert!(page.contains("href=\"//outside.invalid/\""));
    for method in [
        Method::GET,
        Method::POST,
        Method::PUT,
        Method::DELETE,
        Method::PATCH,
    ] {
        let request = browser
            .request(
                method.clone(),
                format!("{base}{prefix}/echo/a%2Fb?x=%2520&x=%26"),
            )
            .header(header::COOKIE, "shared-cookie=fixture")
            .header(header::AUTHORIZATION, "Bearer fixture")
            .header(header::REFERER, &base)
            .header("x-csrf-token", "fixture")
            .header(header::CONNECTION, "x-hop-fixture")
            .header("x-hop-fixture", "secret")
            .header("x-user-fixture", "kept")
            .body("request fixture");
        let response = request.send().await.unwrap();
        assert_eq!(response.status(), StatusCode::ACCEPTED, "{method}");
        let response: Value = response.json().await.unwrap();
        assert_eq!(response["method"], method.as_str());
        assert_eq!(response["uri"], "/echo/a%2Fb?x=%2520&x=%26");
        assert_eq!(
            response["body"],
            if method == Method::GET {
                ""
            } else {
                "request fixture"
            }
        );
        for sensitive in [
            "cookie",
            "authorization",
            "referer",
            "x-csrf-token",
            "x-hop-fixture",
        ] {
            assert!(
                !response["headers"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(sensitive))
            );
        }
        assert!(
            response["headers"]
                .as_array()
                .unwrap()
                .contains(&json!("x-user-fixture"))
        );
    }
    let response = browser
        .head(format!("{base}{prefix}/page"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.bytes().await.unwrap().is_empty());
    let response = browser
        .request(Method::OPTIONS, format!("{base}{prefix}/page"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        response.headers()[header::ALLOW]
            .to_str()
            .unwrap()
            .contains("PATCH")
    );
    let response = browser
        .get(format!("{base}{prefix}/redirect-local"))
        .send()
        .await
        .unwrap();
    assert_eq!(
        response.headers()[header::LOCATION],
        format!("{prefix}/page?value=%26")
    );
    let response = browser
        .get(format!("{base}{prefix}/redirect"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
    assert!(!response.headers().contains_key(header::LOCATION));
    let response = browser
        .get(format!(
            "{base}/{LEGACY_TOKEN}/proxy/{denied_port}/?conversation_id={SECOND}"
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(denied_calls.load(Ordering::SeqCst), 0);

    let fallback = web_request(
        &web,
        Method::GET,
        &format!("/api/preview/proxy/{port}/page?conversation_id={FIRST}"),
        "app.example.invalid",
        "",
    )
    .await;
    assert_eq!(fallback.status(), StatusCode::OK);
    assert_eq!(
        fallback.headers()[header::CONTENT_SECURITY_POLICY],
        "sandbox allow-scripts allow-forms"
    );
    let removed = web_request(
        &web,
        Method::POST,
        "/api/preview/targets/remove",
        "localhost:8091",
        &json!({"key":format!("server:http://localhost:{port}/page")}).to_string(),
    )
    .await;
    assert_eq!(removed.status(), StatusCode::OK);
    assert_eq!(json_response(removed).await["preview_targets"], json!([]));
    let response = browser
        .get(format!("{base}{prefix}/page"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    denied_task.abort();
    upstream_task.abort();
}
