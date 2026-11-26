//! Account-wide Chat defaults stored in the shared `personalization.json`.
//!
//! Python's `ChatStorage` only owns conversation files; account defaults live in
//! the same personalization document as Agent preferences. Chat writes only its
//! own fields (plus the explicitly shared personalization fields) and preserves
//! every unknown key in place, so opening either mode never rewrites the other's
//! settings.

use serde_json::{Map, Value, json};

use super::{ChatError, ChatMode};
use crate::catalog::Catalog;

pub(super) const CHAT_FIELDS: &[&str] = &[
    "chat_default_model",
    "chat_default_thinking_mode",
    "chat_default_reasoning_effort",
    "chat_network_enabled",
    "chat_instructions",
    "chat_deep_compress_trigger_tokens",
];

pub(super) const SHARED_FIELDS: &[&str] = &[
    "self_identify",
    "user_name",
    "profession",
    "tone",
    "considerations",
    "theme",
    "communication_style",
    "conversation_continuity",
];

const EFFORTS: [&str; 5] = ["low", "medium", "high", "xhigh", "max"];

fn defaults() -> Value {
    json!({
        "chat_default_model": "",
        "chat_default_thinking_mode": true,
        "chat_default_reasoning_effort": null,
        "chat_network_enabled": true,
        "chat_instructions": "",
        "chat_deep_compress_trigger_tokens": 250_000,
    })
}

pub(super) fn preferences_path(mode: &ChatMode) -> std::path::PathBuf {
    mode.preferences_dir.join("personalization.json")
}

/// Read the personalization object without provisioning or normalizing Agent
/// defaults. Chat only needs its own keys, and PATCH must leave everything else
/// byte-for-structure untouched.
pub(super) fn load_preferences(mode: &ChatMode) -> Result<Value, ChatError> {
    let Some(personalization) = mode.personalization.as_ref() else {
        return Err(ChatError::unavailable(
            "Chat personalization state is unavailable",
        ));
    };
    let value = personalization
        .chat_preferences()
        .map_err(|error| ChatError::storage(format!("Chat preferences are unavailable: {error}")))?;
    if value.is_object() {
        Ok(value)
    } else {
        Err(ChatError::storage("Chat preferences must be an object"))
    }
}

