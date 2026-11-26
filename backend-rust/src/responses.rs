//! Translation between Astrion chat messages and the Responses wire protocol.
//!
//! Build the request with [`build_responses_body`], then pass SSE data payloads
//! to [`ResponsesSseParser::push_data`] or individual decoded HTTP lines to
//! [`ResponsesSseParser::push_line`]. Forward the chunks in order and call
//! [`ResponsesSseParser::finish`] at EOF before saving the reasoning items.

mod request;
mod stream;
mod timing;

pub use request::{
    ResponsesProfile, ResponsesRequest, ToolRequiredMap, build_responses_body,
    chat_request_to_input, flatten_tools, sanitize_json_schema, sanitize_tool_arguments,
    should_send_prompt_cache_key, should_use_developer_prompt, split_system_messages,
    tool_required_map,
};
pub use stream::{ResponsesApiError, ResponsesSseParser};

use serde_json::{Number, Value};

fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn string_or_empty(value: &Value) -> String {
    if is_truthy(value) {
        python_string(value)
    } else {
        String::new()
    }
}

pub(crate) fn python_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        _ => python_repr(value),
    }
}

fn python_repr(value: &Value) -> String {
    match value {
        Value::String(text) => python_quote(text),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(python_repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(members) => format!(
            "{{{}}}",
            members
                .iter()
                .map(|(key, value)| format!("{}: {}", python_quote(key), python_repr(value)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Number(value) => python_number(value),
    }
}

fn python_quote(text: &str) -> String {
    use std::fmt::Write;

    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut quoted = String::from(quote);
    for character in text.chars() {
        match character {
            '\t' => quoted.push_str("\\t"),
            '\n' => quoted.push_str("\\n"),
            '\r' => quoted.push_str("\\r"),
            '\\' => quoted.push_str("\\\\"),
            character if character == quote => {
                quoted.push('\\');
                quoted.push(quote);
            }
            character
                if character.is_control() || (character.is_whitespace() && character != ' ') =>
            {
                let code = character as u32;
                if code <= 0xff {
                    let _ = write!(quoted, "\\x{code:02x}");
                } else {
                    let _ = write!(quoted, "\\u{code:04x}");
                }
            }
            character => quoted.push(character),
        }
    }
    quoted.push(quote);
    quoted
}

fn python_number(number: &Number) -> String {
    if let Some(value) = number.as_f64().filter(|_| number.is_f64()) {
        let magnitude = value.abs();
        if magnitude >= 1e16 || (magnitude > 0.0 && magnitude < 1e-4) {
            let scientific = format!("{value:e}");
            if let Some((digits, exponent)) = scientific.split_once('e')
                && let Ok(exponent) = exponent.parse::<i32>()
            {
                return format!("{digits}e{exponent:+03}");
            }
        }
        let text = value.to_string();
        if !text.contains('.') {
            return format!("{text}.0");
        }
        return text;
    }
    number.to_string()
}

// Python's json.dumps defaults are observable inside tool-argument and content strings.
pub(crate) fn python_json(value: &Value) -> String {
    match value {
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(python_json).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(members) => format!(
            "{{{}}}",
            members
                .iter()
                .map(|(key, value)| format!("{}: {}", serde_json::json!(key), python_json(value)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Number(value) => python_number(value),
        _ => value.to_string(),
    }
}

fn strip_server_id(item: &Value) -> Value {
    let mut item = item.clone();
    if let Some(item) = item.as_object_mut() {
        item.shift_remove("id");
    }
    item
}

fn values(value: &Value) -> &[Value] {
    value.as_array().map_or(&[], Vec::as_slice)
}

fn python_trim(value: &str) -> &str {
    value.trim_matches(|character: char| {
        character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
    })
}

fn value_or<'a>(value: &'a Value, default: &'a Value) -> &'a Value {
    if is_truthy(value) { value } else { default }
}

fn string_field<'a>(item: &'a Value, key: &str) -> Option<&'a str> {
    item.get(key).and_then(Value::as_str)
}

fn has_type(value: &Value, kind: &str) -> bool {
    string_field(value, "type") == Some(kind)
}
