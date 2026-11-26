use std::{
    net::{Ipv4Addr, Ipv6Addr},
    sync::Arc,
    time::Duration,
};

use axum::{
    Router,
    extract::{Query, State},
    http::{StatusCode, header},
    response::{Html, IntoResponse, Response},
    routing::get,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use reqwest::Client;
use ring::{
    digest,
    rand::{SecureRandom, SystemRandom},
};
use serde::Deserialize;
use serde_json::{Value, json};
use subtle::ConstantTimeEq;
use tokio::{
    net::TcpListener,
    sync::{Mutex, oneshot, watch},
};

use super::super::codex_oauth::{CodexConfig, OAUTH_CLIENT_ID, oauth_client};
use super::{
    Flow,
    accounts::{Tokens, nonempty},
};

const AUTHORIZE_URL: &str = "https://auth.openai.com/oauth/authorize";
const BROWSER_REDIRECT_URI: &str = "http://localhost:1455/auth/callback";
const CALLBACK_PORT: u16 = 1455;
const DEVICE_REDIRECT_URI: &str = "https://auth.openai.com/deviceauth/callback";
pub(super) const DEVICE_VERIFY_URL: &str = "https://auth.openai.com/codex/device";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const DEVICE_POLL_INTERVAL: Duration = Duration::from_secs(5);

pub(super) struct BrowserLogin {
    listeners: [TcpListener; 2],
    oauth_state: String,
    verifier: String,
    url: reqwest::Url,
}

impl BrowserLogin {
    pub async fn bind() -> Result<Self, String> {
        let (oauth_state, verifier, url) = browser_parameters()?;
        let listeners = bind_loopback(CALLBACK_PORT).await.map_err(|error| {
            format!("无法监听 {CALLBACK_PORT} 端口（{error}）。回调端口可能被其他程序占用，请关闭后重试。")
        })?;
        Ok(Self {
            listeners,
            oauth_state,
            verifier,
            url,
        })
    }

    pub fn authorize_url(&self) -> &str {
        self.url.as_str()
    }

    pub async fn authorize(self, config: &CodexConfig, flow: &Flow) -> Result<Tokens, String> {
        let code = wait_for_callback(self.listeners, self.oauth_state).await?;
        flow.exchanging().await;
        let client = oauth_client(config)
            .await
            .map_err(|_| "无法初始化 OAuth 网络连接".to_owned())?;
        exchange(
            &client,
            config.token_url(),
            &code,
            &self.verifier,
            BROWSER_REDIRECT_URI,
        )
        .await
    }
}

async fn wait_for_callback(
    listeners: [TcpListener; 2],
    oauth_state: String,
) -> Result<String, String> {
    let (sender, receiver) = oneshot::channel();
    let callback = callback_router(oauth_state, sender);
    let (stop, _) = watch::channel(false);
    let [ipv4, ipv6] = listeners;
    let ipv4 = serve_callback(ipv4, callback.clone(), stop.subscribe());
    let ipv6 = serve_callback(ipv6, callback, stop.subscribe());
    tokio::pin!(ipv4, ipv6);
    let code = tokio::select! {
        code = receiver => code.map_err(|_| "OAuth 回调服务已关闭".to_owned())?,
        _ = &mut ipv4 => return Err("OAuth 回调服务已关闭".into()),
        _ = &mut ipv6 => return Err("OAuth 回调服务已关闭".into()),
    };
    let _ = stop.send(true);
    // Allow the callback response to reach the browser before dropping the servers.
    let _ = tokio::time::timeout(Duration::from_millis(250), async {
        let _ = tokio::join!(ipv4, ipv6);
    })
    .await;
    code
}

async fn serve_callback(
    listener: TcpListener,
    router: Router,
    mut stop: watch::Receiver<bool>,
) -> std::io::Result<()> {
    axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            let _ = stop.wait_for(|stop| *stop).await;
        })
        .await
}

async fn bind_loopback(port: u16) -> std::io::Result<[TcpListener; 2]> {
    let v4 = TcpListener::bind((Ipv4Addr::LOCALHOST, port)).await?;
    let v6 = TcpListener::bind((Ipv6Addr::LOCALHOST, v4.local_addr()?.port())).await?;
    Ok([v4, v6])
}

