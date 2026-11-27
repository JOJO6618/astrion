use serde_json::{Map, Value, json};

use crate::{approval::ExecutionMode, conversation_db::Database};
use super::mode_notice;
use super::{Runtime, RuntimeError, Status, emit_user_message, now, store::Store, writable_task};

impl Runtime {
    pub(crate) fn queue_running_host_mode(
        &self, workspace: &str, conversation: &str, key: &str, value: &str,
        validate: impl FnOnce(&Value) -> Result<(), RuntimeError>,
    ) -> Result<Option<Value>, RuntimeError> {
        let mut store = self.store()?;
        let Some(task) = store.tasks.values().find(|task| task.status == Status::Running
            && task.workspace_id == workspace && task.conversation_id == conversation)
        else { return Ok(None); };
        if task.goal_run.is_some() && key == "pending_permission_mode"
            && !matches!(value, "unrestricted" | "auto_approval")
        {
            return Err(RuntimeError::new(409, "The current goal requires automatic approval or unrestricted permission"));
        }
        let id = task.start.id.clone();
        let (header, _) = store.header(workspace, conversation)?;
        validate(&header["metadata"])?;
        let metadata = header["metadata"].as_object().ok_or_else(|| RuntimeError::storage("Conversation metadata unavailable"))?;
        let mut after = metadata.clone();
        after.insert(key.to_owned(), json!(value));
        persist_metadata(&store, workspace, conversation, metadata, &after)?;
        store.tasks.get_mut(&id).expect("locked active task").pending_host_modes = true;
        Ok(Some(Value::Object(after)))
    }

    /// 应用排队中的模式切换，返回本轮要注入模型的 user 通知。
    pub(super) fn apply_pending_host_modes(&self, task_id: &str, include_saved: bool) -> Result<Vec<Value>, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        writable_task(&mut store, task_id)?;
        apply_pending_to_task(&mut store, task_id, include_saved).map_err(|error| error.message)
    }
}

pub(super) fn apply_pending_to_task(store: &mut Store, task_id: &str, include_saved: bool) -> Result<Vec<Value>, RuntimeError> {
    let task = store.tasks.get(task_id).ok_or_else(RuntimeError::task_not_found)?;
    if !include_saved && !task.pending_host_modes { return Ok(Vec::new()); }
    let (workspace, conversation) = (task.workspace_id.clone(), task.conversation_id.clone());
    let goal = task.goal_run.is_some();
    let (header, _) = store.header(&workspace, &conversation)?;
    let before = &header["metadata"];
    let mut after = crate::web_status::apply_pending_host_modes(
        &store.data_dir, before, store.process_resources.available_mode(ExecutionMode::Direct),
        store.process_resources.available(),
    );
    if goal && &after != before { crate::goals::enforce_modes(&mut after); }
    // 生效值一旦与模型已知晓的基线不同就要通知：排队切换、空闲期直接切换、以及
    // plan/只读联动都会在这里被同一次比对捕获。
    let changes = after.as_object_mut()
        .map(|metadata| mode_notice::drift(&store.data_dir, metadata))
        .unwrap_or_default();
    if !changes.is_empty() {
        if let Some(metadata) = after.as_object_mut() { mode_notice::mark_known(&store.data_dir, metadata); }
    }
    if &after != before {
        let old = before.as_object().ok_or_else(|| RuntimeError::storage("Conversation metadata unavailable"))?;
        let updated = after.as_object().ok_or_else(|| RuntimeError::storage("Conversation metadata unavailable"))?;
        persist_metadata(store, &workspace, &conversation, old, updated)?;
        if ["permission_mode", "execution_mode", "network_permission"].iter().any(|key| before[*key] != after[*key]) {
            store.process_resources.close_terminals(Some(&workspace), Some(&conversation));
        }
    }
    let messages: Vec<Value> = changes.iter()
        .map(|(kind, mode)| mode_notice::message(kind, mode))
        .collect();
    for notice in &messages {
        let content = notice["content"].as_str().unwrap_or_default();
        store.append_messages(&workspace, &conversation, vec![notice.clone()], now(), None, None)?;
        let task = writable_task(store, task_id).map_err(|message| RuntimeError::new(409, message))?;
        emit_user_message(task, content, notice["metadata"].clone());
    }
    let task = store.tasks.get_mut(task_id).expect("locked active task");
    task.pending_host_modes = false;
    if let Some(settings) = &mut task.approval { settings.apply_conversation_modes(&after); }
    Ok(messages)
}

fn persist_metadata(
    store: &Store, workspace: &str, conversation: &str, before: &Map<String, Value>, after: &Map<String, Value>,
) -> Result<(), RuntimeError> {
    if before == after { return Ok(()); }
    let path = store.conversation_path(workspace, conversation)?;
    let directory = path.parent().ok_or_else(|| RuntimeError::storage("Invalid conversation path"))?;
    Database::open(directory).map_err(RuntimeError::storage)?
        .replace_metadata_fields(conversation, before, after).map_err(RuntimeError::storage)
}