fn model_rows(
    mode: &ChatMode,
    preferences: &Value,
) -> (Vec<Value>, Option<String>) {
    let Some(source) = mode.catalog.as_ref() else {
        return (Vec::new(), None);
    };
    let hidden: std::collections::HashSet<&str> = preferences["hidden_models"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    let catalog = source.load();
    let default = catalog.default_key().map(str::to_owned);
    (model_rows_from(&catalog, &hidden), default)
}

fn model_rows_from(catalog: &Catalog, hidden: &std::collections::HashSet<&str>) -> Vec<Value> {
    let response = catalog.models_response();
    response["items"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| {
            let key = item["model_key"].as_str()?;
            if hidden.contains(key) {
                return None;
            }
            let multimodal = item["multimodal"].as_str().unwrap_or("none");
            Some(json!({
                "key": key,
                "name": item["name"],
                "api_protocol": item["api_protocol"],
                "supports_thinking": item["supports_thinking"],
                "is_chatgpt_model": item["is_chatgpt_model"],
                "supports_chatgpt_fast_mode": item["supports_chatgpt_fast_mode"],
                "supports_images": multimodal.split(',').any(|kind| kind.trim() == "image"),
                "supports_videos": multimodal.split(',').any(|kind| kind.trim() == "video"),
                "provider_id": item["provider_id"],
                "provider_name": item["provider_name"],
                "context_window": item["context_window"]
                    .as_u64()
                    .filter(|value| *value > 0)
                    .unwrap_or(128_000),
            }))
        })
        .collect()
}

fn account_settings(preferences: &Value, default_model: Option<&str>) -> Value {
    let mut settings = defaults();
    for key in CHAT_FIELDS {
        if let Some(value) = preferences.get(*key) {
            settings[*key] = value.clone();
        }
    }
    // Python falls back to the configured registry/environment default model,
    // never to catalog order (the first visible row is not a preference).
    if settings["chat_default_model"]
        .as_str()
        .is_none_or(|value| value.trim().is_empty())
        && let Some(key) = default_model
    {
        settings["chat_default_model"] = json!(key);
    }
    settings
}

pub(super) fn payload(mode: &ChatMode) -> Result<Value, ChatError> {
    let preferences = load_preferences(mode)?;
    let (rows, default_model) = model_rows(mode, &preferences);
    let settings = account_settings(&preferences, default_model.as_deref());
    Ok(json!({
        "settings": settings,
        "models": rows,
        "personalization": preferences,
        "theme": preferences.get("theme").cloned().unwrap_or(Value::Null),
        "username": "host",
    }))
}

pub(super) fn validate_patch(
    mode: &ChatMode,
    patch: &Map<String, Value>,
) -> Result<(), ChatError> {
    if patch.keys().any(|key| {
        !CHAT_FIELDS.contains(&key.as_str()) && !SHARED_FIELDS.contains(&key.as_str())
    }) {
        return Err(ChatError::bad_request("Unsupported Chat preference"));
    }
    let preferences = load_preferences(mode)?;
    let (rows, _) = model_rows(mode, &preferences);
    let model_keys: std::collections::HashSet<&str> = rows
        .iter()
        .filter_map(|row| row["key"].as_str())
        .collect();

    for (key, value) in patch {
        match key.as_str() {
            "chat_default_model" => {
                let model = value
                    .as_str()
                    .ok_or_else(|| ChatError::bad_request("Invalid Chat default model"))?
                    .trim();
                if !model.is_empty() && !model_keys.is_empty() && !model_keys.contains(model) {
                    return Err(ChatError::bad_request("Unknown Chat default model"));
                }
            }
            "chat_default_thinking_mode" | "chat_network_enabled" => {
                if !value.is_boolean() {
                    return Err(ChatError::bad_request(format!("{key} must be a boolean")));
                }
            }
            "chat_default_reasoning_effort" => {
                if !value.is_null()
                    && value
                        .as_str()
                        .is_none_or(|effort| !EFFORTS.contains(&effort))
                {
                    return Err(ChatError::bad_request("Invalid Chat reasoning effort"));
                }
            }
            "chat_instructions" => {
                let instructions = value
                    .as_str()
                    .ok_or_else(|| ChatError::bad_request("Invalid Chat instructions"))?;
                if instructions.chars().count() > 20_000 {
                    return Err(ChatError::bad_request(
                        "Chat instructions must be at most 20000 characters",
                    ));
                }
            }
            "chat_deep_compress_trigger_tokens" => {
                if value
                    .as_u64()
                    .is_none_or(|tokens| !(1_000..=2_000_000).contains(&tokens))
                {
                    return Err(ChatError::bad_request(
                        "Chat deep compression context must be an integer from 1000 to 2000000",
                    ));
                }
            }
            "theme" => {
                if value
                    .as_str()
                    .is_none_or(|theme| !matches!(theme, "classic" | "light" | "dark"))
                {
                    return Err(ChatError::bad_request("Invalid theme"));
                }
            }
            "communication_style" => {
                if value
                    .as_str()
                    .is_none_or(|style| !matches!(style, "default" | "human_like" | "auto"))
                {
                    return Err(ChatError::bad_request("Invalid communication style"));
                }
            }
            "conversation_continuity" => {
                if value
                    .as_str()
                    .is_none_or(|level| !matches!(level, "low" | "medium" | "high"))
                {
                    return Err(ChatError::bad_request("Invalid conversation continuity"));
                }
            }
            "considerations" => {
                if value.as_array().is_none_or(|items| {
                    items.len() > 10
                        || items.iter().any(|item| {
                            item.as_str()
                                .is_none_or(|text| text.chars().count() > 2_000)
                        })
                }) {
                    return Err(ChatError::bad_request("Invalid considerations"));
                }
            }
            "self_identify" | "user_name" | "profession" | "tone" => {
                if value.as_str().is_none() || value.as_str().is_some_and(|text| text.chars().count() > 2_000) {
                    return Err(ChatError::bad_request(format!("Invalid {key}")));
                }
            }
            _ => {}
        }
    }
    Ok(())
}

fn normalized(key: &str, value: &Value) -> Value {
    match key {
        "chat_default_model" => json!(value.as_str().unwrap_or_default().trim()),
        _ => value.clone(),
    }
}

pub(super) fn patch(
    mode: &ChatMode,
    patch: &Map<String, Value>,
) -> Result<Value, ChatError> {
    validate_patch(mode, patch)?;
    let Some(personalization) = mode.personalization.as_ref() else {
        return Err(ChatError::unavailable(
            "Chat personalization state is unavailable",
        ));
    };
    let normalized: Map<String, Value> = patch
        .iter()
        .map(|(key, value)| (key.clone(), normalized(key, value)))
        .collect();
    personalization
        .chat_patch(&normalized)
        .map_err(|error| ChatError::storage(format!("Chat preferences are unavailable: {error}")))?;
    payload(mode)
}

/// Fill only missing new-conversation fields from account defaults. Existing
/// conversations keep their own metadata, and explicit request values always win.
pub(super) fn apply_new_conversation_defaults(
    mode: &ChatMode,
    payload: &Value,
) -> Result<Value, ChatError> {
    let mut payload = payload.clone();
    let requested = payload["conversation_id"]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if requested.is_some() {
        return Ok(payload);
    }
    let preferences = load_preferences(mode)?;
    let model = preferences["chat_default_model"]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if payload["model_key"]
        .as_str()
        .is_none_or(|value| value.trim().is_empty())
        && let Some(model) = model
    {
        payload["model_key"] = json!(model);
    }
    if payload["thinking_mode"].as_bool().is_none() {
        payload["thinking_mode"] = preferences["chat_default_thinking_mode"]
            .as_bool()
            .map(Value::Bool)
            .unwrap_or(Value::Bool(true));
    }
    if !payload["settings"].is_object() {
        payload["settings"] = json!({});
    }
    if let Some(settings) = payload["settings"].as_object_mut() {
        if !settings.contains_key("network_enabled") {
            settings.insert(
                "network_enabled".into(),
                preferences["chat_network_enabled"]
                    .as_bool()
                    .map(Value::Bool)
                    .unwrap_or(Value::Bool(true)),
            );
        }
        if !settings.contains_key("reasoning_effort") {
            settings.insert(
                "reasoning_effort".into(),
                preferences["chat_default_reasoning_effort"].clone(),
            );
        }
    }
    Ok(payload)
}
