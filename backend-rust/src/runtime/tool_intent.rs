//! Streaming extraction of the top-level `intent` argument.
//!
//! Mirrors the retired Python helpers (`server/tool_intent_stream.py` and
//! `server/chat_flow_runner_helpers.py`) so the preparing block can show the
//! intent while the model is still streaming the remaining arguments.

/// Apply the Agent display preference to first-party JSON object parameters only.
/// MCP properties (including a genuine remote `intent` field) belong to the server.
pub(super) fn configure_schema(schema: &mut serde_json::Value, enabled: bool) {
    let function = &mut schema["function"];
    if function["name"].as_str().is_none_or(|name| name.starts_with("mcp__")) {
        return;
    }
    let parameters = &mut function["parameters"];
    if parameters["type"] != "object" {
        return;
    }
    let Some(properties) = parameters["properties"].as_object_mut() else {
        return;
    };
    if enabled {
        if !properties.contains_key("intent") {
            let existing = std::mem::take(properties);
            properties.insert("intent".into(), serde_json::json!({
                "type":"string", "description":"用不超过 15 个字向用户说明即将执行的操作目的"
            }));
            properties.extend(existing);
        }
        if !parameters["required"].is_array() {
            parameters["required"] = serde_json::json!([]);
        }
        if let Some(required) = parameters["required"].as_array_mut()
            && !required.iter().any(|value| value == "intent")
        {
            required.insert(0, serde_json::json!("intent"));
        }
    } else {
        properties.shift_remove("intent");
        if let Some(required) = parameters["required"].as_array_mut() {
            required.retain(|value| value != "intent");
        }
    }
}

/// Return a closed intent, or `Some("")` when the object is complete but has no intent.
///
/// `None` means the arguments are still incomplete. Keys and preceding values are
/// decoded structurally, so nested keys, escaped quotes and braces inside strings
/// cannot masquerade as a complete top-level intent; the rest of the object may
/// still be unfinished.
pub(crate) fn extract_complete_intent(arguments: &str) -> Option<String> {
    let bytes = arguments.as_bytes();
    let mut at = 0;
    skip_ws(bytes, &mut at);
    if bytes.get(at) != Some(&b'{') {
        return None;
    }
    at += 1;
    loop {
        skip_ws(bytes, &mut at);
        match bytes.get(at) {
            Some(b'}') => return complete_object(arguments),
            Some(b'"') => {}
            _ => return None,
        }
        let end = string_end(bytes, at)?;
        let key = serde_json::from_str::<String>(&arguments[at..end]).ok()?;
        at = end;
        skip_ws(bytes, &mut at);
        if bytes.get(at) != Some(&b':') {
            return None;
        }
        at += 1;
        skip_ws(bytes, &mut at);
        let value_end = value_end(bytes, at)?;
        if key == "intent" {
            if bytes.get(at) != Some(&b'"') {
                return None;
            }
            return serde_json::from_str::<String>(&arguments[at..value_end]).ok();
        }
        at = value_end;
        skip_ws(bytes, &mut at);
        match bytes.get(at) {
            Some(b',') => at += 1,
            Some(b'}') => return complete_object(arguments),
            _ => return None,
        }
    }
}

/// Coarse extraction tolerant of an incomplete JSON string, used while streaming.
///
/// Only the first quoted fragment after the key is kept (bounded to 128
/// characters), matching the previous regular expression: escaped quotes end
/// the match, and JSON escapes are decoded when possible with a raw fallback.
pub(crate) fn extract_partial_intent(arguments: &str) -> Option<String> {
    if arguments.is_empty() || !contains_key(arguments.as_bytes(), b"intent") {
        return None;
    }
    let bytes = arguments.as_bytes();
    let mut at = 0;
    while at < bytes.len() {
        if bytes[at] != b'"' {
            at += 1;
            continue;
        }
        let Some(end) = string_end(bytes, at) else {
            return None;
        };
        let key = &arguments[at + 1..end - 1];
        at = end;
        if !key.eq_ignore_ascii_case("intent") {
            continue;
        }
        skip_ws(bytes, &mut at);
        if bytes.get(at) != Some(&b':') {
            return None;
        }
        at += 1;
        skip_ws(bytes, &mut at);
        if bytes.get(at) != Some(&b'"') {
            return None;
        }
        at += 1;
        // The retired regex kept at most 128 characters and stopped at the
        // first quote, so escapes are not treated as string boundaries here.
        let raw = arguments[at..]
            .chars()
            .take_while(|character| *character != '"')
            .take(128)
            .collect::<String>();
        return Some(
            serde_json::from_str::<String>(&format!("\"{raw}\""))
                .unwrap_or(raw),
        );
    }
    None
}

