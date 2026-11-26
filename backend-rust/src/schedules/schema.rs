use jiff::Timestamp;
use serde_json::{Map, Value, json};

use super::{Result, ScheduleError, clock::Rule};

const MAX_SAFE_INTEGER: i64 = 9_007_199_254_740_991;
const MAX_CONFIG_BYTES: usize = 256 * 1024;

pub(super) fn input(value: &Value) -> Result<Value> {
    if let Some(text) = value.as_str() {
        if text.len() > MAX_CONFIG_BYTES {
            return Err(ScheduleError::invalid("配置文件不能超过 256 KB"));
        }
        super::yaml::parse(text)
    } else {
        if serde_json::to_vec(value)
            .map_err(ScheduleError::storage)?
            .len()
            > MAX_CONFIG_BYTES
        {
            return Err(ScheduleError::invalid("配置文件不能超过 256 KB"));
        }
        Ok(value.clone())
    }
}

pub(super) fn object<'a>(
    value: &'a Value,
    keys: &[&str],
    label: &str,
) -> Result<&'a Map<String, Value>> {
    let fields = value
        .as_object()
        .ok_or_else(|| ScheduleError::invalid(format!("{label}：需要对象")))?;
    let mut extra: Vec<_> = fields
        .keys()
        .filter(|key| !keys.contains(&key.as_str()))
        .map(String::as_str)
        .collect();
    if !extra.is_empty() {
        extra.sort_unstable();
        return Err(ScheduleError::invalid(format!(
            "{label}：未知字段 {}",
            extra.join(", ")
        )));
    }
    Ok(fields)
}

pub(super) fn integer(value: &Value, min: i64, max: i64, label: &str) -> Result<i64> {
    value
        .as_i64()
        .filter(|actual| (min..=max).contains(actual))
        .ok_or_else(|| ScheduleError::invalid(format!("{label}：应为 {min}–{max} 的整数")))
}

pub(super) fn identifier<'a>(value: &'a Value, label: &str) -> Result<&'a str> {
    value
        .as_str()
        .filter(|id| {
            (1..=160).contains(&id.len())
                && id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
        })
        .ok_or_else(|| ScheduleError::invalid(format!("{label}：ID 格式无效")))
}

pub(super) fn boolean(value: &Value, label: &str) -> Result<bool> {
    value
        .as_bool()
        .ok_or_else(|| ScheduleError::invalid(format!("{label}：应为 true 或 false")))
}

fn choice<'a>(value: &'a Value, choices: &[&str], label: &str) -> Result<&'a str> {
    value
        .as_str()
        .filter(|text| choices.contains(text))
        .ok_or_else(|| ScheduleError::invalid(format!("{label}：无效选项")))
}

