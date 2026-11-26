use serde_json::Value;
use tokio::{runtime::Handle, sync::watch};

use crate::process_runtime::{Invocation, child_schemas};

use super::{Parent, SubAgents, execution::Stop, scope::ChildScope};

pub(super) fn is_tool(name: &str) -> bool {
    matches!(
        name,
        "run_command" | "terminal_session" | "terminal_input" | "terminal_snapshot"
    )
}

impl SubAgents {
    pub(super) fn process_tools(&self, scope: &ChildScope) -> Vec<Value> {
        if self.runtime.child_processes_available(scope.process_mode()) {
            child_schemas()
        } else {
            Vec::new()
        }
    }

    pub(super) async fn process_tool(
        &self,
        scope: &ChildScope,
        actor: &str,
        name: &str,
        args: &Value,
        stop: watch::Receiver<Stop>,
    ) -> Result<Value, String> {
        let (scope, fixed_write_root) = scope.process_scope(actor)?;
        let invocation = Invocation::parse(name, args).map_err(str::to_owned)?;
        let runtime = self.runtime.clone();
        let operation = tokio::task::spawn_blocking(move || {
            if stop.borrow().cancelled() {
                return Err("子智能体已停止。".to_owned());
            }
            let owner = scope.owner.clone();
            let operation =
                runtime.start_child_process(scope, invocation, fixed_write_root.as_deref())?;
            if stop.borrow().cancelled() {
                drop(operation);
                runtime.close_child_processes(
                    &owner.workspace_id,
                    &owner.conversation,
                    owner.actor.as_deref().expect("child scope"),
                );
                return Err("子智能体已停止。".into());
            }
            Ok(operation)
        })
        .await
        .map_err(|_| "子智能体执行器已退出。".to_owned())??;
        Ok(operation.finish().await.0)
    }

    pub(super) fn retire_child_processes(&self, parent: &Parent, id: &str) {
        if let Ok(handle) = Handle::try_current() {
            let runtime = self.runtime.clone();
            let parent = parent.clone();
            let id = id.to_owned();
            handle.spawn_blocking(move || {
                runtime.close_child_processes(&parent.workspace_id, &parent.conversation_id, &id)
            });
        }
    }
}
