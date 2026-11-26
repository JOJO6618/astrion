use std::{collections::BTreeMap, sync::Arc};

use crate::{legacy::LegacyKind, mcp, workspace_catalog::RegisteredWorkspace};

use super::{Runtime, RuntimeError, Status, goals::verify_conversation_root, store::Store, verify_workspace_binding};

impl Runtime {
    pub(super) async fn mcp_for_task(&self, task_id: &str) -> Result<Option<Arc<mcp::Runtime>>, String> {
        let (manager, overrides) = {
            let mut store = self.store().map_err(|error| error.message)?;
            let task = store.tasks.get(task_id).filter(|task| task.status == Status::Running && !task.cancel_requested)
                .ok_or("Task stopped")?;
            let Some(settings) = &task.approval else { return Ok(None); };
            if settings.principal.username() != "host" || settings.principal.workspace_id() != task.workspace_id {
                return Ok(None);
            }
            let key = (task.workspace_id.clone(), task.conversation_id.clone());
            verify_workspace_binding(&store, &key.0, None).map_err(|error| error.message)?;
            let (document, _) = store.header(&key.0, &key.1).map_err(|error| error.message)?;
            let path = store.workspaces[&key.0].clone();
            verify_conversation_root(&path, &document).map_err(|error| error.message)?;
            if document["metadata"]["conversation_kind"] == "chat" { return Ok(None); }
            let overrides = document["metadata"]["runtime_tool_category_overrides"]
                .as_object().into_iter().flatten().filter_map(|(name, enabled)| {
                    enabled.as_bool().map(|enabled| (name.clone(), enabled))
                }).collect::<BTreeMap<_, _>>();
            let manager = if let Some(manager) = store.mcp_clients.get(&key) {
                manager.clone()
            } else {
                let workspace = RegisteredWorkspace { id: key.0.clone(), path, label: String::new() };
                let scope = mcp::RuntimeScope::main(&workspace, &store.data_dir, &key.1)
                    .map_err(|error| error.to_string())?;
                let manager = Arc::new(mcp::Runtime::new(mcp::Registry::for_data(&store.data_dir), scope));
                store.mcp_clients.insert(key, manager.clone());
                manager
            };
            (manager, overrides)
        };
        manager.set_category_overrides(overrides).await;
        Ok(Some(manager))
    }

    pub(super) fn release_mcp_scope(store: &mut Store, workspace: &str, conversation: Option<&str>) {
        let keys = store.mcp_clients.keys().filter(|(id, parent)| {
            id == workspace && conversation.is_none_or(|conversation| parent == conversation)
        }).cloned().collect::<Vec<_>>();
        for key in keys {
            let manager = store.mcp_clients.remove(&key).expect("loaded MCP manager");
            if let Ok(handle) = tokio::runtime::Handle::try_current() {
                handle.spawn(async move { manager.close().await });
            }
        }
    }

    pub async fn shutdown_mcp_resources(&self) -> Result<(), RuntimeError> {
        let managers = std::mem::take(&mut self.store()?.mcp_clients);
        for manager in managers.into_values() {
            manager.close().await;
        }
        if let Some(children) = &self.sub_agents {
            children.shutdown_mcp().await;
        }
        Ok(())
    }
}
