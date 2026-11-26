use serde::Serialize;
use serde_json::{Map, Value};
use std::{
    fmt,
    path::{Component, Path, PathBuf},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalType {
    SandboxWrite,
    FullAccess,
    DirectExecution,
    BrowserAction,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionMode {
    Sandbox,
    Direct,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PermissionMode {
    ReadOnly,
    Approval,
    AutoApproval,
    Unrestricted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct ApprovalRequirements {
    pub(super) auto_review_required: bool,
    pub(super) human_approval_required: bool,
}

pub fn resolve_approval_requirements(
    permission_mode: PermissionMode,
    approval_type: ApprovalType,
    allow_auto_approve_full_access: bool,
) -> Result<ApprovalRequirements, ApprovalError> {
    if permission_mode == PermissionMode::ReadOnly {
        return Err(ApprovalError::Invalid(
            "Read-only tasks cannot request tool approval",
        ));
    }
    let full_access = approval_type == ApprovalType::FullAccess;
    let auto_full_access = full_access
        && matches!(
            permission_mode,
            PermissionMode::AutoApproval | PermissionMode::Unrestricted
        )
        && allow_auto_approve_full_access;
    let automatic = permission_mode == PermissionMode::AutoApproval || auto_full_access;
    Ok(ApprovalRequirements {
        auto_review_required: automatic,
        human_approval_required: !automatic || (full_access && !auto_full_access),
    })
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ApprovalPrincipal {
    username: String,
    workspace_id: String,
}

impl ApprovalPrincipal {
    /// Build from authenticated identity and server-resolved workspace membership.
    pub fn new(
        username: impl Into<String>,
        workspace_id: impl Into<String>,
    ) -> Result<Self, ApprovalError> {
        let principal = Self {
            username: username.into(),
            workspace_id: workspace_id.into(),
        };
        if [principal.username(), principal.workspace_id()]
            .iter()
            .any(|id| id.trim().is_empty())
        {
            return Err(ApprovalError::Invalid("Approval principal is incomplete"));
        }
        Ok(principal)
    }

    pub fn username(&self) -> &str {
        &self.username
    }

    pub fn workspace_id(&self) -> &str {
        &self.workspace_id
    }
}

/// Capture from a live, accepted task while holding the task-state lock.
/// The runtime resolves workspace_root, including symlinks, before building this snapshot.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct TaskScope {
    #[serde(flatten)]
    pub(super) principal: ApprovalPrincipal,
    pub(super) conversation_id: String,
    pub(super) task_id: String,
    executor_id: String,
    workspace_root: PathBuf,
    pub(super) execution_mode: ExecutionMode,
}

impl TaskScope {
    pub fn new(
        principal: ApprovalPrincipal,
        conversation_id: impl Into<String>,
        task_id: impl Into<String>,
        executor_id: impl Into<String>,
        workspace_root: PathBuf,
        execution_mode: ExecutionMode,
    ) -> Result<Self, ApprovalError> {
        let scope = Self {
            principal,
            conversation_id: conversation_id.into(),
            task_id: task_id.into(),
            executor_id: executor_id.into(),
            workspace_root,
            execution_mode,
        };
        if [&scope.conversation_id, &scope.task_id, &scope.executor_id]
            .iter()
            .any(|id| id.trim().is_empty())
            || !scope.workspace_root.is_absolute()
            || scope
                .workspace_root
                .components()
                .any(|part| matches!(part, Component::CurDir | Component::ParentDir))
            || scope.workspace_root.to_str().is_none()
        {
            return Err(ApprovalError::Invalid("Approval task scope is incomplete"));
        }
        Ok(scope)
    }

    pub fn principal(&self) -> &ApprovalPrincipal {
        &self.principal
    }

    pub fn conversation_id(&self) -> &str {
        &self.conversation_id
    }

    pub fn task_id(&self) -> &str {
        &self.task_id
    }

    pub fn executor_id(&self) -> &str {
        &self.executor_id
    }

    pub fn workspace_root(&self) -> &Path {
        &self.workspace_root
    }

    pub fn execution_mode(&self) -> ExecutionMode {
        self.execution_mode
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ToolRequest {
    pub tool_call_id: String,
    pub tool_name: String,
    pub arguments: Map<String, Value>,
    pub preview: Map<String, Value>,
    pub approval_type: ApprovalType,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub browser_site: Option<String>,
}

impl ToolRequest {
    pub(super) fn validate(&self, mode: ExecutionMode) -> Result<(), ApprovalError> {
        if self.tool_call_id.trim().is_empty() || self.tool_name.trim().is_empty() {
            return Err(ApprovalError::Invalid(
                "Approval tool identity is incomplete",
            ));
        }
        let valid = match self.approval_type {
            ApprovalType::SandboxWrite => {
                mode == ExecutionMode::Sandbox && self.browser_site.is_none()
            }
            ApprovalType::FullAccess => {
                mode == ExecutionMode::Sandbox
                    && self.tool_name == "run_command"
                    && self.browser_site.is_none()
            }
            ApprovalType::DirectExecution => {
                mode == ExecutionMode::Direct
                    && matches!(self.tool_name.as_str(), "run_command" | "terminal_input" | "terminal_session" | "write_file" | "edit_file")
                    && self.browser_site.is_none()
            }
            ApprovalType::BrowserAction => self
                .browser_site
                .as_deref()
                .is_some_and(|site| !site.trim().is_empty()),
        };
        if !valid {
            return Err(ApprovalError::Invalid(
                "Approval type does not match the execution scope",
            ));
        }
        Ok(())
    }

    pub(super) fn matches_execution(&self, current: &Self) -> bool {
        self.tool_call_id == current.tool_call_id
            && self.tool_name == current.tool_name
            && self.arguments == current.arguments
            && self.approval_type == current.approval_type
            && self.browser_site == current.browser_site
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    Approved,
    Rejected,
}

impl Decision {
    pub(super) fn parse(value: &str) -> Result<Self, ApprovalError> {
        match value.trim().to_ascii_lowercase().as_str() {
            "approved" => Ok(Self::Approved),
            "rejected" => Ok(Self::Rejected),
            _ => Err(ApprovalError::Invalid("Invalid approval decision")),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pending,
    Approved,
    Rejected,
    Expired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AutoReviewStatus {
    Pending,
    Reviewing,
    Approved,
    Rejected,
    Error,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ApprovalError {
    Invalid(&'static str),
    NotFound,
    Forbidden,
    NotApproved,
    ScopeMismatch,
    ReviewChanged,
    Unavailable,
}

impl fmt::Display for ApprovalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Invalid(message) => message,
            Self::NotFound => "Tool approval not found",
            Self::Forbidden => "Tool approval is outside the authenticated scope",
            Self::NotApproved => "Tool approval is pending, expired, or already claimed",
            Self::ScopeMismatch => "Tool execution does not match the approved scope or arguments",
            Self::ReviewChanged => "The review attempt or its inputs changed",
            Self::Unavailable => "Tool approval state is unavailable",
        })
    }
}

impl std::error::Error for ApprovalError {}

/// Legacy wire fields are projected from the trusted record. This value cannot authorize execution.
#[derive(Clone, Debug, Serialize)]
pub struct Approval {
    pub approval_id: String,
    #[serde(flatten)]
    pub(super) scope: TaskScope,
    #[serde(flatten)]
    pub(super) request: ToolRequest,
    #[serde(flatten)]
    pub requirements: ApprovalRequirements,
    pub status: Status,
    pub decision: Option<Decision>,
    pub created_at: f64,
    pub decided_at: Option<f64>,
    pub auto_review_status: Option<AutoReviewStatus>,
    pub auto_review_started_at: Option<f64>,
    pub auto_reviewed_at: Option<f64>,
    pub human_decided_at: Option<f64>,
    pub human_decision: Option<Decision>,
    pub human_reason: Option<String>,
    pub auto_review_reason: Option<String>,
    pub reason: Option<String>,
    pub review_error_code: Option<String>,
    pub decider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remember_site: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_claimed_at: Option<f64>,
}

impl Approval {
    pub fn scope(&self) -> &TaskScope {
        &self.scope
    }

    pub fn request(&self) -> &ToolRequest {
        &self.request
    }

    pub(crate) fn task_id(&self) -> &str {
        self.scope.task_id()
    }

    pub fn needs_human_decision(&self) -> bool {
        self.status == Status::Pending
            && self.requirements.human_approval_required
            && self.human_decision.is_none()
    }

    /// Payload for the runtime's `tool_approval_required` event.
    pub fn required_event_payload(&self) -> Option<Value> {
        (self.status == Status::Pending).then(|| {
            serde_json::json!({
                "approval": self,
                "conversation_id": self.scope.conversation_id(),
            })
        })
    }

    /// Payload for the runtime's `tool_approval_resolved` event.
    pub fn resolved_event_payload(&self) -> Option<Value> {
        let (decision, code) = match self.status {
            Status::Pending => return None,
            Status::Approved => ("approved", None),
            Status::Rejected => ("rejected", None),
            Status::Expired => match self.review_error_code.as_deref() {
                Some(code) => ("error", Some(code)),
                None => ("expired", Some("approval_expired")),
            },
        };
        Some(serde_json::json!({
            "approval_id": self.approval_id,
            "conversation_id": self.scope.conversation_id(),
            "decision": decision,
            "reason": self.reason,
            "code": code,
        }))
    }
}

impl ApprovalRequirements {
    pub fn auto_review_required(self) -> bool {
        self.auto_review_required
    }

    pub fn human_approval_required(self) -> bool {
        self.human_approval_required
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionAccess {
    SandboxWrite,
    FullAccessOnce,
    Direct,
    BrowserAction,
}

/// Tool-specific authority returned after the second, launch-time check.
#[derive(Debug)]
pub struct ExecutionScope {
    pub(super) task: TaskScope,
    pub(super) call: ToolRequest,
}

impl ExecutionScope {
    pub fn task(&self) -> &TaskScope {
        &self.task
    }

    pub fn call(&self) -> &ToolRequest {
        &self.call
    }

    pub fn access(&self) -> ExecutionAccess {
        match self.call.approval_type {
            ApprovalType::SandboxWrite => ExecutionAccess::SandboxWrite,
            ApprovalType::FullAccess => ExecutionAccess::FullAccessOnce,
            ApprovalType::DirectExecution => ExecutionAccess::Direct,
            ApprovalType::BrowserAction => ExecutionAccess::BrowserAction,
        }
    }
}

/// Claim handle; only the store which issued it can exchange it for execution scope.
#[derive(Debug)]
#[must_use]
pub struct ExecutionGrant(pub(super) String);

/// Binds a reviewer result to the latest review attempt and its input snapshot.
#[derive(Debug)]
#[must_use]
pub struct ReviewTicket {
    pub(super) approval_id: String,
    pub(super) attempt: uuid::Uuid,
}

pub(super) fn clean_reason(reason: Option<&str>) -> Option<String> {
    reason
        .map(str::trim)
        .filter(|reason| !reason.is_empty())
        .map(str::to_owned)
}

pub(super) fn epoch_seconds() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
