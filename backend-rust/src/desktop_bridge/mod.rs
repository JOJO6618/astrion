//! Browser-session REST endpoints for the desktop shell's loopback control bridge.

mod update;

use crate::web_assets::personalization::english;
use axum::{
    Json, Router,
    extract::{Path, State, rejection::JsonRejection},
    http::{Method, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use reqwest::{Client, redirect::Policy};
use serde_json::{Value, json};
use std::{env, path::PathBuf, sync::Arc, time::Duration};

#[derive(Clone)]
pub struct DesktopBridge(Arc<Bridge>);

struct Bridge {
    data: PathBuf,
    desktop: Option<Desktop>,
    local: Client,
    manifest: Client,
    update_cache: update::Cache,
}

struct Desktop {
    version: String,
    port: Option<u16>,
    build_id: String,
    platform: String,
    arch: String,
    policy_protocol: bool,
}

impl Desktop {
    fn from_env() -> Option<Self> {
        let version = env::var("ASTRION_DESKTOP_VERSION").unwrap_or_default();
        let version = version.trim();
        if version.is_empty() {
            return None;
        }
        let native_platform = match env::consts::OS {
            "macos" => "darwin",
            "windows" => "win32",
            platform => platform,
        };
        let native_arch = match (env::consts::OS, env::consts::ARCH) {
            ("macos", "aarch64") => "arm64",
            ("windows", "x86_64") => "AMD64",
            ("windows", "aarch64") => "ARM64",
            (_, arch) => arch,
        };
        Some(Self {
            version: version.to_owned(),
            port: env::var("ASTRION_DESKTOP_BRIDGE_PORT")
                .ok()
                .and_then(|port| parse_port(&port)),
            build_id: env::var("ASTRION_DESKTOP_BUILD_ID").unwrap_or_else(|_| "legacy".into()),
            platform: env::var("ASTRION_DESKTOP_PLATFORM")
                .unwrap_or_else(|_| native_platform.into()),
            arch: env::var("ASTRION_DESKTOP_ARCH").unwrap_or_else(|_| native_arch.into()),
            policy_protocol: env::var("ASTRION_DESKTOP_POLICY_PROTOCOL").as_deref() == Ok("1"),
        })
    }
}

fn parse_port(value: &str) -> Option<u16> {
    let port = value.trim();
    if port.bytes().all(|byte| byte.is_ascii_digit()) {
        port.parse().ok().filter(|port| *port != 0)
    } else {
        None
    }
}

impl DesktopBridge {
    pub fn from_env(data: impl Into<PathBuf>) -> Result<Self, reqwest::Error> {
        let local = Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .build()?;
        Ok(Self(Arc::new(Bridge {
            data: data.into(),
            desktop: Desktop::from_env(),
            local,
            manifest: Client::builder().build()?,
            update_cache: update::Cache::default(),
        })))
    }

    /// Mount with the entry point's browser-session API guard.
    pub fn router(&self) -> Router {
        Router::new()
            .route(
                "/api/desktop/quick/{operation}",
                get(quick_get).post(quick_post),
            )
            .route("/api/desktop/window/drag", post(window_drag))
            .route("/api/desktop/window/control", post(window_control))
            .route("/api/desktop/window/state", get(window_state))
            .route("/api/desktop/update/check", get(update::check))
            .route("/api/desktop/update/install", post(update_install))
            .route("/api/desktop/update/progress", get(update_progress))
            .route("/api/desktop/rundata/info", get(rundata_info))
            .route("/api/desktop/rundata/choose", post(rundata_choose))
            .route("/api/desktop/rundata/apply", post(rundata_apply))
            .route("/api/desktop/rundata/restart", post(rundata_restart))
            .with_state(self.0.clone())
    }
}

#[derive(Debug)]
enum BridgeError {
    NotDesktop,
    Unavailable,
    Unreachable(String),
    InvalidAction,
    UnsupportedPlatform,
    CheckFailed(String),
}

impl BridgeError {
    fn response(self, bridge: &Bridge) -> Response {
        let (code, zh, en, detail) = match self {
            Self::NotDesktop => (
                "not_desktop",
                "当前环境不是桌面应用",
                "Not running inside the desktop app",
                None,
            ),
            Self::Unavailable => (
                "bridge_unavailable",
                "更新服务不可用，请重启应用后重试",
                "Update service unavailable, please restart the app and retry",
                None,
            ),
            Self::Unreachable(detail) => (
                "bridge_unreachable",
                "无法连接更新服务",
                "Cannot reach the update service",
                Some(detail),
            ),
            Self::InvalidAction => (
                "invalid_action",
                "不支持的标签操作",
                "Unsupported tab action",
                None,
            ),
            Self::UnsupportedPlatform => (
                "unsupported_platform",
                "当前平台不支持自动更新",
                "Auto-update is not supported on this platform",
                None,
            ),
            Self::CheckFailed(detail) => (
                "check_failed",
                "检查更新失败，请稍后重试",
                "Failed to check for updates, please try again later",
                Some(detail),
            ),
        };
        let mut payload = json!({"success": false, "code": code, "error": if english(&bridge.data) { en } else { zh }});
        if let Some(detail) = detail {
            payload["detail"] = Value::String(detail.chars().take(200).collect());
        }
        Json(payload).into_response()
    }
}

impl Bridge {
    fn desktop(&self) -> Result<&Desktop, BridgeError> {
        self.desktop.as_ref().ok_or(BridgeError::NotDesktop)
    }

    async fn request(
        &self,
        method: Method,
        route: &str,
        seconds: u64,
        payload: Option<Value>,
    ) -> Result<Value, BridgeError> {
        let port = self.desktop()?.port.ok_or(BridgeError::Unavailable)?;
        let url = format!("http://127.0.0.1:{port}{route}");
        let mut request = self
            .local
            .request(method, &url)
            .timeout(Duration::from_secs(seconds));
        if let Some(payload) = payload {
            request = request.json(&payload);
        }
        let error = |error: reqwest::Error| BridgeError::Unreachable(error.to_string());
        let response = request.send().await.map_err(error)?;
        if !response.status().is_success() {
            return Err(BridgeError::Unreachable(format!(
                "HTTP {} for {url}",
                response.status()
            )));
        }
        response.json().await.map_err(error)
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

type OptionalJson = Result<Json<Value>, JsonRejection>;

enum Reply {
    Raw(StatusCode),
    Wrapped,
    Drag,
    Install,
}

impl Reply {
    fn send(self, result: Result<Value, BridgeError>, bridge: &Bridge) -> Response {
        let body = match result {
            Ok(body) => body,
            Err(error) => return error.response(bridge),
        };
        let (status, body) = match self {
            Self::Raw(status) => (status, body),
            Self::Wrapped => (StatusCode::OK, json!({"success": true, "data": body})),
            Self::Drag => (
                StatusCode::OK,
                json!({"success": truthy(&body["started"]), "data": body}),
            ),
            Self::Install => {
                let started = truthy(&body["started"]);
                let status = if started {
                    StatusCode::ACCEPTED
                } else {
                    StatusCode::CONFLICT
                };
                (status, json!({"success": started, "data": body}))
            }
        };
        (status, Json(body)).into_response()
    }
}

async fn quick_get(
    State(bridge): State<Arc<Bridge>>,
    Path(operation): Path<String>,
    method: Method,
) -> Response {
    quick(&bridge, method, &operation, None).await
}

async fn quick_post(
    State(bridge): State<Arc<Bridge>>,
    Path(operation): Path<String>,
    body: OptionalJson,
) -> Response {
    let payload = body.ok().map(|Json(value)| value).filter(Value::is_object);
    quick(&bridge, Method::POST, &operation, payload).await
}

async fn quick(
    bridge: &Bridge,
    method: Method,
    operation: &str,
    payload: Option<Value>,
) -> Response {
    let route = match (method.clone(), operation) {
        (Method::GET, "info") => "/quick/info",
        (Method::GET, "permissions") => "/quick/permissions",
        (Method::POST, "configure") => "/quick/configure",
        (Method::POST, "open") => "/quick/open",
        (Method::POST, "capture-permission") => "/quick/capture-permission",
        (Method::POST, "input-permission") => "/quick/input-permission",
        _ => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"ok": false, "error": "unsupported_quick_operation"})),
            )
                .into_response();
        }
    };
    Reply::Raw(StatusCode::OK).send(bridge.request(method, route, 10, payload).await, bridge)
}

