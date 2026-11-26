use super::*;
use axum::Extension;
use axum::extract::ConnectInfo;
use axum::http::header::{AUTHORIZATION, HOST, ORIGIN};
use reqwest::{Client, Response as HttpResponse};
use std::fs;
use std::net::SocketAddr;
use std::path::Path;
use tokio::net::TcpListener;
use uuid::Uuid;

pub(crate) const PASSWORD: &str = "  合成 host passphrase 🔑  ";
pub(crate) const HASH: &str = concat!(
    "scrypt:32768:8:1$syntheticSalt12$",
    "9225209df795e5473fa76a7f9c8c45772475a3d258ebbdb82ef8d66afb81b906827a",
    "65c70680befe1a59d7e2fbd473f231232ca588606f79318f4a46411297d4"
);
pub(crate) const FIRST_GENERATION: &str = "synthetic-first-generation";
const SECOND_GENERATION: &str = "synthetic-second-generation";

pub(crate) struct Scratch(pub(crate) PathBuf);

impl Scratch {
    pub(crate) fn new() -> Self {
        let path = std::env::temp_dir().join(format!("astrion-auth-test-{}", Uuid::new_v4()));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

pub(crate) fn write_config(data: &Path, generation: &str, enabled: bool) {
    fs::write(
        data.join("host_auth.json"),
        serde_json::to_vec(&json!({
            "version": 1, "enabled": enabled, "password_hash": if enabled { HASH } else { "" },
            "generation": generation,
        }))
        .unwrap(),
    )
    .unwrap();
}

struct Site {
    app: tokio::task::JoinHandle<()>,
    client: Client,
    address: SocketAddr,
    authority: String,
    origin: String,
}

impl Site {
    async fn start(
        scratch: &Scratch,
        configure: impl FnOnce(&mut WebAuthConfig),
        source: Option<SocketAddr>,
    ) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let mut config = WebAuthConfig::new(&scratch.0, address.port());
        config.workspace_ready = true;
        configure(&mut config);
        let authority = match config.allowed_origins.first() {
            Some(origin) => origin.split_once("://").unwrap().1.to_owned(),
            None => address.to_string(),
        };
        let origin = format!(
            "{}://{authority}",
            if config.secure_cookie {
                "https"
            } else {
                "http"
            }
        );
        let auth = WebAuth::new(config).unwrap();
        let protected = Router::new()
            .route(
                "/api/web-private",
                get(|| async { Json(json!({"access": "browser"})) })
                    .post(|body: String| async { body }),
            )
            .route(
                "/api/host/workspaces/create",
                get(|| async { StatusCode::NO_CONTENT }),
            )
            .route(
                "/api/host/workspaces/select",
                get(|Extension(session): Extension<BrowserSession>| async move {
                    if session.compare_and_select_workspace(None, Some("synthetic-workspace")) {
                        StatusCode::NO_CONTENT
                    } else {
                        StatusCode::CONFLICT
                    }
                }),
            )
            .route(
                "/api/workspace-selection",
                get(|Extension(session): Extension<BrowserSession>| async move {
                    Json(session.selected_workspace_id())
                }),
            );
        let pages = Router::new().route("/new", get(|| async { "protected entry" }));
        let mut router = auth
            .router()
            .merge(auth.protect_api(protected))
            .merge(auth.protect_pages(pages));
        if let Some(source) = source {
            router = router.layer(Extension(ConnectInfo(source)));
        }
        let app = tokio::spawn(async move {
            axum::serve(
                listener,
                router.into_make_service_with_connect_info::<SocketAddr>(),
            )
            .await
            .unwrap();
        });
        let client = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .build()
            .unwrap();
        Self {
            app,
            client,
            address,
            authority,
            origin,
        }
    }

    fn request(&self, method: Method, path: &str) -> reqwest::RequestBuilder {
        self.client
            .request(method, format!("http://{}{path}", self.address))
            .header(HOST, &self.authority)
    }

    async fn challenge(&self, cookie: Option<&str>) -> (String, String) {
        let mut request = self.request(Method::GET, "/api/csrf-token");
        if let Some(cookie) = cookie {
            request = request.header(COOKIE, cookie);
        }
        let response = request.send().await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers().get(CACHE_CONTROL).unwrap(), "no-store");
        let cookie = if let Some(issued) = response.headers().get(SET_COOKIE) {
            cookie_pair(issued.to_str().unwrap())
        } else {
            cookie
                .expect("a valid CSRF response includes or reuses a session")
                .to_owned()
        };
        let body: Value = response.json().await.unwrap();
        (cookie, body["token"].as_str().unwrap().to_owned())
    }

    fn login(&self, cookie: &str, token: &str, password: Value) -> reqwest::RequestBuilder {
        self.request(Method::POST, "/host-login")
            .header(ORIGIN, &self.origin)
            .header(COOKIE, cookie)
            .header(CSRF, token)
            .json(&json!({"password": password}))
    }

