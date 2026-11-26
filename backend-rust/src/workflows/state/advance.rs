use super::{State, WorkflowStates, files, load_definition, lock, text};
use crate::workflows::{Node, NodeKind, Result, ReviewMode, Workflow, WorkflowError};
use serde_json::{Value, json};
use std::{collections::HashSet, path::PathBuf};

#[derive(Debug)]
pub enum Advance {
    Progress(Progress),
    Review(Box<ReviewRequest>),
}

#[derive(Debug)]
pub struct Progress {
    pub message: String,
    pub snapshot: Value,
    /// Payload for the caller's `workflow_progress` task event. Completion
    /// has its own active snapshot so the UI can display the end transition.
    pub event: Value,
}

#[derive(Debug)]
pub struct ReviewRequest {
    pub mode: ReviewMode,
    pub payload: String,
    /// Payload for the caller's `workflow_review_progress` task event.
    pub progress: Value,
    review_id: String,
    plan: Plan,
}

/// Supplied by the trusted review service after processing `ReviewRequest`.
/// No HTTP body or main-model tool arguments deserialize this type.
#[derive(Debug)]
pub enum ReviewDecision {
    Pass(String),
    Reject(String),
    Unavailable(String),
}

#[derive(Debug)]
struct Plan {
    dir: PathBuf,
    conversation_id: String,
    before: State,
    state: State,
    definition: Workflow,
    stage: Option<StageReport>,
    message_index: usize,
    trace: String,
    traversed: HashSet<String>,
    messages: Vec<String>,
}

#[derive(Debug)]
pub(super) struct StageReport {
    pub(super) node_id: String,
    pub(super) summary: String,
    rounds: u64,
}

impl WorkflowStates {
    pub fn report_stage(
        &self,
        conversation_id: &str,
        summary: &str,
        history: &[Value],
    ) -> Result<Advance> {
        let _guard = lock()?;
        let mut plan = self.plan(conversation_id, history)?;
        let current = plan.current()?;
        let target = match &current.kind {
            NodeKind::Stage { next, .. } => next
                .clone()
                .ok_or_else(|| invalid("流程定义异常：当前阶段没有有效的后续节点。"))?,
            NodeKind::Branch { .. } => {
                return Err(invalid(format!(
                    "当前停在分支点「{}」，请先调用 choose_workflow_branch(target_node_id) 选择路径。",
                    current.name
                )));
            }
            _ => {
                return Err(invalid(format!(
                    "当前不在执行阶段（位于「{}」），无法汇报阶段完成。",
                    current.name
                )));
            }
        };
        plan.stage = Some(StageReport {
            node_id: current.id.clone(),
            summary: summary.trim().into(),
            rounds: plan.state.stage_rounds,
        });
        plan.arrive(target)
    }

    pub fn choose_branch(
        &self,
        conversation_id: &str,
        target: &str,
        history: &[Value],
    ) -> Result<Advance> {
        let _guard = lock()?;
        let mut plan = self.plan(conversation_id, history)?;
        let current = plan.current()?;
        let NodeKind::Branch { next } = &current.kind else {
            return Err(invalid("当前不在分支点，无需选择路径。"));
        };
        let target = target.trim();
        let route = next
            .iter()
            .find(|route| route.target == target)
            .ok_or_else(|| {
                let choices = next
                    .iter()
                    .map(|route| route.target.as_str())
                    .collect::<Vec<_>>()
                    .join("、");
                invalid(format!("「{target}」不在候选路径中。可选：{choices}"))
            })?;
        let label = if route.condition.is_empty() {
            &plan.node(target)?.name
        } else {
            &route.condition
        };
        plan.messages.push(format!("已选择路径：{label}"));
        plan.arrive(target.into())
    }

    pub fn resolve_review(
        &self,
        request: ReviewRequest,
        decision: ReviewDecision,
    ) -> Result<Advance> {
        let _guard = lock()?;
        let ReviewRequest {
            review_id,
            mut plan,
            ..
        } = request;
        let current_path = self.directory(&plan.conversation_id)?;
        if current_path != plan.dir || State::load(&current_path)? != plan.before {
            return Err(WorkflowError::new(
                409,
                "工作流已更新或退出，本次审核结果已失效。",
            ));
        }
        let review = plan.node(&review_id)?.clone();
        let NodeKind::Review {
            next,
            reject_to,
            max_rejects,
            ..
        } = review.kind
        else {
            return Err(invalid("审核节点不存在。"));
        };
        let (decision, message) = match decision {
            ReviewDecision::Pass(message) => ("pass", message),
            ReviewDecision::Reject(message) => ("reject", message),
            ReviewDecision::Unavailable(message) => (
                "reject",
                format!(
                    "审核智能体执行异常（{message}）。本次按驳回处理：请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。"
                ),
            ),
        };
        plan.state.history.push(json!({
            "node_id":review_id, "name":review.name, "kind":"review", "decision":decision, "message":message, "at":files::now_epoch(),
        }));
        if decision == "pass" {
            let target = next.ok_or_else(|| invalid("审核通过路由未连接。"))?;
            plan.messages
                .push(format!("审核「{}」通过：{message}", review.name));
            return plan.arrive(target);
        }
        plan.stage = None;
        let count = plan.state.reject_counts.entry(review_id).or_default();
        *count = count.saturating_add(1);
        let count = *count;
        if count >= max_rejects.max(1) as u64 {
            plan.state.terminate("failed", "max_rejects");
            plan.messages.push(format!("审核「{}」未通过（第 {count} 次，已达连续驳回上限 {max_rejects}）：{message}\n\n工作流已连续驳回超限而终止（failed）。请告知用户审核意见与终止原因；如需重新开始，可在调整流程或准备充分后重新激活。", review.name));
            return plan.commit(json!({"event":"workflow_failed", "reason":"max_rejects"}));
        }
        let target = reject_to.ok_or_else(|| invalid("审核驳回路由未连接。"))?;
        let node = plan.node(&target)?.clone();
        plan.messages.push(format!("审核「{}」未通过（第 {count}/{max_rejects} 次）：{message}\n\n你已回到「{}」。请按整改意见修改后，重新汇报。", review.name, node.name));
        if matches!(node.kind, NodeKind::Stage { .. } | NodeKind::Branch { .. }) {
            plan.state.move_to(&target, plan.message_index);
            plan.messages.push(text::node_brief(&node));
            return plan.commit(json!({"event":"workflow_rejected", "review_node":review.name, "reject_count":count}));
        }
        plan.arrive(target)
    }

