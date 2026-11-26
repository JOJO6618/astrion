//! Workspace knowledge and Host account memory in the normal Agent loop.
use serde_json::{Value, json};

use crate::{
    conversation_db::Database,
    knowledge::{
        self, Knowledge, KnowledgeAccess, KnowledgeReads,
        global_memory::{self, GlobalMemoryAccess},
    },
};

use super::{Runtime, approval::task_scope, writable_task};

const FIELD: &str = "project_memory_reads";

pub(super) fn write_schemas() -> impl Iterator<Item = Value> {
    knowledge::definitions(KnowledgeAccess::ReadWrite)
        .into_iter()
        .filter(|spec| spec["function"]["name"] == knowledge::UPDATE_PROJECT_MEMORY)
        .chain(global_memory::definition(GlobalMemoryAccess::Manage))
}

pub(super) fn is_managed(name: &str) -> bool {
    matches!(
        name,
        knowledge::RECALL_PROJECT_MEMORY
            | knowledge::UPDATE_PROJECT_MEMORY
            | global_memory::UPDATE_TOOL
    )
}

impl Runtime {
    pub(super) async fn execute_knowledge(
        &self,
        task_id: &str,
        name: &str,
        args: &Value,
        batch: &[Value],
    ) -> Result<(Value, Option<Value>), String> {
        let (root, data, conversation, access, reads) = {
            let mut store = self.store().map_err(|error| error.message)?;
            let (workspace, conversation) = {
                let task = writable_task(&mut store, task_id)?;
                (task.workspace_id.clone(), task.conversation_id.clone())
            };
            let modes = task_scope(&store, task_id)
                .ok()
                .and_then(|(_, settings)| settings.process_modes());
            if modes.is_none() && name != knowledge::RECALL_PROJECT_MEMORY {
                return Ok((
                    json!({"success":false,"error_code":"tool_authority_unavailable","error":"缺少记忆操作的对话权限。"}),
                    None,
                ));
            }
            let access = if modes.is_some_and(|(permission, _)| {
                permission != crate::approval::PermissionMode::ReadOnly
            }) {
                KnowledgeAccess::ReadWrite
            } else {
                KnowledgeAccess::ReadOnly
            };
            let path = store
                .conversation_path(&workspace, &conversation)
                .map_err(|error| error.message)?;
            let root = store
                .workspaces
                .get(&workspace)
                .ok_or("Missing knowledge workspace")?
                .clone();
            let reads = match checkpoint(batch) {
                Some(reads) => Some(reads.clone()),
                None => Database::open(path.parent().ok_or("Missing conversation directory")?)?
                    .field(&conversation, "metadata", FIELD)?,
            };
            (root, store.data_dir.clone(), conversation, access, reads)
        };
        let name = name.to_owned();
        let args = args.clone();
        tokio::task::spawn_blocking(move || {
            let knowledge = Knowledge::open(&root, &data).map_err(|error| error.to_string())?;
            if name == global_memory::UPDATE_TOOL {
                let memory = knowledge
                    .global_memory()
                    .map_err(|error| error.to_string())?;
                return Ok((
                    memory.execute(&args, GlobalMemoryAccess::Manage, Some(&conversation)),
                    None,
                ));
            }
            let previous = reads.clone();
            let mut reads: KnowledgeReads = reads
                .and_then(|raw| serde_json::from_value(raw).ok())
                .unwrap_or_default();
            let result = knowledge
                .execute(&name, &args, access, &mut reads)
                .ok_or("Unknown knowledge tool")?;
            let tested = serde_json::to_value(reads).map_err(|error| error.to_string())?;
            let updated =
                (result["success"] == true && previous.as_ref() != Some(&tested)).then_some(tested);
            Ok((result, updated))
        })
        .await
        .map_err(|error| error.to_string())?
    }
}

pub(super) fn checkpoint(batch: &[Value]) -> Option<&Value> {
    let calls = batch.first()?["tool_calls"].as_array()?;
    batch
        .iter()
        .skip(1)
        .rev()
        .filter(|row| {
            row["role"] == "tool"
                && calls.iter().any(|call| {
                    call["id"] == row["tool_call_id"]
                        && call["function"]["name"].as_str().is_some_and(is_managed)
                })
        })
        .find_map(|row| row["metadata"].get(FIELD).filter(|value| value.is_object()))
}

pub(super) fn annotate(result: &mut Value, read_state: Value) {
    result["metadata"][FIELD] = read_state;
}

pub(super) fn persist(raw: &mut Value, read_state: Value) {
    raw["metadata"][FIELD] = read_state;
}
