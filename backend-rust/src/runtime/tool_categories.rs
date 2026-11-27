use std::collections::HashSet;
use serde_json::{Value, json};
use super::{Runtime,RuntimeError, verify_workspace_binding};
use crate::{conversation_db::Database, web_assets::personalization::runtime_categories, workspace_catalog::RegisteredWorkspace};

fn apply_overrides(rows: &mut [Value], overrides: &Value) {
    for row in rows {
        let Some(id) = row["id"].as_str() else { continue };
        if row["locked"] != true && let Some(flag) = overrides[id].as_bool() { row["enabled"] = json!(flag); }
    }
}

pub(super) fn category_tools(data: &std::path::Path, category: &str, metadata: &Value) -> Result<Vec<String>,String> {
    let (mut rows,_) = runtime_categories(data).map_err(|e|e.to_string())?;
    apply_overrides(&mut rows,&metadata["runtime_tool_category_overrides"]);
    Ok(rows.iter().find(|row| row["id"]==category && row["enabled"]==true)
        .and_then(|row|row["tools"].as_array()).into_iter().flatten().filter_map(|tool|tool.as_str().map(str::to_owned)).collect())
}

fn effective_rows(data: &std::path::Path, overrides: &Value) -> Result<Vec<Value>, String> {
    let (mut rows, _) = runtime_categories(data).map_err(|error| error.to_string())?;
    apply_overrides(&mut rows, overrides);
    Ok(rows)
}

fn non_mcp_disabled(rows: &[Value]) -> impl Iterator<Item = &Value> {
    rows.iter().filter(|row| row["enabled"] == false
        && !row["id"].as_str().is_some_and(|id| id == "mcp" || id.starts_with("mcp_server__")))
}

pub(super) fn prompt_states(
    data: &std::path::Path, overrides: &Value,
) -> Result<(HashSet<String>, Vec<String>), String> {
    let rows = effective_rows(data, overrides)?;
    let mut blocked = HashSet::new();
    let mut notice = std::collections::BTreeSet::new();
    for row in non_mcp_disabled(&rows) {
        for tool in row["tools"].as_array().into_iter().flatten().filter_map(Value::as_str) {
            blocked.insert(tool.to_owned());
            if row["silent_when_disabled"] != true {
                notice.insert(tool.to_owned());
            }
        }
    }
    Ok((blocked, notice.into_iter().collect()))
}

impl Runtime {
    pub fn tool_settings(&self, ws: &RegisteredWorkspace, id: Option<&str>, patch: Option<&Value>) -> Result<Value,RuntimeError> {
        let mut store = self.store()?;
        verify_workspace_binding(&store, &ws.id, Some(&ws.path))?;
        let id = id.filter(|s| !s.is_empty() && *s != "new");
        let (mut rows,blocked) = runtime_categories(&store.data_dir).map_err(RuntimeError::storage)?;
        let allowed_mcp = crate::mcp::main_categories(&store.data_dir,&ws.id);
        rows.retain(|row| row["id"].as_str().is_none_or(|id|!id.starts_with("mcp_server__") || allowed_mcp.contains(id)));
        let mut overrides = if let Some(id) = id {
            store.header(&ws.id,id)?.0["metadata"]["runtime_tool_category_overrides"].clone()
        } else { store.draft_categories.get(&ws.id).cloned().unwrap_or_default() };
        if let Some(body) = patch {
            if blocked { return Err(RuntimeError::new(403,"工具开关已由管理员禁用")); }
            let category = body["category"].as_str().filter(|s| !s.is_empty()).ok_or_else(||RuntimeError::bad_request("缺少 category 字段"))?;
            let flag = body["enabled"].as_bool().ok_or_else(||RuntimeError::bad_request("enabled 必须为布尔值"))?;
            let row = rows.iter().find(|r| r["id"]==category).ok_or_else(||RuntimeError::bad_request("未知工具类别"))?;
            if row["locked_state"].as_bool().is_some_and(|value| value!=flag) { return Err(RuntimeError::new(403,"该类别已由管理员指定状态")); }
            if !overrides.is_object() { overrides=json!({}); }
            overrides[category]=json!(flag);
            if let Some(id) = id {
                let path = store.conversation_path(&ws.id,id)?;
                Database::open(path.parent().unwrap()).map_err(RuntimeError::storage)?
                    .set_field(id,"metadata","runtime_tool_category_overrides",&overrides).map_err(RuntimeError::storage)?;
            } else { store.draft_categories.insert(ws.id.clone(),overrides.clone()); }
        }
        apply_overrides(&mut rows,&overrides);
        Ok(json!({"success":true,"categories":rows}))
    }

    pub(super) fn disabled_task_tools(&self, task_id: &str) -> Result<HashSet<String>,String> {
        let store = self.store().map_err(|e|e.message)?;
        let task = store.tasks.get(task_id).ok_or("Task not found")?;
        let path = store.conversation_path(&task.workspace_id,&task.conversation_id).map_err(|e|e.message)?;
        let overrides = Database::open(path.parent().unwrap())?.field(&task.conversation_id,"metadata","runtime_tool_category_overrides")?.unwrap_or_default();
        let rows = effective_rows(&store.data_dir, &overrides)?;
        Ok(non_mcp_disabled(&rows).flat_map(|row| row["tools"].as_array().into_iter().flatten())
            .filter_map(|tool| tool.as_str().map(str::to_owned)).collect())
    }
}
