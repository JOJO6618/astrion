//! The complete initial Agent system is persisted with the first admitted turn.

mod sections;

/// 运行期模式切换通知复用同一份段落渲染，避免与首次冻结的系统提示词规则漂移。
pub(super) use sections::{execution_section, permission_section, work_section};

use std::path::Path;

use serde_json::{Map, Value, json};

use crate::knowledge::{Knowledge, prompt::workspace_name};

use super::{ModelConfig, RuntimeError, store::Store};

const KEY: &str = "frozen_system_prompt";
const LEGACY_KEYS: [&str; 18] = [
    "frozen_main_system_prompt", "frozen_permission_prompt", "frozen_execution_prompt",
    "frozen_work_mode_prompt", "frozen_recent_conversations_prompt", "frozen_personalization_prompt",
    "frozen_workspace_prompt", "frozen_agents_md_prompt", "frozen_claude_md_prompt",
    "frozen_skills_prompt", "frozen_tool_loading_prompt", "frozen_preview_panel_prompt",
    "frozen_native_browser_prompt", "frozen_inline_ui_prompt", "frozen_memory_prompt",
    "frozen_workspace_memory_prompt", "frozen_custom_system_prompt", "frozen_disabled_tools_prompt",
];
const LEGACY_COLLABORATION: &str = concat!(
    "子智能体按name命名并保留上下文，所有任务后台执行，结束一轮后最后回复会以user消息通知。",
    "完成后默认保留idle；本轮完成、暂时无任务或准备最终回复都不是终结理由，不要例行清理或预防性释放名额。",
    "仅在用户明确要求、经核实实例发生无法恢复且无法继续使用的错误，或严重违反明确指令且纠正后仍继续偏离时，才可终结；",
    "实际达到实例上限、已有实例无法复用且确需新建时，也可终结不再需要的空闲实例。",
    "发现正在或即将执行未经授权的破坏性操作时，立即stop_sub_agent，不必等待纠正；无法可靠继续协作时再terminate_sub_agent。",
    "普通报错、可恢复故障、理解偏差或意见分歧不构成终结理由。停止和终结不能撤销已发生的操作。",
    "后续优先复用已有实例，向原name发送消息，不重复创建同名实例。无需角色模板、finish工具或sleep等待。",
    "主子、子子提问可正常通信，及时处理问题。目录写入档位只约束内置工具，MCP沿用外置插件权限。",
);
const STATIC: &str = include_str!("../../../prompts/main_agent/static.txt");

macro_rules! dynamic {
    ($name:literal) => { include_str!(concat!("../../../prompts/main_agent/dynamic/", $name, ".txt")) };
}

/// Call while holding the same store lock as the turn commit. Used only on admission.
pub(super) fn freeze(
    store: &Store, workspace_id: &str, document: &mut Value, model: &ModelConfig, writer: bool,
) -> Result<(), RuntimeError> {
    let created_at = document["created_at"].as_str().unwrap_or_default().to_owned();
    let conversation_id = document["id"].as_str().unwrap_or_default().to_owned();
    let metadata = document["metadata"].as_object_mut()
        .ok_or_else(|| RuntimeError::bad_request("Invalid conversation metadata"))?;
    let prefix = model.subscription.as_ref().map_or("", |request| request.instructions.as_str());
    freeze_metadata(metadata, prefix, |metadata| {
        let root = &store.workspaces[workspace_id];
        let knowledge = Knowledge::open(root, &store.data_dir).map_err(RuntimeError::storage)?;
        let preferences = metadata.get("custom_personalization_config")
            .filter(|prefs| prefs.as_object().is_some_and(|fields| !fields.is_empty()))
            .cloned().unwrap_or_else(|| knowledge.prompt_preferences());
        let recent = sections::recent_conversations(store, workspace_id, &conversation_id, &preferences);
        let input = Inputs { root, created_at: &created_at, knowledge: &knowledge, preferences: &preferences, model, writer, recent, browser: store.native_browser.is_some() && writer };
        build(metadata, input)
    })
}

fn freeze_metadata(
    metadata: &mut Map<String, Value>, prefix: &str,
    build: impl FnOnce(&mut Map<String, Value>) -> Result<String, RuntimeError>,
) -> Result<(), RuntimeError> {
    match metadata.get(KEY) {
        Some(Value::String(text)) if !text.trim().is_empty() => return Ok(()),
        None | Some(Value::Null) => {}
        Some(_) => return Err(RuntimeError::new(422, "Invalid frozen system prompt")),
    }
    let body = match legacy_body(metadata) {
        Some(text) => text,
        None => build(metadata)?,
    };
    let text = if prefix.is_empty() { body } else { format!("{prefix}\n\n{body}") };
    if text.trim().is_empty() {
        return Err(RuntimeError::storage("empty Agent system prompt"));
    }
    metadata.insert(KEY.into(), Value::String(text));
    for key in LEGACY_KEYS {
        metadata.shift_remove(key);
    }
    Ok(())
}

fn legacy_body(metadata: &Map<String, Value>) -> Option<String> {
    let saved = |key| metadata.get(key).and_then(Value::as_str).filter(|part| !part.is_empty());
    if !LEGACY_KEYS.into_iter().any(|key| saved(key).is_some()) {
        return None;
    }
    let mut parts = Vec::new();
    for key in LEGACY_KEYS {
        if key == "frozen_disabled_tools_prompt" {
            parts.push(LEGACY_COLLABORATION);
        }
        parts.extend(saved(key));
    }
    Some(parts.join("\n\n"))
}

