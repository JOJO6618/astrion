//! The complete Chat tool catalog. It intentionally contains no file, shell,
//! workspace, MCP, sub-agent, workflow or skill tool.

use serde_json::{Value, json};

fn text(description: &str) -> Value {
    json!({"type": "string", "description": description})
}

fn tool(name: &str, description: &str, properties: Value, required: &[&str]) -> Value {
    let mut properties = properties;
    properties["intent"] = text("用不超过15个字说明操作目的");
    json!({"type":"function","function":{
        "name":name,"description":description,
        "parameters":{"type":"object","properties":properties,"required":required},
    }})
}

pub fn definitions() -> Vec<Value> {
    let mut definitions = crate::tools::web::definitions();
    definitions.extend([
        tool(
            "manage_personalization",
            "读取或更新共用偏好及Chat设置。不能修改Agent执行设置。",
            json!({
                "action": {"type":"string","enum":["read","update"]},
                "field": text("偏好字段名"),
                "value": {"description":"偏好字段的新值；字符串、布尔或null"},
            }),
            &["action"],
        ),
        tool(
            "update_memory",
            "更新Chat专属全局长期记忆，与Agent记忆隔离。",
            json!({
                "operation": {"type":"string","enum":["append","replace","delete"]},
                "content": text(""),
                "index": {"type":"integer","minimum":1},
            }),
            &["operation"],
        ),
        tool(
            "search_chat_memory",
            "按关键词查找Chat专题记忆。命中后用read_chat_memory读取全文。",
            json!({
                "keywords": {"type":"array","items":text(""),"minItems":1,"maxItems":5},
                "max_results": {"type":"integer","minimum":1,"maximum":10},
            }),
            &["keywords"],
        ),
        tool(
            "read_chat_memory",
            "读取Chat专题记忆全文。",
            json!({"name": text("")}),
            &["name"],
        ),
        tool(
            "update_chat_memory",
            "保存Chat专题记忆，描述说明应在何时检索。",
            json!({
                "name": text("英文小写下划线名称"),
                "description": text("不超过100字"),
                "content": text(""),
            }),
            &["name", "description", "content"],
        ),
        tool(
            "search_chat_history",
            "搜索当前账号Chat历史，仅搜索各对话最新分支。",
            json!({
                "query": text(""),
                "max_results": {"type":"integer","minimum":1,"maximum":100},
            }),
            &["query"],
        ),
        tool(
            "read_chat_history",
            "读取当前账号指定Chat对话最新分支的纯净记录。",
            json!({"conversation_id": text("")}),
            &["conversation_id"],
        ),
    ]);
    definitions
}

pub const CHAT_TOOL_NAMES: [&str; 9] = [
    "web_search",
    "extract_webpage",
    "manage_personalization",
    "update_memory",
    "search_chat_memory",
    "read_chat_memory",
    "update_chat_memory",
    "search_chat_history",
    "read_chat_history",
];

pub(crate) fn is_chat_tool(name: &str) -> bool {
    CHAT_TOOL_NAMES.contains(&name)
}
