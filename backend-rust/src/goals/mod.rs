mod http;
mod policy;
mod retry;
mod review;
mod state;
mod turn;

use std::{fmt, path::Path};

use serde_json::Value;

use crate::runtime::RuntimeError;

pub use http::{router, router_with_notice_models};
pub use policy::{Action, GoalRequest, INTERACTION_DENIED, Limits, enforce_modes};
pub use retry::{Dispatch, Finished, GoalFailure, Outcome, RetryTicket};
pub use review::GoalReviewer;
pub use state::{ReviewMode, Status};
pub use turn::{ReviewedTurn, TurnResult};

use policy::{goal_prompt, rejects_interaction, truthy};
use retry::RecoveryTimers;
use state::{GoalState, GoalStore, new_id, now, process_id};

#[derive(Debug, Clone)]
pub struct GoalError {
    pub status: u16,
    pub message: String,
}

impl GoalError {
    pub fn new(status: u16, message: impl Into<String>) -> Self {
        Self { status, message: message.into() }
    }

    pub(super) fn bad_request(message: impl Into<String>) -> Self {
        Self::new(400, message)
    }

    pub(super) fn conflict(message: impl Into<String>) -> Self {
        Self::new(409, message)
    }

    pub(super) fn storage(error: impl fmt::Display) -> Self {
        Self::new(503, format!("目标状态存储不可用：{error}"))
    }
}

impl fmt::Display for GoalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for GoalError {}

impl From<GoalError> for RuntimeError {
    fn from(error: GoalError) -> Self {
        Self { status: error.status, message: error.message }
    }
}

#[derive(Clone)]
pub struct Goals {
    store: GoalStore,
    timers: RecoveryTimers,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GoalRun {
    pub conversation_id: String,
    pub run_id: String,
    pub task_id: String,
}

impl GoalRun {
    fn matches(&self, state: &GoalState) -> bool {
        state.run_id.as_deref() == Some(self.run_id.as_str()) && state.task_id.as_deref() == Some(self.task_id.as_str())
    }

    fn active(&self, state: &GoalState) -> bool {
        self.matches(state) && state.status.active()
    }
}

#[derive(Clone, Copy)]
pub enum Origin<'a> {
    User,
    Automatic,
    Passive,
    Recovery(&'a RetryTicket),
}

pub struct Admission<'a> {
    pub conversation_id: &'a str,
    pub task_id: &'a str,
    pub message: &'a str,
    pub request: &'a GoalRequest,
    pub origin: Origin<'a>,
}

#[derive(Clone, Debug)]
pub struct Assignment {
    pub run: GoalRun,
    pub progress: Value,
}

#[derive(Clone, Debug)]
pub struct Control {
    action: ControlAction,
    run_id: String,
}

#[derive(Clone, Copy, Debug)]
enum ControlAction {
    Close,
    Pause,
}

impl Control {
    pub fn parse(payload: &Value) -> Result<Self, GoalError> {
        let action = match payload["action"].as_str() {
            Some("close") => ControlAction::Close,
            Some("pause") => ControlAction::Pause,
            _ => return Err(GoalError::bad_request("Invalid goal action")),
        };
        let run_id = payload["run_id"].as_str().filter(|id| !id.is_empty()).ok_or_else(stale_goal)?;
        Ok(Self { action, run_id: run_id.into() })
    }
}

impl Goals {
    pub fn new(data_dir: impl AsRef<Path>) -> Self {
        Self { store: GoalStore::new(data_dir), timers: RecoveryTimers::default() }
    }