pub(super) fn normalize(
    value: &Value,
    default_workspace: &Value,
    now: Timestamp,
    previous: Option<&Value>,
) -> Result<(Value, Rule)> {
    let plan = object(
        value,
        &[
            "schema_version",
            "id",
            "revision",
            "name",
            "enabled",
            "schedule",
            "target",
            "message",
            "configuration",
            "goal",
            "policies",
            "notifications",
        ],
        "plan",
    )?;
    if value["schema_version"].as_i64() != Some(1) {
        return Err(ScheduleError::invalid("schema_version：只支持版本 1"));
    }
    let name = value["name"]
        .as_str()
        .map(str::trim)
        .filter(|name| (1..=80).contains(&name.chars().count()))
        .ok_or_else(|| ScheduleError::invalid("name：需要 1–80 字符的名称"))?;
    let message = value["message"]
        .as_str()
        .filter(|text| !text.trim().is_empty() && text.chars().count() <= 100000)
        .ok_or_else(|| ScheduleError::invalid("message：需要非空任务指令（最多 100000 字符）"))?;
    let enabled = boolean(
        plan.get("enabled").unwrap_or(&Value::Bool(false)),
        "enabled",
    )?;
    let empty = json!({});
    let target = plan.get("target").unwrap_or(&empty);
    let target_fields = object(target, &["workspace_id", "conversation"], "target")?;
    let workspace = target_fields
        .get("workspace_id")
        .filter(|v| !v.is_null() && v.as_str() != Some(""))
        .unwrap_or(default_workspace);
    let workspace_id = identifier(workspace, "target.workspace_id")?;
    let new_conversation = json!({"mode":"new"});
    let conversation = target_fields
        .get("conversation")
        .unwrap_or(&new_conversation);
    object(conversation, &["mode", "id"], "target.conversation")?;
    let mode = choice(
        &conversation["mode"],
        &["new", "existing"],
        "target.conversation.mode",
    )?;
    let existing = mode == "existing";
    let normalized_conversation = if existing {
        json!({"mode": mode, "id": identifier(&conversation["id"], "target.conversation.id")?})
    } else {
        if !conversation["id"].is_null() {
            return Err(ScheduleError::invalid("新对话计划不能指定已有对话 ID"));
        }
        new_conversation
    };
    let default_configuration = json!({"mode":"inherit"});
    let configuration = plan.get("configuration").unwrap_or(&default_configuration);
    validate_configuration(configuration)?;
    let default_goal = json!({"mode":"off"});
    let goal = plan.get("goal").unwrap_or(&default_goal);
    let goal_fields = object(goal, &["mode", "max_turns", "max_tokens"], "goal")?;
    let goal_mode = choice(&goal["mode"], &["off", "start", "continue"], "goal.mode")?;
    if goal_mode == "continue" && !existing {
        return Err(ScheduleError::invalid("继续目标需要指定已有对话"));
    }
    if goal_mode != "off" && configuration["work_mode"].as_str().unwrap_or("execute") != "execute" {
        return Err(ScheduleError::invalid("目标模式必须使用执行模式"));
    }
    for key in ["max_turns", "max_tokens"] {
        if let Some(limit) = goal_fields.get(key) {
            integer(limit, 1, MAX_SAFE_INTEGER, &format!("goal.{key}"))?;
        }
    }
    let policies = plan.get("policies").unwrap_or(&empty);
    let policy_fields = object(
        policies,
        &["busy", "missed", "approval_timeout_seconds"],
        "policies",
    )?;
    let default_skip = json!("skip");
    let busy = choice(
        policy_fields.get("busy").unwrap_or(&default_skip),
        &["skip", "merge"],
        "policies.busy",
    )?;
    let missed = choice(
        policy_fields.get("missed").unwrap_or(&default_skip),
        &["skip", "latest"],
        "policies.missed",
    )?;
    let timeout = integer(
        policy_fields
            .get("approval_timeout_seconds")
            .unwrap_or(&json!(3600)),
        1,
        86400,
        "policies.approval_timeout_seconds",
    )?;
    let notifications = plan.get("notifications").unwrap_or(&empty);
    let notification_fields = object(notifications, &["enabled"], "notifications")?;
    let notify = boolean(
        notification_fields
            .get("enabled")
            .unwrap_or(&Value::Bool(true)),
        "notifications.enabled",
    )?;
    let (rule, schedule) = Rule::read(
        &value["schedule"],
        now,
        previous.map(|previous| &previous["schedule"]),
    )?;
    let mut result = json!({"schema_version":1, "name":name, "message":message, "enabled":enabled, "schedule":schedule,
        "target":{"workspace_id":workspace_id,"conversation":normalized_conversation}, "configuration":configuration, "goal":goal,
        "policies":{"busy":busy,"missed":missed,"approval_timeout_seconds":timeout}, "notifications":{"enabled":notify}});
    if let Some(id) = plan.get("id") {
        result["id"] = json!(identifier(id, "id")?);
    }
    if let Some(revision) = plan.get("revision") {
        result["revision"] = json!(integer(revision, 1, MAX_SAFE_INTEGER, "revision")?);
    }
    Ok((result, rule))
}

fn validate_configuration(value: &Value) -> Result<()> {
    let options = object(
        value,
        &[
            "mode",
            "model_key",
            "thinking",
            "reasoning_effort",
            "work_mode",
            "permission_mode",
            "execution_mode",
            "network_enabled",
            "ask_user",
            "worktree",
        ],
        "configuration",
    )?;
    if choice(&value["mode"], &["inherit", "custom"], "configuration.mode")? == "inherit" {
        return if options.len() == 1 {
            Ok(())
        } else {
            Err(ScheduleError::invalid(
                "configuration：继承模式不能同时指定覆盖字段",
            ))
        };
    }
    if options
        .get("model_key")
        .is_some_and(|value| value.as_str().is_none_or(|key| key.trim().is_empty()))
    {
        return Err(ScheduleError::invalid(
            "configuration.model_key：模型不能为空",
        ));
    }
    for field in ["thinking", "network_enabled", "ask_user"] {
        if let Some(value) = options.get(field) {
            boolean(value, &format!("configuration.{field}"))?;
        }
    }
    for (field, choices) in [
        (
            "reasoning_effort",
            &(["low", "medium", "high", "xhigh", "max"])[..],
        ),
        ("work_mode", &["execute", "ask", "plan"]),
        (
            "permission_mode",
            &["readonly", "approval", "auto_approval", "unrestricted"],
        ),
        ("execution_mode", &["sandbox", "direct"]),
    ] {
        if let Some(value) = options.get(field) {
            if field == "reasoning_effort" && value.is_null() {
                continue;
            }
            choice(value, choices, &format!("configuration.{field}"))?;
        }
    }
    if let Some(tree) = options.get("worktree").filter(|value| *value != "main") {
        object(tree, &["branch_name"], "configuration.worktree")?;
        if tree["branch_name"]
            .as_str()
            .is_none_or(|name| name.trim().is_empty())
        {
            return Err(ScheduleError::invalid(
                "configuration.worktree.branch_name：需要已注册工作树分支",
            ));
        }
    }
    let permission = value["permission_mode"].as_str().unwrap_or("readonly");
    let direct = value["execution_mode"] == "direct";
    if value["work_mode"] == "plan" && (permission != "readonly" || direct) {
        return Err(ScheduleError::invalid("计划模式必须使用只读权限和沙箱"));
    }
    if value["permission_mode"] == "readonly" && direct {
        return Err(ScheduleError::invalid("只读权限必须使用沙箱"));
    }
    Ok(())
}
