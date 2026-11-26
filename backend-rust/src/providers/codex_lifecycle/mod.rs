//! Account status and independent browser/device OAuth login for the Host API.

mod accounts;
mod oauth;

use std::{future::Future, sync::Arc, time::Duration};

use axum::{
    Router,
    body::Bytes,
    extract::{Query, State},
    http::StatusCode,
    middleware,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::{
    sync::{Mutex, Notify, watch},
    time::Instant,
};

use super::{
    Failure, codex,
    codex_oauth::{self, CodexConfig},
    json_response, object_body, require_admin_session, store,
};
use accounts::{AccountKey, LoginTarget, Registration, Tokens, registry_lock};

pub(super) fn router(config: CodexConfig) -> Router {
    Router::new()
        .route("/api/codex/status", get(status))
        .route("/api/codex/login/start", post(start_browser))
        .route("/api/codex/login/device/start", post(start_device))
        .route("/api/codex/login/poll", get(poll))
        .route("/api/codex/login/cancel", post(cancel))
        .route("/api/codex/logout", post(logout))
        .route_layer(middleware::from_fn(require_admin_session))
        .with_state(Arc::new(Lifecycle::new(config)))
}

struct Lifecycle {
    config: CodexConfig,
    current: Mutex<Option<Arc<Flow>>>,
    device_endpoints: oauth::DeviceEndpoints,
}

impl Lifecycle {
    fn new(config: CodexConfig) -> Self {
        Self {
            config,
            current: Mutex::new(None),
            device_endpoints: oauth::DeviceEndpoints::default(),
        }
    }

    async fn snapshot(&self) -> Value {
        match &*self.current.lock().await {
            Some(flow) => flow.snapshot().await,
            None => json!({"status": "idle"}),
        }
    }

    async fn resolve(&self, requested: String) -> Result<Option<AccountKey>, Failure> {
        let data_dir = self.config.data_dir().to_path_buf();
        blocking(move || {
            let _registry = registry_lock();
            let index = codex::lifecycle_index(&data_dir).map_err(Failure::internal)?;
            Ok(accounts::resolve(&index, &requested))
        })
        .await
    }

    async fn start(self: &Arc<Self>, requested: String, mode: Mode) -> Result<Response, Failure> {
        let reconnect = if requested.is_empty() {
            None
        } else {
            Some(
                self.resolve(requested)
                    .await?
                    .ok_or_else(account_not_found)?,
            )
        };
        let mut current = self.current.lock().await;
        if let Some(flow) = &*current {
            let mut state = flow.snapshot().await;
            if state["status"] == "pending" {
                state["reused"] = json!(true);
                state["error"] = json!("login_in_progress");
                state["success"] = json!(false);
                return Ok(json_response(StatusCode::CONFLICT, state));
            }
            if matches!(flow.mode, Mode::Browser) {
                flow.wait_finished().await;
            }
        }
        *current = None;
        let flow = Arc::new(Flow::new(mode, reconnect));
        let mut payload = flow.snapshot().await;
        match mode {
            Mode::Browser => {
                let browser = oauth::BrowserLogin::bind()
                    .await
                    .map_err(Failure::internal)?;
                payload["authorize_url"] = json!(browser.authorize_url());
                let worker = flow.clone();
                let app = self.clone();
                tokio::spawn(async move {
                    let login = browser.authorize(&app.config, &worker);
                    app.run(&worker, login).await;
                    worker.finished.send_replace(true);
                });
            }
            Mode::Device => {
                let worker = flow.clone();
                let app = self.clone();
                tokio::spawn(async move {
                    let login = oauth::device_tokens(&app.config, &worker, &app.device_endpoints);
                    app.run(&worker, login).await;
                });
            }
        }
        *current = Some(flow);
        Ok(success(payload))
    }

    async fn cancel(&self) -> Value {
        let mut current = self.current.lock().await;
        if let Some(flow) = &*current {
            let mut state = flow.state.lock().await;
            match state.stage {
                Stage::Pending(_) => {
                    state.stage = Stage::Cancelled;
                    flow.stop.notify_one();
                    drop(state);
                    if matches!(flow.mode, Mode::Browser) {
                        flow.wait_finished().await;
                    }
                }
                Stage::Committed(_) | Stage::Completed(_) => return state.snapshot(flow.mode),
                Stage::Failed(_) | Stage::Cancelled => {}
            }
        }
        *current = None;
        json!({"status": "cancelled"})
    }

    async fn run(&self, flow: &Arc<Flow>, login: impl Future<Output = Result<Tokens, String>>) {
        let tokens = tokio::select! {
            biased;
            _ = flow.stop.notified() => return,
            result = tokio::time::timeout_at(flow.deadline, login) => match result {
                Ok(Ok(tokens)) => tokens,
                Ok(Err(error)) => { flow.fail(error).await; return; }
                Err(_) => { flow.fail(flow.mode.timeout_message().into()).await; return; }
            },
        };
        let registration_id = match self.commit(flow, tokens).await {
            Ok(Some(id)) => id,
            Ok(None) => return,
            Err(error) => {
                flow.fail(error).await;
                return;
            }
        };
        let result = codex_oauth::refresh_account(&self.config, &registration_id).await;
        let lock = codex_oauth::account_lock(self.config.data_dir(), &registration_id);
        let _account = lock.lock().await;
        let mut state = flow.state.lock().await;
        if let Stage::Committed(registration) = &state.stage {
            state.stage = Stage::Completed(registration.clone());
            state.models_refresh_failed =
                result.result.get("refreshed") != Some(&Value::Bool(true));
        }
    }

    async fn commit(&self, flow: &Arc<Flow>, tokens: Tokens) -> Result<Option<String>, String> {
        let data_dir = self.config.data_dir().to_path_buf();
        let reconnect = flow.reconnect.clone();
        let target = tokio::task::spawn_blocking({
            let data_dir = data_dir.clone();
            move || {
                let _registry = registry_lock();
                let index = codex::lifecycle_index(&data_dir)?;
                let target = LoginTarget::select(&data_dir, &index, &tokens, reconnect.as_ref())?;
                Ok::<_, String>((target, tokens))
            }
        })
        .await
        .map_err(|_| "登录流程内部错误".to_owned())??;
        let (target, tokens) = target;
        let lock = codex_oauth::account_lock(&data_dir, &target.id);
        let account = tokio::select! {
            biased;
            _ = flow.stop.notified() => return Ok(None),
            _ = tokio::time::sleep_until(flow.deadline) => return Err(flow.mode.timeout_message().into()),
            account = lock.lock_owned() => account,
        };
        let worker = flow.clone();
        tokio::task::spawn_blocking(move || {
            let _account = account;
            let mut state = worker.state.blocking_lock();
            if !matches!(state.stage, Stage::Pending(_)) {
                return Ok(None);
            }
            if Instant::now() >= worker.deadline {
                return Err(worker.mode.timeout_message().into());
            }
            let _registry = registry_lock();
            let index = codex::lifecycle_index(&data_dir)?;
            let registration =
                target.commit(&data_dir, index, tokens, worker.reconnect.is_some())?;
            let id = registration.connection.id.clone();
            state.stage = Stage::Committed(registration);
            Ok(Some(id))
        })
        .await
        .map_err(|_| "登录流程内部错误".to_owned())?
    }

    async fn logout(self: Arc<Self>, account: AccountKey) -> Result<Value, Failure> {
        let data_dir = self.config.data_dir().to_owned();
        let lock = codex_oauth::account_lock(&data_dir, &account.id);
        let _account = lock.lock().await;
        let worker_account = account.clone();
        let value = blocking(move || {
            let _registry = registry_lock();
            let index = codex::lifecycle_index(&data_dir).map_err(Failure::internal)?;
            if !worker_account.matches(&index) {
                return Err(account_not_found());
            }
            accounts::logout(&data_dir, &worker_account, index).map_err(|error| Failure {
                status: StatusCode::INTERNAL_SERVER_ERROR,
                body: json!({
                    "success": false, "error": "logout_failed", "account_id": worker_account.id,
                    "detail": error.to_string(),
                }),
            })
        })
        .await?;
        if let Some(flow) = &*self.current.lock().await {
            let mut state = flow.state.lock().await;
            match &state.stage {
                Stage::Pending(_) if flow.reconnect.as_ref() == Some(&account) => {
                    state.stage = Stage::Failed("reconnect_account_mismatch".into());
                    flow.stop.notify_one();
                }
                Stage::Committed(registration) if registration.connection == account => {
                    state.stage = Stage::Failed("account_not_found".into());
                }
                _ => {}
            }
        }
        Ok(value)
    }
}

#[derive(Clone, Copy)]
enum Mode {
    Browser,
    Device,
}

impl Mode {
    fn label(self) -> &'static str {
        match self {
            Self::Browser => "browser",
            Self::Device => "device",
        }
    }

    fn duration(self) -> Duration {
        match self {
            Self::Browser => Duration::from_secs(5 * 60),
            Self::Device => Duration::from_secs(15 * 60),
        }
    }

    fn timeout_message(self) -> &'static str {
        match self {
            Self::Browser => "登录超时（5 分钟未完成授权）",
            Self::Device => "登录超时（15 分钟未完成授权）",
        }
    }
}

