use serde_json::{Value, json};
use tokio::sync::oneshot;
use uuid::Uuid;

use super::{Runtime, RuntimeError, Status, Task, TaskStart};

pub struct ChatTaskStart {
    pub conversation_id: String,
    pub message: String,
    pub model_key: Option<String>,
    pub message_source: Option<String>,
    pub created_at: f64,
}

impl Runtime {
    pub(crate) fn with_chat_display<T, E>(
        &self, id: &str, action: impl FnOnce(&std::path::Path, Value) -> Result<T, E>,
    ) -> Result<T, E>
    where E: From<RuntimeError> {
        let store = self.store()?;
        let mut task = store.tasks.values().filter(|task| task.workspace_id.is_empty() && task.conversation_id == id)
            .max_by(|a, b| {
                (a.status == Status::Running).cmp(&(b.status == Status::Running))
                    .then(a.start.created_at.total_cmp(&b.start.created_at))
            }).map_or(Value::Null, |task| task.activity(u64::MAX));
        task["title_pending"] = json!(store.pending_titles.contains(&(String::new(), id.to_owned())));
        action(&store.data_dir.join("chat"), task)
    }

    pub fn register_chat_task(
        &self,
        start: ChatTaskStart,
    ) -> Result<(String, oneshot::Receiver<()>), RuntimeError> {
        let mut store = self.store()?;
        if store.tasks.values().any(|task| {
            task.workspace_id.is_empty()
                && task.conversation_id == start.conversation_id
                && task.status == Status::Running
        }) {
            return Err(RuntimeError::new(409, "A Chat task is already running in this conversation"));
        }
        let task_id = Uuid::new_v4().to_string();
        let (sender, receiver) = oneshot::channel();
        let task = Task::new(TaskStart {
            id: task_id.clone(),
            created_at: start.created_at,
            message: start.message,
            model_key: start.model_key,
            message_source: start.message_source,
        }, "", start.conversation_id, sender);
        store.register_task(task);
        Ok((task_id, receiver))
    }

    pub fn emit_chat_event(&self, task_id: &str, kind: &str, data: Value) -> Result<(), RuntimeError> {
        let mut store = self.store()?;
        let task = store.tasks.get_mut(task_id).filter(|task| task.workspace_id.is_empty())
            .ok_or_else(RuntimeError::task_not_found)?;
        if task.status != Status::Running || task.cancel_requested {
            return Err(RuntimeError::new(409, "Chat task is stopped"));
        }
        task.emit(kind, data);
        Ok(())
    }

    pub fn finish_chat_task(&self, task_id: &str, status: &str, error: Option<&str>) -> Result<(), RuntimeError> {
        let mut store = self.store()?;
        let task = store.tasks.get_mut(task_id).filter(|task| task.workspace_id.is_empty())
            .ok_or_else(RuntimeError::task_not_found)?;
        if task.status != Status::Running {
            return Ok(());
        }
        let (status, kind, data, error) = match status {
            "succeeded" => (Status::Succeeded, "task_complete", json!({}), None),
            "stopped" | "canceled" => (Status::Stopped, "task_stopped", json!({"message":"Task stopped"}), None),
            "failed" => {
                let error = error.unwrap_or("Chat task failed");
                (Status::Failed, "error", json!({"message":error,"retry":false}), Some(error.to_owned()))
            }
            _ => return Err(RuntimeError::bad_request("Invalid Chat task terminal status")),
        };
        task.terminal(status, error, kind, data, super::now());
        Ok(())
    }
}