fn browser_parameters() -> Result<(String, String, reqwest::Url), String> {
    let rng = SystemRandom::new();
    let mut verifier = [0u8; 48];
    let mut oauth_state = [0u8; 24];
    rng.fill(&mut verifier)
        .and_then(|_| rng.fill(&mut oauth_state))
        .map_err(|_| "无法生成 OAuth 安全参数".to_owned())?;
    let verifier = URL_SAFE_NO_PAD.encode(verifier);
    let oauth_state = URL_SAFE_NO_PAD.encode(oauth_state);
    let challenge = URL_SAFE_NO_PAD.encode(digest::digest(&digest::SHA256, verifier.as_bytes()));
    let mut url = reqwest::Url::parse(AUTHORIZE_URL).expect("static OAuth authorization URL");
    url.query_pairs_mut().extend_pairs([
        ("response_type", "code"),
        ("client_id", OAUTH_CLIENT_ID),
        ("redirect_uri", BROWSER_REDIRECT_URI),
        ("scope", "openid profile email offline_access"),
        ("code_challenge", &challenge),
        ("code_challenge_method", "S256"),
        ("state", &oauth_state),
        ("id_token_add_organizations", "true"),
        ("codex_cli_simplified_flow", "true"),
    ]);
    Ok((oauth_state, verifier, url))
}

type CallbackSender = oneshot::Sender<Result<String, String>>;

struct Callback {
    oauth_state: String,
    sender: Mutex<Option<CallbackSender>>,
}

fn callback_router(oauth_state: String, sender: CallbackSender) -> Router {
    Router::new()
        .route("/auth/callback", get(callback))
        .with_state(Arc::new(Callback {
            oauth_state,
            sender: Mutex::new(Some(sender)),
        }))
}

#[derive(Deserialize)]
struct CallbackQuery {
    state: Option<String>,
    code: Option<String>,
    error: Option<String>,
}

async fn callback(
    State(callback): State<Arc<Callback>>,
    Query(query): Query<CallbackQuery>,
) -> Response {
    let state = query.state.as_deref().unwrap_or_default();
    if !bool::from(state.as_bytes().ct_eq(callback.oauth_state.as_bytes())) {
        return callback_page(StatusCode::BAD_REQUEST, "授权回调参数校验失败");
    }
    let code = if query.error.as_ref().is_some_and(|error| !error.is_empty()) {
        Err("授权页返回错误".to_owned())
    } else {
        query
            .code
            .filter(|code| !code.is_empty() && code.len() <= 4096)
            .ok_or_else(|| "回调参数校验失败（缺少 code）".to_owned())
    };
    let authorized = code.is_ok();
    let Some(sender) = callback.sender.lock().await.take() else {
        return callback_page(StatusCode::GONE, "授权回调已处理");
    };
    if sender.send(code).is_err() {
        return callback_page(StatusCode::GONE, "授权流程已结束");
    }
    if authorized {
        callback_page(
            StatusCode::OK,
            "Codex 授权成功，可以关闭此页面并回到 Astrion",
        )
    } else {
        callback_page(StatusCode::OK, "Codex 授权失败，请回到 Astrion 查看状态")
    }
}

fn callback_page(status: StatusCode, message: &'static str) -> Response {
    let html = format!(
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\"><title>Codex 登录</title></head><body><h2>{message}</h2></body></html>"
    );
    (
        status,
        [
            (header::CACHE_CONTROL, "no-store"),
            (header::REFERRER_POLICY, "no-referrer"),
        ],
        Html(html),
    )
        .into_response()
}

pub(super) struct DeviceEndpoints {
    pub code: String,
    pub poll: String,
}

impl Default for DeviceEndpoints {
    fn default() -> Self {
        Self {
            code: "https://auth.openai.com/api/accounts/deviceauth/usercode".into(),
            poll: "https://auth.openai.com/api/accounts/deviceauth/token".into(),
        }
    }
}

