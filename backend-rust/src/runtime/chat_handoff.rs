use std::{path::Path, sync::Arc};

use cap_fs_ext::DirExt;
use serde_json::{Value, json};

use super::{
    ModelConfig, Runtime, RuntimeError, TaskAdmission, TaskApprovalSettings,
    verify_workspace_binding,
};
use crate::{

    legacy::LegacyKind,
    workspace_catalog::RegisteredWorkspace,
};

impl Runtime {
    /// Admit the first Agent turn with its source association in the same durable
    /// document. A receipt recovered after restart is sufficient to prevent replay.
    pub(crate) fn start_chat_handoff(
        &self,
        workspace: &RegisteredWorkspace,
        agent_id: &str,
        source_id: &str,
        handoff_id: &str,
        payload: &Value,
        model: ModelConfig,
        settings: TaskApprovalSettings,
    ) -> Result<Value, RuntimeError> {
        let admitted = {
            let mut store = self.store()?;
            verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
            verify_source(
                &store.data_dir,
                source_id,
                handoff_id,
                &payload["handoff_operation_id"],
            )?;
            let mut request = payload.clone();
            let existing = match store.read(&workspace.id, agent_id) {
                Ok(document) => Some(document),
                Err(error) if error.status == 404 => None,
                Err(error) => return Err(error),
            };
            if let Some(document) = existing {
                let association = &document.raw()["metadata"]["handoff_from_chat"];
                if document.kind() != LegacyKind::Agent
                    || association["conversation_id"] != source_id
                    || association["handoff_id"] != handoff_id
                {
                    return Err(RuntimeError::new(
                        409,
                        "Agent conversation belongs to a different handoff",
                    ));
                }
                if document.raw()["messages"]
                    .as_array()
                    .is_some_and(|messages| {
                        messages.iter().any(|message| message["role"] == "user")
                    })
                {
                    let task = store.tasks.values().find(|task| {
                        task.workspace_id == workspace.id
                            && task.conversation_id == agent_id
                            && task.start.message_source.as_deref() == Some("chat_handoff")
                    });
                    return Ok(json!({"success":true,"data":{
                        "workspace_id":workspace.id,"conversation_id":agent_id,
                        "task_id":task.map(|task| task.start.id.as_str()),
                    }}));
                }
                // Python can leave a correctly associated empty Agent before admission.
                request["conversation_id"] = json!(agent_id);
            } else if store.conversation_id_exists(agent_id)? {
                return Err(RuntimeError::new(
                    409,
                    "Agent conversation identity is already assigned",
                ));
            }
            let mut metadata = payload["handoff_agent_metadata"]
                .as_object()
                .cloned()
                .unwrap_or_default();
            metadata.insert(
                "handoff_from_chat".into(),
                json!({"conversation_id":source_id,"handoff_id":handoff_id}),
            );
            let tools = store
                .workspace_tools
                .get(&workspace.id)
                .ok_or_else(|| RuntimeError::new(409, "Handoff workspace tools are unavailable"))?
                .clone();
            self.admit_task(
                &mut store,
                &workspace.id,
                &request,
                &model,
                TaskAdmission {
                    exclusive_workspace: false,
                    title: None,
                    metadata: Some(Value::Object(metadata)),
                    new_conversation_id: Some(agent_id.to_owned()),
                    user_message_metadata: Some(
                        json!({"message_source":"chat_handoff","handoff_id":handoff_id}),
                    ),
                    tools: Arc::new(tools),
                },
                Some(settings),
            )?
        };
        Ok(self.spawn_admitted(admitted, model))
    }
}

fn verify_source(
    data: &Path,
    id: &str,
    handoff: &str,
    operation: &Value,
) -> Result<(), RuntimeError> {
    if !id.starts_with("conv_")
        || id.len() > 100
        || !id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        || !operation.is_string()
    {
        return Err(RuntimeError::bad_request("Invalid Chat handoff identity"));
    }
    let dir=data.join("chat").join("conversations");
    for parent in [data.join("chat"),dir.clone()]{
        super::store::require_directory_if_present(&parent)?;
    }
    if !dir.join(crate::conversation_db::FILENAME).exists() {return Err(RuntimeError::session_not_found());}
    let document=crate::conversation_db::Database::open(&dir).map_err(RuntimeError::storage)?
        .read(id).map_err(RuntimeError::storage)?.ok_or_else(RuntimeError::session_not_found)?;
    let chat = &document["metadata"]["chat"];
    let state = &chat["handoffs"][handoff];
    if document["id"] != id
        || document["metadata"]["conversation_kind"] != "chat"
        || !chat["active_turn_id"].is_null()
        || !chat["latest_leaf_id"].is_string()
        || state["status"] != "preparing"
        || state["present"] == false
        || state["leaf_id"] != chat["latest_leaf_id"]
        || state["operation_id"] != *operation
    {
        return Err(RuntimeError::new(
            409,
            "Chat changed before the Agent handoff began",
        ));
    }
    Ok(())
}
