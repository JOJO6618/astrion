//! This preference only requires reading a guide. It never grants tool access.

use std::{fs, io::ErrorKind, path::Path};

use serde_json::Value;

use crate::{conversation_db::Database, knowledge::SkillSettings, tools::read_file};
use super::{Runtime, writable_task};

const TERMINAL: (&str, &str, &str) = ("terminal-guide", "skill_read_terminal_guide", ".astrion/skills/terminal-guide/SKILL.md");
const SUB_AGENT: (&str, &str, &str) = ("sub-agent-guide", "skill_read_sub_agent_guide", ".astrion/skills/sub-agent-guide/SKILL.md");
const COMMAND: (&str, &str, &str) = ("run-command-guide", "skill_read_run_command_guide", ".astrion/skills/run-command-guide/SKILL.md");
pub(super) const SUB_AGENT_SETTING: &str = "skill_strict_sub_agent_enabled";

pub(super) fn enforce(data: &Path, setting: &str, metadata: &Value) -> Result<(), String> {
    let (guide, checkpoint, path) = match setting {
        "skill_strict_terminal_enabled" => TERMINAL,
        "skill_strict_run_command_foreground_enabled" | "skill_strict_run_command_background_enabled" => COMMAND,
        SUB_AGENT_SETTING => SUB_AGENT,
        _ => return Err("未知的严格技能配置。".into()),
    };
    let preferences: Value = match fs::read(data.join("personalization.json")) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(|error| error.to_string())?,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error.to_string()),
    };
    if !preferences.is_object() { return Err("无效的个性化配置。".into()); }
    match preferences.get(setting) {
        None | Some(Value::Bool(false)) => Ok(()),
        Some(Value::Bool(true)) => {
            if metadata[checkpoint] == true || !SkillSettings::enabled_in_preferences(&preferences, guide) {
                return Ok(());
            }
            Err(format!("请先用 read_skill 或 read_file 完整阅读 {path}，然后重试。"))
        }
        _ => Err("无效的严格技能配置。".into()),
    }
}

fn completed_read(result: &Value) -> Option<&'static str> {
    let total = result["total_lines"].as_u64()?;
    if result["success"] != true || result["truncated"] != false || result["type"] != "read"
        || total == 0 || result["line_start"] != 1 || result["line_end"] != total
    {
        return None;
    }
    let path = result["path"].as_str()?.replace('\\', "/");
    [TERMINAL, SUB_AGENT, COMMAND].into_iter()
        .find(|(_, _, expected)| path == *expected)
        .map(|(_, checkpoint, _)| checkpoint)
}

impl Runtime {
    pub(super) fn record_strict_guide_read(&self, task_id: &str, name: &str, result: &Value) -> Result<(), String> {
        if !matches!(name, read_file::NAME | crate::knowledge::READ_SKILL) {
            return Ok(());
        }
        let Some(checkpoint) = completed_read(result) else { return Ok(()); };
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        // The read_skill service stays bound to the base workspace under a
        // worktree. Ordinary read_file is rebound; a same-named worktree
        // file must not be taken as the installed workspace guide.
        if name == read_file::NAME && task.execution_root.is_some() { return Ok(()); }
        let (workspace, conversation) = (task.workspace_id.clone(), task.conversation_id.clone());
        let path = store.conversation_path(&workspace, &conversation).map_err(|error| error.message)?;
        let mut db = Database::open(path.parent().ok_or("对话数据库目录不可用。")?)?;
        if db.field(&conversation, "metadata", checkpoint)?.as_ref() == Some(&Value::Bool(true)) {
            return Ok(());
        }
        db.set_field(&conversation, "metadata", checkpoint, &Value::Bool(true))
    }
}

#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust严格技能/strict_guides_contract.rs"]
mod tests;
