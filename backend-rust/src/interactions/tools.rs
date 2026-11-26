use std::collections::HashSet;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::Error;

pub const ASK_USER: &str = "ask_user";
pub const SUBMIT_PLAN: &str = "submit_plan";
const MAX_PLAN_CHARS: usize = 20_000;

pub fn is_tool(name: &str) -> bool {
    matches!(name, ASK_USER | SUBMIT_PLAN)
}

pub(crate) fn schemas(allow_questions: bool) -> Vec<Value> {
    let mut tools = vec![];
    if allow_questions {
        tools.push(json!({
            "type":"function", "function":{
                "name":ASK_USER,
                "description":"向用户提问并等待回答。仅在任务遇到关键不确定性时使用，优先给出 2–4 个选项。可在同一轮提出多个独立问题。",
                "parameters":{"type":"object", "properties":{
                    "question":{"type":"string", "description":"要向用户提出的问题"},
                    "context":{"type":"string", "description":"帮助用户作答的背景"},
                    "options":{"type":"array", "maxItems":8, "items":{"type":"object", "properties":{
                        "id":{"type":"string"}, "label":{"type":"string"}, "description":{"type":"string"}
                    }, "required":["label"]}},
                    "intent":{"type":"string"}
                }, "required":["question"]}
            }
        }));
    }
    tools.push(json!({
        "type":"function", "function":{
            "name":SUBMIT_PLAN,
            "description":"在计划模式下提交工作区 .astrion/plan/ 下的 Markdown 计划，并等待用户批准或拒绝。批准后进入执行模式。",
            "parameters":{"type":"object", "properties":{
                "plan_file":{"type":"string", "description":"Markdown 计划的工作区相对路径"},
                "summary":{"type":"string", "description":"计划摘要"},
                "intent":{"type":"string"}
            }, "required":["plan_file"]}
        }
    }));
    tools
}

#[derive(Serialize)]
pub(crate) struct Question {
    pub(crate) tool_call_id: String,
    question: String,
    context: String,
    options: Vec<Choice>,
}

#[derive(Serialize)]
struct Choice {
    id: String,
    label: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    description: String,
}

impl Question {
    pub(crate) fn parse(tool_call_id: &str, arguments: &Value) -> Result<Self, &'static str> {
        let question = text(&arguments["question"]);
        if question.is_empty() || tool_call_id.trim().is_empty() {
            return Err("请提供要向用户提出的问题。");
        }
        let mut used = HashSet::new();
        let options = arguments["options"]
            .as_array()
            .into_iter()
            .flatten()
            .take(8)
            .enumerate()
            .filter_map(|(index, option)| {
                let label = clip(text(&option["label"]), 120);
                if label.is_empty() {
                    return None;
                }
                let supplied = text(&option["id"]);
                let mut id = if supplied.is_empty() {
                    format!("option_{}", index + 1)
                } else {
                    supplied.to_owned()
                };
                if !used.insert(id.clone()) {
                    let mut suffix = 2;
                    while !used.insert(format!("{id}_{suffix}")) {
                        suffix += 1;
                    }
                    id = format!("{id}_{suffix}");
                }
                Some(Choice {
                    id,
                    label,
                    description: clip(text(&option["description"]), 500),
                })
            })
            .collect();
        Ok(Self {
            tool_call_id: tool_call_id.to_owned(),
            question: question.to_owned(),
            context: text(&arguments["context"]).to_owned(),
            options,
        })
    }
}

#[derive(Default, Deserialize)]
pub(crate) struct QuestionAnswer {
    #[serde(default)]
    pub(crate) selected_option_id: Option<String>,
    #[serde(default)]
    pub(crate) text: Option<String>,
    #[serde(default)]
    pub(crate) dismissed: Option<bool>,
}

impl QuestionAnswer {
    pub(crate) fn apply(&self, question: &Value) -> Result<Value, Error> {
        let text = self.text.as_deref().unwrap_or_default().trim();
        let id = self.selected_option_id.as_deref().unwrap_or_default().trim();
        if self.dismissed == Some(true) {
            return Ok(json!({"type":"dismissed", "text":""}));
        }
        if id.is_empty() {
            return if text.is_empty() {
                Err(Error::Invalid("Please select an option, enter an answer, or dismiss the question"))
            } else {
                Ok(json!({"type":"free_text", "text":text}))
            };
        }
        let option = question["options"]
            .as_array()
            .and_then(|options| options.iter().find(|option| option["id"] == id))
            .ok_or(Error::Invalid("The selected option does not exist"))?;
        let label = option["label"].as_str().unwrap_or_default();
        let mut answer = json!({
            "type":if text.is_empty() { "option" } else { "option_with_text" },
            "text":if text.is_empty() { label } else { text },
            "selected_option_id":id,
            "selected_option_label":label,
        });
        if let Some(description) = option.get("description") {
            answer["selected_option_description"] = description.clone();
        }
        Ok(answer)
    }
}

#[derive(Serialize)]
pub(crate) struct Plan {
    tool_call_id: String,
    plan_file: String,
    plan_content: String,
    plan_content_truncated: bool,
    summary: String,
}

impl Plan {
    pub(crate) fn new(tool_call_id: &str, file: &str, content: &str, summary: &Value) -> Self {
        let shortened = clip(content, MAX_PLAN_CHARS);
        Self {
            tool_call_id: tool_call_id.to_owned(),
            plan_file: file.to_owned(),
            plan_content_truncated: shortened.len() < content.len(),
            plan_content: shortened,
            summary: clip(text(summary), 300),
        }
    }
}

pub(crate) fn question_result(item: &Value) -> Value {
    let mut status = text(&item["status"]);
    let answer = &item["answer"];
    let message = match (status, text(&answer["type"])) {
        ("answered", "dismissed") => {
            status = "dismissed";
            "用户没有回答或不想回答这个问题，请直接输出内容向用户提问".to_owned()
        }
        ("answered", _) => {
            let label = text(&answer["selected_option_label"]);
            let response = text(&answer["text"]);
            if label.is_empty() {
                format!("用户回答：{response}")
            } else if response == label || response.is_empty() {
                format!("用户选择：{label}")
            } else {
                format!("用户选择：{label}\n用户补充：{response}")
            }
        }
        ("expired", _) => {
            status = "timeout";
            "等待用户回答超时。".to_owned()
        }
        _ => "用户未回答。".to_owned(),
    };
    json!({
        "success":status == "answered", "status":status, "answer_status":status,
        "message":message, "answer_text":if status == "answered" { &message } else { "" },
        "question_id":item["question_id"],
    })
}

pub(super) fn clip(text: &str, max: usize) -> String {
    text.chars().take(max).collect()
}

pub(crate) fn text(value: &Value) -> &str {
    value.as_str().unwrap_or_default().trim()
}
