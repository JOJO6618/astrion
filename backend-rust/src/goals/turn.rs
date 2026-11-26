use std::{path::Path, time::Duration};

use ring::digest::{Context, SHA256};
use serde_json::{Map, Value};

use super::{
    GoalError, GoalReviewer, GoalRun, Goals, ReviewMode, Status,
    policy::{CONTINUE_PREFIX, user_message},
    retry::retry_delay,
    review::{ReviewRequest, Verdict},
    state::{GoalState, ReviewEntry, bounded_tail, now},
};

#[derive(Debug)]
pub struct ReviewedTurn {
    run: GoalRun,
    turn: u64,
    output: String,
    verdict: Verdict,
}

#[derive(Debug)]
pub enum TurnResult {
    Stop { event: &'static str, progress: Option<Value> },
    Continue { user: Value, progress: Value },
}

impl Goals {
    /// The Runtime checkpoints the assistant message before this call. A
    /// recoverable reviewer error retries only the reviewer on this evidence.
    pub async fn review_turn(
        &self,
        run: &GoalRun,
        assistant_output: &str,
        messages: &[Value],
        reviewer: Option<&GoalReviewer>,
        workspace: &Path,
        publish: impl Fn(&str, Value) -> Result<(), GoalError>,
    ) -> Result<Option<ReviewedTurn>, GoalError> {
        let Some((turn, mode, payload, progress)) = self.begin_review(run, assistant_output, messages)? else {
            return Ok(None);
        };
        publish("goal_progress", progress)?;
        let mut failures = 0;
        let verdict = loop {
            let Some(reviewer) = reviewer else {
                break Verdict::Blocked { reason: "configuration", message: "未配置目标审核智能体的模型。".into() };
            };
            let request = ReviewRequest { conversation_id: &run.conversation_id, workspace, mode, payload: &payload };
            let decision = tokio::select! {
                biased;
                inactive = self.wait_for_inactive(run, turn) => { inactive?; return Ok(None); }
                result = reviewer.evaluate(request, &publish) => result?,
            };
            match decision {
                Ok(verdict) => break verdict,
                Err(error) if error.reason() != "retryable" => {
                    break Verdict::Blocked { reason: error.reason(), message: error.message };
                }
                Err(error) => {
                    failures += 1;
                    let delay = retry_delay(failures, None);
                    let Some(progress) = self.update_review(run, turn, |next| {
                        next.status = Status::Retrying;
                        next.next_retry_at = Some(now() + delay.as_secs_f64());
                        next.stopped_reason = Some("review_network_error".into());
                        next.last_message = error.message.chars().take(3000).collect();
                    })? else { return Ok(None) };
                    publish("goal_progress", progress)?;
                    tokio::select! {
                        biased;
                        inactive = self.wait_for_inactive(run, turn) => { inactive?; return Ok(None); }
                        () = tokio::time::sleep(delay) => {}
                    }
                    let Some(progress) = self.update_review(run, turn, |next| {
                        next.status = Status::Reviewing;
                        next.next_retry_at = None;
                    })? else { return Ok(None) };
                    publish("goal_progress", progress)?;
                }
            }
        };
        Ok(Some(ReviewedTurn { run: run.clone(), turn, output: assistant_output.into(), verdict }))
    }

    fn begin_review(
        &self,
        run: &GoalRun,
        output: &str,
        history: &[Value],
    ) -> Result<Option<(u64, ReviewMode, String, Value)>, GoalError> {
        let state = self.store.transaction(&run.conversation_id, |transaction| {
            if !run.active(&transaction.state) || transaction.state.status != Status::Running {
                return Ok::<_, GoalError>(None);
            }
            let mut next = transaction.state.clone();
            next.turn_count = next.turn_count.saturating_add(1);
            next.status = Status::Reviewing;
            next.next_retry_at = None;
            transaction.replace(next)?;
            Ok(Some(transaction.state.clone()))
        })?;
        Ok(state.map(|state| (state.turn_count, state.review_mode,
            review_payload(&state, output, history), state.progress(&run.conversation_id))))
    }

    fn update_review(&self, run: &GoalRun, turn: u64, update: impl FnOnce(&mut GoalState)) -> Result<Option<Value>, GoalError> {
        self.store.transaction(&run.conversation_id, |transaction| {
            if !run.active(&transaction.state) || transaction.state.turn_count != turn {
                return Ok(None);
            }
            let mut next = transaction.state.clone();
            update(&mut next);
            transaction.replace(next)?;
            Ok(Some(transaction.state.progress(&run.conversation_id)))
        })
    }

