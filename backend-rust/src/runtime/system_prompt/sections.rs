use std::time::SystemTime;

use jiff::{Timestamp, civil::DateTime, tz::TimeZone};
use serde_json::{Map, Value};
use time::{OffsetDateTime, format_description::well_known::{Iso8601, Rfc3339}};

use crate::knowledge::{Knowledge, prompt::Rules};

use super::{Store, render};

pub(crate) fn personalization(prefs: &Value) -> Option<String> {
    if prefs["enabled"] != true {
        return None;
    }
    let mut lines = Vec::new();
    for (key, prefix, suffix) in [
        ("self_identify", "用户希望你自称：", ""),
        ("user_name", "用户希望你称呼为：", ""),
        ("profession", "用户的职业是：", ""),
        ("tone", "用户希望你使用 ", " 的语气与TA交流"),
        ("considerations", "用户希望你在回答问题时必须考虑的信息是：\n", ""),
    ] {
        if let Some(value) = prefs[key].as_str().filter(|value| !value.is_empty()) {
            lines.push(format!("{prefix}{value}{suffix}"));
        }
    }
    lines.push(match prefs["conversation_continuity"].as_str() {
        Some("high") => "\n【对话连续性：高】\n当前对话应积极延续用户长期背景。回答时优先参考用户过往对话、长期记忆和项目背景；遇到可能相关的问题，应主动搜索/回顾历史对话；发现值得长期保留的偏好、项目事实、稳定经验或用户明确要求记住的内容，应主动调用记忆工具记录。",
        Some("low") => "\n【对话连续性：低】\n当前对话应尽量独立。不要主动回顾之前的对话，不要主动调用记忆或历史对话工具，不要主动参考用户历史记忆来回答；只有用户明确要求、当前任务必须依赖历史，或安全/连续性需要时，才可搜索历史或读取记忆。",
        _ => "\n【对话连续性：中】\n当前对话优先，但可以在适当时候参考历史。若用户问题明显与过往偏好、项目背景或未完成事项有关，可以调用记忆或历史对话工具；不要为了普通问题主动翻历史。",
    }.into());
    let style = match prefs["communication_style"].as_str() {
        Some("auto") => Some(("自动", include_str!("../../../../prompts/auto_style.txt"))),
        Some("human_like") => Some(("交流风格要求", include_str!("../../../../prompts/human_like_style.txt"))),
        _ => None,
    };
    if let Some((name, style)) = style {
        lines.push(format!("\n【{name}】\n{}", style.trim()));
    }
    Some(render(include_str!("../../../../prompts/main_agent/dynamic/personalization.txt"), &[("personalization_block", &lines.join("\n"))]))
}

pub(super) fn project_rules(filename: &str, rules: Rules) -> String {
    let updated = rules.modified.and_then(|time| Timestamp::try_from(time).ok())
        .map(|time| format!("（最后修改：{}）", time.to_zoned(TimeZone::system()).strftime("%Y-%m-%d %H:%M")))
        .unwrap_or_default();
    let mut children = String::new();
    if !rules.children.is_empty() {
        children = format!("另外，工作区以下子目录也存在 {filename} 规范文件：\n\n");
        for path in rules.children.iter().take(20) {
            children.push_str(&format!("- `{path}`\n"));
        }
        if rules.children.len() > 20 {
            children.push_str(&format!("\n...共 {} 个\n", rules.children.len()));
        }
        children.push_str(&format!("\n这些子目录规范仅适用于其所在目录范围。若你的操作涉及上述目录，请先自行读取对应 {filename} 了解局部规范后再动手，不得将子目录规范当作全局规范套用。"));
    }
    render(include_str!("../../../../prompts/main_agent/dynamic/project_rules.txt"), &[
        ("filename", filename), ("updated_at", &updated), ("content", rules.text.trim()), ("sub_notice", &children),
    ])
}

pub(super) fn memory(knowledge: &Knowledge, prefs: &Value) -> String {
    let global = knowledge.prompt_global_memory();
    let topics = knowledge.prompt_project_memory();
    let limit = if prefs.get("project_memory_inject_limit").is_some_and(Value::is_null) {
        topics.len()
    } else {
        prefs["project_memory_inject_limit"].as_i64().filter(|limit| *limit >= 5).unwrap_or(20).min(100_000) as usize
    };
    let count = topics.len();
    let limit = limit.min(count);
    let mut template = include_str!("../../../../prompts/main_agent/dynamic/memory.txt").to_owned();
    optional(&mut template, "global_memory_empty", global.trim().is_empty());
    optional(&mut template, "project_memory_empty", count == 0);
    optional(&mut template, "project_memory_truncated", count > limit);
    render(&template, &[
        ("global_memory", global.trim()), ("project_memory_list", &topics[..limit].join("\n")),
        ("project_memory_total", &count.to_string()), ("project_memory_limit", &limit.to_string()),
        ("project_memory_remaining", &(count - limit).to_string()),
    ])
}

