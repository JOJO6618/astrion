mod drafts;
mod search;
mod statistics;
use super::{LegacyDocument, Runtime, RuntimeError, legacy_error, new_document, now};
use crate::workspace_catalog::RegisteredWorkspace;
use serde_json::{Map, Value, json};

const SUMMARY_FIELDS: &[&str] = &[
    "id",
    "title",
    "created_at",
    "updated_at",
    "project_path",
    "project_relative_path",
    "thinking_mode",
    "run_mode",
    "model_key",
    "custom_prompt_name",
    "personalization_name",
    "total_messages",
    "total_tools",
    "status",
    "quick_entry",
];

impl Runtime {
    pub fn web_conversation_list(
        &self,
        workspace: &str,
        limit: usize,
        offset: usize,
        non_empty: bool,
    ) -> Result<Value, RuntimeError> {
        let mut rows = self.store()?.session_rows(workspace)?;
        if non_empty {
            rows.retain(|row| row["incompatible"] == true || row["total_messages"].as_u64().unwrap_or(0) > 0);
        }
        rows.sort_by(|a, b| {
            b["updated_at"]
                .as_str()
                .cmp(&a["updated_at"].as_str())
                .then_with(|| a["id"].as_str().cmp(&b["id"].as_str()))
        });
        let total = rows.len();
        let conversations: Vec<_> = rows
            .into_iter()
            .skip(offset)
            .take(limit)
            .map(|row| {
                let mut fields = Map::new();
                if row["incompatible"] == true {fields.insert("incompatible".into(),json!(true));}
                for &key in SUMMARY_FIELDS {
                    let value = row.get(key).cloned().unwrap_or_else(|| match key {
                        "title" => json!("未命名对话"),
                        "status" => json!("active"),
                        "total_messages" | "total_tools" => json!(0),
                        "thinking_mode" => json!(false),
                        _ => Value::Null,
                    });
                    fields.insert(key.into(), value);
                }
                fields.insert("quick_entry".into(), json!(row["quick_entry"] == true));
                fields
            })
            .collect();
        Ok(
            json!({"success":true,"data":{"conversations":conversations,"total":total,"limit":limit,"offset":offset,"has_more":offset.saturating_add(limit)<total}}),
        )
    }

    pub fn web_conversation_info(&self, workspace: &str, id: &str) -> Result<Value, RuntimeError> {
        let doc = self.store()?.read(workspace, id)?;
        let raw = doc.raw();
        Ok(json!({"success":true,"data":{
            "id":doc.source_id(), "title":raw["title"], "created_at":raw["created_at"],
            "updated_at":raw["updated_at"], "metadata":raw["metadata"],
            "messages_count":raw["messages"].as_array().map(Vec::len).unwrap_or(0)
        }}))
    }

    pub fn create_web_conversation(
        &self,
        workspace: &RegisteredWorkspace,
        metadata: Map<String, Value>,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        if store.workspaces.get(&workspace.id) != Some(&workspace.path) {
            return Err(RuntimeError::new(409, "Workspace binding has changed"));
        }
        let settings = Value::Object(metadata);
        let mut raw = new_document(&store, &workspace.id, &settings, now())?.into_raw();
        let Value::Object(overrides) = settings else {
            unreachable!()
        };
        raw["metadata"]
            .as_object_mut()
            .ok_or_else(|| RuntimeError::storage("Invalid new conversation metadata"))?
            .extend(overrides);
        let doc = LegacyDocument::from_value(raw).map_err(legacy_error)?;
        let id = doc.source_id().to_owned();
        store.persist(&workspace.id, doc, true)?;
        Ok(json!({"success":true,"conversation_id":id}))
    }
}