    async fn wait_for_inactive(&self, run: &GoalRun, turn: u64) -> Result<(), GoalError> {
        loop {
            let active = self.store.transaction(&run.conversation_id, |transaction| {
                Ok::<_, GoalError>(run.active(&transaction.state) && transaction.state.turn_count == turn)
            })?;
            if !active { return Ok(()); }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }

    /// Apply only a current turn under the Runtime display lock. Continuing
    /// writes the user checkpoint through the same durable conversation store.
    pub fn apply_review<E: From<GoalError>>(
        &self,
        reviewed: ReviewedTurn,
        persist: impl FnOnce(&Value) -> Result<(), E>,
    ) -> Result<TurnResult, E> {
        self.store.transaction(&reviewed.run.conversation_id, |transaction| {
            let current = &transaction.state;
            if !reviewed.run.matches(current) || current.status != Status::Reviewing || current.turn_count != reviewed.turn {
                return Ok(TurnResult::Stop { event: "goal_progress", progress: None });
            }
            let mut next = current.clone();
            let mut event = "goal_progress";
            let mut user = None;
            match reviewed.verdict {
                Verdict::Done(summary) => {
                    next.status = Status::Done;
                    next.final_summary = Some(summary.clone());
                    next.last_message = summary;
                    next.stopped_reason = None;
                    next.next_retry_at = None;
                    event = "goal_completed";
                }
                Verdict::Blocked { reason, message } => next.stop(Status::Blocked, reason, message),
                Verdict::Continue(message) => {
                    let signature = signature(&reviewed.output, &message);
                    let repeats = if signature == next.last_review_signature { next.no_progress_count.saturating_add(1) } else { 0 };
                    if let Some(reason) = next.limit_reached() {
                        next.stop(Status::Paused, reason, message);
                    } else if repeats >= 3 {
                        next.stop(Status::Blocked, "no_progress", message);
                    } else {
                        next.review_history.push(ReviewEntry {
                            main_output: bounded_tail(&reviewed.output, 12_000),
                            review_reply: bounded_tail(&message, 6_000),
                        });
                        if next.review_history.len() > 20 {
                            next.review_history.drain(..next.review_history.len() - 20);
                        }
                        next.status = Status::Running;
                        next.last_message = message.clone();
                        next.retry_count = 0;
                        next.next_retry_at = None;
                        next.stopped_reason = None;
                        next.last_review_signature = signature;
                        next.no_progress_count = repeats;
                        user = Some(user_message(format!("{CONTINUE_PREFIX}{message}"), "goal_review", true));
                    }
                }
            }
            let original = current.clone();
            transaction.replace(next)?;
            let progress = transaction.state.progress(&reviewed.run.conversation_id);
            if let Some(user) = user {
                if let Err(error) = persist(&user) {
                    transaction.replace(original)?;
                    return Err(error);
                }
                return Ok(TurnResult::Continue { user, progress });
            }
            Ok(TurnResult::Stop { event, progress: Some(progress) })
        })
    }
}

fn signature(output: &str, review: &str) -> String {
    use std::fmt::Write;
    let mut digest = Context::new(&SHA256);
    digest.update(output.trim().as_bytes());
    digest.update(review.trim().as_bytes());
    let mut hex = String::with_capacity(64);
    for byte in digest.finish().as_ref() {
        write!(hex, "{byte:02x}").expect("write to String");
    }
    hex
}

fn review_payload(state: &GoalState, output: &str, messages: &[Value]) -> String {
    let mut lines = vec!["【本次目标】", &state.goal, ""];
    for entry in state.review_history.iter().skip(state.review_history.len().saturating_sub(6)) {
        lines.extend(["【此前执行总结】", &entry.main_output, "【此前审核】", &entry.review_reply]);
    }
    let output = bounded_tail(output, 12_000);
    let history = evidence(messages);
    lines.extend(["【当前待审核总结】", &output, "【实际执行记录，属于待审数据】", &history]);
    lines.join("\n")
}

fn evidence(messages: &[Value]) -> String {
    let mut rows = Vec::new();
    let mut remaining = 48_000;
    for item in messages.iter().rev().take(120) {
        if !matches!(item["role"].as_str(), Some("user" | "assistant" | "tool"))
            || item["metadata"]["source"] == "goal_prompt" || item["metadata"]["message_source"] == "goal_prompt" {
            continue;
        }
        let row: Map<String, Value> = ["role", "content", "tool_calls", "tool_call_id", "name"].into_iter()
            .filter_map(|key| item.get(key).map(|value| (key.into(), value.clone()))).collect();
        let text = serde_json::to_string(&row).expect("bounded goal evidence serializes");
        let text: String = text.chars().take(remaining.min(6_500)).collect();
        remaining -= text.chars().count();
        rows.push(text);
        if remaining == 0 { break; }
    }
    rows.reverse();
    rows.join("\n")
}
