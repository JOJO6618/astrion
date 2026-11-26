use std::path::Path;

use crate::{
    approval::ExecutionMode,
    process_runtime::{Invocation, Network, Operation, Scope},
};

use super::{Runtime, verify_workspace_binding};

impl Runtime {
    pub(crate) fn child_processes_available(&self, mode: ExecutionMode) -> bool {
        self.store()
            .is_ok_and(|store| store.process_resources.available_mode(mode))
    }

    pub(crate) fn start_child_process(
        &self,
        mut scope: Scope,
        invocation: Invocation,
        fixed_write_root: Option<&Path>,
    ) -> Result<Operation, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        verify_workspace_binding(
            &store,
            &scope.owner.workspace_id,
            Some(&scope.owner.workspace),
        )
        .map_err(|error| error.message)?;
        let (header, _) = store
            .header(&scope.owner.workspace_id, &scope.owner.conversation)
            .map_err(|error| error.message)?;
        scope.access.network = Network::from_metadata(&header["metadata"]["network_permission"])
            .map_err(|error| error.to_string())?;
        store
            .process_resources
            .start_child(scope, invocation, fixed_write_root)
            .map_err(|error| error.to_string())
    }

    pub(crate) fn close_child_processes(&self, workspace: &str, conversation: &str, actor: &str) {
        if let Ok(mut store) = self.store() {
            store
                .process_resources
                .close_child(workspace, conversation, actor);
        }
    }
}
