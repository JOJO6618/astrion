use serde_json::{Map, Value};


use crate::interactions::Kind;

use super::{Runtime, RuntimeError, Status, iso_time, now, public_document};

/// Durable messages and the current live projection captured at one runtime cursor.
pub struct WebConversationSnapshot {
    pub document: Value,
    pub activity: Option<Value>,
    pub goal_progress: Option<Value>,
    pub compression: Value,
    pub pending_user_questions: Vec<Value>,
    pub pending_plan_approvals: Vec<Value>,
    pub pending_tool_approvals: Vec<Value>,
    pub approval_review_records: Vec<Value>,
    pub resolved_tool_approval_ids: Vec<String>,
    pub has_running_background_commands: bool,
    pub has_running_multi_agent: bool,
}

impl Runtime {
    pub fn web_snapshot(
        &self,
        workspace_id: &str,
        conversation_id: &str,
    ) -> Result<WebConversationSnapshot, RuntimeError> {
        let mut store = self.store()?;
        let document = store.read(workspace_id, conversation_id)?;
        let task = store.tasks.values().find(|task| {
            task.status == Status::Running
                && task.workspace_id == workspace_id
                && task.conversation_id == conversation_id
        });
        let principal = task.and_then(|task| task.approval.as_ref()).map(|settings| settings.principal.clone());
        let activity = task.map(|task| task.activity(u64::MAX));
        let compression = super::compression::projection(task, conversation_id, &document.raw()["metadata"]);
        let goal_progress = self.read_goal_progress(&store, workspace_id, &document)?;
        let has_running_background_commands = Self::conversation_has_background(&store, workspace_id, conversation_id);
        let (children_running, children_pending) = self.child_activity(&store, workspace_id, conversation_id);
        let questions = principal.as_ref().map_or_else(Vec::new, |principal| {
            store.interactions.pending(principal, Kind::Question, Some(conversation_id))
        });
        let plans = principal.as_ref().map_or_else(Vec::new, |principal| {
            store.interactions.pending(principal, Kind::Plan, Some(conversation_id))
        });
        let tool_approvals = principal.as_ref().map_or_else(
            || Ok(Vec::new()),
            |principal| store.approvals.list_pending(principal, Some(conversation_id)),
        ).map_err(RuntimeError::storage)?;
        // Pending approvals come from the trusted store; enrich them only with the
        // task's UI projection, captured at the same cursor under the same lock.
        let task = activity.as_ref().and_then(|value| value["task_id"].as_str())
            .and_then(|id| store.tasks.get(id));
        let pending = tool_approvals.into_iter().map(|approval| {
            let mut row = serde_json::to_value(approval).expect("approval is serializable");
            if let Some(task) = task { task.approval_display.decorate_pending(&mut row); }
            row
        }).collect();
        let (approval_review_records, resolved_tool_approval_ids) = task
            .map(|task| (task.approval_display.records(), task.approval_display.resolved()))
            .unwrap_or_default();
        let mut document = public_document(document);
        let path = store.conversation_path(workspace_id, conversation_id)?;
        super::thinking::decorate(path.parent().expect("conversation parent"), conversation_id, &mut document);
        Ok(WebConversationSnapshot {
            document,
            activity,
            goal_progress,
            compression,
            pending_user_questions: questions,
            pending_plan_approvals: plans,
            pending_tool_approvals: pending,
            approval_review_records,
            resolved_tool_approval_ids,
            has_running_background_commands,
            has_running_multi_agent: children_running || children_pending,
        })
    }

