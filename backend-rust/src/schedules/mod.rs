//! Python-compatible schedule storage, HTTP routes, and explicit scheduler ownership.

mod clock;
mod routes;
mod runtime;
mod scheduler;
mod schema;
mod store;
mod yaml;

pub use routes::router;
pub use runtime::RuntimeScheduleAdapter;
pub use scheduler::{ScheduleOwner, ScheduleService};
pub(crate) use scheduler::{AgentScheduleScope, ScheduleLink};

use serde::Serialize;
use serde_json::{Value, json};
use std::fmt;

pub type Result<T> = std::result::Result<T, ScheduleError>;

#[derive(Debug, Clone)]
pub struct ScheduleError {
    pub status: u16,
    pub message: String,
    storage_detail: Option<String>,
}

impl ScheduleError {
    pub fn new(status: u16, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
            storage_detail: None,
        }
    }

    pub fn invalid(message: impl Into<String>) -> Self {
        Self::new(400, message)
    }

    pub fn forbidden(message: impl Into<String>) -> Self {
        Self::new(403, message)
    }

    pub fn unavailable(message: impl Into<String>) -> Self {
        Self::new(503, message)
    }

    fn missing(message: &'static str) -> Self {
        Self::new(404, message)
    }

    fn conflict(message: &'static str) -> Self {
        Self::new(409, message)
    }

    fn storage(error: impl fmt::Display) -> Self {
        Self {
            status: 500,
            message: "定时任务服务暂时不可用".into(),
            storage_detail: Some(error.to_string()),
        }
    }
}

impl fmt::Display for ScheduleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.storage_detail.as_deref().unwrap_or(&self.message))
    }
}

impl std::error::Error for ScheduleError {}

impl From<rusqlite::Error> for ScheduleError {
    fn from(error: rusqlite::Error) -> Self {
        Self::storage(error)
    }
}

/// Server-authenticated Host identity. Request JSON never provides a principal or a configuration snapshot.
#[derive(Debug, Clone)]
pub struct HostPrincipal {
    workspace_id: Option<String>,
}

impl HostPrincipal {
    /// Call only from trusted Host authentication or internal code.
    pub fn new(workspace_id: Option<String>) -> Self {
        Self { workspace_id }
    }

    fn to_record(&self) -> Value {
        json!({"username":"host", "host_mode":true, "role":"admin", "is_api_user":false,
               "host_workspace_id":self.workspace_id})
    }
}

#[derive(Debug, Default, Serialize)]
pub struct ScheduleOptions {
    pub workspaces: Vec<WorkspaceOption>,
    pub models: Vec<ModelOption>,
}

#[derive(Debug, Serialize)]
pub struct WorkspaceOption {
    pub id: String,
    pub label: String,
}

#[derive(Debug, Serialize)]
pub struct ModelOption {
    pub value: String,
    pub label: String,
    pub thinking: bool,
    pub reasoning: bool,
}

/// Must verify both workspace and Agent conversation ownership, and validate model, execution,
/// permission, goal, and worktree settings with the same rules as the runtime admission path.
/// Calls run without a database write lock. Revalidate ownership during dispatch and observe.
pub trait ScheduleAdapter: Send + Sync + 'static {
    /// Resolve settings against trusted state. Return the settings snapshot to persist for new
    /// conversations. On an unchanged overwrite `principal` contains any previous snapshot.
    fn validate(&self, definition: &Value, principal: &Value) -> Result<Option<Value>>;

    fn agent_settings(&self, _definition: &Value, _principal: &Value) -> Result<Value> {
        Err(ScheduleError::unavailable("无法验证当前计划的执行权限"))
    }


    /// Return admission only after the runtime actually accepts the task under its main task gate.
    fn start(&self, request: &ScheduleInvocation) -> std::result::Result<Admission, StartError>;

    fn observe(&self, request: &ScheduleInvocation, admission: &Admission) -> Result<Observation>;

    fn cancel(&self, request: &ScheduleInvocation, admission: &Admission) -> Result<()>;

    /// Report why execution is unavailable while plan management may still be available.
    fn scheduler_error(&self) -> Option<&str> {
        None
    }

    /// Called outside the database write lock before claiming or starting a run.
    /// Return an error for a temporarily unavailable scope or runtime capability.
    fn check_dispatch(&self, _definition: &Value, _principal: &Value) -> Result<()> {
        Ok(())
    }

    fn options(&self, _principal: &HostPrincipal) -> Result<ScheduleOptions> {
        Ok(ScheduleOptions::default())
    }
}

