use std::{fs, io, path::Path};

use serde_json::Value;

use super::{Runtime, RuntimeError, Status};

#[derive(Clone)]
pub struct NavigationSession {
    pub title: String,
}

pub struct NavigationRun {
    pub task_id: String,
    pub workspace_id: String,
    pub conversation_id: String,
    pub created_at: f64,
    pub status: &'static str,
    pub running: bool,
}

impl Runtime {
    /// Index metadata and an authoritative file existence check, without loading messages.
    pub fn navigation_session(
        &self,
        workspace_id: &str,
        conversation_id: &str,
    ) -> Result<Option<NavigationSession>, RuntimeError> {
        if !workspace_id.is_empty() {
            return Ok(self
                .store()?
                .session_index(workspace_id, conversation_id)?
                .map(session));
        }
        if !conversation_id.starts_with("conv_")
            || conversation_id.len() > 185
            || !conversation_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
        {
            return Err(RuntimeError::bad_request("Invalid conversation ID"));
        }
        let directory = self.store()?.data_dir.join("chat");
        if !exists(&directory, true)? {
            return Ok(None);
        }
        let directory = directory.join("conversations");
        if !exists(&directory,true)?{return Ok(None);}
        if directory.join(crate::conversation_db::FILENAME).exists() {
            let db=crate::conversation_db::Database::open(&directory).map_err(RuntimeError::storage)?;
            if let Some(row)=db.summary(conversation_id).map_err(RuntimeError::storage)?{return Ok(Some(session(row)));}
            if db.imported(conversation_id).map_err(RuntimeError::storage)?{return Ok(None);}
        }
        if !exists(&directory.join(format!("{conversation_id}.json")), false)? {
            return Ok(None);
        }
        let path = directory.join("index.json");
        let index = if exists(&path, false)? {
            match fs::read(&path) {
                Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or(Value::Null),
                Err(error) if error.kind() == io::ErrorKind::NotFound => Value::Null,
                Err(error) => return Err(RuntimeError::storage(error)),
            }
        } else {
            Value::Null
        };
        Ok(Some(session(
            index.get(conversation_id).cloned().unwrap_or(Value::Null),
        )))
    }

    /// Retained run summaries exclude messages, event buffers and execution resources.
    pub fn navigation_runs(&self) -> Result<Vec<NavigationRun>, RuntimeError> {
        Ok(self
            .store()?
            .tasks
            .values()
            .map(|task| NavigationRun {
                task_id: task.start.id.clone(),
                workspace_id: task.workspace_id.clone(),
                conversation_id: task.conversation_id.clone(),
                created_at: task.start.created_at,
                status: match task.status {
                    Status::Running if task.cancel_requested => "cancel_requested",
                    Status::Running => "running",
                    Status::Succeeded => "succeeded",
                    Status::Failed => "failed",
                    Status::Stopped => "stopped",
                },
                running: task.status == Status::Running,
            })
            .collect())
    }
}

fn session(row: Value) -> NavigationSession {
    NavigationSession {
        title: row["title"].as_str().unwrap_or_default().trim().into(),
    }
}

fn exists(path: &Path, directory: bool) -> Result<bool, RuntimeError> {
    match fs::symlink_metadata(path) {
        Ok(metadata)
            if if directory {
                metadata.is_dir()
            } else {
                metadata.is_file()
            } =>
        {
            Ok(true)
        }
        Ok(_) => Err(RuntimeError::storage("Unexpected Chat storage file type")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(RuntimeError::storage(error)),
    }
}
