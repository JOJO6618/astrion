mod operations;

use super::{
    LegacyDocument, Runtime, RuntimeError, Status, goals::verify_conversation_root, legacy_error,
    verify_workspace_binding, writable_task,
};
use crate::{web_assets::personalization::english, workspace_catalog::RegisteredWorkspace};
pub(super) use operations::{CREATE, UPDATE, schemas};
use serde_json::{Value, json};

impl Runtime {


    pub fn web_todo_list(
        &self,
        ws: &RegisteredWorkspace,
        id: Option<&str>,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        verify_workspace_binding(&store, &ws.id, Some(&ws.path))?;
        let active = |key: &str| {
            store.tasks.values().any(|t| {
                t.workspace_id == ws.id && t.conversation_id == key && t.status == Status::Running
            })
        };
        let todo = match id.filter(|s| !s.is_empty()) {
            Some(id) => {
                let (doc, _) = store.header(&ws.id, id).map_err(|e| {
                    if matches!(e.status, 400 | 404) {
                        RuntimeError::new(404, "Conversation not found")
                    } else {
                        e
                    }
                })?;
                verify_conversation_root(&ws.path, &doc)
                    .map_err(|_| RuntimeError::new(404, "Conversation not found"))?;
                let todo = doc["todo_list"].clone();
                if active(id) {
                    operations::snapshot(todo)
                } else {
                    todo
                }
            }
            None => Value::Null,
        };
        Ok(json!({"success":true,"data":todo}))
    }

    pub(super) async fn execute_todo(
        &self,
        task_id: &str,
        name: &str,
        args: Value,
    ) -> Result<Value, String> {
        let runtime = self.clone();
        let task_id = task_id.to_owned();
        let create = name == CREATE;
        tokio::task::spawn_blocking(move || {
            let mut store = runtime.store().map_err(|e| e.message)?;
            let task = writable_task(&mut store, &task_id)?;
            let workspace = task.workspace_id.clone();
            let conversation = task.conversation_id.clone();
            let en = english(&store.data_dir);
            let path = store.conversation_path(&workspace, &conversation).map_err(|e| e.message)?;
            let mut db = crate::conversation_db::Database::open(path.parent().unwrap())?;
            let change = if create {
                operations::create(&args, en)
            } else {
                operations::update(db.field(&conversation, "root", "todo_list")?.unwrap_or_default(), &args, en)
            };
            let (todo, message) = match change {
                Ok(result) => result,
                Err(error) => return Ok(json!({"success":false,"error":error})),
            };
            db.set_field(&conversation, "root", "todo_list", &todo)?;
            writable_task(&mut store, &task_id)?.emit(
                "todo_updated",
                json!({"todo_list":operations::snapshot(todo.clone())}),
            );
            Ok(json!({"success":true,"todo_list":todo,"message":message}))
        })
        .await
        .map_err(|e| e.to_string())?
    }
}
