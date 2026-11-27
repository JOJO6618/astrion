use super::{ModelConfig, Runtime, iso_time, now, system_prompt, writable_task};
use crate::{conversation_db::Database, web_assets::personalization::shallow_options};
use serde_json::{Value, json};
use std::collections::HashSet;

pub(crate) const PLACEHOLDER: &str = "过早的工具结果已经被自动压缩";
pub(crate) struct Settings {
    pub threshold: u64,
    pub interval: u64,
    pub batch: usize,
    pub recent_tools: usize,
    pub user_turns: usize,
}

pub(super) fn after_batch(
    runtime: &Runtime,
    id: &str,
    model: &ModelConfig,
    messages: &mut [Value],
) -> Result<(), String> {
    let mut store = runtime.store().map_err(|e| e.message)?;
    let Some(settings) = shallow_options(&store.data_dir).map_err(|e| e.to_string())? else {
        return Ok(());
    };
    let task = writable_task(&mut store, id)?;
    let (ws, conversation) = (task.workspace_id.clone(), task.conversation_id.clone());
    let path = store
        .conversation_path(&ws, &conversation)
        .map_err(|e| e.message)?;
    let mut db = Database::open(path.parent().unwrap())?;
    let mut prompt = None;
    let changed = db.shallow_compress(
        &conversation,
        &settings,
        |header| {
            let meta = &mut header["metadata"];
            meta["workspace_memory_enabled"] = json!(true);
            meta["workspace_memory_snapshot"] = Value::Null;
            meta["workspace_memory_write_version"] = Value::Null;
            meta["browser_snapshot_epoch"] = json!(
                meta["browser_snapshot_epoch"]
                    .as_u64()
                    .unwrap_or_default()
                    .saturating_add(1)
            );
            if meta["tool_loading"].is_object() {
                meta["tool_loading"]["pending"] = meta["tool_loading"]["deferred_set"].clone();
                meta["tool_loading"]["loaded"] = json!([]);
            }
            system_prompt::invalidate_after_compression(
                meta.as_object_mut().ok_or("Invalid metadata")?,
            );
            system_prompt::freeze(runtime, &store, &ws, header, model, system_prompt::ToolContext {
                writer: true, workflow: true, approval: store.tasks[id].approval.as_ref(),
            }).map_err(|e| e.message)?;
            prompt = header["metadata"]["frozen_system_prompt"]
                .as_str()
                .map(str::to_owned);
            Ok(())
        },
        &iso_time(now()).ok_or("Invalid time")?,
    )?;
    if changed.is_empty() {
        return Ok(());
    }
    let marked: HashSet<_> = changed.iter().map(String::as_str).collect();
    for msg in messages {
        if msg["_frozen_system_prompt"] == true
            && let Some(text) = &prompt
        {
            msg["content"] = json!(text);
        }
        if msg["role"] == "tool"
            && msg["tool_call_id"]
                .as_str()
                .is_some_and(|key| marked.contains(key))
        {
            msg["content"] = json!(PLACEHOLDER);
        }
    }
    writable_task(&mut store,id)?.emit("shallow_compression",json!({"conversation_id":conversation,"compressed_count":changed.len(),"keep_recent_tools":settings.recent_tools,"keep_user_turn_tools":settings.user_turns}));
    Ok(())
}