pub(super) async fn device_tokens(
    config: &CodexConfig,
    flow: &Flow,
    endpoints: &DeviceEndpoints,
) -> Result<Tokens, String> {
    let client = oauth_client(config)
        .await
        .map_err(|_| "无法初始化 OAuth 网络连接".to_owned())?;
    let response = client
        .post(&endpoints.code)
        .timeout(REQUEST_TIMEOUT)
        .header(header::ACCEPT, "application/json")
        .json(&json!({"client_id": OAUTH_CLIENT_ID}))
        .send()
        .await
        .map_err(|_| "请求设备码网络失败".to_owned())?;
    let status = response.status();
    if status != StatusCode::OK {
        let hint = if status == StatusCode::NOT_FOUND {
            "（设备码登录未开启：请在 ChatGPT 设置 → 安全中开启 device code authentication）"
        } else {
            ""
        };
        return Err(format!("请求设备码失败 (HTTP {}){hint}", status.as_u16()));
    }
    let payload: Value = response
        .json()
        .await
        .map_err(|_| "设备码响应不是合法 JSON".to_owned())?;
    let device_id = nonempty(&payload["device_auth_id"]);
    let user_code = nonempty(&payload["user_code"]).or_else(|| nonempty(&payload["usercode"]));
    let (Some(device_id), Some(user_code)) = (device_id, user_code) else {
        return Err("设备码响应缺少必要字段（device_auth_id/user_code）".into());
    };
    let interval = payload["interval"]
        .as_f64()
        .or_else(|| payload["interval"].as_str()?.trim().parse().ok())
        .filter(|value: &f64| *value > 0.0 && value.is_finite())
        .map(|value| Duration::from_secs_f64(value.clamp(0.001, 900.0)))
        .unwrap_or(DEVICE_POLL_INTERVAL);
    flow.waiting_for_device_code(user_code.to_owned()).await;
    let body = json!({"device_auth_id": device_id, "user_code": user_code});
    loop {
        tokio::time::sleep(interval).await;
        let Ok(response) = client
            .post(&endpoints.poll)
            .timeout(REQUEST_TIMEOUT)
            .header(header::ACCEPT, "application/json")
            .json(&body)
            .send()
            .await
        else {
            continue;
        };
        let status = response.status();
        match status {
            StatusCode::OK => {
                let Ok(payload) = response.json::<Value>().await else {
                    continue;
                };
                if let (Some(code), Some(verifier)) = (
                    nonempty(&payload["authorization_code"]),
                    nonempty(&payload["code_verifier"]),
                ) {
                    flow.exchanging().await;
                    return exchange(
                        &client,
                        config.token_url(),
                        code,
                        verifier,
                        DEVICE_REDIRECT_URI,
                    )
                    .await;
                }
            }
            StatusCode::NOT_FOUND | StatusCode::FORBIDDEN => {}
            StatusCode::BAD_REQUEST => {
                if response
                    .json::<Value>()
                    .await
                    .is_ok_and(|body| body["error"]["code"] == "deviceauth_authorization_pending")
                {
                    continue;
                }
                return Err(device_authorization_error(status));
            }
            _ => return Err(device_authorization_error(status)),
        }
    }
}

fn device_authorization_error(status: StatusCode) -> String {
    format!("设备授权失败 (HTTP {})", status.as_u16())
}

async fn exchange(
    client: &Client,
    token_url: &str,
    code: &str,
    verifier: &str,
    redirect_uri: &str,
) -> Result<Tokens, String> {
    let form = serde_urlencoded::to_string([
        ("grant_type", "authorization_code"),
        ("code", code),
        ("redirect_uri", redirect_uri),
        ("client_id", OAUTH_CLIENT_ID),
        ("code_verifier", verifier),
    ])
    .map_err(|_| "无法编码 OAuth 换取 token 请求".to_owned())?;
    for delay in [0, 2, 4] {
        if delay != 0 {
            tokio::time::sleep(Duration::from_secs(delay)).await;
        }
        let request = client
            .post(token_url)
            .timeout(REQUEST_TIMEOUT)
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(form.clone());
        let Ok(response) = request.send().await else {
            continue;
        };
        let status = response.status();
        if status != StatusCode::OK {
            // Provider error bodies can echo the submitted code or other secrets.
            return Err(format!("换取 token 失败 (HTTP {})", status.as_u16()));
        }
        let payload: Value = response
            .json()
            .await
            .map_err(|_| "token 响应不是合法 JSON".to_owned())?;
        return Tokens::parse(&payload).map_err(str::to_owned);
    }
    Err("换取 token 网络失败".into())
}
