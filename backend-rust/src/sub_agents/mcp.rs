use std::{sync::Arc, time::Duration};

use crate::{mcp, workspace_catalog::RegisteredWorkspace};

use super::{SubAgents, execution::{Parent, Stop}, scope::ChildScope};

impl SubAgents {
    pub(super) fn mcp_for_child(
        &self, parent: &Parent, id: &str, scope: &ChildScope,
    ) -> Result<Arc<mcp::Runtime>, String> {
        scope.check()?;
        let mut live = self.lock()?;
        if live.shutting_down || live.workers.get(id).is_none_or(|stop| stop.borrow().cancelled()) {
            return Err("子智能体已停止。".into());
        }
        if let Some(manager) = live.mcp_clients.get(id) {
            return Ok(manager.clone());
        }
        let workspace = RegisteredWorkspace {
            id: parent.workspace_id.clone(),
            label: String::new(),
            path: parent.root.clone(),
        };
        let data = self.registry.data_dir();
        let scope = mcp::RuntimeScope::child(&workspace, data, &parent.conversation_id, id, scope.working_dir())
            .map_err(|error| error.to_string())?;
        let manager = Arc::new(mcp::Runtime::new(mcp::Registry::for_data(data), scope));
        live.mcp_clients.insert(id.to_owned(), manager.clone());
        Ok(manager)
    }

    pub(crate) async fn shutdown_mcp(&self) {
        let managers = match self.lock() {
            Ok(mut live) => {
                live.shutting_down = true;
                for worker in live.workers.values() {
                    if !worker.borrow().cancelled() {
                        worker.send_replace(Stop::Shutdown);
                    }
                }
                std::mem::take(&mut live.mcp_clients)
            }
            Err(_) => return,
        };
        for manager in managers.into_values() {
            manager.close().await;
        }
        let _ = tokio::time::timeout(Duration::from_secs(10), async {
            while self.lock().is_ok_and(|live| !live.workers.is_empty()) {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        }).await;
    }
}
