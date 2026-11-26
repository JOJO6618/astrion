use serde_json::{Map, Value, json};
use uuid::Uuid;

use super::{Record, string};

pub(crate) const CREATE: &str = "create_sub_agent";
pub(crate) const SEND: &str = "send_message_to_sub_agent";
pub(crate) const STATUS: &str = "get_sub_agent_status";
pub(crate) const STOP: &str = "stop_sub_agent";
pub(crate) const TERMINATE: &str = "terminate_sub_agent";
pub(crate) const ANSWER: &str = "answer_sub_agent_question";
pub(super) const ASK_MASTER: &str = "ask_master";
pub(super) const ASK_OTHER: &str = "ask_other_agent";
pub(super) const ANSWER_OTHER: &str = "answer_other_agent";

pub(crate) fn main_schemas() -> Vec<Value> {
    vec![
        tool(CREATE, "创建具名子智能体，在后台开始工作。完成后保留为 idle，可发送消息复用。", &[
            ("name", Spec::String, true), ("summary", Spec::String, true), ("task", Spec::String, true),
            ("intelligence", Spec::Choice(&["same", "high", "medium", "low"]), true),
            ("access_level", Spec::Choice(&["working_dir_write", "workspace_write", "sandbox_write", "full_access"]), true),
            ("working_dir", Spec::String, false), ("timeout_seconds", Spec::Seconds, false),
        ]),
        tool(SEND, "向具名子智能体发送消息。空闲时用原上下文开新轮，运行时在安全点读取。", &[
            ("name", Spec::String, true), ("message", Spec::String, true), ("timeout_seconds", Spec::Seconds, false),
        ]),
        status_schema(),
        tool(STOP, "取消具名子智能体的当前轮次，保留原上下文，等待后续消息。", &[("name", Spec::String, true)]),
        tool(TERMINATE, "永久终结具名子智能体，禁止再次激活。", &[("name", Spec::String, true)]),
        tool(ANSWER, "按 question_id 回复子智能体交给主智能体的问题。", &[("question_id", Spec::String, true), ("answer", Spec::String, true)]),
    ]
}

pub(super) fn child_schemas() -> Vec<Value> {
    use crate::tools::{read_file, write_file::{edit_file_tool, write_file_tool}};
    vec![
        read_file::schema(), write_file_tool(), edit_file_tool(), status_schema(),
        tool(ASK_MASTER, "向主智能体提问并等待回答。", &[("question", Spec::String, true), ("question_id", Spec::String, false), ("timeout_seconds", Spec::Seconds, false)]),
        tool(ASK_OTHER, "向同一主对话的具名子智能体提问并等待回答。", &[("name", Spec::String, true), ("question", Spec::String, true), ("question_id", Spec::String, false), ("timeout_seconds", Spec::Seconds, false)]),
        tool(ANSWER_OTHER, "按 question_id 回复另一子智能体交给你的问题。", &[("question_id", Spec::String, true), ("answer", Spec::String, true)]),
    ]
}

fn status_schema() -> Value {
    tool(STATUS, "查询同一主对话的具名子智能体状态。省略 names 查询全部，含 idle 及 terminated。", &[("names", Spec::Names, false)])
}

enum Spec {
    String,
    Choice(&'static [&'static str]),
    Seconds,
    Names,
}

fn tool(name: &str, description: &str, specs: &[(&str, Spec, bool)]) -> Value {
    let mut properties = Map::new();
    properties.insert("intent".into(), json!({"type":"string"}));
    let mut required = Vec::new();
    for (key, spec, mandatory) in specs {
        let definition = match spec {
            Spec::String => json!({"type":"string"}),
            Spec::Choice(options) => json!({"type":"string","enum":options}),
            Spec::Seconds => json!({"type":"integer","minimum":1,"description":"本轮秒数上限。省略表示不限时。"}),
            Spec::Names => json!({"type":"array","items":{"type":"string"},"minItems":1}),
        };
        properties.insert((*key).into(), definition);
        if *mandatory { required.push(*key); }
    }
    json!({"type":"function","function":{"name":name,"description":description,"parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":false}}})
}

