use serde_json::{Value, json};

use super::GoalError;

pub const INTERACTION_DENIED: &str = "目标模式不允许等待人工交互或申请完全访问权限。请在当前权限内继续；无法推进时说明具体阻塞原因。";
const GOAL_PROMPT: &str = concat!(
    "【目标模式已开启】请持续推进下述目标，工作模式为执行。",
    "禁止 ask_user、submit_plan 或任何需要人工回答、批准、登录的交互。",
    "在当前权限内自主决定常规细节，自动审核拒绝后应调整做法，不得转人工确认。",
    "以用户目标为范围，不擅自扩大任务。无法推进时明确说明阻塞原因；",
    "完成时给出成果和必要验证证据，由独立审核方判断是否达成。\n",
);
const FULL_ACCESS_BLOCKED: &str = "禁止申请完全访问权限：当前申请仍需要人工批准。沙箱内无法完成时说明具体限制。\n";
const FULL_ACCESS_ALLOWED: &str = concat!(
    "单次完全访问申请由审核智能体自动裁决，不需要人工批准：",
    "目标确需脱离沙箱时，可用 run_command 的 request_full_access=true 与 full_access_reason 发起申请，",
    "说明具体命令、原因和访问范围；审核拒绝后调整做法，不得反复申请或绕过决定。\n",
);
pub(super) const CONTINUE_PREFIX: &str = "审核智能体对于你的工作结束给出了以下内容：";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Action {
    Start,
    Continue,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Limits {
    pub max_turns: Option<u64>,
    pub max_tokens: Option<u64>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct GoalRequest {
    pub action: Option<Action>,
    pub limits: Option<Limits>,
}

impl GoalRequest {
    pub fn parse(payload: &Value) -> Result<Self, GoalError> {
        let action = match &payload["goal_action"] {
            Value::Null => None,
            Value::String(action) if action == "start" => Some(Action::Start),
            Value::String(action) if action == "continue" => Some(Action::Continue),
            _ => return Err(GoalError::bad_request("Invalid goal action")),
        };
        if action.is_none() && truthy(&payload["goal_mode"]) {
            return Err(GoalError::bad_request("请通过目标弹窗开始目标模式"));
        }
        let options = &payload["goal_options"];
        let limits = if options.is_null() {
            None
        } else {
            if !options.is_object() {
                return Err(GoalError::bad_request("目标限制必须为对象"));
            }
            Some(Limits {
                max_turns: limit(options, "max_turns", 10_000)?,
                max_tokens: limit(options, "max_tokens", 1_000_000_000)?,
            })
        };
        Ok(Self { action, limits })
    }
}

fn limit(options: &Value, name: &str, maximum: u64) -> Result<Option<u64>, GoalError> {
    if options[name].is_null() {
        return Ok(None);
    }
    let value = options[name].as_u64().filter(|value| (1..=maximum).contains(value))
        .ok_or_else(|| GoalError::bad_request(format!("{name} 必须为空或 1 到 {maximum} 的整数")))?;
    Ok(Some(value))
}

pub(super) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null | Value::Bool(false) => false,
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(fields) => !fields.is_empty(),
        Value::Bool(true) => true,
    }
}

pub fn enforce_modes(metadata: &mut Value) {
    metadata["work_mode"] = json!("execute");
    if !matches!(metadata["permission_mode"].as_str(), Some("auto_approval" | "unrestricted")) {
        metadata["permission_mode"] = json!("auto_approval");
    }
    if metadata["pending_permission_mode"].as_str().is_some_and(|mode| !matches!(mode, "" | "auto_approval" | "unrestricted")) {
        metadata["pending_permission_mode"] = Value::Null;
    }
}

pub(super) fn rejects_interaction(name: &str, args: &Value, automatic_full_access: bool) -> bool {
    matches!(name, "ask_user" | "submit_plan") || (truthy(&args["request_full_access"]) && !automatic_full_access)
}

pub(super) fn goal_prompt(goal: &str, automatic_full_access: bool) -> Value {
    let access = if automatic_full_access { FULL_ACCESS_ALLOWED } else { FULL_ACCESS_BLOCKED };
    user_message(format!("{GOAL_PROMPT}{access}{goal}"), "goal_prompt", false)
}

pub(super) fn user_message(content: String, source: &str, starts_work: bool) -> Value {
    json!({"role":"user", "content":content, "metadata": {
        "source":source,"message_source":source,"visibility":"compact","starts_work":starts_work,
    }})
}
