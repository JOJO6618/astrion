use crate::catalog::Catalog;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use std::{collections::HashSet, sync::OnceLock};

pub(super) type Object = Map<String, Value>;

#[derive(Deserialize)]
pub(super) struct Schema {
    pub defaults: Object,
    pub tool_categories: Object,
    pub tool_loading_registry: Vec<Value>,
}

pub(super) fn schema() -> &'static Schema {
    static SCHEMA: OnceLock<Schema> = OnceLock::new();
    SCHEMA.get_or_init(|| {
        serde_json::from_str(include_str!("schema.json")).expect("valid Host preference schema")
    })
}

const EFFORTS: &[&str] = &["low", "medium", "high", "xhigh", "max"];

pub(super) fn normalize(
    input: &Object,
    prior: &Object,
    catalog: &Catalog,
    validating_update: bool,
) -> Result<Object, &'static str> {
    let defaults = &schema().defaults;
    let value = |key: &str| {
        input
            .get(key)
            .or_else(|| prior.get(key))
            .unwrap_or(&defaults[key])
    };
    let mut config = defaults.clone();
    for (key, default) in defaults {
        let raw = value(key);
        config[key] = match key.as_str() {
            "full_access_warning_dismissed"
            | "allow_auto_approve_full_access"
            | "require_read_before_edit" | "git_worktree_enabled" => json!(raw == &Value::Bool(true)),
            "chat_default_thinking_mode" | "chat_network_enabled" if raw.is_boolean() => {
                raw.clone()
            }
            "chat_default_thinking_mode" if validating_update => {
                return Err("chat_default_thinking_mode must be a boolean");
            }
            "chat_network_enabled" if validating_update => {
                return Err("chat_network_enabled must be a boolean");
            }
            "chat_default_thinking_mode" | "chat_network_enabled" => default.clone(),
            _ if default.is_boolean() => json!(truthy(raw)),
            "self_identify" | "user_name" | "profession" | "tone" => json!(text(raw, 20)),
            "title_model"
            | "thinking_summary_model"
            | "sub_agent_model_high"
            | "sub_agent_model_medium"
            | "sub_agent_model_low" => json!(text(raw, usize::MAX)),
            "tavily_api_key" | "bocha_api_key" | "exa_api_key" | "parallel_api_key"
            | "jina_api_key" | "searxng_base_url" => json!(text(raw, 512)),
            "browser_download_dir" => {
                let raw = raw.as_str().unwrap_or_default().trim();
                json!(if raw.contains('\0') || raw.chars().count() > 1024 {
                    ""
                } else {
                    raw
                })
            }
            "considerations" => json!(considerations(raw)),
            "default_run_mode" => match strtolower(raw).as_str() {
                "deep" | "thinking" => json!("thinking"),
                "fast" => json!("fast"),
                _ => Value::Null,
            },
            "default_reasoning_effort" => json!(choice(&strtolower(raw), EFFORTS)),
            "default_work_mode" => choice(&strtolower(raw), &["plan", "ask", "execute"])
                .map(|value| json!(value))
                .unwrap_or_else(|| prior.get(key).unwrap_or(default).clone()),
            "default_permission_mode"
            | "image_compression"
            | "theme"
            | "ui_locale"
            | "goal_review_mode" => {
                let allowed: &[&str] = match key.as_str() {
                    "default_permission_mode" => {
                        &["readonly", "approval", "auto_approval", "unrestricted"]
                    }
                    "image_compression" => &["original", "1080p", "720p", "540p"],
                    "theme" => &["classic", "light", "dark"],
                    "ui_locale" => &["zh-CN", "en-US"],
                    _ => &["readonly", "active"],
                };
                choice(raw.as_str().unwrap_or_default(), allowed)
                    .map(|value| json!(value))
                    .unwrap_or_else(|| prior.get(key).unwrap_or(default).clone())
            }
            "communication_style" => exact(raw, &["default", "human_like", "auto"], default),
            "conversation_continuity" | "morality_level" => {
                exact(raw, &["low", "medium", "high"], default)
            }
            "adult_content_restriction" => exact(raw, &["none", "low", "medium", "high"], default),
            "send_shortcut" => exact(raw, &["mod_enter", "enter"], default),
            "display_currency" => exact(raw, &["USD", "CNY"], default),
            "subscription_quota_source" => exact(raw, &["auto", "opencode", "chatgpt"], default),
            "compact_message_display" => exact(
                &json!(strtolower(raw)),
                &["full", "brief", "hidden"],
                default,
            ),
            "block_display_mode" => exact(
                &json!(strtolower(raw)),
                &["traditional", "stacked", "minimal"],
                default,
            ),
            "versioning_backup_mode" => {
                exact(&json!(strtolower(raw)), &["shallow", "full"], default)
            }
            "versioning_restore_mode" => default.clone(),
            "search_provider" => exact(
                &json!(strtolower(raw)),
                &["tavily", "bocha", "exa", "parallel", "searxng"],
                default,
            ),
            "webpage_extract_provider" => exact(
                &json!(strtolower(raw)),
                &["jina", "tavily", "exa", "parallel"],
                default,
            ),
            "webpage_direct_extract_domains" => json!(domains(raw)),
            "thinking_summary_interval_seconds" => bounded(raw, 1, 3600, default),
            "recent_conversations_prompt_limit" => bounded(raw, 1, 30, default),
            "goal_max_turns" => bounded(raw, 1, 100, default),
            "goal_max_tokens" => bounded(raw, 1_000, 100_000_000, default),
            "shallow_compress_trigger_tokens" | "deep_compress_trigger_tokens" => {
                bounded(raw, 1_000, 2_000_000, default)
            }
            "shallow_compress_keep_recent_tools" => bounded(raw, 0, 500, default),
            "shallow_compress_keep_user_turn_tools" => bounded(raw, 0, 50, default),
            "shallow_compress_max_replace_per_round"
            | "shallow_compress_trigger_tool_calls_interval" => bounded(raw, 1, 200, default),
            "sub_agent_compress_threshold_tokens" => bounded(raw, 10_000, 10_000_000, default),
            "sub_agent_max_turns" => integer(raw)
                .filter(|v| *v >= 0)
                .map(|v| json!(v.min(100_000)))
                .unwrap_or_default(),
            "sub_agent_max_instances" => raw
                .as_i64()
                .filter(|value| (1..=9_007_199_254_740_991).contains(value))
                .map(|v| json!(v))
                .unwrap_or_else(|| default.clone()),
            "project_memory_inject_limit" if raw.is_null() || raw.as_str() == Some("") => {
                Value::Null
            }
            "project_memory_inject_limit" => integer(raw)
                .map(|i| {
                    if i <= 0 {
                        Value::Null
                    } else {
                        json!(i.clamp(5, 100_000))
                    }
                })
                .unwrap_or_else(|| default.clone()),
            "disabled_tool_categories" => json!(
                strings(raw, usize::MAX)
                    .into_iter()
                    .filter(|name| schema().tool_categories.contains_key(*name)
                        || *name == "custom"
                        || name.starts_with("mcp_server__"))
                    .collect::<Vec<_>>()
            ),
            "enabled_skills" | "skills_catalog_snapshot" if raw.is_null() => Value::Null,
            "enabled_skills" | "skills_catalog_snapshot" => json!(strings(raw, usize::MAX)),
            "hidden_models" => json!(strings(raw, 500)),
            "sidebar_pinned_workspaces" => json!(strings(raw, 100)),
            "sidebar_workspace_order" => json!(strings(raw, 200)),
            "tool_loading_deferred" => json!(deferred_tools(raw)),
            "goal_end_conditions" => json!(goal_end_conditions(raw)),
            "review_agents" => review_agents(raw, default),
            "default_model" => raw
                .as_str()
                .filter(|model| !model.is_empty())
                .and_then(|model| catalog.resolve_key(Some(model), true).ok())
                .or_else(|| {
                    prior
                        .get(key)
                        .and_then(Value::as_str)
                        .filter(|model| catalog.resolve_key(Some(model), true).is_ok())
                })
                .or_else(|| catalog.resolve_key(None, true).ok())
                .map_or(Value::Null, |key| json!(key)),
            "chat_default_model" => {
                let model = raw.as_str().unwrap_or_default().trim();
                if validating_update
                    && !model.is_empty()
                    && prior.get(key).and_then(Value::as_str) != Some(model)
                    && catalog.resolve_key(Some(model), true).is_err()
                {
                    return Err("Unknown Chat default model");
                }
                json!(model)
            }
            "chat_default_reasoning_effort"
                if raw.is_null() || raw.as_str().is_some_and(|value| EFFORTS.contains(&value)) =>
            {
                raw.clone()
            }
            "chat_default_reasoning_effort" if validating_update => {
                return Err("Invalid Chat reasoning effort");
            }
            "chat_instructions"
                if raw
                    .as_str()
                    .is_some_and(|value| value.chars().count() <= 20_000) =>
            {
                raw.clone()
            }
            "chat_instructions" if validating_update => {
                return Err("Chat instructions must be at most 20000 characters");
            }
            "chat_deep_compress_trigger_tokens"
                if raw
                    .as_i64()
                    .is_some_and(|value| (1_000..=2_000_000).contains(&value)) =>
            {
                raw.clone()
            }
            "chat_deep_compress_trigger_tokens" if validating_update => {
                return Err(
                    "Chat deep compression context must be an integer from 1000 to 2000000",
                );
            }
            _ => default.clone(),
        };
    }
    if config["thinking_summary_model"] == "" {
        config["thinking_summary_enabled"] = json!(false);
    }
    if !input.contains_key("sub_agent_model_low")
        && config["sub_agent_model_low"] == ""
        && let Some(legacy) = input.get("sub_agent_model")
    {
        config["sub_agent_model_low"] = json!(text(legacy, usize::MAX));
    }
    Ok(config)
}

