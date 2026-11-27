use std::{collections::BTreeSet, io, path::Path, sync::Mutex};

use cap_std::fs::Dir;
use serde_json::{Map, Value, json};

use crate::{
    catalog::Catalog, web_assets::personalization::default_admin_tool_categories,
    web_status::admin_policy,
};

use super::{AdminError, Result, store};

const FILENAME: &str = "admin_policy.json";
const UI_BLOCK_KEYS: &[&str] = &[
    "collapse_workspace",
    "block_file_manager",
    "block_personal_space",
    "block_upload",
    "block_conversation_review",
    "block_tool_toggle",
    "block_realtime_terminal",
    "block_focus_panel",
    "block_token_panel",
    "block_compress_conversation",
    "block_virtual_monitor",
];

#[derive(Default)]
pub(super) struct Policy {
    updates: Mutex<()>,
}

impl Policy {
    pub(super) fn effective(&self, data: &Path, catalog: &Catalog) -> Result<Value> {
        let _lock = self.updates.lock().map_err(AdminError::lock)?;
        let dir = policy_dir(data)?;
        let policy = load(&dir)?;
        let mut effective = admin_policy(data, catalog)?;
        effective
            .as_object_mut()
            .expect("policy object")
            .shift_remove("version");
        effective["updated_at"] = policy["updated_at"].clone();
        effective["categories"] = Value::Object(effective_categories(data, &policy));
        Ok(json!({"success": true, "data": effective}))
    }

    pub(super) fn describe(&self, data: &Path, catalog: &Catalog) -> Result<Value> {
        let _lock = self.updates.lock().map_err(AdminError::lock)?;
        let policy = load(&policy_dir(data)?)?;
        let mut models = catalog.registered_model_keys();
        models.sort();
        models.dedup();
        Ok(json!({"success": true, "data": policy, "defaults": {
            "models": models, "categories": default_admin_tool_categories(data), "ui_block_keys": UI_BLOCK_KEYS
        }}))
    }

    pub(super) fn save(&self, data: &Path, catalog: &Catalog, payload: &Value) -> Result<Value> {
        let target = match payload["target_type"].as_str() {
            Some("global") => None,
            Some("role") => Some("roles"),
            Some("user") => Some("users"),
            Some("invite") => Some("invites"),
            _ => return Err(AdminError::bad("invalid target_type")),
        };
        let target_value = match &payload["target_value"] {
            Value::Null => "",
            Value::String(target_value) => target_value,
            _ => return Err(AdminError::bad("invalid target_value")),
        };
        let config = normalized(&payload["config"], catalog)?;
        let _lock = self.updates.lock().map_err(AdminError::lock)?;
        let dir = policy_dir(data)?;
        let mut policy = load(&dir)?;
        if let Some(target) = target {
            let scopes = policy[target]
                .as_object_mut()
                .ok_or_else(AdminError::invalid_policy)?;
            scopes.insert(target_value.into(), config);
        } else {
            policy["global"] = config;
        }
        policy["updated_at"] = json!(timestamp());
        save(&dir, &policy)?;
        Ok(json!({"success": true, "data": policy}))
    }
}

fn policy_dir(data: &Path) -> io::Result<Dir> {
    store::directory(data, true)?.ok_or_else(|| io::Error::other("Policy directory unavailable"))
}

fn blank() -> Value {
    json!({"category_overrides": {}, "remove_categories": [], "forced_category_states": {}, "disabled_models": [], "ui_blocks": {}})
}

fn timestamp() -> String {
    let now = time::OffsetDateTime::now_local().unwrap_or_else(|_| time::OffsetDateTime::now_utc());
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second()
    )
}

fn load(dir: &Dir) -> Result<Value> {
    let existing = store::read_object(dir, FILENAME)?;
    let missing = existing.is_none();
    let mut policy = existing.unwrap_or_else(|| json!({}));
    let fields = policy.as_object_mut().expect("policy object");
    fields
        .entry("updated_at")
        .or_insert_with(|| json!(timestamp()));
    fields.entry("global").or_insert_with(blank);
    for name in ["roles", "users", "invites"] {
        fields.entry(name).or_insert_with(|| json!({}));
    }
    if missing {
        save(dir, &policy)?;
    }
    Ok(policy)
}

fn save(dir: &Dir, policy: &Value) -> io::Result<()> {
    store::write(dir, FILENAME, &store::pretty(policy)?)
}

fn normalized(config: &Value, catalog: &Catalog) -> Result<Value> {
    if !config.is_null() && !config.is_object() {
        return Err(AdminError::bad("invalid config"));
    }
    let mut normalized = blank();
    for key in ["category_overrides", "forced_category_states", "ui_blocks"] {
        if !config[key].is_null() {
            if !config[key].is_object() {
                return Err(AdminError::bad("invalid policy map"));
            }
            normalized[key] = config[key].clone();
        }
    }
    let models: BTreeSet<_> = catalog.registered_model_keys().into_iter().collect();
    for key in ["remove_categories", "disabled_models"] {
        if !config[key].is_null() {
            let array = config[key]
                .as_array()
                .ok_or_else(|| AdminError::bad("invalid policy list"))?;
            let items: BTreeSet<_> = array
                .iter()
                .filter_map(Value::as_str)
                .filter(|name| key != "disabled_models" || models.contains(*name))
                .collect();
            normalized[key] = json!(items);
        }
    }
    Ok(normalized)
}

fn effective_categories(data: &Path, policy: &Value) -> Map<String, Value> {
    let base = default_admin_tool_categories(data);
    let mut categories = base.clone();
    let mut remove = BTreeSet::new();
    let mut overrides = Map::new();
    for scope in [
        &policy["global"],
        &policy["roles"]["admin"],
        &policy["invites"][""],
        &policy["users"]["host"],
    ] {
        if let Some(names) = scope["remove_categories"].as_array() {
            remove.extend(names.iter().filter_map(Value::as_str));
        }
        if let Some(fields) = scope["category_overrides"].as_object() {
            overrides.extend(fields.clone());
        }
    }
    categories.retain(|id, _| !remove.contains(id.as_str()));
    for (id, value) in overrides {
        if id == "custom" || !value.is_object() || !value["tools"].is_null() && !value["tools"].is_array() {
            continue;
        }
        let tools: Vec<_> = value["tools"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect();
        let label = store::nonempty(&value["label"]).unwrap_or(&id);
        categories.insert(
            id.clone(),
            json!({"label": label, "tools": tools,
                "default_enabled": value.get("default_enabled").is_none_or(store::truthy),
                "silent_when_disabled": store::truthy(&value["silent_when_disabled"])
            }),
        );
    }
    let mcp = categories
        .entry("mcp")
        .or_insert_with(|| base["mcp"].clone());
    mcp["tools"] = json!(["list_mcp_servers"]);
    categories.retain(|id, _| !id.starts_with("mcp_server__") || base.contains_key(id));
    for (id, mut category) in base
        .into_iter()
        .filter(|(id, _)| id.starts_with("mcp_server__"))
    {
        if let Some(override_) = categories.get(&id) {
            for key in ["default_enabled", "silent_when_disabled"] {
                if let Some(value) = override_.get(key) {
                    category[key] = value.clone();
                }
            }
        }
        categories.insert(id, category);
    }
    categories
}