async fn window_drag(State(bridge): State<Arc<Bridge>>) -> Response {
    Reply::Drag.send(
        bridge.request(Method::POST, "/window/drag", 3, None).await,
        &bridge,
    )
}

async fn window_control(State(bridge): State<Arc<Bridge>>, body: OptionalJson) -> Response {
    if let Err(error) = bridge.desktop() {
        return error.response(&bridge);
    }
    let body = body.ok().map(|Json(value)| value).unwrap_or_default();
    let action = body["action"].as_str().unwrap_or_default().trim();
    if !matches!(action, "minimize" | "maximize-toggle" | "close") {
        return BridgeError::InvalidAction.response(&bridge);
    }
    let result = bridge
        .request(
            Method::POST,
            "/window/control",
            3,
            Some(json!({"action": action})),
        )
        .await;
    Reply::Wrapped.send(result, &bridge)
}

async fn window_state(State(bridge): State<Arc<Bridge>>) -> Response {
    Reply::Wrapped.send(
        bridge.request(Method::GET, "/window/state", 3, None).await,
        &bridge,
    )
}

async fn update_install(State(bridge): State<Arc<Bridge>>) -> Response {
    Reply::Install.send(
        bridge
            .request(Method::POST, "/update/install", 12, None)
            .await,
        &bridge,
    )
}

async fn update_progress(State(bridge): State<Arc<Bridge>>) -> Response {
    Reply::Wrapped.send(
        bridge
            .request(Method::GET, "/update/progress", 3, None)
            .await,
        &bridge,
    )
}

async fn rundata_info(State(bridge): State<Arc<Bridge>>) -> Response {
    Reply::Raw(StatusCode::OK).send(
        bridge.request(Method::GET, "/rundata/info", 10, None).await,
        &bridge,
    )
}

async fn rundata_choose(State(bridge): State<Arc<Bridge>>) -> Response {
    Reply::Raw(StatusCode::OK).send(
        bridge
            .request(Method::POST, "/rundata/choose", 300, Some(json!({})))
            .await,
        &bridge,
    )
}

async fn rundata_apply(State(bridge): State<Arc<Bridge>>, body: OptionalJson) -> Response {
    let payload = body
        .ok()
        .map(|Json(value)| value)
        .filter(truthy)
        .unwrap_or_else(|| json!({}));
    let result = bridge
        .request(Method::POST, "/rundata/apply", 1800, Some(payload))
        .await;
    Reply::Raw(StatusCode::OK).send(result, &bridge)
}

async fn rundata_restart(State(bridge): State<Arc<Bridge>>) -> Response {
    Reply::Raw(StatusCode::ACCEPTED).send(
        bridge
            .request(Method::POST, "/rundata/restart", 5, Some(json!({})))
            .await,
        &bridge,
    )
}
