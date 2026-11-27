use std::{collections::HashMap, sync::{Arc, Mutex}, time::Duration};

use serde_json::Value;
use tokio::{runtime::Handle, task::AbortHandle};

use super::{GoalError, GoalRun, Goals, Status, state::{GoalState, new_id, now}};

#[derive(Clone, Debug, Default)]
pub struct GoalFailure {
    pub message: String,
    pub status_code: Option<u16>,
    pub error_code: Option<String>,
    pub error_type: Option<String>,
    pub retry_after: Option<f64>,
}

impl GoalFailure {
    pub fn from_message(message: impl Into<String>) -> Self {
        let message = message.into();
        let status_code = message.to_ascii_lowercase().split_once("http ")
            .and_then(|(_, status)| status.get(..3))
            .and_then(|status| status.parse().ok());
        Self { status_code, message, ..Self::default() }
    }

    pub fn from_details(raw: &Value) -> Self {
        let string = |names: &[&str]| names.iter().find_map(|name| raw[*name].as_str()).map(str::to_owned);
        let mut failure = Self::from_message(string(&["message", "error_message", "error_text"]).unwrap_or_default());
        failure.error_code = string(&["error_code", "code"]);
        failure.error_type = string(&["error_type", "type"]);
        failure.status_code = raw["status_code"].as_u64().and_then(|status| status.try_into().ok())
            .or_else(|| raw["status_code"].as_str()?.parse().ok()).or(failure.status_code);
        failure.retry_after = raw["retry_after"].as_f64();
        failure
    }

    pub fn reason(&self) -> &'static str {
        let text = format!("{} {} {}", self.error_code.as_deref().unwrap_or_default(),
            self.error_type.as_deref().unwrap_or_default(), self.message).to_lowercase();
        let has = |terms: &[&str]| terms.iter().any(|term| text.contains(term));
        if has(&["insufficient_quota", "usage_limit", "usage limit", "out_of_usage", "out of usage",
            "outof usage", "outofusage", "credit balance", "insufficient balance", "quota_exceeded",
            "quota exceeded", "billing_hard_limit", "额度不足", "额度已用", "余额不足", "用量上限"]) {
            "quota_exhausted"
        } else if matches!(self.status_code, Some(401 | 403))
            || has(&["authentication", "invalid_api_key", "unauthorized", "http 401", "http 403"]) {
            "authentication"
        } else if matches!(self.status_code, Some(400 | 404 | 405 | 413 | 422))
            || has(&["invalid_request", "context_overflow", "no_model", "not configured", "profile incomplete"]) {
            "configuration"
        } else if matches!(self.status_code, Some(408 | 409 | 425 | 429 | 500..=599))
            || (self.status_code.is_none() && has(&[
                "rate_limit", "rate limit", "timeout", "timed out", "connection", "connecterror",
                "readerror", "writeerror", "protocolerror", "network", "disconnect", "断流", "超时", "连接", "网络",
                "request_error: connect:", "tls handshake eof", "stream read failed:", "stream ended before [done]",
                "responses stream error (stream_incomplete):", " request failed: connect:",
                " request failed: body:", " request failed: transport:",
            ])) {
            "retryable"
        } else {
            "unknown_error"
        }
    }
}

#[derive(Clone, Debug)]
pub enum Outcome {
    Cancelled,
    Interrupted,
    Failed(GoalFailure),
}

#[derive(Clone, Debug)]
pub struct RetryTicket {
    pub run: GoalRun,
    pub next_retry_at: f64,
    message: String,
}

impl RetryTicket {
    pub fn message(&self) -> &str { &self.message }

    pub(super) fn matches(&self, state: &GoalState) -> bool {
        self.run.matches(state) && state.status == Status::Retrying
            && state.stopped_reason.as_deref() == Some("network_error")
            && state.next_retry_at == Some(self.next_retry_at)
    }
}

