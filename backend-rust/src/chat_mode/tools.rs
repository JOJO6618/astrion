//! Chat tools use account-owned memory and the shared webpage service.

use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use super::{
    ChatError, ChatMode,
    definitions::is_chat_tool,
    store::{self, ChatPaths, content_text},
};

const SHARED_FIELDS: &[&str] = &[
    "self_identify",
    "user_name",
    "profession",
    "tone",
    "considerations",
    "theme",
    "communication_style",
    "conversation_continuity",
];
const CHAT_FIELDS: &[&str] = &[
    "chat_default_model",
    "chat_default_thinking_mode",
    "chat_default_reasoning_effort",
    "chat_network_enabled",
    "chat_instructions",
    "chat_deep_compress_trigger_tokens",
];

pub(super) async fn execute(
    mode: &ChatMode,
    name: &str,
    arguments: &Value,
    conversation_id: &str,
) -> Result<Value, ChatError> {
    if !is_chat_tool(name) {
        return Ok(json!({"success":false,"error":"Tool unavailable in Chat"}));
    }
    if matches!(name, "web_search" | "extract_webpage") && !network_enabled(mode, conversation_id)? {
        return Ok(json!({
            "success": false,
            "code": "chat_network_disabled",
            "error": "联网已关闭",
        }));
    }
    match name {
        "web_search" | "extract_webpage" =>
            Ok(crate::tools::web::execute(&mode.preferences_dir, name, arguments).await),
        "manage_personalization" => personalization(mode, arguments),
        "update_memory" => update_memory(mode, arguments),
        "search_chat_memory" => search_memory(mode, arguments),
        "read_chat_memory" => read_memory(mode, arguments),
        "update_chat_memory" => write_topic(mode, arguments),
        "search_chat_history" => search_history(mode, arguments),
        "read_chat_history" => read_history(mode, arguments),
        _ => Ok(json!({"success":false,"error":"Unknown Chat tool"})),
    }
}

fn network_enabled(mode: &ChatMode, conversation_id: &str) -> Result<bool, ChatError> {
    mode.storage(|paths| Ok(paths.read_raw(conversation_id)?["metadata"]["network_enabled"] == true))
}

fn memory_path(root: &Path) -> PathBuf {
    root.join("memory").join("memories.json")
}

fn load_memory(paths: &ChatPaths) -> Result<Value, ChatError> {
    let value = store::read_json(&memory_path(paths.root()))?;
    if value.is_null() {
        return Ok(json!({"version":1,"global":[],"topics":{}}));
    }
    if value["version"].as_u64() != Some(1)
        || !value["global"].is_array()
        || !value["topics"].is_object()
    {
        return Err(ChatError::storage("Invalid Chat memory document"));
    }
    Ok(value)
}

fn save_memory(paths: &ChatPaths, value: &Value) -> Result<(), ChatError> {
    let directory = memory_path(paths.root())
        .parent()
        .expect("Chat memory file has a parent")
        .to_path_buf();
    std::fs::create_dir_all(&directory)?;
    store::write_json(&memory_path(paths.root()), value)
}

fn update_memory(mode: &ChatMode, arguments: &Value) -> Result<Value, ChatError> {
    let operation = arguments["operation"]
        .as_str()
        .filter(|value| matches!(*value, "append" | "replace" | "delete"))
        .ok_or_else(|| ChatError::bad_request("Invalid memory operation"))?;
    let content = arguments["content"]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if operation != "delete" && content.is_none() {
        return Err(ChatError::bad_request("Memory content is required"));
    }
    let index = arguments["index"].as_u64().map(|value| value as usize);
    mode.storage(|paths| {
        let mut memory = load_memory(paths)?;
        let entries = memory["global"]
            .as_array_mut()
            .expect("validated Chat memory entries");
        if operation != "append"
            && index.is_none_or(|index| index == 0 || index > entries.len())
        {
            return Err(ChatError::bad_request("Memory index is out of range"));
        }
        match operation {
            "append" => entries.push(json!(content.expect("validated memory content"))),
            "replace" => {
                entries[index.expect("validated index") - 1] =
                    json!(content.expect("validated memory content"));
            }
            _ => {
                entries.remove(index.expect("validated index") - 1);
            }
        }
        let count = entries.len();
        save_memory(paths, &memory)?;
        Ok(json!({"success":true,"count":count,"operation":operation}))
    })
}