#[derive(Clone, Copy)]
enum Phase {
    Starting,
    Waiting,
    Exchanging,
}

impl Phase {
    fn label(self) -> &'static str {
        match self {
            Self::Starting => "starting",
            Self::Waiting => "waiting",
            Self::Exchanging => "exchanging",
        }
    }
}

enum Stage {
    Pending(Phase),
    Committed(Registration),
    Completed(Registration),
    Failed(String),
    Cancelled,
}

struct FlowState {
    stage: Stage,
    user_code: Option<String>,
    models_refresh_failed: bool,
}

impl FlowState {
    fn snapshot(&self, mode: Mode) -> Value {
        let (status, phase, registration, error) = match &self.stage {
            Stage::Pending(phase) => ("pending", Some(phase.label()), None, None),
            Stage::Committed(registration) => (
                "pending",
                Some("refreshing_models"),
                Some(registration),
                None,
            ),
            Stage::Completed(registration) => ("completed", None, Some(registration), None),
            Stage::Failed(error) => ("failed", None, None, Some(error)),
            Stage::Cancelled => ("cancelled", None, None, None),
        };
        let mut payload = json!({
            "status": status, "error": error, "flow_mode": mode.label(),
            "models_refresh_failed": self.models_refresh_failed,
            "account_id": registration.and_then(|value| value.account_id.as_deref()),
            "connection_id": registration.map(|value| &value.connection.id),
            "account_hash": registration.map(|value| &value.hash),
        });
        if let Some(phase) = phase {
            payload["phase"] = json!(phase);
        }
        if matches!(mode, Mode::Device) {
            payload["user_code"] = json!(self.user_code);
            payload["verification_uri"] =
                json!(self.user_code.as_ref().map(|_| oauth::DEVICE_VERIFY_URL));
        }
        payload
    }
}

