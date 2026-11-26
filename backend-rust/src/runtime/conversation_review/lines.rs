use crate::responses::{python_json, python_string};
use serde_json::Value;

fn text(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Object(_) => content["text"].as_str().unwrap_or_default().to_owned(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|part| match part {
                Value::String(s) => Some(s.as_str()),
                _ if part["type"] == "text" => part["text"].as_str(),
                _ => None,
            })
            .collect(),
        _ => String::new(),
    }
}

fn tool_call(function: &Value) -> String {
    let name = function["name"]
        .as_str()
        .filter(|s| !s.is_empty())
        .unwrap_or("tool");
    let arguments = &function["arguments"];
    let decoded = arguments
        .as_str()
        .and_then(|s| serde_json::from_str(s).ok());
    format!(
        "tool_call：{name} {}",
        python_json(decoded.as_ref().unwrap_or(arguments))
    )
}

fn action_result(tool: &Value) -> String {
    if !tool["content"].is_null() {
        return python_string(&tool["content"]);
    }
    match &tool["result"] {
        Value::Null => tool["message"].as_str().unwrap_or_default().to_owned(),
        Value::String(s) => s.clone(),
        result => python_json(result),
    }
}

/// A row can contain embedded newlines. Python's limit counts rows, not visual lines.
pub(super) fn build(messages: &Value, full: bool, limit: Option<i64>) -> Vec<String> {
    let mut rows = Vec::new();
    let cap = limit.filter(|n| *n > 0).unwrap_or(i64::MAX) as usize;
    for msg in messages.as_array().into_iter().flatten() {
        let content = match &msg["content"] {
            v @ (Value::String(_) | Value::Array(_) | Value::Object(_)) => v,
            _ => &msg["text"],
        };
        let role = msg["role"].as_str().unwrap_or_default();
        let body = text(content);
        if (matches!(role, "user" | "assistant") && (full || !body.trim().is_empty()))
            || (full && matches!(role, "tool" | "system"))
        {
            rows.push(format!("{role}：{}", body.trim_end()));
        }
        if rows.len() >= cap {
            break;
        }
        if !full || role != "assistant" {
            continue;
        }
        for action in msg["actions"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|a| a["type"] == "tool")
        {
            let tool = &action["tool"];
            let name = tool["name"]
                .as_str()
                .filter(|s| !s.is_empty())
                .unwrap_or("tool");
            let args = if tool["arguments"].is_null() {
                &tool["argumentSnapshot"]
            } else {
                &tool["arguments"]
            };
            rows.push(format!("tool_call：{name} {}", python_json(args)));
            if rows.len() >= cap {
                return rows;
            }
            rows.push(format!("tool：{}", action_result(tool).trim_end()));
            if rows.len() >= cap {
                return rows;
            }
        }
        let calls = msg["tool_calls"].as_array().into_iter().flatten();
        let embedded = content
            .as_array()
            .into_iter()
            .flatten()
            .filter(|part| part["type"] == "tool_call");
        for call in calls.chain(embedded) {
            rows.push(tool_call(&call["function"]));
            if rows.len() >= cap {
                return rows;
            }
        }
    }
    if let Some(end) = limit.filter(|n| *n < 0) {
        rows.truncate(rows.len().saturating_sub(end.unsigned_abs() as usize));
    }
    rows
}