fn search_memory(mode: &ChatMode, arguments: &Value) -> Result<Value, ChatError> {
    let keywords: Vec<String> = arguments["keywords"]
        .as_array()
        .filter(|values| (1..=5).contains(&values.len()))
        .ok_or_else(|| ChatError::bad_request("Provide one to five nonempty keywords"))?
        .iter()
        .map(|value| value.as_str().unwrap_or_default().trim().to_lowercase())
        .filter(|value| !value.is_empty())
        .collect();
    if keywords.is_empty() {
        return Err(ChatError::bad_request("Provide one to five nonempty keywords"));
    }
    let limit = arguments["max_results"]
        .as_u64()
        .unwrap_or(5)
        .clamp(1, 10) as usize;
    mode.storage(|paths| {
        let memory = load_memory(paths)?;
        let mut ranked = Vec::new();
        if let Some(topics) = memory["topics"].as_object() {
            for topic in topics.values() {
                let haystack = format!(
                    "{}\n{}\n{}",
                    topic["name"].as_str().unwrap_or_default(),
                    topic["description"].as_str().unwrap_or_default(),
                    topic["content"].as_str().unwrap_or_default(),
                )
                .to_lowercase();
                let score = keywords.iter().filter(|key| haystack.contains(*key)).count();
                if score > 0 {
                    ranked.push((
                        score,
                        json!({
                            "name": topic["name"],
                            "description": topic["description"],
                            "snippet": topic["content"].as_str().unwrap_or_default().chars().take(400).collect::<String>(),
                        }),
                    ));
                }
            }
        }
        ranked.sort_by(|left, right| {
            right
                .0
                .cmp(&left.0)
                .then_with(|| left.1["name"].as_str().cmp(&right.1["name"].as_str()))
        });
        Ok(json!({"results": ranked.into_iter().take(limit).map(|(_, value)| value).collect::<Vec<_>>()}))
    })
}

fn read_memory(mode: &ChatMode, arguments: &Value) -> Result<Value, ChatError> {
    let name = arguments["name"]
        .as_str()
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ChatError::bad_request("Chat memory name is required"))?;
    mode.storage(|paths| {
        let memory = load_memory(paths)?;
        let topic = memory["topics"].get(name).cloned().unwrap_or(Value::Null);
        if topic.is_null() {
            return Err(ChatError::not_found("Chat memory not found"));
        }
        Ok(topic)
    })
}

fn write_topic(mode: &ChatMode, arguments: &Value) -> Result<Value, ChatError> {
    let name = arguments["name"]
        .as_str()
        .filter(|value| valid_topic_name(value))
        .ok_or_else(|| ChatError::bad_request("Memory names use lowercase letters, digits and underscores"))?;
    let description = arguments["description"]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty() && value.chars().count() <= 100)
        .ok_or_else(|| ChatError::bad_request("A description of at most 100 characters is required"))?;
    let content = arguments["content"]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ChatError::bad_request("Topic content is required"))?;
    mode.storage(|paths| {
        let mut memory = load_memory(paths)?;
        memory["topics"][name] = json!({
            "name": name,
            "description": description,
            "content": content,
            "updated_at": crate::chat_mode::store::timestamp(crate::chat_mode::tasks::now())?,
        });
        save_memory(paths, &memory)?;
        Ok(json!({"success":true,"name":name}))
    })
}

fn valid_topic_name(value: &str) -> bool {
    let mut bytes = value.bytes();
    bytes
        .next()
        .is_some_and(|byte| byte.is_ascii_lowercase())
        && bytes.all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
        && value.len() <= 80
}

fn search_history(mode: &ChatMode, arguments: &Value) -> Result<Value, ChatError> {
    let query = arguments["query"].as_str().unwrap_or_default().trim();
    if query.is_empty() {
        return Err(ChatError::bad_request("Search query is required"));
    }
    let limit = arguments["max_results"]
        .as_u64()
        .unwrap_or(20)
        .clamp(1, 100) as usize;
    mode.storage(|paths| Ok(json!({"results": paths.search(query, limit)?})))
}

fn read_history(mode: &ChatMode, arguments: &Value) -> Result<Value, ChatError> {
    let id = arguments["conversation_id"]
        .as_str()
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ChatError::bad_request("Conversation identity is required"))?;
    mode.storage(|paths| {
        let document = paths.read_document(id)?;
        let messages: Vec<_> = document
            .chat_path(None)?
            .into_iter()
            .filter(|message| matches!(message["role"].as_str(), Some("user" | "assistant")))
            .filter(|message| message["metadata"]["visibility"] != "hidden")
            .map(|message| {
                json!({
                    "role": message["role"],
                    "content": content_text(&message["content"]),
                })
            })
            .collect();
        Ok(json!({"title": document.raw()["title"], "messages": messages}))
    })
}

fn personalization(mode: &ChatMode, arguments: &Value) -> Result<Value, ChatError> {
    match arguments["action"].as_str() {
        Some("read") => {
            let preferences = super::settings::load_preferences(mode)?;
            let mut result = Map::new();
            for key in SHARED_FIELDS.iter().chain(CHAT_FIELDS) {
                if let Some(value) = preferences.get(*key)
                    && !value.is_null()
                {
                    result.insert((*key).into(), value.clone());
                }
            }
            Ok(Value::Object(result))
        }
        Some("update") => {
            let field = arguments["field"]
                .as_str()
                .filter(|field| SHARED_FIELDS.contains(field) || CHAT_FIELDS.contains(field))
                .ok_or_else(|| ChatError::bad_request("This preference is unavailable in Chat"))?;
            let mut patch = Map::new();
            patch.insert(field.to_owned(), arguments["value"].clone());
            super::settings::patch(mode, &patch)?;
            Ok(json!({"success":true,"field":field,"value":arguments["value"]}))
        }
        _ => Err(ChatError::bad_request("Invalid personalization action")),
    }
}
