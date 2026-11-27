use serde_json::{Map, Value, json};
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

/// Reconnectable snapshot for only the current model attempt. Completed rows
/// from earlier attempts are already in the Chat document, not the event ring.
#[derive(Default)]
pub(super) struct ChatProgress {
    response_id: Option<String>,
    tools: Map<String, Value>,
}

impl ChatProgress {
    pub(super) fn apply(&mut self, task: &str, kind: &str, data: &Value, idx: u64, ts: f64) {
        match kind {
            "api_request_start" => {
                self.tools.clear();
                self.response_id = data["response_message_id"].as_str().map(str::to_owned);
                return;
            }
            "stream_reset" => {
                *self = Self::default();
                return;
            }
            "tool_preparing" | "tool_intent" | "tool_start" | "update_action" => {}
            _ => return,
        }
        let Some(call) = data["preparing_id"].as_str().or_else(|| data["id"].as_str())
            .filter(|id| !id.is_empty()) else { return; };
        let action = self.tools.entry(call.to_owned()).or_insert_with(|| {
            let id = format!("{task}:{idx}:tool");
            json!({
                "id":id,"blockId":id,"type":"tool","content":"","timestamp":ts,
                "toolBatchId":self.response_id.as_deref().unwrap_or(task),"start_idx":idx,
                "tool":{"id":call,"preparingId":call,"name":data["name"],"status":"preparing",
                        "arguments":{},"result":null,"intent_complete":false,
                        "intent_full":"","intent_rendered":""}
            })
        });
        let tool = &mut action["tool"];
        if let Some(name) = data["name"].as_str().filter(|name| !name.is_empty()) {
            tool["name"] = json!(name);
        }
        let intent = match kind {
            "tool_start" => {
                tool["status"] = json!("running");
                tool["arguments"] = data["arguments"].clone();
                tool["intent_complete"] = json!(true);
                data["arguments"]["intent"].as_str()
            }
            "update_action" => {
                tool["status"] = data["status"].clone();
                tool["result"] = data["result"].clone();
                None
            }
            _ => {
                if data["intent_complete"] == true {
                    tool["intent_complete"] = json!(true);
                }
                data["intent"].as_str()
            }
        };
        if let Some(intent) = intent {
            tool["intent_full"] = json!(intent);
            tool["intent_rendered"] = json!(intent);
        }
    }

    pub(super) fn display(&self) -> Value {
        json!({"response_id":self.response_id,"tools":self.tools.values().collect::<Vec<_>>()})
    }
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
            }).map_or(Value::Null, |task| task.chat_display_snapshot(u64::MAX));
        task["title_pending"] = json!(store.pending_titles.contains(&(String::new(), id.to_owned())));
        action(&store.data_dir.join("chat"), task)
    }

    /// Poll the task and read its display document against the same runtime
    /// store lock, so the view's event cursor cannot skip a later task event.
    pub(crate) fn with_chat_task_display<T, E>(
        &self, id: &str, from: u64,
        action: impl FnOnce(&std::path::Path, Value) -> Result<T, E>,
    ) -> Result<T, E>
    where E: From<RuntimeError> {
        let store = self.store()?;
        let task = store.tasks.get(id).filter(|task| task.workspace_id.is_empty())
            .ok_or_else(RuntimeError::task_not_found)?;
        let mut activity = task.chat_display_snapshot(from);
        activity["title_pending"] = json!(store.pending_titles.contains(&(String::new(), task.conversation_id.clone())));
        action(&store.data_dir.join("chat"), activity)
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
