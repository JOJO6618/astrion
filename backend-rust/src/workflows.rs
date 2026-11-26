//! Python Host workflow library and conversation-scoped state.

mod definition;
mod files;
mod registry;
mod reviewer;
mod routes;
mod state;

pub use definition::{BranchRoute, Node, NodeKind, Position, ReviewMode, Source, Workflow};
pub use registry::WorkflowRegistry;
pub use reviewer::{ReviewEvidence, run_model_review};
pub(crate) use reviewer::run_recorded_review;
pub use routes::{browser_router, browser_scoped_router, registry_list_router};
pub use state::{Activation, Advance, Progress, ReviewDecision, ReviewRequest, WorkflowStates};

use std::{fmt, io};

#[derive(Debug)]
pub struct WorkflowError {
    pub status: u16,
    pub message: String,
}

impl WorkflowError {
    pub fn new(status: u16, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
        }
    }
}

impl fmt::Display for WorkflowError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for WorkflowError {}

impl From<io::Error> for WorkflowError {
    fn from(error: io::Error) -> Self {
        Self::new(500, format!("工作流文件操作失败：{error}"))
    }
}

pub type Result<T> = std::result::Result<T, WorkflowError>;

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/workflow_contract.rs"]
mod tests;