struct Flow {
    mode: Mode,
    reconnect: Option<AccountKey>,
    deadline: Instant,
    state: Mutex<FlowState>,
    stop: Notify,
    finished: watch::Sender<bool>,
}

impl Flow {
    fn new(mode: Mode, reconnect: Option<AccountKey>) -> Self {
        let phase = match mode {
            Mode::Browser => Phase::Waiting,
            Mode::Device => Phase::Starting,
        };
        Self {
            mode,
            reconnect,
            deadline: Instant::now() + mode.duration(),
            stop: Notify::new(),
            finished: watch::channel(false).0,
            state: Mutex::new(FlowState {
                stage: Stage::Pending(phase),
                user_code: None,
                models_refresh_failed: false,
            }),
        }
    }

    async fn snapshot(&self) -> Value {
        self.state.lock().await.snapshot(self.mode)
    }

    async fn wait_finished(&self) {
        let mut finished = self.finished.subscribe();
        let _ = finished.wait_for(|finished| *finished).await;
    }

    async fn fail(&self, error: String) {
        let mut state = self.state.lock().await;
        if matches!(state.stage, Stage::Pending(_)) {
            state.stage = Stage::Failed(error);
        }
    }

    async fn waiting_for_device_code(&self, code: String) {
        let mut state = self.state.lock().await;
        if matches!(state.stage, Stage::Pending(_)) {
            state.user_code = Some(code);
            state.stage = Stage::Pending(Phase::Waiting);
        }
    }