    fn plan(&self, conversation_id: &str, history: &[Value]) -> Result<Plan> {
        let dir = self.directory(conversation_id)?;
        let state = State::require_active(&dir)?;
        let definition =
            load_definition(&dir)?.ok_or_else(|| invalid("工作流定义快照不存在或无法解析。"))?;
        let trace = text::stage_trace(
            history
                .get(state.stage_start_msg_index..)
                .unwrap_or_default(),
        );
        Ok(Plan {
            dir,
            conversation_id: conversation_id.trim().into(),
            before: state.clone(),
            state,
            definition,
            stage: None,
            message_index: history.len(),
            trace,
            traversed: HashSet::new(),
            messages: Vec::new(),
        })
    }
}

impl Plan {
    fn node(&self, id: &str) -> Result<&Node> {
        self.definition
            .node(id)
            .ok_or_else(|| invalid(format!("目标节点不存在：{id}")))
    }

    fn current(&self) -> Result<&Node> {
        self.state
            .current_node_id
            .as_deref()
            .and_then(|id| self.definition.node(id))
            .ok_or_else(|| {
                invalid(
                    "工作流状态异常：当前节点不存在于定义快照中。可调用 get_workflow_status 自查。",
                )
            })
    }

    fn arrive(mut self, mut target: String) -> Result<Advance> {
        loop {
            let node = self.node(&target)?.clone();
            match &node.kind {
                NodeKind::Branch { next } if next.len() <= 1 => {
                    self.visit(&target)?;
                    target = next
                        .first()
                        .ok_or_else(|| invalid("流程定义异常：分支节点没有有效的出线。"))?
                        .target
                        .clone();
                }
                NodeKind::Review { .. } => {
                    self.visit(&target)?;
                    return Ok(Advance::Review(Box::new(ReviewRequest {
                        mode: self.definition.review_mode,
                        payload: text::review(
                            &self.definition,
                            &self.state,
                            self.stage.as_ref(),
                            &node,
                            &self.trace,
                        ),
                        progress: json!({"conversation_id":self.conversation_id, "progress":{
                            "stage":"start", "message":format!("审核「{}」开始", node.name),
                        }}),
                        review_id: target,
                        plan: self,
                    })));
                }
                NodeKind::Stage { .. } | NodeKind::Branch { .. } => {
                    self.record_stage();
                    self.state.move_to(&target, self.message_index);
                    self.messages.push(text::node_brief(&node));
                    return self.commit(json!({}));
                }
                NodeKind::End => {
                    self.record_stage();
                    let mut completed = self.state.snapshot(Some(&self.definition));
                    completed["status"] = json!("completed");
                    completed["current"] = Value::Null;
                    completed["next"] = Value::Null;
                    completed["footnote"] = json!({"kind":"success", "text":"工作流已完成"});
                    completed["event"] = json!("workflow_completed");
                    self.state.terminate("completed", "completed");
                    self.messages.push(format!(
                        "工作流「{}」已到达终点「{}」，全部完成。请向用户输出总结后结束。",
                        self.state.workflow_name, node.name
                    ));
                    return self.commit(completed);
                }
                NodeKind::Start { .. } => {
                    return Err(invalid("流程定义异常：不能推进至开始节点。"));
                }
            }
        }
    }

    fn visit(&mut self, id: &str) -> Result<()> {
        if self.traversed.insert(id.into()) {
            Ok(())
        } else {
            Err(invalid("流程定义异常：审核或单出线分支形成循环。"))
        }
    }

    fn record_stage(&mut self) {
        if let Some(stage) = self.stage.take() {
            let name = self
                .definition
                .node(&stage.node_id)
                .map_or(stage.node_id.as_str(), |node| &node.name);
            self.state.history.push(json!({
                "node_id":stage.node_id, "kind":"stage", "name":name, "summary":stage.summary, "rounds":stage.rounds, "at":files::now_epoch(),
            }));
            self.messages.push(format!("阶段「{name}」已记录完成。"));
        }
    }

    fn commit(self, extras: Value) -> Result<Advance> {
        self.state.save(&self.dir)?;
        let snapshot = self.state.snapshot(Some(&self.definition));
        let mut event = snapshot.clone();
        event["conversation_id"] = json!(self.conversation_id);
        if let Value::Object(extras) = extras {
            event
                .as_object_mut()
                .expect("snapshot has object shape")
                .extend(extras);
        }
        Ok(Advance::Progress(Progress {
            message: self.messages.join("\n\n"),
            snapshot,
            event,
        }))
    }
}

fn invalid(message: impl Into<String>) -> WorkflowError {
    WorkflowError::new(400, message)
}