    pub(crate) fn web_compression_projection(&self, workspace: &str, id: &str, metadata: &Value) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        let task = store.tasks.values().find(|task| task.workspace_id == workspace && task.conversation_id == id && task.status == Status::Running);
        Ok(super::compression::projection(task, id, metadata))
    }

    pub(crate) fn web_header(&self, workspace: &str, id: &str) -> Result<(Value,usize),RuntimeError> {
        self.store()?.header(workspace,id)
    }

    pub(crate) fn web_activity_counts(&self, workspace: &str, id: &str) -> Result<(usize,bool,bool),RuntimeError> {
        let path = self.store()?.conversation_path(workspace,id)?;
        let db = crate::conversation_db::Database::open(path.parent().unwrap()).map_err(RuntimeError::storage)?;
        db.activity_counts(id).map_err(RuntimeError::storage)
    }

    /// The slash command resets this process's model context. Durable history is preserved.
    pub(crate) fn clear_context(&self, workspace: &str, id: &str) -> Result<(),RuntimeError> {
        let mut store = self.store()?;
        let (_, count) = store.header(workspace,id)?;
        if store.tasks.values().any(|t| t.workspace_id == workspace && t.conversation_id == id && t.status == Status::Running) {
            return Err(RuntimeError::new(409,"The conversation is running"));
        }
        store.cleared_through.insert((workspace.to_owned(), id.to_owned()), count);
        Ok(())
    }

    /// Read only the document fields used by settings views.
    pub fn web_metadata(
        &self,
        workspace_id: &str,
        conversation_id: &str,
    ) -> Result<Value, RuntimeError> {
        let (mut raw, _) = self.store()?.header(workspace_id, conversation_id)?;
        Ok(raw["metadata"].take())
    }

    /// The idle check shares the task-admission lock with the document update.
    pub fn update_idle_web_metadata<T>(
        &self,
        workspace_id: &str,
        conversation_id: &str,
        update: impl FnOnce(&mut Map<String, Value>) -> T,
    ) -> Result<T, RuntimeError> {
        self.edit_idle_web_document(workspace_id, conversation_id, |document| {
            Ok((update(metadata_mut(document)), true))
        })
    }

    /// Skip persistence on validation errors or unchanged settings.
    pub(crate) fn try_update_idle_web_metadata<T>(
        &self,
        workspace_id: &str,
        conversation_id: &str,
        update: impl FnOnce(&mut Map<String, Value>) -> Result<T, RuntimeError>,
    ) -> Result<T, RuntimeError> {
        self.edit_idle_web_document(workspace_id, conversation_id, |document| {
            let metadata = metadata_mut(document);
            let original = metadata.clone();
            let result = update(metadata)?;
            let changed = *metadata != original;
            if changed && let Some(timestamp) = iso_time(now()) {
                document["updated_at"] = Value::String(timestamp);
            }
            Ok((result, changed))
        })
    }

    fn edit_idle_web_document<T>(
        &self,
        workspace_id: &str,
        conversation_id: &str,
        update: impl FnOnce(&mut Value) -> Result<(T, bool), RuntimeError>,
    ) -> Result<T, RuntimeError> {
        let mut store = self.store()?;
        let (mut document, _) = store.header(workspace_id,conversation_id)?;
        if store.tasks.values().any(|task| {
            task.status == Status::Running
                && task.workspace_id == workspace_id
                && task.conversation_id == conversation_id
        }) {
            return Err(RuntimeError::new(
                409,
                "Change settings after the current task finishes",
            ));
        }
        let mode_keys = ["permission_mode", "execution_mode", "network_permission"];
        let before = mode_keys.map(|field| document["metadata"][field].clone());
        let (result, changed) = update(&mut document)?;
        if changed {
            let reset_terminals = mode_keys.iter().zip(before).any(|(field, prior)| {
                document["metadata"][*field] != prior
            });
            let path=store.conversation_path(workspace_id,conversation_id)?;
            let mut db=crate::conversation_db::Database::open(path.parent().unwrap()).map_err(RuntimeError::storage)?;
            db.append(conversation_id,Vec::new(),|_,_,_,_,prior|{
                let summary=super::store::index_row(&document,Some(&prior));
                Ok((document,summary))
            }).map_err(RuntimeError::storage)?;
            if reset_terminals {
                store.process_resources.close_terminals(Some(workspace_id), Some(conversation_id));
            }
        }
        Ok(result)
    }
}

fn metadata_mut(document: &mut Value) -> &mut Map<String, Value> {
    document["metadata"]
        .as_object_mut()
        .expect("validated conversation metadata")
}