fn optional(template: &mut String, key: &str, enabled: bool) {
    let open = format!("[{key}]");
    let close = format!("[/{key}]");
    while let Some(start) = template.find(&open) {
        let body = start + open.len();
        let end = body + template[body..].find(&close).expect("paired memory template markers");
        if enabled {
            template.replace_range(end..end + close.len(), "");
            template.replace_range(start..body, "");
        } else {
            template.replace_range(start..end + close.len(), "");
        }
    }
}

pub(super) fn modes(metadata: &Map<String, Value>, prefs: &Value, writer: bool) -> [String; 3] {
    let mode = |key: &str, default: &str| {
        metadata.get(key).and_then(Value::as_str).or_else(|| prefs[default].as_str())
    };
    let permission = match (writer, mode("permission_mode", "default_permission_mode")) {
        (true, Some("unrestricted")) => "unrestricted",
        (true, Some("auto_approval")) => "auto_approval",
        (true, None | Some("approval")) => "approval",
        _ => "readonly",
    };
    let permission = permission_section(permission);
    let execution = execution_section("sandbox");
    let work = match mode("work_mode", "default_work_mode") {
        Some("ask") => "ask",
        Some("execute") => "execute",
        _ => "plan",
    };
    let work = work_section(work);
    [permission, execution, work]
}

/// 权限模式段落。系统提示词首次冻结与运行期切换通知共用同一份模板，避免两处规则漂移。
pub(crate) fn permission_section(mode: &str) -> String {
    let (mode, label, detail) = match mode {
        "unrestricted" => ("unrestricted", "完全权限", "可以在当前运行方式允许时使用已提供的工作区文件修改工具。"),
        "auto_approval" => ("auto_approval", "自动审核", "文件工具按当前任务的能力及自动审核结果执行。"),
        "approval" => ("approval", "人工审批", "文件修改须由当前任务的审批流程批准。"),
        _ => ("readonly", "只读", "请使用当前提供的只读工具，文件修改受当前任务能力限制。"),
    };
    render(include_str!("../../../../prompts/permission_mode.txt"), &[
        ("permission_mode", mode), ("permission_mode_label", label), ("mode_description", ""), ("detailed_rules", detail),
    ])
}

/// 执行环境段落。sandbox 分支与系统提示词一致；direct 供切换通知使用。
pub(crate) fn execution_section(mode: &str) -> String {
    let (mode, label, rules) = match mode {
        "direct" => ("direct", "完全访问", "直接在宿主机执行，拥有用户的完整权限。保持最小化命令范围，操作尽量限制在工作区内；涉及删除、覆盖或系统级变更前先说明风险。"),
        _ => ("sandbox", "受限执行", "仅在当前任务提供的能力范围内操作。"),
    };
    let template = if cfg!(windows) { include_str!("../../../../prompts/execution_mode/windows.txt") } else { include_str!("../../../../prompts/execution_mode/macos.txt") };
    render(template, &[
        ("execution_mode", mode), ("execution_mode_label", label),
        ("environment_rules", ""), ("rules", rules),
        ("network_rules", ""), ("switch_invariant", ""),
    ])
}

/// 运行模式段落。
pub(crate) fn work_section(mode: &str) -> String {
    let (mode, label, detail) = match mode {
        "ask" => ("ask", "询问", "根据当前用户的任务说明澄清必要的范围。"),
        "execute" => ("execute", "执行", "按当前用户指示推进任务并遵循当前工具权限。"),
        _ => ("plan", "计划", "需要确认的变更，先与用户确认方案。"),
    };
    render(include_str!("../../../../prompts/work_mode.txt"), &[
        ("work_mode", mode), ("work_mode_label", label), ("mode_description", ""), ("detailed_rules", detail),
    ])
}

pub(super) fn recent_conversations(store: &Store, workspace: &str, conversation: &str, prefs: &Value) -> String {
    if prefs["recent_conversations_prompt_enabled"] != true {
        return String::new();
    }
    let limit = prefs["recent_conversations_prompt_limit"].as_i64().unwrap_or(10).clamp(1, 30) as usize;
    let mut rows = store.session_rows(workspace).unwrap_or_default();
    rows.retain(|row| row["id"] != conversation);
    rows.sort_unstable_by(|left, right| right["updated_at"].as_str().cmp(&left["updated_at"].as_str()));
    rows.iter().take(limit).filter_map(|row| {
        Some(format!("- {}（{}）", row["title"].as_str()?, row["id"].as_str()?))
    }).collect::<Vec<_>>().join("\n")
}

pub(super) fn start_time(value: &str) -> String {
    let value = value.trim();
    let zone = TimeZone::system();
    let instant = OffsetDateTime::parse(value, &Rfc3339)
        .or_else(|_| OffsetDateTime::parse(value, &Iso8601::DEFAULT))
        .ok()
        .and_then(|time| Timestamp::from_second(time.unix_timestamp()).ok());
    let local = match instant {
        Some(time) => Some(time.to_zoned(zone)),
        None => value.parse::<DateTime>().ok().and_then(|time| time.to_zoned(zone).ok()),
    };
    local.map(|time| time.strftime("%Y/%m/%d %H:%M:%S（时区：UTC%:z）").to_string())
        .unwrap_or_else(|| "未记录（时区未知）".into())
}
