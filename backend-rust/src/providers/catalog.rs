//! Provider catalog rows: static definitions plus one row per connection.

use std::{collections::HashMap, fs, path::Path};

use serde_json::{Map, Value, json};

use super::{codex, modelsdev::Registry, store::Data};

const CODEX_CATALOG_ID: &str = "openai-codex";

pub(super) fn entries(repo_config: &Path) -> Vec<Value> {
    let raw: Value = fs::read(repo_config.join("providers_catalog.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or(Value::Null);
    raw["providers"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|entry| entry.is_object() && entry["id"].as_str().is_some_and(|id| !id.is_empty()))
        .cloned()
        .collect()
}

pub(super) fn names(entries: &[Value]) -> HashMap<String, String> {
    entries
        .iter()
        .filter_map(|entry| {
            let id = entry["id"].as_str()?;
            let name = entry["name"]
                .as_str()
                .filter(|name| !name.is_empty())
                .unwrap_or(id);
            Some((id.to_owned(), name.to_owned()))
        })
        .collect()
}

pub(super) fn find<'a>(entries: &'a [Value], id: &str) -> Option<&'a Value> {
    entries
        .iter()
        .find(|entry| entry["id"].as_str() == Some(id))
}

pub(super) fn ids(entries: &[Value]) -> std::collections::HashSet<String> {
    entries
        .iter()
        .filter_map(|entry| entry["id"].as_str().map(str::to_owned))
        .collect()
}

/// Menu rows followed by every connection row. The frontend classifies rows by
/// `entry_kind`, not by `connected`.
pub(super) fn rows(
    data: &Data,
    entries: &[Value],
    accounts: &[codex::Account],
    registry: &Registry,
) -> Vec<Value> {
    let records = data.records();
    let mut kinds: HashMap<&str, usize> = HashMap::new();
    for record in records.values() {
        let catalog_id = record["catalog_id"].as_str().unwrap_or_default();
        *kinds.entry(catalog_id).or_default() += 1;
    }
    let mut output = Vec::with_capacity(entries.len() + records.len() + accounts.len());
    for entry in entries {
        let mut item = entry.as_object().cloned().unwrap_or_default();
        let icon = item.get("icon").and_then(Value::as_str).map(str::to_owned);
        item.insert(
            "icon_url".into(),
            icon.as_deref()
                .map(|icon| json!(format!("/static/icons/providers/{icon}")))
                .unwrap_or(Value::Null),
        );
        item.insert("catalog_id".into(), entry["id"].clone());
        item.insert("kind".into(), json!("catalog"));
        item.insert("entry_kind".into(), json!("catalog"));
        item.insert("connection".into(), json!(false));
        item.insert("connected".into(), json!(false));
        let catalog_id = entry["id"].as_str().unwrap_or_default();
        let (has, count) = if entry["auth"].as_str() == Some("codex_oauth") {
            (!accounts.is_empty(), accounts.len())
        } else {
            let count = kinds.get(catalog_id).copied().unwrap_or(0);
            (count > 0, count)
        };
        item.insert("has_connections".into(), json!(has));
        item.insert("connection_count".into(), json!(count));
        item.insert(
            "can_add".into(),
            json!(entry["auth"].as_str() != Some("none") || !has),
        );
        output.push(Value::Object(item));
    }

    for (provider_id, record) in records {
        if !record.get("enabled").map(is_truthy).unwrap_or(true) {
            continue;
        }
        let catalog_id = record["catalog_id"].as_str().unwrap_or_default();
        let entry = find(entries, catalog_id);
        let summary = summary(provider_id, record);
        let mut item = Map::new();
        item.insert("id".into(), json!(provider_id));
        item.insert(
            "catalog_id".into(),
            if catalog_id.is_empty() {
                Value::Null
            } else {
                json!(catalog_id)
            },
        );
        item.insert(
            "requested_id".into(),
            json!(requested_id(provider_id, record)),
        );
        item.insert("kind".into(), json!("connection"));
        item.insert("entry_kind".into(), json!("connection"));
        item.insert("connection".into(), json!(true));
        item.insert("name".into(), summary["name"].clone());
        item.insert(
            "base_name".into(),
            record.get("base_name").cloned().unwrap_or(Value::Null),
        );
        item.insert(
            "display_index".into(),
            json!(record["display_index"].as_i64().unwrap_or_default()),
        );
        item.insert("base_url".into(), summary["base_url"].clone());
        let icon = entry
            .and_then(|entry| entry["icon"].as_str())
            .filter(|icon| !icon.is_empty());
        item.insert(
            "icon".into(),
            icon.map(|icon| json!(icon)).unwrap_or(Value::Null),
        );
        item.insert(
            "icon_url".into(),
            icon.map(|icon| json!(format!("/static/icons/providers/{icon}")))
                .unwrap_or(Value::Null),
        );
        item.insert(
            "icon_mono".into(),
            json!(
                entry
                    .map(|entry| is_truthy(&entry["icon_mono"]))
                    .unwrap_or(false)
            ),
        );
        item.insert(
            "auth".into(),
            json!(if record["api_key"].as_str() == Some("not-required") {
                "none"
            } else {
                "api_key"
            }),
        );
        item.insert(
            "key_url".into(),
            entry
                .map(|entry| entry["key_url"].clone())
                .unwrap_or(Value::Null),
        );
        item.insert(
            "badge".into(),
            entry
                .map(|entry| entry["badge"].clone())
                .unwrap_or(Value::Null),
        );
        item.insert(
            "local".into(),
            json!(
                entry
                    .map(|entry| is_truthy(&entry["local"]))
                    .unwrap_or(false)
            ),
        );
        item.insert(
            "protocol_note".into(),
            entry
                .map(|entry| entry["protocol_note"].clone())
                .unwrap_or(Value::Null),
        );
        item.insert("custom".into(), json!(entry.is_none()));
        item.insert("connected".into(), json!(true));
        item.insert("state".into(), json!("ready"));
        item.insert("credential_missing".into(), json!(false));
        item.insert("models_count".into(), json!(model_count(record)));
        item.insert(
            "models_fetched_at".into(),
            summary["models_fetched_at"].clone(),
        );
        item.insert("models_error".into(), summary["models_error"].clone());
        item.insert("api_key_masked".into(), summary["api_key_masked"].clone());
        item.insert(
            "has_custom_headers".into(),
            summary["has_custom_headers"].clone(),
        );
        if let Some(entry) = entry {
            let models = summary["models"].as_array().cloned().unwrap_or_default();
            let unsupported = registry.unsupported_count(entry, &models);
            if unsupported > 0 {
                item.insert("unsupported_protocol_count".into(), json!(unsupported));
            }
        }
        output.push(Value::Object(item));
    }

    let codex_entry = find(entries, CODEX_CATALOG_ID);
    for account in accounts {
        let mut item = Map::new();
        item.insert("id".into(), json!(&account.id));
        item.insert("catalog_id".into(), json!(CODEX_CATALOG_ID));
        item.insert("kind".into(), json!("connection"));
        item.insert("entry_kind".into(), json!("connection"));
        item.insert("connection".into(), json!(true));
        item.insert("name".into(), json!(&account.display_name));
        item.insert("account_hash".into(), json!(&account.hash));
        item.insert(
            "base_url".into(),
            json!(
                codex_entry
                    .and_then(|entry| entry["base_url"].as_str())
                    .unwrap_or_default()
            ),
        );
        let icon = codex_entry
            .and_then(|entry| entry["icon"].as_str())
            .filter(|icon| !icon.is_empty());
        item.insert(
            "icon".into(),
            icon.map(|icon| json!(icon)).unwrap_or(Value::Null),
        );
        item.insert(
            "icon_url".into(),
            icon.map(|icon| json!(format!("/static/icons/providers/{icon}")))
                .unwrap_or(Value::Null),
        );
        item.insert(
            "icon_mono".into(),
            json!(
                codex_entry
                    .map(|entry| is_truthy(&entry["icon_mono"]))
                    .unwrap_or(false)
            ),
        );
        item.insert("auth".into(), json!("codex_oauth"));
        item.insert(
            "badge".into(),
            codex_entry
                .map(|entry| entry["badge"].clone())
                .unwrap_or(Value::Null),
        );
        item.insert("protocol_note".into(), Value::Null);
        item.insert("custom".into(), json!(false));
        item.insert("connected".into(), json!(account.connected));
        item.insert("state".into(), json!(account.state));
        item.insert(
            "credential_missing".into(),
            json!(account.credential_missing),
        );
        item.insert("models_count".into(), json!(account.models_count));
        item.insert(
            "models_fetched_at".into(),
            account
                .models_fetched_at
                .as_deref()
                .map(|value| json!(value))
                .unwrap_or(Value::Null),
        );
        item.insert("models_error".into(), Value::Null);
        output.push(Value::Object(item));
    }
    output
}

