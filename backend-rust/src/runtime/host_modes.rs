use serde_json::{Map, Value, json};

use crate::{approval::ExecutionMode, conversation_db::Database};
use super::{Runtime, RuntimeError, Status, store::Store, writable_task};

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

    pub(super) fn apply_pending_host_modes(&self, task_id: &str, include_saved: bool) -> Result<(), String> {
        let mut store = self.store().map_err(|error| error.message)?;
        writable_task(&mut store, task_id)?;
        apply_pending_to_task(&mut store, task_id, include_saved).map_err(|error| error.message)
    }
}

pub(super) fn apply_pending_to_task(store: &mut Store, task_id: &str, include_saved: bool) -> Result<(), RuntimeError> {
    let task = store.tasks.get(task_id).ok_or_else(RuntimeError::task_not_found)?;
    if !include_saved && !task.pending_host_modes { return Ok(()); }
    let (workspace, conversation) = (task.workspace_id.clone(), task.conversation_id.clone());
    let goal = task.goal_run.is_some();
    let (header, _) = store.header(&workspace, &conversation)?;
    let before = &header["metadata"];
    let mut after = crate::web_status::apply_pending_host_modes(
        &store.data_dir, before, store.process_resources.available_mode(ExecutionMode::Direct),
        store.process_resources.available(),
    );
    if goal && &after != before { crate::goals::enforce_modes(&mut after); }
    if &after != before {
        let old = before.as_object().ok_or_else(|| RuntimeError::storage("Conversation metadata unavailable"))?;
        let updated = after.as_object().ok_or_else(|| RuntimeError::storage("Conversation metadata unavailable"))?;
        persist_metadata(store, &workspace, &conversation, old, updated)?;
        if ["permission_mode", "execution_mode", "network_permission"].iter().any(|key| before[*key] != after[*key]) {
            store.process_resources.close_terminals(Some(&workspace), Some(&conversation));
        }
    }
    let task = store.tasks.get_mut(task_id).expect("locked active task");
    task.pending_host_modes = false;
    if let Some(settings) = &mut task.approval { settings.apply_conversation_modes(&after); }
    Ok(())
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
