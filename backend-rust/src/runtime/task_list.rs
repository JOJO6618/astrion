use super::{Runtime, RuntimeError, Status};
use serde_json::{Value, json};

impl Runtime {
    pub fn list_tasks(
        &self,
        workspace_id: &str,
        workspace_label: &str,
        status_filter: Option<&str>,
    ) -> Result<Value, RuntimeError> {
        let normalized = status_filter
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase();
        let (mut rows, index_path) = {
            let store = self.store()?;
            let mut rows = Vec::new();
            for task in store
                .tasks
                .values()
                .filter(|task| task.workspace_id == workspace_id)
            {
                let status = match task.status {
                    Status::Running if task.cancel_requested => "cancel_requested",
                    Status::Running => "running",
                    Status::Succeeded => "succeeded",
                    Status::Failed => "failed",
                    Status::Stopped => "stopped",
                };
                if !matches_filter(&normalized, status) {
                    continue;
                }
                rows.push((task.start.created_at, json!({
                    "task_id":task.start.id, "username":"host", "workspace_id":workspace_id,
                    "conversation_kind":"agent", "workspace_label":workspace_label, "status":status,
                    "created_at":task.start.created_at, "updated_at":task.updated_at,
                    "message":task.start.message, "conversation_id":task.conversation_id,
                    "error":task.error, "message_source":task.start.message_source,
                    "goal_mode":task.goal_run.is_some(), "goal_progress":task.goal_progress, "task_type":task.kind,
                })));
            }
            let index = store.data_dir.join("conversations").join(workspace_id);
            (rows, index)
        };
        if rows.is_empty() {
            return Ok(json!({"success":true,"data":[]}));
        }
        // Titles are optional. One atomic index read serves every row without
        // opening any conversation or holding the event producer's lock during I/O.
        let mut index=json!({});
        if index_path.join(crate::conversation_db::FILENAME).exists(){
            let db=crate::conversation_db::Database::open(&index_path).map_err(RuntimeError::storage)?;
            for row in db.rows().map_err(RuntimeError::storage)?{if let Some(id)=row["id"].as_str(){index[id]=row.clone();}}
        }
        rows.sort_by(|left, right| right.0.total_cmp(&left.0));
        let data: Vec<_> = rows
            .into_iter()
            .map(|(_, mut row)| {
                let id = row["conversation_id"].as_str().unwrap_or_default();
                let title = index[id]["title"]
                    .as_str()
                    .map(str::trim)
                    .filter(|s| !s.is_empty());
                row["conversation_title"] = json!(title);
                row
            })
            .collect();
        Ok(json!({"success":true,"data":data}))
    }
}

fn matches_filter(filter: &str, status: &str) -> bool {
    match filter {
        "" => true,
        "active" => matches!(status, "pending" | "running" | "cancel_requested"),
        csv => csv.split(',').any(|value| value.trim() == status),
    }
}
