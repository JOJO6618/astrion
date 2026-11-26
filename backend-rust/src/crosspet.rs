//! Host CrossPet v1 output. Runtime hooks only enqueue typed metadata. The
//! observer owns external writes, model lookups and activity probes.

mod files;
mod observer;
mod projection;
mod quota;

pub use quota::SubscriptionQuota;

use std::{
    collections::BTreeMap,
    io,
    sync::{
        Arc, Weak,
        atomic::{AtomicBool, Ordering},
    },
};

use axum::{
    Json, Router,
    extract::{State, rejection::JsonRejection},
    http::{StatusCode, header::ALLOW},
    response::IntoResponse,
    routing::get,
};
use serde::Serialize;
use serde_json::{Value, json};
use uuid::Uuid;

pub use files::CrossPetConfig;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SessionIdentity {
    pub workspace_id: String,
    pub conversation_id: String,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct TaskRef {
    pub session: SessionIdentity,
    pub task_id: String,
}

#[derive(Clone, Debug)]
pub struct TaskEvent {
    pub task: TaskRef,
    pub model_key: String,
    pub action: Action,
}

#[derive(Clone, Debug)]
pub enum Action {
    Started,
    Thinking,
    ToolStarted { call_id: String, name: String },
    ToolFinished { failed: bool },
    Waiting(WaitReason),
    Resolved(WaitReason),
    Compression { active: bool },
    Error { retry: bool },
    Response { error: bool },
    Finished(TerminalStatus),
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum WaitReason {
    ToolApproval,
    PlanApproval,
    UserQuestions,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TerminalStatus {
    Succeeded,
    Failed,
    Stopped,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct TaskObservation {
    pub terminal: Option<TerminalStatus>,
    /// None preserves the last confirmed state. A terminal task with no prior
    /// idle observation waits for confirmation before showing a completion pose.
    pub background: Option<bool>,
}

#[derive(Clone, Debug)]
pub struct Foreground {
    pub session: SessionIdentity,
    pub model_key: String,
}

/// Hooks run on the observer thread and must not hold a task, navigation or
/// provider lock while waiting on external services.
pub trait CrossPetRuntime: Send + Sync + 'static {
    fn model_id(&self, model_key: &str) -> Option<String>;

    /// Resolve the last committed navigation focus without writing navigation
    /// state or creating an execution instance. Errors preserve the last focus.
    fn foreground(&self) -> io::Result<Option<Foreground>>;

    /// Read actual task terminal status and background work in one batch. Omit
    /// unknown tasks. Return Some(false) only after confirming no work remains.
    fn observe(&self, tasks: &[TaskRef]) -> BTreeMap<TaskRef, TaskObservation>;

    /// Optional cached subscription view. This runs under the settings gate to
    /// order refresh dispatch and disable. It may schedule refreshes elsewhere
    /// and must never block on network, filesystem, or runtime work. Disabling
    /// must prevent new refreshes and invalidate any in-flight result.
    fn quota(&self, _enabled: bool, _busy: bool) -> Option<GptQuota> {
        None
    }
}

/// The adapter owns account selection, request timing, and epoch rollover. The
/// observer only writes these protocol fields and never receives credentials.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GptQuota {
    pub connected: bool,
    pub status: QuotaStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub epoch: Option<Uuid>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub at: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rate_limits: Option<QuotaLimits>,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum QuotaStatus {
    Checking,
    Live,
    Stale,
    Unavailable,
}

#[derive(Clone, Debug, Serialize)]
pub struct QuotaLimits {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub primary: Option<QuotaWindow>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub secondary: Option<QuotaWindow>,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QuotaWindow {
    pub used_percent: f64,
    pub window_duration_mins: f64,
    pub resets_at: f64,
}

impl QuotaWindow {
    fn valid(&self) -> bool {
        (0.0..=100.0).contains(&self.used_percent)
            && self.window_duration_mins > 0.0
            && self.window_duration_mins <= 366.0 * 24.0 * 60.0
            && self.resets_at > 0.0
            && self.resets_at <= 253_402_300_799.0
    }
}

#[derive(Clone)]
pub struct CrossPet(Arc<observer::Control>);

/// A runtime hook can keep this handle without extending the worker's lifetime.
#[derive(Clone)]
pub struct CrossPetEvents {
    control: Weak<observer::Control>,
    enabled: Arc<AtomicBool>,
}

impl CrossPetEvents {
    /// The task emitter can skip building frequent progress events when disabled.
    /// Continue submitting task start, finish, compaction and response error events.
    pub fn observes_progress(&self) -> bool {
        self.enabled.load(Ordering::Acquire)
    }

    pub fn event(&self, event: TaskEvent) {
        if let Some(service) = self.control.upgrade() {
            service.event(event);
        }
    }

    pub fn wake(&self) {
        if self.observes_progress()
            && let Some(service) = self.control.upgrade()
        {
            service.wake();
        }
    }
}

impl CrossPet {
    /// Start one service per Host backend, independent of browser login and
    /// workspace selection. A persisted opt-in is picked up on startup.
    pub fn start(config: CrossPetConfig, runtime: Arc<dyn CrossPetRuntime>) -> io::Result<Self> {
        Ok(Self(Arc::new(observer::Control::start(config, runtime)?)))
    }

    pub fn events(&self) -> CrossPetEvents {
        CrossPetEvents {
            control: Arc::downgrade(&self.0),
            enabled: self.0.progress_flag(),
        }
    }

    /// Enqueue a whitelisted runtime action. No I/O, model lookup, or activity
    /// query occurs on the caller's thread.
    pub fn event(&self, event: TaskEvent) {
        self.0.event(event);
    }

    /// Promptly re-read the foreground or background state after a committed change.
    pub fn wake(&self) {
        self.0.wake();
    }

    /// Stop the observation worker and wait for any current write to finish.
    pub fn shutdown(&self) {
        self.0.shutdown();
    }

    /// Mount under the Host browser session and CSRF middleware. The Host web
    /// identity is always the local administrator, including on this route.
    pub fn router(&self) -> Router {
        Router::new()
            .route(
                "/api/crosspet",
                get(get_settings).post(set_settings).options(options),
            )
            .with_state(self.clone())
    }
}

async fn options() -> impl IntoResponse {
    (StatusCode::OK, [(ALLOW, "GET, HEAD, POST, OPTIONS")])
}

async fn get_settings(State(service): State<CrossPet>) -> impl IntoResponse {
    match tokio::task::spawn_blocking(move || service.0.enabled()).await {
        Ok(Ok(enabled)) => setting(enabled),
        _ => unavailable(),
    }
}

async fn set_settings(
    State(service): State<CrossPet>,
    payload: Result<Json<Value>, JsonRejection>,
) -> impl IntoResponse {
    let Some(enabled) = payload
        .ok()
        .and_then(|Json(value)| value["enabled"].as_bool())
    else {
        return failure(StatusCode::BAD_REQUEST, "enabled must be a boolean");
    };
    match tokio::task::spawn_blocking(move || service.0.set_enabled(enabled)).await {
        Ok(Ok(())) => setting(enabled),
        Ok(Err(error)) => {
            eprintln!("CrossPet preference update failed ({:?})", error.kind());
            failure(
                StatusCode::BAD_REQUEST,
                "CrossPet preference or state directory is unavailable",
            )
        }
        Err(_) => unavailable(),
    }
}

fn setting(enabled: bool) -> (StatusCode, Json<Value>) {
    (
        StatusCode::OK,
        Json(json!({"success": true, "supported": true, "enabled": enabled})),
    )
}

fn unavailable() -> (StatusCode, Json<Value>) {
    failure(
        StatusCode::SERVICE_UNAVAILABLE,
        "CrossPet settings are unavailable",
    )
}

fn failure(status: StatusCode, message: &str) -> (StatusCode, Json<Value>) {
    (status, Json(json!({"success": false, "error": message})))
}

#[cfg(test)]
#[path = "../../test/2026-11-25_RustCrossPet/crosspet_contract.rs"]
mod tests;
