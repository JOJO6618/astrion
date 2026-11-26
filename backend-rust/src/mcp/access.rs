use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

use serde_json::Value;

use super::registry::{Server, category};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Actor {
    Main,
    Sub,
}

impl Actor {
    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "main" => Some(Self::Main),
            "sub" => Some(Self::Sub),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Main => "main",
            Self::Sub => "sub",
        }
    }
}

pub(super) fn defaults(access: &Value, actor: Actor) -> Option<&Vec<Value>> {
    access["defaults"][actor.name()].as_array()
}

pub(super) fn workspace<'a>(access: &'a Value, id: &str, actor: Actor) -> Option<&'a Value> {
    let selection = &access["workspaces"][id][actor.name()];
    selection["inherit"].as_bool()?;
    selection["server_ids"].as_array()?;
    Some(selection)
}

#[derive(Default)]
pub(super) struct LegacyPolicy {
    defaults: BTreeMap<String, bool>,
    forced: BTreeMap<String, bool>,
    disabled: BTreeSet<String>,
}

impl LegacyPolicy {
    pub fn load(data: &Path) -> Self {
        let read = |name: &str| {
            fs::read(data.join(name))
                .ok()
                .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
                .unwrap_or_default()
        };
        let config = read("personalization.json");
        let policy = read("admin_policy.json");
        let mut defaults = BTreeMap::new();
        let mut forced = BTreeMap::new();
        let mut removed = BTreeSet::new();
        for scope in [
            &policy["global"],
            &policy["roles"]["admin"],
            &policy["invites"][""],
            &policy["users"]["host"],
        ] {
            removed.extend(
                scope["remove_categories"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str),
            );
            defaults.extend(
                scope["category_overrides"]
                    .as_object()
                    .into_iter()
                    .flatten()
                    .filter(|(_, value)| {
                        value.is_object()
                            && value
                                .get("tools")
                                .is_none_or(|v| v.is_array() || v.is_null())
                    })
                    .map(|(key, value)| {
                        (
                            key.clone(),
                            value["default_enabled"].as_bool().unwrap_or(true),
                        )
                    }),
            );
            forced.extend(
                scope["forced_category_states"]
                    .as_object()
                    .into_iter()
                    .flatten()
                    .map(|(key, value)| (key.clone(), value.as_bool())),
            );
        }
        defaults.retain(|key, _| {
            !removed.contains(key.as_str()) || key == "mcp" || key.starts_with("mcp_server__")
        });
        let forced = forced
            .into_iter()
            .filter_map(|(key, state)| state.map(|state| (key, state)))
            .collect();
        let disabled = config["disabled_tool_categories"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect();
        Self {
            defaults,
            forced,
            disabled,
        }
    }

    fn enabled(&self, category: &str) -> bool {
        self.forced.get(category).copied().unwrap_or_else(|| {
            !self.disabled.contains(category)
                && self.defaults.get(category).copied().unwrap_or(true)
        })
    }

    pub fn selection<'a>(&self, servers: &'a [Server]) -> Vec<&'a str> {
        servers
            .iter()
            .filter(|server| self.enabled("mcp") && self.enabled(&category(&server.id)))
            .map(|server| server.id.as_str())
            .collect()
    }
}

pub(super) fn available(
    servers: Vec<Server>,
    access: &Value,
    actor: Actor,
    id: &str,
    data: &Path,
    overrides: &BTreeMap<String, bool>,
) -> Vec<Server> {
    let enabled =
        |category: &str| actor == Actor::Sub || overrides.get(category).copied().unwrap_or(true);
    if !enabled("mcp") {
        return Vec::new();
    }
    let selected = workspace(access, id, actor)
        .filter(|selection| selection["inherit"] == false)
        .and_then(|selection| selection["server_ids"].as_array())
        .or_else(|| defaults(access, actor));
    let legacy = selected.is_none().then(|| LegacyPolicy::load(data));
    if legacy.as_ref().is_some_and(|policy| !policy.enabled("mcp")) {
        return Vec::new();
    }
    servers
        .into_iter()
        .filter(|server| {
            let category = category(&server.id);
            server.enabled
                && enabled(&category)
                && selected.is_none_or(|ids| ids.iter().any(|id| id == &server.id))
                && legacy
                    .as_ref()
                    .is_none_or(|policy| policy.enabled(&category))
        })
        .collect()
}