fn choice<'a>(raw: &'a str, allowed: &[&str]) -> Option<&'a str> {
    allowed.contains(&raw).then_some(raw)
}

fn exact(raw: &Value, allowed: &[&str], default: &Value) -> Value {
    if raw.as_str().is_some_and(|text| allowed.contains(&text)) {
        raw.clone()
    } else {
        default.clone()
    }
}

fn strtolower(raw: &Value) -> String {
    raw.as_str().unwrap_or_default().trim().to_ascii_lowercase()
}

fn text(raw: &Value, limit: usize) -> String {
    raw.as_str()
        .unwrap_or_default()
        .trim()
        .chars()
        .take(limit)
        .collect()
}

pub(super) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64().is_some_and(|n| n != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn integer(raw: &Value) -> Option<i64> {
    raw.as_i64().or_else(|| match raw {
        Value::Number(n) => n.as_f64().map(|value| value as i64),
        Value::String(text) => text.trim().parse().ok(),
        _ => None,
    })
}

fn bounded(raw: &Value, min: i64, max: i64, default: &Value) -> Value {
    integer(raw)
        .map(|value| json!(value.clamp(min, max)))
        .unwrap_or_else(|| default.clone())
}

pub(super) fn strings(value: &Value, limit: usize) -> Vec<&str> {
    let mut seen = HashSet::new();
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|name| !name.is_empty() && seen.insert(*name))
        .take(limit)
        .collect()
}

