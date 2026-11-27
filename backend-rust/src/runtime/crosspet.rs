use super::{Runtime, RuntimeError, Status, Store, Task};
use crate::crosspet::{
    Action, CrossPetEvents, SessionIdentity, TaskEvent, TaskObservation, TaskRef, TerminalStatus,
    WaitReason,
};
use crate::workspace_catalog::RegisteredWorkspace;
use serde_json::Value;

#[cfg(all(test, unix))]
#[path = "../../../test/2026-11-27_CrossPet后台状态/runtime_observation.rs"]
mod tests;

impl Runtime {
    pub fn set_crosspet_events(&self, events: CrossPetEvents) -> Result<(), RuntimeError> {
        self.store()?.crosspet = Some(events);
        Ok(())
    }

    pub fn crosspet_observation(
        &self, id: &TaskRef, workspace: Option<&RegisteredWorkspace>,
    ) -> Option<TaskObservation> {
        let store = self.store().ok()?;
        let task = store.tasks.get(&id.task_id)?;
        if task.workspace_id != id.session.workspace_id
            || task.conversation_id != id.session.conversation_id
        {
            return None;
        }
        let background = workspace
            .filter(|entry| entry.id == task.workspace_id)
            .filter(|entry| store.workspaces.get(&entry.id) == Some(&entry.path))
            .map(|_| Self::conversation_has_background(
                &store, &task.workspace_id, &task.conversation_id,
            ));
        Some(TaskObservation { terminal: terminal(task.status), background })
    }
}

fn terminal(status: Status) -> Option<TerminalStatus> {
    Some(match status {
        Status::Running => return None,
        Status::Stopped => TerminalStatus::Stopped,
        Status::Failed => TerminalStatus::Failed,
        Status::Succeeded => TerminalStatus::Succeeded,
    })
}

impl Store {
    pub(super) fn register_task(&mut self, mut task: Task) {
        task.crosspet = self.crosspet.clone();
        task.notify_crosspet(Action::Started);
        self.tasks.insert(task.start.id.clone(), task);
    }
}

impl Task {
    pub(super) fn crosspet_event(&self, kind: &str, data: &Value) {
        let Some(events) = &self.crosspet else { return };
        if !events.observes_progress()
            && !matches!(
                kind,
                "compression_state"
                    | "compression_started"
                    | "compression_finished"
                    | "task_complete"
            )
        {
            return;
        }
        let action = match kind {
            "api_request_start" | "thinking_start" | "text_start" => Action::Thinking,
            "tool_start" => Action::ToolStarted {
                call_id: data["id"]
                    .as_str()
                    .unwrap_or_default()
                    .chars()
                    .take(160)
                    .collect(),
                name: data["name"]
                    .as_str()
                    .unwrap_or_default()
                    .chars()
                    .take(160)
                    .collect(),
            },
            "update_action" => match data["status"].as_str() {
                Some("success" | "completed") => Action::ToolFinished { failed: false },
                Some("error" | "failed") => Action::ToolFinished { failed: true },
                _ => return,
            },
            "tool_approval_required" => Action::Waiting(WaitReason::ToolApproval),
            "tool_approval_resolved" => Action::Resolved(WaitReason::ToolApproval),
            "plan_approval_required" => Action::Waiting(WaitReason::PlanApproval),
            "plan_approval_resolved" => Action::Resolved(WaitReason::PlanApproval),
            "user_questions_required" => Action::Waiting(WaitReason::UserQuestions),
            "user_questions_resolved" => Action::Resolved(WaitReason::UserQuestions),
            "compression_state" | "compression_started" => Action::Compression {
                active: data["in_progress"].as_bool().unwrap_or(true),
            },
            "compression_finished" => Action::Compression { active: false },
            "error" => Action::Error {
                retry: data["retry"].as_bool().unwrap_or(false),
            },
            "task_complete" => Action::Response {
                error: data["error"]
                    .as_bool()
                    .unwrap_or_else(|| data["error"].as_str().is_some_and(|s| !s.is_empty())),
            },
            _ => return,
        };
        self.notify_crosspet(action);
    }

    pub(super) fn crosspet_finished(&self) {
        if let Some(status) = terminal(self.status) {
            self.notify_crosspet(Action::Finished(status));
        }
    }

    fn notify_crosspet(&self, action: Action) {
        let Some(events) = &self.crosspet else { return };
        events.event(TaskEvent {
            task: TaskRef {
                session: SessionIdentity {
                    workspace_id: self.workspace_id.clone(),
                    conversation_id: self.conversation_id.clone(),
                },
                task_id: self.start.id.clone(),
            },
            model_key: self.start.model_key.clone().unwrap_or_default(),
            action,
        });
    }
}