    async fn snapshot(&self, cookie: &str) -> Value {
        self.request(Method::GET, "/api/session-status")
            .header(COOKIE, cookie)
            .send()
            .await
            .unwrap()
            .json()
            .await
            .unwrap()
    }
}

impl Drop for Site {
    fn drop(&mut self) {
        self.app.abort();
    }
}

fn cookie_pair(set_cookie: &str) -> String {
    set_cookie.split(';').next().unwrap().to_owned()
}

fn issued_cookie(response: &HttpResponse) -> String {
    cookie_pair(
        response
            .headers()
            .get(SET_COOKIE)
            .unwrap()
            .to_str()
            .unwrap(),
    )
}

#[tokio::test]
async fn workspace_selection_is_per_login_and_legacy_mutations_check_origin() {
    let scratch = Scratch::new();
    let site = Site::start(&scratch, |_| {}, None).await;
    let (challenge, csrf) = site.challenge(None).await;
    let first = issued_cookie(
        &site
            .login(&challenge, &csrf, Value::Null)
            .send()
            .await
            .unwrap(),
    );
    let (challenge, csrf) = site.challenge(None).await;
    let second = issued_cookie(
        &site
            .login(&challenge, &csrf, Value::Null)
            .send()
            .await
            .unwrap(),
    );

    for path in ["/api/host/workspaces/create", "/api/host/workspaces/select"] {
        for (header, value) in [
            ("sec-fetch-site", "cross-site"),
            ("origin", "https://cross-site.invalid"),
            ("referer", "https://cross-site.invalid/path"),
        ] {
            let response = site
                .request(Method::GET, path)
                .header(COOKIE, &first)
                .header(header, value)
                .send()
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::FORBIDDEN, "{path} {header}");
        }
    }
    let select = || {
        site.request(Method::GET, "/api/host/workspaces/select")
            .header(COOKIE, &first)
            .header("sec-fetch-site", "same-origin")
    };
    assert_eq!(
        select().send().await.unwrap().status(),
        StatusCode::NO_CONTENT
    );
    assert_eq!(
        select().send().await.unwrap().status(),
        StatusCode::CONFLICT
    );
    for (cookie, expected) in [(first, Some("synthetic-workspace")), (second, None)] {
        let selected: Option<String> = site
            .request(Method::GET, "/api/workspace-selection")
            .header(COOKIE, cookie)
            .send()
            .await
            .unwrap()
            .json()
            .await
            .unwrap();
        assert_eq!(selected.as_deref(), expected);
    }
}

#[tokio::test]
async fn loopback_login_rotates_and_logout_revokes() {
    let scratch = Scratch::new();
    let site = Site::start(&scratch, |_| {}, None).await;
    let page = site.request(Method::GET, "/login").send().await.unwrap();
    assert_eq!(page.status(), StatusCode::OK);
    assert_eq!(page.headers()[CACHE_CONTROL], "no-store");
    assert!(page.text().await.unwrap().contains("Sign in to Astrion"));
    let page = site.request(Method::GET, "/new").send().await.unwrap();
    assert_eq!(page.status(), StatusCode::FOUND);
    assert_eq!(page.headers()[LOCATION], "/login");
    assert_eq!(
        site.request(Method::GET, "/api/web-private")
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::UNAUTHORIZED
    );

    let (challenge, token) = site.challenge(None).await;
    let json: Value = site
        .request(Method::GET, "/api/session-status")
        .header(AUTHORIZATION, "Bearer synthetic-gateway-only")
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(json["session"]["logged_in"], false);
    assert_eq!(
        site.login(&challenge, "not-the-token", Value::Null)
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        site.login(&challenge, &token, Value::Null)
            .header(CSRF, "duplicate-token")
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::FORBIDDEN
    );
    let response = site
        .login(&challenge, &token, Value::Null)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let set_cookie = response.headers()[SET_COOKIE].to_str().unwrap();
    assert!(set_cookie.starts_with(&format!("agents_session_{}=r1.", site.address.port())));
    for attribute in ["Path=/", "Max-Age=43200", "HttpOnly", "SameSite=Strict"] {
        assert!(set_cookie.contains(attribute));
    }
    let session = issued_cookie(&response);
    assert_eq!(
        response.json::<Value>().await.unwrap()["workspace_required"],
        false
    );
    assert_ne!(challenge, session);
    assert_eq!(site.snapshot(&challenge).await["logged_in"], false);
    let snapshot = site.snapshot(&session).await;
    assert_eq!(snapshot["logged_in"], true);
    assert_eq!(snapshot["session"]["username"], "host");
    assert_eq!(snapshot["session"]["role"], "admin");
    let (same_cookie, fresh_token) = site.challenge(Some(&session)).await;
    assert_eq!(same_cookie, session);
    assert_ne!(fresh_token, token);
    assert_eq!(
        site.request(Method::GET, "/login")
            .header(COOKIE, &session)
            .send()
            .await
            .unwrap()
            .headers()[LOCATION],
        "/new"
    );

    assert_eq!(
        site.request(Method::POST, "/api/web-private")
            .header(COOKIE, &session)
            .header(CSRF, &token)
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::FORBIDDEN
    );
    let form = format!("csrf_token={fresh_token}&user_input=preserved");
    let response = site
        .request(Method::POST, "/api/web-private")
        .header(COOKIE, &session)
        .header(CONTENT_TYPE, "application/x-www-form-urlencoded")
        .body(form.clone())
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()[CACHE_CONTROL], "no-store");
    assert_eq!(response.text().await.unwrap(), form);
    assert_eq!(
        site.request(Method::POST, "/logout")
            .header(COOKIE, &session)
            .header(CSRF, &token)
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::FORBIDDEN
    );
    let response = site
        .request(Method::POST, "/logout")
        .header(COOKIE, &session)
        .header(CSRF, fresh_token)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        response.headers()[SET_COOKIE]
            .to_str()
            .unwrap()
            .contains("Max-Age=0")
    );
    assert_eq!(site.snapshot(&session).await["logged_in"], false);
    assert!(!scratch.0.join("host_auth.json").exists());
}