#[derive(Clone, Debug, Default)]
pub struct Finished {
    pub progress: Option<Value>,
    pub retry: Option<RetryTicket>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Dispatch {
    Started,
    Busy,
    Stale,
    Failed,
}

#[derive(Clone, Default)]
pub(super) struct RecoveryTimers {
    entries: Arc<Mutex<HashMap<String, Timer>>>,
}

struct Timer {
    identity: String,
    abort: AbortHandle,
}

type Dispatcher = dyn Fn(&RetryTicket) -> Dispatch + Send + Sync;

impl Goals {
    /// Invoke at the normal task completion boundary. Any returned timer must
    /// be installed only after the Runtime releases its running-task gate.
    pub fn finish(&self, run: &GoalRun, outcome: Outcome) -> Result<Finished, GoalError> {
        if matches!(outcome, Outcome::Cancelled) {
            let progress = self.pause_task(run)?;
            return Ok(Finished { progress, retry: None });
        }
        self.store.transaction(&run.conversation_id, |transaction| {
            let state = &transaction.state;
            if !run.matches(state) { return Ok(Finished::default()); }
            if !state.status.active() || (state.status == Status::Retrying && state.stopped_reason.as_deref() == Some("network_error")) {
                return Ok(Finished { progress: Some(state.progress(&run.conversation_id)), retry: None });
            }
            let mut next = state.clone();
            let failure = match outcome {
                Outcome::Failed(failure) => Some(failure),
                _ => None,
            };
            let reason = failure.as_ref().map_or("execution_interrupted", GoalFailure::reason);
            let mut message = failure.as_ref().map(|failure| failure.message.chars().take(3000).collect::<String>()).unwrap_or_default();
            let retry = if reason == "retryable" && state.status != Status::Retrying {
                next.retry_count = next.retry_count.saturating_add(1);
                let delay = retry_delay(next.retry_count, failure.as_ref().and_then(|failure| failure.retry_after));
                next.status = Status::Retrying;
                next.stopped_reason = Some("network_error".into());
                next.last_message = message;
                let next_retry_at = now() + delay.as_secs_f64();
                next.next_retry_at = Some(next_retry_at);
                Some(RetryTicket {
                    run: run.clone(), next_retry_at,
                    message: format!("前次执行因暂时故障中断。请继续当前目标，从已保存的进度推进，先核实已执行的操作，避免重复产生副作用。\n目标：{}\n最近错误：{}", next.goal, next.last_message),
                })
            } else {
                if message.is_empty() { message = "执行已中断，点击继续重试。".into(); }
                let reason = if reason == "retryable" { "execution_interrupted" } else { reason };
                next.stop(Status::Blocked, reason, message);
                None
            };
            transaction.replace(next)?;
            Ok(Finished { progress: Some(transaction.state.progress(&run.conversation_id)), retry })
        })
    }

    pub fn recovery_current(&self, ticket: &RetryTicket) -> Result<bool, GoalError> {
        self.store.transaction(&ticket.run.conversation_id, |transaction| Ok(ticket.matches(&transaction.state)))
    }

    /// `dispatch` must enter the same Runtime admission path and revalidate
    /// `Origin::Recovery` while it holds the task gate. Only trusted callers
    /// receive retry tickets. A state revision change from usage is harmless.
    pub fn schedule_recovery(
        &self,
        ticket: RetryTicket,
        dispatch: impl Fn(&RetryTicket) -> Dispatch + Send + Sync + 'static,
    ) -> Result<(), GoalError> {
        let handle = Handle::try_current().map_err(GoalError::storage)?;
        let dispatch: Arc<Dispatcher> = Arc::new(dispatch);
        let goals = self.clone();
        let key = ticket.run.conversation_id.clone();
        let mut entries = self.timers.entries.lock().map_err(GoalError::storage)?;
        let identity = new_id();
        let task_identity = identity.clone();
        let task = handle.spawn(async move {
            goals.recover(ticket, task_identity, dispatch).await;
        });
        if let Some(previous) = entries.insert(key, Timer { identity, abort: task.abort_handle() }) {
            previous.abort.abort();
        }
        Ok(())
    }

    async fn recover(&self, ticket: RetryTicket, identity: String, dispatch: Arc<Dispatcher>) {
        let key = &ticket.run.conversation_id;
        let mut delay = Duration::from_secs_f64((ticket.next_retry_at - now()).max(0.0));
        loop {
            tokio::time::sleep(delay).await;
            let owned = self.timers.entries.lock().is_ok_and(|entries| entries.get(key).is_some_and(|timer| timer.identity == identity));
            if !owned { return; }
            let state = self.clone();
            let ticket = ticket.clone();
            let dispatch = dispatch.clone();
            let result = tokio::task::spawn_blocking(move || {
                if state.recovery_current(&ticket).unwrap_or(false) { dispatch(&ticket) } else { Dispatch::Stale }
            }).await;
            match result {
                Ok(Dispatch::Busy) => delay = Duration::from_secs(2),
                Ok(Dispatch::Failed) => delay = Duration::from_secs(10),
                _ => break,
            }
        }
        if let Ok(mut entries) = self.timers.entries.lock() && entries.get(key).is_some_and(|timer| timer.identity == identity) {
            entries.remove(key);
        }
    }

    pub fn cancel_recovery(&self, conversation_id: &str) {
        if let Ok(mut entries) = self.timers.entries.lock() && let Some(timer) = entries.remove(conversation_id) {
            timer.abort.abort();
        }
    }
}

pub(super) fn retry_delay(attempt: u64, retry_after: Option<f64>) -> Duration {
    let seconds = (30_u32 << attempt.saturating_sub(1).min(4)).min(300);
    let provider = retry_after.filter(|value| value.is_finite()).unwrap_or_default().clamp(0.0, 3600.0);
    Duration::from_secs_f64(f64::from(seconds).max(provider))
}