    async fn exchanging(&self) {
        let mut state = self.state.lock().await;
        if matches!(state.stage, Stage::Pending(_)) {
            state.stage = Stage::Pending(Phase::Exchanging);
        }
    }
}

#[derive(Deserialize)]
struct AccountQuery {
    account_id: Option<String>,
}

impl AccountQuery {
    fn requested(self, body: &Bytes) -> String {
        self.account_id.map_or_else(
            || {
                object_body(body)
                    .get("account_id")
                    .map(store::trimmed)
                    .unwrap_or_default()
            },
            |value| value.trim().to_owned(),
        )
    }
}

async fn status(
    State(app): State<Arc<Lifecycle>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Result<Response, Failure> {
    let requested = query.requested(&body);
    let data_dir = app.config.data_dir().to_owned();
    let mut payload = blocking(move || {
        let _registry = registry_lock();
        let index = codex::lifecycle_index(&data_dir).map_err(Failure::internal)?;
        match accounts::resolve(&index, &requested) {
            Some(account) => Ok(accounts::status(&data_dir, &account, &index)),
            None if !requested.is_empty() => Err(account_not_found()),
            None => Ok(json!({
                "connected": false, "account_id": null, "account_hash": null, "account_name": null,
                "is_primary": true, "models": {"model_count": 0, "total_models": 0}, "error": "no_codex_account",
            })),
        }
    }).await?;
    payload["login_flow"] = app.snapshot().await;
    Ok(success(payload))
}

async fn start_browser(
    State(app): State<Arc<Lifecycle>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Result<Response, Failure> {
    app.start(query.requested(&body), Mode::Browser).await
}

async fn start_device(
    State(app): State<Arc<Lifecycle>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Result<Response, Failure> {
    app.start(query.requested(&body), Mode::Device).await
}

async fn poll(State(app): State<Arc<Lifecycle>>) -> Response {
    success(app.snapshot().await)
}

async fn cancel(State(app): State<Arc<Lifecycle>>) -> Response {
    success(app.cancel().await)
}

async fn logout(
    State(app): State<Arc<Lifecycle>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Result<Response, Failure> {
    let account = app
        .resolve(query.requested(&body))
        .await?
        .ok_or_else(account_not_found)?;
    // The account lock and final flow update must survive an HTTP disconnect.
    let payload = tokio::spawn(app.logout(account))
        .await
        .map_err(|_| Failure::internal("account_registry_unavailable"))??;
    Ok(json_response(StatusCode::OK, payload))
}

async fn blocking<T: Send + 'static>(
    operation: impl FnOnce() -> Result<T, Failure> + Send + 'static,
) -> Result<T, Failure> {
    tokio::task::spawn_blocking(operation)
        .await
        .map_err(|_| Failure::internal("account_registry_unavailable"))?
}

fn account_not_found() -> Failure {
    Failure::not_found("account_not_found")
}

fn success(mut payload: Value) -> Response {
    payload["success"] = json!(true);
    (
        [(axum::http::header::CACHE_CONTROL, "no-store")],
        json_response(StatusCode::OK, payload),
    )
        .into_response()
}
