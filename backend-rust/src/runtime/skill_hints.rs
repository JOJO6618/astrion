//! Optional keyword hints are per user turn, never part of the frozen prompt.

use std::{path::Path, sync::OnceLock};

use serde_json::{Value, json};

const MARKER: &str = "[系统自动插入的提示信息] ";
static RULES: OnceLock<Value> = OnceLock::new();

pub(super) fn for_message(data: &Path, message: &str) -> Option<Value> {
    let prefs = super::store::read_json(&data.join("personalization.json")).ok()??;
    if prefs["skill_hints_enabled"] != true {
        return None;
    }
    let rules = RULES.get_or_init(|| {
        serde_json::from_str(include_str!("../../../config/skill_hints.json"))
            .expect("bundled skill hint rules must be valid JSON")
    });
    let hint = combined_hint(rules, message)?;
    Some(json!({"role":"system","content":hint,"metadata":{
        "visibility":"hidden","message_source":"skill_hints","starts_work":false
    }}))
}

fn combined_hint(rules: &Value, message: &str) -> Option<String> {
    let message = message.to_lowercase();
    let matched: Vec<_> = rules.as_object()?.values().filter_map(|rule| {
        let has_match = rule["keywords"].as_array()?.iter().filter_map(Value::as_str)
            .any(|key| !key.is_empty() && message.contains(&key.to_lowercase()));
        let hint = rule["hint"].as_str()?;
        let hint = hint.strip_prefix(MARKER).unwrap_or(hint).trim();
        (has_match && !hint.is_empty()).then_some(hint)
    }).collect();
    (!matched.is_empty()).then(|| format!("{MARKER}{}", matched.join("\n")))
}

#[cfg(test)]
#[path = "../../../test/2026-11-27_RustAgent设置生效/skill_hints_contract.rs"]
mod contract;