/// Public summary used by list, connect and refresh responses. Sensitive
/// fields (`api_key`, `headers`) never leave the store.
pub(super) fn summary(provider_id: &str, record: &Value) -> Value {
    json!({
        "id": provider_id,
        "catalog_id": record["catalog_id"].clone(),
        "requested_id": requested_id(provider_id, record),
        "name": record["name"].clone(),
        "base_name": record["base_name"].clone(),
        "display_index": record["display_index"].as_i64().unwrap_or_default(),
        "base_url": record["base_url"].clone(),
        "api_key_masked": mask_key(record["api_key"].as_str()),
        "has_custom_headers": record
            .get("headers")
            .and_then(Value::as_object)
            .is_some_and(|headers| !headers.is_empty()),
        "enabled": record.get("enabled").map(is_truthy).unwrap_or(true),
        "connected_at": record["connected_at"].clone(),
        "models": record.get("models").cloned().unwrap_or_else(|| json!([])),
        "models_fetched_at": record["models_fetched_at"].clone(),
        "models_error": record["models_error"].clone(),
    })
}

pub(super) fn requested_id(provider_id: &str, record: &Value) -> String {
    let requested = record["requested_id"].as_str().unwrap_or_default().trim();
    if requested.is_empty() {
        record["catalog_id"]
            .as_str()
            .filter(|value| !value.is_empty())
            .unwrap_or(provider_id)
            .to_owned()
    } else {
        requested.to_owned()
    }
}

pub(super) fn model_count(record: &Value) -> usize {
    record["models"]
        .as_array()
        .map(Vec::len)
        .unwrap_or_default()
}

pub(super) fn mask_key(api_key: Option<&str>) -> Option<String> {
    let api_key = api_key?;
    if api_key == "not-required" {
        return None;
    }
    let characters: Vec<char> = api_key.chars().collect();
    if characters.len() <= 8 {
        return Some(if characters.len() > 2 {
            format!("{}...", characters[..2].iter().collect::<String>())
        } else {
            "***".into()
        });
    }
    Some(format!(
        "{}...{}",
        characters[..5].iter().collect::<String>(),
        characters[characters.len() - 4..]
            .iter()
            .collect::<String>()
    ))
}

fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}