pub(super) fn text<'a>(args: &'a Value, key: &str) -> Result<&'a str, String> {
    args[key].as_str().map(str::trim).filter(|value| !value.is_empty())
        .ok_or_else(|| format!("{key}不能为空。"))
}

pub(super) fn name(raw: &Value) -> Result<&str, String> {
    let name = raw.as_str().unwrap_or_default().trim();
    if name.is_empty() || name.chars().count() > 80 {
        return Err("name必须为1至80个字符的非空名称。".into());
    }
    if name.chars().any(|char| char.is_control() || "<>\\/\"".contains(char)) {
        return Err("name不能包含控制字符、路径分隔符或消息标签字符。".into());
    }
    Ok(name)
}

pub(super) fn timeout(args: &Value) -> Result<Option<u64>, String> {
    match &args["timeout_seconds"] {
        Value::Null => Ok(None),
        value => value.as_u64().filter(|seconds| *seconds > 0).map(Some)
            .ok_or_else(|| "timeout_seconds必须为正整数；不传表示本轮不限时。".into()),
    }
}

pub(super) fn public_record(record: &Record) -> Value {
    Value::Object(["status", "summary", "created_at", "updated_at", "model_key", "intelligence",
        "access_level", "round_id", "end_reason", "final_result", "name", "working_dir"]
        .into_iter().map(|key| (key.into(), record.get(key).cloned().unwrap_or_default())).collect())
}

pub(super) fn record_name(record: &Value) -> Option<&str> {
    string(record.as_object()?, "name")
}

pub(super) fn failed(error: impl ToString) -> Value {
    json!({"success":false,"error":error.to_string()})
}

#[derive(Clone, Copy)]
pub(super) enum NoticeType {
    Output,
    AskMaster,
    AskOther,
    Answer,
    Stopped,
}

pub(super) fn message_id(prefix: &str) -> String {
    format!("{prefix}_{}", Uuid::new_v4().simple())
}

pub(super) fn notice(kind: NoticeType, name: &str, target: Option<&str>, id: &str, content: &str) -> String {
    let (tag, label, subtype) = match kind {
        NoticeType::Output => ("output", "任务结束汇报", "completion_report"),
        NoticeType::AskMaster => ("ask", "提问", "ask_master"),
        NoticeType::AskOther => ("ask", "提问", "ask_other"),
        NoticeType::Answer => ("answer", "回答", "answer_other"),
        NoticeType::Stopped => ("output", "任务结束汇报", "completion_report"),
    };
    let (directed, attribute) = target.map_or_else(|| (String::new(), String::new()), |target| {
        (format!("→{target}"), format!(" target=\"{target}\""))
    });
    format!("来自 {name}{directed} 的{label}\nid: {id}\n\n<{name}>\n<{tag}{attribute} subtype=\"{subtype}\">\n{content}\n</{tag}>\n</{name}>")
}

pub(crate) fn notice_metadata(content: &str, timestamp: &str) -> Value {
    let kind = if content.contains("\n<ask ") { "multi_agent_ask" }
        else if content.contains("\n<answer ") { "multi_agent_answer" }
        else { "multi_agent_output" };
    let id = notice_id(content).map(str::to_owned).unwrap_or_else(|| {
        use ring::digest::{SHA256, digest};
        let hash = digest(&SHA256, content.as_bytes());
        let mut id = String::from("sub_notice_");
        for byte in hash.as_ref() { use std::fmt::Write; let _ = write!(id, "{byte:02x}"); }
        id
    });
    json!({"message_id":id,"message_source":"multi_agent_notice","auto_message_type":kind,
        "visibility":"chat","starts_work":false,"timestamp":timestamp})
}

fn notice_id(content: &str) -> Option<&str> {
    let id = content.lines().nth(1)?.strip_prefix("id:")?.trim();
    (!id.is_empty() && id.len() <= 180 && super::safe_id(id)).then_some(id)
}

pub(super) fn casefold(name: &str) -> String {
    let mut folded = String::with_capacity(name.len());
    for character in name.chars() {
        if let Some(special) = super::casefold::special(character) {
            folded.push_str(special);
        } else {
            folded.extend(character.to_lowercase());
        }
    }
    folded
}
