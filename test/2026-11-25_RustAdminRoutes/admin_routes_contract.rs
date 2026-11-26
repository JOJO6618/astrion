use super::*;

use axum::{
    body::{Body, to_bytes},
    extract::ConnectInfo,
    http::{
        Method,
        header::{AUTHORIZATION, CONTENT_TYPE, COOKIE, HOST, LOCATION, ORIGIN, SET_COOKIE},
    },
};
use std::{fs, net::SocketAddr, path::PathBuf};
use tower::ServiceExt;
use uuid::Uuid;

use crate::web_auth::WebAuthConfig;

const PORT: u16 = 8313;
const PASSWORD: &str = "synthetic-admin-secondary";
const EFFECTIVE: &str = "/api/effective-policy";
const POLICY: &str = "/api/admin/policy";
const SECONDARY: &str = "/api/admin/secondary/status";
const VERIFY: &str = "/api/admin/secondary/verify";
const TOOLS: &str = "/api/admin/custom-tools";
const TOOL_FILE: &str = "/api/admin/custom-tools/file";
const RELOAD: &str = "/api/admin/custom-tools/reload";

struct Fixture {
    root: PathBuf,
    catalog: CatalogSource,
    app: Router,
}

impl Fixture {
    fn new(settings: Value) -> Self {
        let root = std::env::temp_dir().join(format!("astrion-admin-contract-{}", Uuid::new_v4()));
        fs::create_dir_all(root.join("deploy")).unwrap();
        fs::write(root.join("deploy/custom_models.json"), json!([
            {"model_name":"fixture_model","model_id":"fixture-model","url":"http://127.0.0.1:1/v1","apikey":"synthetic"},
            {"model_name":"fixture_hidden","model_id":"fixture-hidden","url":"http://127.0.0.1:1/v1","apikey":"synthetic","visible":false}
        ]).to_string()).unwrap();
        let catalog = CatalogSource::with_paths(&root, &root.join("deploy"), &root);
        let auth = WebAuth::new(WebAuthConfig::new(&root, PORT)).unwrap();
        let secondary = Secondary::from_configuration(&settings, |_| None);
        let tools = ToolRegistry::open(&root, root.join("custom_tools"), true).unwrap();
        let admin = AdminRoutes(Arc::new(AdminState {
            catalog: catalog.clone(),
            policy: Policy::default(),
            secondary,
            tools,
        }));
        let app = auth.router().merge(admin.router(&auth));
        Self { root, catalog, app }
    }

    fn configured() -> Self {
        Self::new(json!({"env_vars": {"ADMIN_SECONDARY_PASSWORD": PASSWORD}}))
    }

    async fn call(&self, request: Request) -> Response {
        self.app.clone().oneshot(request).await.unwrap()
    }

    async fn login(&self) -> Browser {
        let preflight = self
            .call(request(Method::GET, "/api/csrf-token", None, None, false))
            .await;
        assert_eq!(preflight.status(), StatusCode::OK);
        let browser = Browser {
            cookie: cookie(&preflight),
            csrf: json_response(preflight).await["token"]
                .as_str()
                .unwrap()
                .into(),
        };
        let response = self
            .call(request(
                Method::POST,
                "/host-login",
                Some(json!({"password":""})),
                Some(&browser),
                true,
            ))
            .await;
        assert_eq!(response.status(), StatusCode::OK);
        let browser = Browser {
            cookie: cookie(&response),
            csrf: String::new(),
        };
        let csrf = self
            .call(request(
                Method::GET,
                "/api/csrf-token",
                None,
                Some(&browser),
                false,
            ))
            .await;
        assert_eq!(csrf.status(), StatusCode::OK);
        Browser {
            csrf: json_response(csrf).await["token"].as_str().unwrap().into(),
            ..browser
        }
    }

    async fn verify(&self, browser: &Browser) {
        assert_eq!(
            self.call(request(
                Method::POST,
                VERIFY,
                Some(json!({"password":PASSWORD})),
                Some(browser),
                false
            ))
            .await
            .status(),
            StatusCode::OK
        );
    }