#[tokio::test]
async fn origins_and_direct_peer_are_checked_independently() {
    let scratch = Scratch::new();
    let site = Site::start(&scratch, |_| {}, None).await;
    let (challenge, token) = site.challenge(None).await;
    let response = site
        .login(&challenge, &token, Value::Null)
        .header(ORIGIN, "https://different.invalid")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let response = site
        .client
        .get(format!("http://{}/api/csrf-token", site.address))
        .header(HOST, "rebind.invalid")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let response = site
        .client
        .get(format!("http://{}/api/host-mode-enabled", site.address))
        .header(HOST, format!("[::1]:{}", site.address.port()))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let response = site
        .login(&challenge, &token, Value::Null)
        .header("x-forwarded-for", "127.0.0.1")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let session = issued_cookie(
        &site
            .login(&challenge, &token, Value::Null)
            .send()
            .await
            .unwrap(),
    );
    let response = site
        .request(Method::GET, "/logout")
        .header(COOKIE, &session)
        .header("sec-fetch-site", "same-site")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let response = site
        .request(Method::GET, "/logout")
        .header(COOKIE, &session)
        .header("referer", "https://different.invalid/")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(site.snapshot(&session).await["logged_in"], true);
    let response = site
        .request(Method::GET, "/logout")
        .header(COOKIE, &session)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FOUND);
    assert_eq!(response.headers()[LOCATION], "/login?logged_out=1");
    assert_eq!(site.snapshot(&session).await["logged_in"], false);

    let remote = Site::start(&scratch, |_| {}, Some("192.0.2.10:50111".parse().unwrap())).await;
    let (challenge, token) = remote.challenge(None).await;
    let response = remote
        .login(&challenge, &token, Value::Null)
        .header("x-forwarded-for", "127.0.0.1")
        .header("x-astrion-desktop", "1")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn existing_python_hash_allows_remote_login_and_reset_revokes_old_session() {
    let scratch = Scratch::new();
    write_config(&scratch.0, FIRST_GENERATION, true);
    let site = Site::start(
        &scratch,
        |config| {
            config
                .allowed_origins
                .push("https://auth.example.test".to_owned());
            config.secure_cookie = true;
        },
        Some("198.51.100.7:45832".parse().unwrap()),
    )
    .await;
    let response = site
        .request(Method::GET, "/api/host-mode-enabled")
        .send()
        .await
        .unwrap();
    assert!(response.headers().contains_key("strict-transport-security"));
    assert_eq!(
        response.json::<Value>().await.unwrap()["password_required"],
        true
    );
    let (challenge, token) = site.challenge(None).await;
    let response = site
        .login(&challenge, &token, json!(PASSWORD.trim()))
        .header("x-astrion-desktop", "1")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(!response.headers().contains_key(SET_COOKIE));
    let response = site
        .login(&challenge, &token, json!(PASSWORD))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        response.headers()[SET_COOKIE]
            .to_str()
            .unwrap()
            .contains("; Secure")
    );
    let session = issued_cookie(&response);
    assert_eq!(site.snapshot(&session).await["logged_in"], true);
    write_config(&scratch.0, SECOND_GENERATION, true);
    assert_eq!(site.snapshot(&session).await["logged_in"], false);
    let (new_challenge, token) = site.challenge(Some(&session)).await;
    assert_ne!(new_challenge, session);
    assert_eq!(
        site.login(&new_challenge, &token, json!(PASSWORD))
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
}