    /// Call with the Runtime admission lock held after checking conversation
    /// ownership and the main task gate. Persist the real user input in the
    /// callback before publishing the task or spawning its worker.
    pub fn admit<E: From<GoalError>>(
        &self,
        admission: Admission<'_>,
        persist: impl FnOnce(Option<&Assignment>) -> Result<(), E>,
    ) -> Result<Option<Assignment>, E> {
        if matches!(admission.origin, Origin::Passive) {
            persist(None)?;
            return Ok(None);
        }
        let conversation_id = admission.conversation_id;
        let assignment = self.store.transaction(conversation_id, |transaction| {
            let old = transaction.state.clone();
            if let Origin::Recovery(ticket) = admission.origin {
                if ticket.run.conversation_id != conversation_id || !ticket.matches(&old) {
                    return Err(stale_goal().into());
                }
            }
            let Some(next) = self.admitted_state(&admission, &old)? else {
                persist(None)?;
                return Ok(None);
            };
            transaction.replace(next)?;
            let assignment = Assignment {
                run: GoalRun {
                    conversation_id: conversation_id.into(),
                    run_id: transaction.state.run_id.clone().expect("active goal run ID"),
                    task_id: admission.task_id.into(),
                },
                progress: transaction.state.progress(conversation_id),
            };
            if let Err(error) = persist(Some(&assignment)) {
                // The goal file and conversation are distinct production files.
                // Restore the goal while the admission and state locks remain held.
                transaction.replace(old)?;
                return Err(error);
            }
            Ok(Some(assignment))
        })?;
        if assignment.is_some() {
            self.cancel_recovery(conversation_id);
        }
        Ok(assignment)
    }

    fn admitted_state(&self, admission: &Admission<'_>, old: &GoalState) -> Result<Option<GoalState>, GoalError> {
        let mut next = old.clone();
        match admission.request.action {
            Some(Action::Start) => {
                if old.status.active() || old.status.resumable() {
                    return Err(GoalError::conflict("已有目标，请继续或关闭后再开始"));
                }
                let goal = admission.message.trim();
                if goal.is_empty() { return Err(GoalError::bad_request("目标不能为空")); }
                let limits = admission.request.limits.unwrap_or_default();
                let settings = self.personalization();
                next = GoalState {
                    goal: goal.into(), run_id: Some(new_id()), started_at: Some(now()),
                    review_mode: if settings["goal_review_mode"] == "active" { ReviewMode::Active } else { ReviewMode::Readonly },
                    max_turns: limits.max_turns, max_tokens: limits.max_tokens,
                    ..GoalState::default()
                };
            }
            Some(Action::Continue) => {
                if !old.status.resumable() || old.run_id.is_none() {
                    return Err(GoalError::conflict("目标当前不可继续"));
                }
                if let Some(limits) = admission.request.limits {
                    next.max_turns = limits.max_turns;
                    next.max_tokens = limits.max_tokens;
                }
                next.turn_baseline = Some(old.turn_count);
                next.token_limit_baseline = Some(old.tokens_used);
                next.stopped_reason = None;
                next.retry_count = 0;
                next.no_progress_count = 0;
                next.last_review_signature.clear();
                next.last_message.clear();
            }
            None if matches!(admission.origin, Origin::Automatic) && old.status.resumable() => {
                return Err(GoalError::conflict("Goal is paused; automatic notifications remain pending"));
            }
            None if !old.status.active() => return Ok(None),
            None => {}
        }
        if next.run_id.is_none() { return Err(GoalError::storage("active goal has no run ID")); }
        next.status = Status::Running;
        next.task_id = Some(admission.task_id.into());
        next.process_id = Some(process_id().into());
        next.next_retry_at = None;
        Ok(Some(next))
    }

    pub fn enforce_admission_modes(&self, metadata: &mut Value) {
        if metadata["permission_mode"].is_null() {
            metadata["permission_mode"] = self.personalization()["default_permission_mode"].clone();
        }
        enforce_modes(metadata);
    }

    pub fn progress(&self, conversation_id: &str) -> Result<Value, GoalError> {
        self.store.transaction(conversation_id, |transaction| Ok(transaction.state.progress(conversation_id)))
    }

    /// Runtime verifies workspace ownership and emits the returned projection
    /// to its matching task under its ordinary display/admission lock.
    pub fn control(&self, conversation_id: &str, control: Control) -> Result<Value, GoalError> {
        let progress = self.store.transaction(conversation_id, |transaction| {
            let mut next = transaction.state.clone();
            if next.run_id.as_deref() != Some(control.run_id.as_str()) {
                return Err(stale_goal());
            }
            match control.action {
                ControlAction::Pause if next.status.active() => {
                    next.stop(Status::Paused, "user_cancel", String::new());
                    transaction.replace(next)?;
                }
                ControlAction::Close if next.status.active() || next.status.resumable() => {
                    let last_message = std::mem::take(&mut next.last_message);
                    next.stop(Status::Stopped, "user_closed", last_message);
                    transaction.replace(next)?;
                }
                _ => {}
            }
            Ok(transaction.state.progress(conversation_id))
        })?;
        self.cancel_recovery(conversation_id);
        Ok(progress)
    }