    async fn json(
        &self,
        method: Method,
        path: &str,
        body: Option<Value>,
        browser: &Browser,
    ) -> Value {
        let response = self
            .call(request(method, path, body, Some(browser), true))
            .await;
        let status = response.status();
        let value = json_response(response).await;
        assert_eq!(status, StatusCode::OK, "{path}: {value}");
        assert_eq!(value["success"], true);
        value
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

struct Browser {
    cookie: String,
    csrf: String,
}

fn request(
    method: Method,
    path: &str,
    body: Option<Value>,
    browser: Option<&Browser>,
    csrf: bool,
) -> Request {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header(HOST, format!("127.0.0.1:{PORT}"));
    if let Some(browser) = browser {
        request = request.header(COOKIE, &browser.cookie);
        if csrf {
            request = request.header("x-csrf-token", &browser.csrf);
        }
    }
    let mut request = if let Some(body) = body {
        request
            .header(CONTENT_TYPE, "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    } else {
        request.body(Body::empty()).unwrap()
    };
    request
        .extensions_mut()
        .insert(ConnectInfo("127.0.0.1:8421".parse::<SocketAddr>().unwrap()));
    request
}

fn cookie(response: &Response) -> String {
    response.headers()[SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned()
}

async fn json_response(response: Response) -> Value {
    let bytes = to_bytes(response.into_body(), 2 << 20).await.unwrap();
    serde_json::from_slice(&bytes)
        .unwrap_or_else(|_| panic!("Expected JSON, got {}", String::from_utf8_lossy(&bytes)))
}

#[tokio::test]
async fn admin_requires_browser_secondary_and_csrf_and_throttles_host() {
    let fixture = Fixture::configured();
    let unauth = fixture
        .call(request(Method::OPTIONS, POLICY, None, None, false))
        .await;
    assert_eq!(unauth.status(), StatusCode::OK);
    assert!(unauth.headers()["allow"].to_str().unwrap().contains("POST"));
    let mut bearer = request(Method::GET, EFFECTIVE, None, None, false);
    bearer.headers_mut().insert(
        AUTHORIZATION,
        HeaderValue::from_static("Bearer gateway-credential"),
    );
    assert_eq!(
        fixture.call(bearer).await.status(),
        StatusCode::UNAUTHORIZED
    );
    let denied = fixture
        .call(request(
            Method::POST,
            VERIFY,
            Some(json!({"password":PASSWORD})),
            None,
            false,
        ))
        .await;
    assert!(denied.status().is_redirection());
    assert_eq!(denied.headers()[LOCATION], "/login");

    let first = fixture.login().await;
    let second = fixture.login().await;
    let pending = fixture.json(Method::GET, SECONDARY, None, &first).await;
    assert_eq!(
        pending,
        json!({"success":true,"verified":false,"configured":true,"ttl":1800})
    );
    assert!(fixture.json(Method::GET, EFFECTIVE, None, &first).await["data"]["categories"]["read_focus"].is_object());
    let denied = fixture
        .call(request(Method::GET, POLICY, None, Some(&first), false))
        .await;
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
    assert_eq!(json_response(denied).await["code"], "secondary_required");

    let mut origin = request(
        Method::POST,
        VERIFY,
        Some(json!({"password":PASSWORD})),
        Some(&first),
        false,
    );
    origin.headers_mut().insert(
        ORIGIN,
        HeaderValue::from_static("https://another-origin.invalid"),
    );
    assert_eq!(fixture.call(origin).await.status(), StatusCode::FORBIDDEN);
    fixture.verify(&first).await;
    assert_eq!(
        fixture.json(Method::GET, SECONDARY, None, &first).await["verified"],
        true
    );
    assert_eq!(
        fixture.json(Method::GET, SECONDARY, None, &second).await["verified"],
        false
    );
    assert_eq!(
        fixture
            .call(request(
                Method::POST,
                POLICY,
                Some(json!({"target_type":"global"})),
                Some(&first),
                false
            ))
            .await
            .status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        fixture
            .call(request(Method::POST, RELOAD, None, Some(&first), false))
            .await
            .status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        fixture
            .call(request(Method::GET, "/logout", None, Some(&first), false))
            .await
            .status(),
        StatusCode::FOUND
    );
    assert!(
        fixture
            .call(request(Method::GET, SECONDARY, None, Some(&first), false))
            .await
            .status()
            .is_redirection()
    );
    let fresh = fixture.login().await;
    assert_eq!(
        fixture.json(Method::GET, SECONDARY, None, &fresh).await["verified"],
        false
    );
    for _ in 0..4 {
        let denied = fixture
            .call(request(
                Method::POST,
                VERIFY,
                Some(json!({"password":"wrong"})),
                Some(&second),
                false,
            ))
            .await;
        assert_eq!(denied.status(), StatusCode::UNAUTHORIZED);
    }
    let throttled = fixture
        .call(request(
            Method::POST,
            VERIFY,
            Some(json!({"password":PASSWORD})),
            Some(&fresh),
            false,
        ))
        .await;
    assert_eq!(throttled.status(), StatusCode::TOO_MANY_REQUESTS);
    assert!(
        json_response(throttled).await["retry_after"]
            .as_u64()
            .unwrap()
            > 0
    );
}

#[tokio::test]
async fn saves_python_policy_and_applies_it_to_current_rust_enforcement() {
    let fixture = Fixture::configured();
    let browser = fixture.login().await;
    fixture.verify(&browser).await;
    let saved = fixture.json(Method::GET, POLICY, None, &browser).await;
    assert_eq!(
        saved["defaults"]["models"],
        json!(["fixture_hidden", "fixture_model"])
    );
    assert!(
        saved["defaults"]["ui_block_keys"]
            .as_array()
            .unwrap()
            .contains(&json!("block_realtime_terminal"))
    );
    let policy_file = fixture.root.join("admin_policy.json");
    let mut initial = saved["data"].clone();
    initial["retained_metadata"] = json!({"from":"python"});
    fs::write(&policy_file, initial.to_string()).unwrap();
    fixture.json(Method::POST, POLICY, Some(json!({"target_type":"global","config":{
        "ui_blocks":{"block_realtime_terminal":true,"unsupported_ui_flag":true},
        "forced_category_states":{"read_focus":false,"memory":"fallback"},
        "disabled_models":["fixture_hidden","fixture_model","missing","fixture_hidden"],
        "remove_categories":["network","network","mcp"],
        "category_overrides":{"fixture":{"label":"Example","tools":["sandbox_example",4]},"mcp":{"label":"MCP label","tools":["stale_mcp_alias"]}}
    }})), &browser).await;
    fixture.json(Method::POST, POLICY, Some(json!({"target_type":"role","target_value":"admin","config":{"ui_blocks":{"block_personal_space":true}}})), &browser).await;
    fixture.json(Method::POST, POLICY, Some(json!({"target_type":"user","target_value":"host","config":{"ui_blocks":{"block_personal_space":false}}})), &browser).await;
    let effective = fixture.json(Method::GET, EFFECTIVE, None, &browser).await;
    let effective = &effective["data"];
    assert_eq!(
        effective["ui_blocks"],
        json!({"block_realtime_terminal":true,"block_personal_space":false})
    );
    assert_eq!(
        effective["forced_category_states"],
        json!({"read_focus":false,"memory": null})
    );
    assert_eq!(
        effective["disabled_models"],
        json!(["fixture_hidden", "fixture_model"])
    );
    assert!(effective["categories"]["network"].is_null());
    assert_eq!(
        effective["categories"]["mcp"]["tools"],
        json!(["list_mcp_servers"])
    );
    assert_eq!(
        effective["categories"]["fixture"]["tools"],
        json!(["sandbox_example"])
    );
    let installed =
        crate::web_status::admin_policy(&fixture.root, &fixture.catalog.load()).unwrap();
    assert_eq!(installed["ui_blocks"], effective["ui_blocks"]);
    assert_eq!(installed["disabled_models"], effective["disabled_models"]);
    let stored: Value = serde_json::from_slice(&fs::read(policy_file).unwrap()).unwrap();
    assert_eq!(stored["retained_metadata"]["from"], "python");
    assert_eq!(
        stored["global"]["remove_categories"],
        json!(["mcp", "network"])
    );
}

#[tokio::test]
async fn manages_the_persisted_custom_tool_registry_and_explicitly_reloads_file_edits() {
    let fixture = Fixture::configured();
    let browser = fixture.login().await;
    fixture.verify(&browser).await;
    let registered = fixture.json(Method::POST, TOOLS, Some(json!({"id":"fixture-tool","description":"Fixture tool","execution_code":"print('first')\n","return":{"truncate":100},"meta":{"notes":"test"}})), &browser).await;
    assert_eq!(registered["data"]["execution"]["type"], "python");
    let tool_dir = fixture.root.join("custom_tools/fixture-tool");
    assert_eq!(
        fs::read_to_string(tool_dir.join("execution.py")).unwrap(),
        "print('first')\n"
    );
    for name in ["definition.json", "return.json", "meta.json"] {
        assert!(tool_dir.join(name).is_file());
    }
    assert_eq!(
        fixture.json(Method::GET, EFFECTIVE, None, &browser).await["data"]["categories"]["custom"]
            ["tools"],
        json!(["fixture-tool"])
    );
    fixture
        .json(
            Method::POST,
            TOOL_FILE,
            Some(json!({"id":"fixture-tool","name":"execution.py","content":"print('edited')\n"})),
            &browser,
        )
        .await;
    let response = fixture
        .call(request(
            Method::GET,
            &format!("{TOOL_FILE}?id=fixture-tool&name=execution.py"),
            None,
            Some(&browser),
            false,
        ))
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        response.headers()[CONTENT_TYPE]
            .to_str()
            .unwrap()
            .starts_with("text/plain")
    );
    assert_eq!(
        to_bytes(response.into_body(), 1024).await.unwrap(),
        "print('edited')\n"
    );
    assert_eq!(
        fixture.json(Method::GET, TOOLS, None, &browser).await["data"][0]["execution_code"],
        "print('first')\n"
    );
    fixture.json(Method::POST, RELOAD, None, &browser).await;
    assert_eq!(
        fixture.json(Method::GET, TOOLS, None, &browser).await["data"][0]["execution_code"],
        "print('edited')\n"
    );
    let restart =
        ToolRegistry::open(&fixture.root, fixture.root.join("custom_tools"), true).unwrap();
    assert_eq!(
        restart.list().ok().unwrap()["data"][0]["execution_code"],
        "print('edited')\n"
    );
    for path in [
        format!("{TOOL_FILE}?id=..%2f..&name=execution.py"),
        format!("{TOOL_FILE}?id=fixture-tool&name=..%2f..%2fadmin_policy.json"),
    ] {
        assert_eq!(
            fixture
                .call(request(Method::GET, &path, None, Some(&browser), false))
                .await
                .status(),
            StatusCode::BAD_REQUEST
        );
    }
    let reserved = fixture
        .call(request(
            Method::POST,
            TOOLS,
            Some(json!({"id":"read_file"})),
            Some(&browser),
            true,
        ))
        .await;
    assert_eq!(reserved.status(), StatusCode::BAD_REQUEST);
    #[cfg(unix)]
    {
        let outside = fixture.root.join("outside.txt");
        fs::write(&outside, "untouched").unwrap();
        fs::remove_file(tool_dir.join("execution.py")).unwrap();
        std::os::unix::fs::symlink(&outside, tool_dir.join("execution.py")).unwrap();
        let write = fixture
            .call(request(
                Method::POST,
                TOOL_FILE,
                Some(json!({"id":"fixture-tool","name":"execution.py","content":"overwrite"})),
                Some(&browser),
                true,
            ))
            .await;
        assert_ne!(write.status(), StatusCode::OK);
        assert_eq!(fs::read_to_string(outside).unwrap(), "untouched");
    }
    fixture
        .json(
            Method::DELETE,
            &format!("{TOOLS}?id=fixture-tool"),
            None,
            &browser,
        )
        .await;
    assert!(!tool_dir.exists());
    assert_eq!(
        fixture.json(Method::GET, TOOLS, None, &browser).await["data"],
        json!([])
    );
}

#[tokio::test]
async fn secondary_distinguishes_unconfigured_and_expiring_werkzeug_hashes() {
    let fixture = Fixture::new(json!({}));
    let browser = fixture.login().await;
    assert_eq!(
        fixture.json(Method::GET, SECONDARY, None, &browser).await["configured"],
        false
    );
    assert_eq!(
        fixture
            .call(request(
                Method::POST,
                VERIFY,
                Some(json!({"password":PASSWORD})),
                Some(&browser),
                false
            ))
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );

    let hash = "pbkdf2:sha256:2$fixture-salt$d0e09dc42898f2a6deb24b54ae23e8aabad77b9b149c4e4067d7d07ff3664e93";
    let fixture = Fixture::new(json!({"admin":{"secondary_password_hash":hash},"env_vars":{
        "ADMIN_SECONDARY_PASSWORD":"wrong", "AGENT_ADMIN_SECONDARY_TTL_SECONDS":"1"
    }}));
    let browser = fixture.login().await;
    fixture.verify(&browser).await;
    assert_eq!(
        fixture.json(Method::GET, SECONDARY, None, &browser).await["verified"],
        true
    );
    tokio::time::sleep(std::time::Duration::from_millis(1050)).await;
    assert_eq!(
        fixture.json(Method::GET, SECONDARY, None, &browser).await["verified"],
        false
    );
    assert_eq!(
        fixture
            .call(request(Method::GET, POLICY, None, Some(&browser), true))
            .await
            .status(),
        StatusCode::FORBIDDEN
    );
}
