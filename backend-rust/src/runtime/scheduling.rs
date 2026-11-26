use std::{path::Path, sync::Arc};

use serde_json::{Value, json};

use crate::{
    approval::ApprovalPrincipal,
    goals::{Control, Origin},
    interactions::Kind,
    schedules::{Observation, RunState},
    workspace_catalog::RegisteredWorkspace,
    worktree::{Target, Worktree},
};

use super::{
    ModelConfig, Runtime, RuntimeError, TaskAdmission, TaskApprovalSettings, goals, host_policy,
    store::{Status, Store, Task},
    verify_workspace_binding,
};

pub(crate) struct ScheduledStart<'a> {
    pub payload: &'a Value,
    pub saved_metadata: Option<&'a Value>,
    pub metadata: Value,
    pub title: Option<String>,
    pub worktree: &'a Value,
    pub hidden_user_notice: Option<&'a str>,
}

impl Runtime {
    pub(crate) fn check_scheduled_worktree(
        &self,
        workspace: &RegisteredWorkspace,
        saved_metadata: Option<&Value>,
        modes: &Value,
        selection: &Value,
    ) -> Result<(), RuntimeError> {
        let store = self.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        scheduled_worktree(&store, &workspace.path, saved_metadata, modes, selection).map(|_| ())
    }

    pub(crate) fn create_scheduled_task(
        &self,
        workspace: &RegisteredWorkspace,
        start: ScheduledStart<'_>,
        model: ModelConfig,
        approval: TaskApprovalSettings,
    ) -> Result<Value, RuntimeError> {
        let admitted = {
            let mut store = self.store()?;
            verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
            let existing = if let Some(id) = start.payload["conversation_id"].as_str() {
                if store.tasks.values().any(|task| {
                    task.workspace_id == workspace.id
                        && task.conversation_id == id
                        && task.status == Status::Running
                }) {
                    return Err(busy("conversation_gate_busy"));
                }
                let (document, _) = store.header(&workspace.id, id)?;
                goals::verify_conversation_root(&workspace.path, &document)?;
                if Self::conversation_has_background(&store, &workspace.id, id)
                    || self.child_activity(&store, &workspace.id, id) != (false, false)
                {
                    return Err(busy("conversation_background_busy"));
                }
                goals::verify_goal_identity(&store, &workspace.id, id)?;
                if self.goals.progress(id)?["active"] == true {
                    return Err(busy("conversation_goal_busy"));
                }
                let current = document["metadata"].clone();
                if start.saved_metadata != Some(&current) {
                    return Err(busy("conversation_settings_changed"));
                }
                Some(current)
            } else {
                None
            };
            let mut metadata = start.metadata;
            if let Some(tree) = scheduled_worktree(
                &store,
                &workspace.path,
                existing.as_ref(),
                &metadata,
                start.worktree,
            )? {
                metadata["git_worktree_enabled"] = json!(true);
                if existing.as_ref().is_some_and(|value| {
                    value["frozen_system_prompt"]
                        .as_str()
                        .is_some_and(|prompt| !prompt.is_empty())
                }) {
                    metadata["git_worktree_notice"] = tree.clone();
                }
                metadata["git_worktree"] = tree;
            }
            let tools = store
                .workspace_tools
                .get(&workspace.id)
                .unwrap_or(&self.tools)
                .clone();
            let admission = TaskAdmission {
                exclusive_workspace: false,
                title: start.title,
                metadata: Some(metadata),
                new_conversation_id: None,
                user_message_metadata: Some(
                    json!({"source":"schedule","message_source":"schedule"}),
                ),
                tools: Arc::new(tools),
            };
            self.admit_goal_task_with_notice(
                &mut store,
                &workspace.id,
                start.payload,
                &model,
                admission,
                Some(approval),
                Origin::Automatic,
                start.hidden_user_notice,
            )?
        };
        Ok(self.spawn_admitted(admitted, model))
    }

    pub(crate) fn observe_scheduled_task(
        &self,
        workspace: &RegisteredWorkspace,
        conversation: &str,
        task_id: &str,
    ) -> Result<Observation, RuntimeError> {
        let mut store = self.store()?;
        let (task, goal) = self.scheduled_task(&store, workspace, conversation, task_id)?;
        let state = task.status;
        let id = task.start.id.clone();
        let next_id = (id != task_id).then_some(id.clone());
        let reason = task.error.clone();
        if state == Status::Running {
            let principal =
                ApprovalPrincipal::new("host", &workspace.id).map_err(RuntimeError::storage)?;
            let tools = store
                .approvals
                .list_pending(&principal, Some(conversation))
                .map_err(RuntimeError::storage)?;
            let waiting = tools
                .iter()
                .any(|item| item.task_id() == id && item.needs_human_decision())
                || [Kind::Question, Kind::Plan].into_iter().any(|kind| {
                    store
                        .interactions
                        .pending(&principal, kind, Some(conversation))
                        .iter()
                        .any(|item| item["task_id"] == id)
                });
            return Ok(Observation {
                state: if waiting {
                    RunState::Waiting
                } else {
                    RunState::Running
                },
                task_id: next_id,
                reason: None,
                summary: None,
            });
        }
        if goal["active"] == true
            || Self::conversation_has_background(&store, &workspace.id, conversation)
            || self.child_activity(&store, &workspace.id, conversation) != (false, false)
        {
            return Ok(Observation {
                task_id: next_id,
                ..Observation::new(RunState::Running)
            });
        }
        let (state, reason) = match goal["status"].as_str() {
            Some("blocked" | "paused") => (
                RunState::Failed,
                goal["last_message"]
                    .as_str()
                    .filter(|message| !message.is_empty())
                    .or_else(|| goal["stopped_reason"].as_str())
                    .map(str::to_owned)
                    .or(reason),
            ),
            Some("done") => (RunState::Success, reason),
            Some("stopped") => (RunState::Stopped, reason),
            _ => (
                match state {
                    Status::Succeeded => RunState::Success,
                    Status::Failed => RunState::Failed,
                    Status::Stopped => RunState::Stopped,
                    Status::Running => unreachable!("running tasks handled above"),
                },
                reason,
            ),
        };
        Ok(Observation {
            state,
            task_id: next_id,
            reason,
            summary: None,
        })
    }