/// Stage invalidation inside a successful compression document update.
/// The caller must commit it atomically with the changed history.
#[allow(dead_code, reason = "The Rust runtime has no compression commit yet")]
pub(super) fn invalidate_after_compression(metadata: &mut Map<String, Value>) {
    for key in std::iter::once(KEY).chain(LEGACY_KEYS) {
        metadata.insert(key.into(), Value::Null);
    }
}

struct Inputs<'a> {
    root: &'a Path,
    created_at: &'a str,
    knowledge: &'a Knowledge,
    preferences: &'a Value,
    model: &'a ModelConfig,
    writer: bool,
    browser: bool,
    recent: String,
}

fn build(metadata: &mut Map<String, Value>, input: Inputs<'_>) -> Result<String, RuntimeError> {
    let Inputs { root, created_at, knowledge, preferences, model, writer, recent, browser } = input;
    let mut parts = vec![STATIC.trim().to_owned()];
    parts.extend(sections::personalization(preferences));
    for (name, enabled) in [("AGENTS.md", "agents_md_auto_inject"), ("CLAUDE.md", "claude_md_auto_inject")] {
        if preferences[enabled] == true {
            parts.extend(knowledge.prompt_rules(name).map(|rules| sections::project_rules(name, rules)));
        }
    }
    let skills = knowledge.skill_list_lines().unwrap_or_default();
    let skills = if skills.is_empty() { "暂无可用的skills".into() } else { skills.join("\n") };
    parts.push(render(dynamic!("skills"), &[("skills_list", &skills)]));
    if let Some(state) = metadata.get("tool_loading") {
        let catalog = super::tool_loading::catalog(state);
        if !catalog.is_empty() {
            parts.push(render(dynamic!("tool_loading"), &[("tool_catalog", &catalog)]));
        }
    }
    parts.push(sections::memory(knowledge, preferences));
    if metadata.get("workspace_memory_enabled") == Some(&Value::Bool(true)) {
        let memory = knowledge.prompt_workspace_memory().map_err(RuntimeError::storage)?;
        let text = render(dynamic!("workspace_memory"), &[
            ("workspace_memory_path", memory.record["path"].as_str().unwrap_or_default()),
            ("workspace_memory_maintenance", "这是当前工作区的私有记忆。请遵循实际提供的记忆和文件工具权限。"),
            ("workspace_memory_content", if memory.text.is_empty() { "（当前记忆为空。）" } else { &memory.text }),
        ]);
        parts.push(format!("<!-- astrion-workspace-memory:start -->\n{text}\n<!-- astrion-workspace-memory:end -->"));
        metadata.insert("workspace_memory_write_version".into(), memory.record["version"].clone());
        metadata.insert("workspace_memory_snapshot".into(), memory.record);
    }
    if let Some(custom) = metadata.get("custom_system_prompt").and_then(Value::as_str).filter(|text| !text.trim().is_empty()) {
        parts.push(render(dynamic!("custom_system"), &[("custom_system_prompt", custom)]));
    }
    parts.push(render(dynamic!("model"), &[("model_description", &model.prompt_description)]));
    parts.push(match model.prompt_multimodal {
        "image,video" | "video" => dynamic!("vision_video"),
        "image" => dynamic!("vision_image"),
        _ => dynamic!("vision_text"),
    }.trim().into());
    parts.extend(sections::modes(metadata, preferences, writer));
    let browser_state = if !browser { "不可用" }
        else if metadata.get("browser_tools_enabled") == Some(&Value::Bool(true)) { "开启" } else { "关闭" };
    parts.push(render(dynamic!("features"), &[("preview_state", "可用"), ("browser_state", browser_state)]));
    if !recent.is_empty() {
        parts.push(render(dynamic!("recent_conversations"), &[("recent_conversations", &recent)]));
    }
    let started = sections::start_time(created_at);
    let tree = format!("（以下为工作区根目录的部分文件和文件夹）\n{}", knowledge.prompt_tree());
    let limits = format!("工作区：{}。可用能力由当前对话的工具及审批配置确定。", root.display());
    let environment = format!("{} / {}，Astrion Host", std::env::consts::OS, std::env::consts::ARCH);
    parts.push(render(include_str!("../../../prompts/workspace_system.txt"), &[
        ("project_name", workspace_name(root)), ("runtime_environment", &environment),
        ("resource_limit", &limits), ("conversation_start_time", &started),
        ("file_tree", &tree), ("recent_uploads", ""),
    ]));
    Ok(parts.join("\n\n"))
}

/// Expand trusted template slots once so injected values cannot introduce new slots.
fn render(template: &str, values: &[(&str, &str)]) -> String {
    let mut result = String::new();
    let mut rest = template;
    while let Some((before, opened)) = rest.split_once('{') {
        result.push_str(before);
        let Some((key, after)) = opened.split_once('}') else {
            result.push('{');
            rest = opened;
            break;
        };
        if let Some((_, value)) = values.iter().find(|(name, _)| name == &key) {
            result.push_str(value);
        } else {
            result.push('{');
            result.push_str(key);
            result.push('}');
        }
        rest = after;
    }
    result.push_str(rest);
    result.trim().to_owned()
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/system_prompt_contract.rs"]
mod contract;