    pub fn pause_task(&self, run: &GoalRun) -> Result<Option<Value>, GoalError> {
        let progress = self.store.transaction(&run.conversation_id, |transaction| {
            if !run.active(&transaction.state) { return Ok::<_,GoalError>(None); }
            let mut next = transaction.state.clone();
            next.stop(Status::Paused, "user_cancel", String::new());
            transaction.replace(next)?;
            Ok(Some(transaction.state.progress(&run.conversation_id)))
        })?;
        if progress.is_some() { self.cancel_recovery(&run.conversation_id); }
        Ok(progress)
    }

    pub fn is_active(&self, run: &GoalRun) -> Result<bool, GoalError> {
        self.store.transaction(&run.conversation_id, |transaction| Ok(run.active(&transaction.state)))
    }

    pub fn blocks_background(&self, conversation_id: &str) -> Result<bool, GoalError> {
        self.store.transaction(conversation_id, |transaction| Ok(transaction.state.status.resumable()))
    }

    pub fn interaction_denied(&self, run: &GoalRun, name: &str, args: &Value, automatic_full_access: bool) -> Result<bool, GoalError> {
        if !rejects_interaction(name, args, automatic_full_access) { return Ok(false); }
        self.is_active(run)
    }

    pub fn automatic_full_access(&self, permission_mode: &str) -> bool {
        matches!(permission_mode, "auto_approval" | "unrestricted")
            && truthy(&self.personalization()["allow_auto_approve_full_access"])
    }

    pub fn prompt(&self, run: &GoalRun) -> Result<Option<Value>, GoalError> {
        self.prompt_with_access(run, false)
    }

    pub fn prompt_with_access(&self, run: &GoalRun, automatic_full_access: bool) -> Result<Option<Value>, GoalError> {
        self.store.transaction(&run.conversation_id, |transaction| {
            Ok(run.active(&transaction.state).then(|| goal_prompt(&transaction.state.goal, automatic_full_access)))
        })
    }

    pub fn record_usage(&self, run: &GoalRun, request_id: &str, total: u64) -> Result<(), GoalError> {
        self.record_conversation_usage(&run.conversation_id, &run.run_id, request_id, total)
    }

    /// Call only after a main or child request has been committed to its ledger.
    /// The ledger lock must be released before this updates the goal file.
    pub fn record_conversation_usage(&self, conversation_id: &str, run_id: &str, request_id: &str, total: u64) -> Result<(), GoalError> {
        self.store.transaction(conversation_id, |transaction| {
            let current = &transaction.state;
            if current.run_id.as_deref() != Some(run_id) || !current.status.active()
                || current.usage_request_ids.iter().any(|id| id == request_id) {
                return Ok(());
            }
            let mut next = current.clone();
            next.tokens_used = next.tokens_used.saturating_add(total);
            next.usage_request_ids.push(request_id.into());
            if next.usage_request_ids.len() > 512 {
                next.usage_request_ids.drain(..next.usage_request_ids.len() - 512);
            }
            transaction.replace(next)
        })
    }

    pub fn record_tool_calls(&self, run: &GoalRun, count: u64) -> Result<(), GoalError> {
        self.store.transaction(&run.conversation_id, |transaction| {
            if count == 0 || !run.active(&transaction.state) { return Ok(()); }
            let mut next = transaction.state.clone();
            next.tool_calls_used = next.tool_calls_used.saturating_add(count);
            transaction.replace(next)
        })
    }

    pub(super) fn personalization(&self) -> Value {
        self.store.personalization()
    }
}

fn stale_goal() -> GoalError {
    GoalError::conflict("目标已改变，请刷新状态后重试")
}