#[tokio::test]
async fn malformed_config_and_failed_passwords_never_enable_passwordless_login() {
    let scratch = Scratch::new();
    fs::write(scratch.0.join("host_auth.json"), b"{").unwrap();
    let site = Site::start(&scratch, |_| {}, None).await;
    let status: Value = site
        .request(Method::GET, "/api/host-mode-enabled")
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(status["password_required"], true);
    assert_eq!(status["config_valid"], false);
    let (challenge, token) = site.challenge(None).await;
    assert_eq!(
        site.login(&challenge, &token, Value::Null)
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    write_config(&scratch.0, FIRST_GENERATION, true);
    for incorrect in [
        json!(true),
        Value::Null,
        json!(12),
        json!("short"),
        json!("x".repeat(1025)),
    ] {
        let response = site
            .login(&challenge, &token, incorrect)
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert!(!response.headers().contains_key(SET_COOKIE));
    }
    let response = site
        .login(&challenge, &token, json!(PASSWORD))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
    assert!(
        response.headers()[RETRY_AFTER]
            .to_str()
            .unwrap()
            .parse::<u64>()
            .unwrap()
            > 0
    );
    assert_eq!(site.snapshot(&challenge).await["logged_in"], false);
}

#[tokio::test]
async fn disable_checks_session_password_and_csrf_then_restores_loopback_only() {
    let scratch = Scratch::new();
    write_config(&scratch.0, FIRST_GENERATION, true);
    let site = Site::start(&scratch, |_| {}, None).await;
    let (challenge, token) = site.challenge(None).await;
    let session = issued_cookie(
        &site
            .login(&challenge, &token, json!(PASSWORD))
            .send()
            .await
            .unwrap(),
    );
    let (_, csrf) = site.challenge(Some(&session)).await;
    let status: Value = site
        .request(Method::GET, "/api/host-password")
        .header(COOKIE, &session)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(
        status,
        json!({"success": true, "applicable": true, "enabled": true})
    );
    let disable = |token: &str, password: &str| {
        site.request(Method::POST, "/api/host-password/disable")
            .header(COOKIE, &session)
            .header(CSRF, token)
            .json(&json!({"password": password}))
    };
    assert_eq!(
        disable(&token, PASSWORD).send().await.unwrap().status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        disable(&csrf, "short").send().await.unwrap().status(),
        StatusCode::UNAUTHORIZED
    );
    let response = disable(&csrf, PASSWORD).send().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        response.headers()[SET_COOKIE]
            .to_str()
            .unwrap()
            .contains("Max-Age=0")
    );
    assert_eq!(site.snapshot(&session).await["logged_in"], false);
    let state = Credentials::load(&scratch.0).unwrap();
    assert!(!state.enabled);
    assert_ne!(state.generation, FIRST_GENERATION);
    let remote = Site::start(&scratch, |_| {}, Some("192.0.2.11:51111".parse().unwrap())).await;
    let (challenge, token) = remote.challenge(None).await;
    assert_eq!(
        remote
            .login(&challenge, &token, json!(PASSWORD))
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::FORBIDDEN
    );
    let (challenge, token) = site.challenge(None).await;
    assert_eq!(
        site.login(&challenge, &token, Value::Null)
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
}

#[tokio::test]
async fn only_launcher_config_grants_desktop_exemption_and_old_cookies_require_login() {
    let scratch = Scratch::new();
    write_config(&scratch.0, FIRST_GENERATION, true);
    let desktop = Site::start(
        &scratch,
        |config| {
            config.desktop = true;
            config.cookie_name = Some("astrion_test_browser".to_owned());
        },
        None,
    )
    .await;
    let (challenge, token) = desktop.challenge(None).await;
    let response = desktop
        .login(&challenge, &token, Value::Null)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let cookie = issued_cookie(&response);
    assert_eq!(desktop.snapshot(&cookie).await["logged_in"], true);
    let status: Value = desktop
        .request(Method::GET, "/api/host-password")
        .header(COOKIE, &cookie)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(status, json!({"success": true, "applicable": false}));
    assert_eq!(
        desktop
            .snapshot("astrion_test_browser=eyJsb2dnZWRfaW4iOnRydWV9.fake.flask")
            .await["logged_in"],
        false
    );
    let restart = Site::start(
        &scratch,
        |config| {
            config.cookie_name = Some("astrion_test_browser".to_owned());
        },
        None,
    )
    .await;
    assert_eq!(restart.snapshot(&cookie).await["logged_in"], false);
    let (challenge, token) = restart.challenge(Some(&cookie)).await;
    assert_eq!(
        restart
            .login(&challenge, &token, Value::Null)
            .header("x-astrion-desktop", "1")
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::UNAUTHORIZED
    );
}