fn considerations(raw: &Value) -> String {
    match raw {
        Value::String(text) => text.chars().take(2000).collect(),
        Value::Array(items) => items
            .iter()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .take(10)
            .collect::<Vec<_>>()
            .join("\n")
            .chars()
            .take(2000)
            .collect(),
        _ => String::new(),
    }
}

fn domains(raw: &Value) -> Vec<String> {
    let mut seen = HashSet::new();
    raw.as_array()
        .into_iter()
        .flatten()
        .take(50)
        .filter_map(Value::as_str)
        .filter_map(|domain| {
            let lower = domain.trim().to_ascii_lowercase();
            let trimmed = lower.trim_matches('.');
            let host = trimmed
                .split_once("//")
                .map_or(trimmed, |(_, host)| host)
                .split(['/', '?'])
                .next()?;
            (host.contains('.')
                && host
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b".-".contains(&byte))
                && seen.insert(host.to_owned()))
            .then(|| host.to_owned())
        })
        .collect()
}

fn review_agents(raw: &Value, default: &Value) -> Value {
    let mut review = default.clone();
    for key in ["auto_approval", "goal_review", "workflow_review"] {
        review[key] = json!({"model": text(&raw[key]["model"], 100), "max_rounds": bounded(&raw[key]["max_rounds"], 1, 50, &default[key]["max_rounds"])});
    }
    review
}

pub(super) fn deferred_tools(raw: &Value) -> Vec<&'static str> {
    let requested = strings(raw, usize::MAX);
    schema()
        .tool_loading_registry
        .iter()
        .flat_map(|category| strings(&category["tools"], usize::MAX))
        .filter(|name| !raw.is_array() || requested.contains(name))
        .collect()
}

fn goal_end_conditions(raw: &Value) -> Vec<&str> {
    let mut conditions: Vec<_> = strings(raw, usize::MAX)
        .into_iter()
        .filter(|value| matches!(*value, "max_turns" | "max_tokens"))
        .collect();
    if !conditions.contains(&"max_turns") {
        conditions.insert(0, "max_turns");
    }
    conditions
}

pub(super) fn compression_triggers(config: &Object) -> (i64, i64) {
    (
        config["shallow_compress_trigger_tokens"]
            .as_i64()
            .unwrap_or(80_000),
        config["deep_compress_trigger_tokens"]
            .as_i64()
            .unwrap_or(250_000),
    )
}

pub(super) fn resolved_deep_trigger(config: &Object) -> i64 {
    let (shallow, deep) = compression_triggers(config);
    deep.max(shallow + 1)
}

pub(super) fn compression(config: &Object, catalog: &Catalog) -> Value {
    let shallow = compression_triggers(config).0;
    let actual = resolved_deep_trigger(config);
    let models = catalog.models_response();
    let model_window = config["default_model"].as_str().and_then(|key| {
        models["items"]
            .as_array()?
            .iter()
            .find(|item| item["model_key"].as_str() == Some(key))?["context_window"]
            .as_i64()
    });
    json!({
        "shallow_trigger_tokens": shallow,
        "shallow_keep_recent_tools": config["shallow_compress_keep_recent_tools"].as_i64().unwrap_or(15),
        "shallow_keep_user_turn_tools": config["shallow_compress_keep_user_turn_tools"].as_i64().unwrap_or(3),
        "shallow_max_replace_per_round": config["shallow_compress_max_replace_per_round"].as_i64().unwrap_or(10),
        "shallow_trigger_tool_calls_interval": config["shallow_compress_trigger_tool_calls_interval"].as_i64().unwrap_or(10),
        "deep_trigger_tokens": actual,
        "context_window_tokens": model_window.unwrap_or(actual).min(actual)
    })
}