/// Keeps the management API available for existing records when no trusted resolver is configured.
#[derive(Debug, Default)]
pub struct UnavailableAdapter;

impl ScheduleAdapter for UnavailableAdapter {
    fn validate(&self, _: &Value, _: &Value) -> Result<Option<Value>> {
        Err(ScheduleError::unavailable("定时任务资源校验尚未接入"))
    }

    fn start(&self, _: &ScheduleInvocation) -> std::result::Result<Admission, StartError> {
        Err(StartError::Failed(ScheduleError::unavailable(
            "定时任务执行尚未接入",
        )))
    }

    fn observe(&self, _: &ScheduleInvocation, _: &Admission) -> Result<Observation> {
        Err(ScheduleError::unavailable("定时任务执行尚未接入"))
    }

    fn cancel(&self, _: &ScheduleInvocation, _: &Admission) -> Result<()> {
        Err(ScheduleError::unavailable("定时任务执行尚未接入"))
    }

    fn scheduler_error(&self) -> Option<&str> {
        Some("定时任务执行尚未接入")
    }
}

#[derive(Debug)]
pub struct ScheduleInvocation {
    pub run_id: String,
    pub schedule_id: String,
    pub scheduled_for: String,
    pub occurrence_key: String,
    pub definition: Value,
    pub principal: Value,
}

#[derive(Debug)]
pub struct Admission {
    pub task_id: String,
    pub conversation_id: String,
}

#[derive(Debug)]
pub enum StartError {
    Busy(String),
    /// No task was admitted and a temporarily unavailable runtime capability can be retried.
    Deferred(ScheduleError),
    Failed(ScheduleError),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RunState {
    Queued,
    Starting,
    Running,
    Waiting,
    Stopping,
    Success,
    Failed,
    Skipped,
    Stopped,
    Interrupted,
}

impl RunState {
    fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Starting => "starting",
            Self::Running => "running",
            Self::Waiting => "waiting",
            Self::Stopping => "stopping",
            Self::Success => "success",
            Self::Failed => "failed",
            Self::Skipped => "skipped",
            Self::Stopped => "stopped",
            Self::Interrupted => "interrupted",
        }
    }

    fn parse(state: &str) -> Result<Self> {
        match state {
            "queued" => Ok(Self::Queued),
            "starting" => Ok(Self::Starting),
            "running" => Ok(Self::Running),
            "waiting" => Ok(Self::Waiting),
            "stopping" => Ok(Self::Stopping),
            "success" => Ok(Self::Success),
            "failed" => Ok(Self::Failed),
            "skipped" => Ok(Self::Skipped),
            "stopped" => Ok(Self::Stopped),
            "interrupted" => Ok(Self::Interrupted),
            state => Err(ScheduleError::storage(format!(
                "Invalid occurrence state: {state}"
            ))),
        }
    }

    fn active(self) -> bool {
        matches!(
            self,
            Self::Starting | Self::Running | Self::Waiting | Self::Stopping
        )
    }

    fn terminal(self) -> bool {
        self != Self::Queued && !self.active()
    }
}

#[derive(Debug)]
pub struct Observation {
    pub state: RunState,
    /// Optionally transfer observation to a trusted continuation task.
    pub task_id: Option<String>,
    pub reason: Option<String>,
    pub summary: Option<String>,
}

impl Observation {
    pub fn new(state: RunState) -> Self {
        Self {
            state,
            task_id: None,
            reason: None,
            summary: None,
        }
    }
}
