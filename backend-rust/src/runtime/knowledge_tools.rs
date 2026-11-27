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

/// The caller holds the runtime store lock across the version check, native write and checkpoint.
pub(super) fn write_workspace_memory(
    store: &super::store::Store, workspace_id: &str, workspace: &std::path::Path,
    conversation: &str, path: &std::path::Path, write: impl FnOnce(bool) -> Value,
) -> Result<Value, String> {
    let namespace = knowledge::workspace_namespace(workspace).map_err(|e| e.to_string())?;
    let directory = store.data_dir.join("memory").join(namespace);
    if path.starts_with(directory.join("topics")) {
        let _lock = knowledge::lock_topics()?;
        return Ok(write(false));
    }
    let memory = directory.join("MEMORY.md");
    if path != memory {
        if path.parent() == memory.parent() && path.file_name().and_then(|s| s.to_str())
            .is_some_and(|name| name.eq_ignore_ascii_case("MEMORY.md")) {
            return Ok(json!({"success":false,"error":"请使用当前工作区记忆的路径。","path":memory}));
        }
        return Ok(write(false));
    }
    let db_path = store.conversation_path(workspace_id, conversation).map_err(|e| e.message)?;
    let mut db = Database::open(db_path.parent().expect("conversation directory"))?;
    let snapshot = db.field(conversation, "metadata", "workspace_memory_snapshot")?.unwrap_or(Value::Null);
    if snapshot["path"].as_str().map(std::path::Path::new) != Some(path) || !snapshot["version"].is_string() {
        return Ok(write(false));
    }
    let knowledge = Knowledge::open(workspace, &store.data_dir).map_err(|e| e.to_string())?;
    let current = knowledge.prompt_workspace_memory().map_err(|e| e.to_string())?;
    let known = db.field(conversation, "metadata", "workspace_memory_write_version")?;
    if known.as_ref().and_then(Value::as_str).or(snapshot["version"].as_str()) != current.record["version"].as_str() {
        db.set_field(conversation, "metadata", "workspace_memory_write_version", &current.record["version"])?;
        return Ok(json!({"success":false,"enforcement":"workspace_memory_conflict","path":path,
            "error":"MEMORY.md 已被其他对话修改，请基于最新内容合并后重试。","current_content":current.text}));
    }
    let result = write(true);
    if result["success"] == true {
        let updated = knowledge.prompt_workspace_memory().map_err(|e| e.to_string())?;
        db.set_field(conversation, "metadata", "workspace_memory_write_version", &updated.record["version"])?;
    }
    Ok(result)
}

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
