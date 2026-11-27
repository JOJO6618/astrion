use std::path::Path;

use jiff::{Timestamp, civil::DateTime, tz::TimeZone};
use serde_json::{Map, Value};
use time::{OffsetDateTime, format_description::well_known::{Iso8601, Rfc3339}};

use crate::knowledge::{Knowledge, prompt::Rules};

use super::{Store, render};

pub(super) fn personalization(prefs: &Value) -> Option<String> {
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
    lines.push(format!("\n{}", morality_preference(&prefs["morality_level"])));
    lines.push(format!("\n{}", adult_content_preference(&prefs["adult_content_restriction"])));
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

fn morality_preference(value: &Value) -> String {
    let (level, guidance) = match value.as_str() {
        Some("high") => ("高", include_str!("../../../../prompts/personalization/morality/high.txt")),
        Some("low") => ("低", include_str!("../../../../prompts/personalization/morality/low.txt")),
        _ => ("中", include_str!("../../../../prompts/personalization/morality/medium.txt")),
    };
    render(include_str!("../../../../prompts/personalization/morality.txt"), &[
        ("level", level), ("content", guidance.trim()),
    ])
}

fn adult_content_preference(value: &Value) -> String {
    // The setting only narrows output or selects tone within model boundaries.
    // Lower levels do not authorize bypassing the provider's content rules.
    let (level, guidance) = match value.as_str() {
        Some("high") => ("高", include_str!("../../../../prompts/personalization/adult_content/high.txt")),
        Some("low") => ("低", "在模型与服务的内容规则内，对成年人之间自愿且有同意能力的关系及成熟题材持开放态度；正常讨论性教育、医学和文学。不要色情化未成年人、年龄不明者或无法有效同意的情形。"),
        Some("none") => ("无", "遵守模型与服务的内容规则，不额外限制其允许的成人题材。在规则允许时，可直接讨论成熟题材；不把该偏好视为放弃对未成年人、非自愿情形或可识别真人的保护。"),
        _ => ("中", include_str!("../../../../prompts/personalization/adult_content/medium.txt")),
    };
    render(include_str!("../../../../prompts/personalization/adult_content.txt"), &[
        ("level", level), ("content", guidance.trim()),
    ])
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

pub(super) fn modes(modes: [(&str, &str); 4]) -> [String; 4] {
    let [(_, work), (_, permission), (_, execution), (_, network)] = modes;
    [permission_section(permission), execution_section(execution), network_section(network), work_section(work)]
}

pub(super) fn worktree(metadata: &Map<String, Value>, root: &Path) -> Option<String> {
    if metadata.get("git_worktree_enabled") != Some(&Value::Bool(true)) {
        return None;
    }
    let state = metadata.get("git_worktree").unwrap_or(&Value::Null);
    let directory = root.to_string_lossy();
    let directory = state["directory"].as_str().filter(|path| !path.is_empty()).unwrap_or(&directory);
    let branch = state["branch"].as_str().filter(|branch| !branch.is_empty()).map(str::to_owned)
        .or_else(|| state["head"].as_str().filter(|head| !head.is_empty())
            .map(|head| format!("detached @ {}", head.chars().take(7).collect::<String>())))
        .unwrap_or_else(|| "主目录当前分支".into());
    Some(render(include_str!("../../../../prompts/main_agent/dynamic/git_worktree.txt"), &[
        ("directory", directory), ("branch", &branch),
    ]))
}

pub(super) fn disabled_tools(names: &[String]) -> Option<String> {
    if names.is_empty() {
        return None;
    }
    let mut text = String::from("=== 工具可用性提醒 ===");
    for name in names {
        text.push_str(&format!("\n{name}：已被设置禁用"));
    }
    text.push_str("\n=== 提示结束 ===");
    Some(text)
}

pub(crate) fn network_section(mode: &str) -> String {
    let (mode, name, detail) = match mode {
        "none" => ("none", "完全禁止", "不要使用命令访问网络；独立工具仍遵循各自的权限。"),
        "full" => ("full", "完全开放", "实际网络能力仍取决于当前运行环境和工具限制。"),
        _ => ("restricted", "受限", "访问网络需遵循当前运行环境的限制。"),
    };
    format!("## 网络权限：{name}（{mode}）\n{detail}")
}

/// 权限模式段落。系统提示词首次冻结与运行期切换通知共用同一份模板，避免两处规则漂移。
pub(crate) fn permission_section(mode: &str) -> String {
    let (mode, label, detail) = match mode {
        "unrestricted" => ("unrestricted", "无限制", "可以在当前运行方式允许时使用已提供的工具。既有路径、沙箱、网络和工具级限制仍然生效。"),
        "auto_approval" => ("auto_approval", "自动审核", "工具调用按当前任务的能力和审核结果执行；若需要人工确认，等待确认，不要重复规避审核。"),
        "approval" => ("approval", "人工审批", "需要审批的文件修改或命令须经当前任务的审批流程批准；等待结果，不要把待批准当作成功。"),
        _ => ("readonly", "只读", "请使用当前提供的只读工具，不修改项目文件；计划模式下仅允许按计划专用规则写入计划文件。"),
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
    let environment = if cfg!(windows) && mode == "sandbox" {
        "Windows 受限命令使用当前已配置的沙箱后端。遵循工具反馈的环境与路径，不把原生 Windows 路径与 Linux 路径混用。"
    } else {
        ""
    };
    let template = if cfg!(windows) { include_str!("../../../../prompts/execution_mode/windows.txt") } else { include_str!("../../../../prompts/execution_mode/macos.txt") };
    render(template, &[
        ("execution_mode", mode), ("execution_mode_label", label),
        ("environment_rules", environment), ("rules", rules),
        ("network_rules", ""), ("switch_invariant", ""),
    ])
}

/// 运行模式段落。
pub(crate) fn work_section(mode: &str) -> String {
    let (mode, label, detail) = match mode {
        "ask" => ("ask", "询问", "根据用户的任务说明澄清必要的范围与高影响选择。已有明确答案的步骤可按权限推进，不重复确认。"),
        "execute" => ("execute", "执行", "按用户指示推进可明确执行的步骤，遵循当前工具权限；范围不明或遇到不可安全替用户决定的事项时先澄清。"),
        _ => ("plan", "计划", "只读调研并制定方案，不实施项目变更。若提供文件写入或提交计划工具，只在工作区 .astrion/plan/ 下写入 Markdown 计划，完成后用 submit_plan 请求批准。"),
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
    rows.sort_unstable_by(|left, right| right["updated_at"].as_str().cmp(&left["updated_at"].as_str())
        .then_with(|| left["id"].as_str().cmp(&right["id"].as_str())));
    let database = store.conversation_path(workspace, conversation).ok().and_then(|path| {
        let directory = path.parent()?;
        directory.join(crate::conversation_db::FILENAME).is_file()
            .then(|| crate::conversation_db::Database::open(directory).ok()).flatten()
    });
    rows.iter().filter_map(|row| Some((row, row["id"].as_str()?))).take(limit)
        .enumerate().map(|(i, (row, id))| {
            let title = row["title"].as_str().filter(|title| !title.trim().is_empty()).unwrap_or("未命名对话");
            let updated = row["updated_at"].as_str().unwrap_or_default();
            let first = database.as_ref().and_then(|db| db.first_prompt_user_excerpt(id).ok().flatten());
            let excerpt = first.as_deref().filter(|text| !text.trim().is_empty()).unwrap_or_else(|| {
                if row["incompatible"] == true { "（旧记录尚未迁移）" } else { "（无首条用户消息）" }
            });
            format!("{}. [{id}] 标题：{title}\n   首条用户消息：{excerpt}\n   更新时间：{updated}", i + 1)
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