/// The whole value must parse before a complete-but-intent-less object is accepted.
fn complete_object(arguments: &str) -> Option<String> {
    serde_json::from_str::<serde_json::Value>(arguments)
        .ok()
        .map(|_| String::new())
}

/// Case-insensitive literal scan, used to skip unrelated arguments early.
fn contains_key(bytes: &[u8], needle: &[u8]) -> bool {
    bytes
        .windows(needle.len())
        .any(|window| window.eq_ignore_ascii_case(needle))
}

fn skip_ws(bytes: &[u8], at: &mut usize) {
    while matches!(bytes.get(*at), Some(b' ' | b'\t' | b'\n' | b'\r')) {
        *at += 1;
    }
}

/// Position after the closing quote of the string starting at `at`.
fn string_end(bytes: &[u8], at: usize) -> Option<usize> {
    if bytes.get(at) != Some(&b'"') {
        return None;
    }
    let mut i = at + 1;
    while i < bytes.len() {
        match bytes[i] {
            b'\\' => i += 2,
            b'"' => return Some(i + 1),
            _ => i += 1,
        }
    }
    None
}

/// Position after one complete JSON value starting at `at`.
fn value_end(bytes: &[u8], at: usize) -> Option<usize> {
    match *bytes.get(at)? {
        b'"' => string_end(bytes, at),
        b'{' | b'[' => {
            let mut i = at;
            let mut depth = 0usize;
            while i < bytes.len() {
                match bytes[i] {
                    b'"' => i = string_end(bytes, i)?,
                    b'{' | b'[' => {
                        depth += 1;
                        i += 1;
                    }
                    b'}' | b']' => {
                        depth -= 1;
                        i += 1;
                        if depth == 0 {
                            return Some(i);
                        }
                    }
                    _ => i += 1,
                }
            }
            None
        }
        _ => {
            let mut i = at;
            while i < bytes.len()
                && !matches!(bytes[i], b',' | b'}' | b']' | b' ' | b'\t' | b'\n' | b'\r')
            {
                i += 1;
            }
            (i > at).then_some(i)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{extract_complete_intent, extract_partial_intent};

    #[test]
    fn complete_intent_returns_once_its_string_closes() {
        // The retired decoder returned the intent value as soon as that string
        // parsed; the rest of the object may still be streaming.
        assert_eq!(
            extract_complete_intent(r#"{"intent": "读取文件""#).as_deref(),
            Some("读取文件")
        );
        assert_eq!(extract_complete_intent(r#"{"intent": "读取文件"#), None);
        assert_eq!(
            extract_complete_intent(r#"{"intent": "读取文件", "path": "a.md"}"#).as_deref(),
            Some("读取文件")
        );
    }

    #[test]
    fn complete_intent_ignores_nested_keys_and_escapes() {
        assert_eq!(extract_complete_intent(r#"{"content": "{\"intent\": \"x\"}""#), None);
        assert_eq!(
            extract_complete_intent(r#"{"content": "a\"b", "intent": "写"},"#).as_deref(),
            Some("写")
        );
    }

    #[test]
    fn complete_object_without_intent_reports_empty() {
        assert_eq!(extract_complete_intent(r#"{"path": "a.md"}"#).as_deref(), Some(""));
        assert_eq!(extract_complete_intent(r#"{"path": "a.md""#), None);
    }

    #[test]
    fn partial_intent_tolerates_unfinished_strings() {
        assert_eq!(extract_partial_intent(r#"{"intent": "读取"#).as_deref(), Some("读取"));
        assert_eq!(extract_partial_intent(r#"{"path": "a.md""#), None);
        assert_eq!(extract_partial_intent(r#"{ "INTENT" : "改" }"#).as_deref(), Some("改"));
    }

    #[test]
    fn partial_intent_decodes_escapes_with_raw_fallback() {
        assert_eq!(extract_partial_intent(r#"{"intent": "\u4e2d\u6587"#).as_deref(), Some("中文"));
        assert_eq!(extract_partial_intent(r#"{"intent": "a\tb"#).as_deref(), Some("a\tb"));
    }
}
