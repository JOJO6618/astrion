use std::sync::Arc;

use serde_json::{Value, json};

use crate::project_git::ReviewTools;

use super::{
    AgentTools, ModelConfig, Runtime, RuntimeError, TaskAdmission, ToolFuture,
    store::{Status, validate_workspace},
};

/// Trusted parameters for a review of a fixed local Git comparison.
pub struct GitReview {
    pub title: String,
    pub message: String,
    pub metadata: Value,
    pub run_mode: String,
}

impl Runtime {
    pub fn create_git_review(
        &self,
        workspace_id: &str,
        review: GitReview,
        model: ModelConfig,
        tools: ReviewTools,
    ) -> Result<Value, RuntimeError> {
        validate_workspace(workspace_id)?;
        {
            let store = self.store()?;
            let root = store
                .workspaces
                .get(workspace_id)
                .ok_or_else(|| RuntimeError::bad_request("Workspace is not bound"))?;
            if !tools.authorized_for(root, &store.data_dir, &review.metadata) {
                return Err(RuntimeError::bad_request(
                    "Git review binding does not match its workspace and comparison",
                ));
            }
        }
        if review.title.trim().is_empty() || review.title.chars().count() > 120 {
            return Err(RuntimeError::bad_request("Invalid Git review title"));
        }
        if !matches!(review.run_mode.as_str(), "fast" | "thinking") {
            return Err(RuntimeError::bad_request("Invalid Git review run mode"));
        }
        self.create_task_internal(
            workspace_id,
            &json!({
                "message":review.message,"run_mode":review.run_mode,"message_source":"git_review",
            }),
            model,
            TaskAdmission {
                exclusive_workspace: true,
                title: Some(review.title),
                metadata: Some(json!({
                    "work_mode":"execute","permission_mode":"readonly","execution_mode":"sandbox",
                    "skip_auto_title_generation":true,"git_review":review.metadata,
                })),
                new_conversation_id: None,
                user_message_metadata: None,
                tools: Arc::new(tools),
            },
        )
    }

    /// The association index can outlive the in-memory run. Match the live run
    /// by workspace, conversation and source before projecting its status.
    pub fn review_status(
        &self,
        workspace_id: &str,
        conversation_id: &str,
        task_id: &str,
    ) -> Result<Option<String>, RuntimeError> {
        validate_workspace(workspace_id)?;
        let store = self.store()?;
        Ok(store
            .tasks
            .get(task_id)
            .filter(|task| {
                task.workspace_id == workspace_id
                    && task.conversation_id == conversation_id
                    && task.start.message_source.as_deref() == Some("git_review")
            })
            .map(|task| {
                match task.status {
                    Status::Running => "running",
                    Status::Succeeded => "succeeded",
                    Status::Failed => "failed",
                    Status::Stopped => "stopped",
                }
                .to_owned()
            }))
    }
}

impl AgentTools for ReviewTools {
    fn schemas(&self) -> Vec<Value> {
        ReviewTools::schemas(self)
    }

    fn validate(&self, name: &str, args: &Value) -> Result<(), String> {
        ReviewTools::validate(self, name, args)
    }

    fn execute<'a>(&'a self, name: &'a str, args: Value) -> ToolFuture<'a> {
        Box::pin(ReviewTools::execute(self, name, args))
    }
}