    pub(crate) fn cancel_scheduled_task(
        &self,
        workspace: &RegisteredWorkspace,
        conversation: &str,
        task_id: &str,
    ) -> Result<(), RuntimeError> {
        let active = {
            let store = self.store()?;
            let (task, goal) = self.scheduled_task(&store, workspace, conversation, task_id)?;
            if task.status == Status::Running {
                Some(task.start.id.clone())
            } else if goal["active"] == true {
                self.goals.control(
                    conversation,
                    Control::parse(&json!({"action":"pause","run_id":goal["run_id"]}))?,
                )?;
                None
            } else {
                None
            }
        };
        if let Some(active) = active {
            self.cancel_task(&workspace.id, &active)?;
        }
        Ok(())
    }

    fn scheduled_task<'a>(
        &self,
        store: &'a Store,
        workspace: &RegisteredWorkspace,
        conversation: &str,
        task_id: &str,
    ) -> Result<(&'a Task, Value), RuntimeError> {
        verify_workspace_binding(store, &workspace.id, Some(&workspace.path))?;
        let owning_task = store
            .tasks
            .get(task_id)
            .filter(|task| {
                task.workspace_id == workspace.id
                    && task.conversation_id == conversation
                    && matches!(
                        task.start.message_source.as_deref(),
                        Some("schedule" | "goal_recovery" | "background_command" | "sub_agent")
                    )
            })
            .ok_or_else(RuntimeError::task_not_found)?;
        let scope = |task: &&Task| {
            task.workspace_id == workspace.id
                && task.conversation_id == conversation
                && task.start.created_at >= owning_task.start.created_at
        };
        if let Some(assignment) = &owning_task.goal_run {
            let progress = self.goals.progress(conversation)?;
            if progress["run_id"] == assignment.run_id {
                let current = progress["task_id"]
                    .as_str()
                    .and_then(|id| store.tasks.get(id))
                    .filter(scope)
                    .filter(|task| {
                        task.goal_run
                            .as_ref()
                            .is_some_and(|run| run.run_id == assignment.run_id)
                            && task.start.message_source.as_deref() == Some("goal_recovery")
                    });
                if progress["task_id"] == owning_task.start.id || current.is_some() {
                    return Ok((current.unwrap_or(owning_task), progress));
                }
            }
        }
        let next = store
            .tasks
            .values()
            .filter(scope)
            .filter(|task| {
                matches!(
                    task.start.message_source.as_deref(),
                    Some("background_command" | "sub_agent")
                )
            })
            .max_by(|a, b| a.start.created_at.total_cmp(&b.start.created_at));
        Ok((next.unwrap_or(owning_task), Value::Null))
    }
}

fn busy(message: &str) -> RuntimeError {
    RuntimeError::new(409, message)
}

fn scheduled_worktree(
    store: &Store,
    root: &Path,
    previous: Option<&Value>,
    modes: &Value,
    selection: &Value,
) -> Result<Option<Value>, RuntimeError> {
    let previous = previous.unwrap_or(&Value::Null);
    let saved = &previous["git_worktree"];
    if (selection.is_null() || selection == "main") && saved["active"] != true {
        return Ok(None);
    }
    let invalid = |error: crate::worktree::Error| RuntimeError::bad_request(error.to_string());
    let service = Worktree::new(root).map_err(invalid)?;
    if selection.is_null() {
        let directory = service.execution_root(previous).map_err(invalid)?;
        verify_tree_authorization(store, root, &directory, modes)?;
        return Ok(None);
    }
    let mut binding = if selection == "main" {
        service.main_binding().map_err(invalid)?
    } else {
        if previous.is_object() && previous["git_worktree_enabled"] != true {
            return Err(RuntimeError::bad_request(
                "目标对话未启用工作树，请选择已启用的对话或新建对话",
            ));
        }
        let branch = selection["branch_name"]
            .as_str()
            .ok_or_else(|| RuntimeError::bad_request("缺少目标工作树分支"))?;
        let binding = service
            .prepare(
                Target::Enter {
                    branch_name: Some(branch.into()),
                    directory: None,
                },
                root,
                modes["work_mode"].as_str().unwrap_or("plan"),
                modes["permission_mode"].as_str().unwrap_or("readonly"),
            )
            .map_err(invalid)?;
        verify_tree_authorization(store, root, &binding.directory, modes)?;
        binding
    };
    if saved["directory"] == json!(binding.directory) && saved["branch"] == binding.branch {
        return Ok(None);
    }
    binding.revision = saved["revision"]
        .as_u64()
        .unwrap_or(0)
        .checked_add(1)
        .ok_or_else(|| RuntimeError::storage("工作树切换版本号超出上限。"))?;
    Ok(Some(json!(binding)))
}

fn verify_tree_authorization(
    store: &Store,
    root: &Path,
    directory: &Path,
    modes: &Value,
) -> Result<(), RuntimeError> {
    if !host_policy::scheduled_worktree_authorized(
        store,
        root,
        directory,
        modes["execution_mode"] == "direct",
    )? {
        return Err(RuntimeError::new(403, "目标工作树不在当前路径授权范围内"));
    }
    Ok(())
}
