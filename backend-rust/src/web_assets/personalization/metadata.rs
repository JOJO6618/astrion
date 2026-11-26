use super::{
    schema::{Object, schema, strings, truthy},
    store::read_object,
};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    fs, io,
    path::{Path, PathBuf},
};

pub(super) struct Policy(Object);

impl Policy {
    pub(super) fn load(data: &Path) -> io::Result<Self> {
        let policy =
            Value::Object(read_object(&data.join("admin_policy.json"))?.unwrap_or_default());
        let mut effective = Object::new();
        for scope in [
            &policy["global"],
            &policy["roles"]["admin"],
            &policy["invites"][""],
            &policy["users"]["host"],
        ] {
            for key in ["ui_blocks", "forced_category_states", "category_overrides"] {
                if let Some(fields) = scope[key].as_object() {
                    let current = effective
                        .entry(key)
                        .or_insert_with(|| json!({}))
                        .as_object_mut()
                        .expect("policy map");
                    current.extend(fields.clone());
                }
            }
            if let Some(removed) = scope["remove_categories"].as_array() {
                let current = effective
                    .entry("remove_categories")
                    .or_insert_with(|| json!([]))
                    .as_array_mut()
                    .expect("policy list");
                current.extend(removed.iter().cloned());
            }
        }
        Ok(Self(effective))
    }

    pub(super) fn blocks_tool_toggles(&self) -> bool {
        self.0.get("ui_blocks").is_some_and(|v| truthy(&v["block_tool_toggle"]))
    }

    pub(super) fn blocks_personalization(&self) -> bool {
        self.0
            .get("ui_blocks")
            .is_some_and(|policy| truthy(&policy["block_personal_space"]))
    }

    pub(super) fn tool_categories(&self, data: &Path, config: &Object) -> Vec<Value> {
        let mut categories = schema().tool_categories.clone();
        let removed = self.0.get("remove_categories").unwrap_or(&Value::Null);
        let custom_tools = custom_tools(data);
        categories.insert(
            "custom".into(),
            json!({"label":"自定义工具", "tools":custom_tools, "default_enabled":true}),
        );
        let dynamic = mcp_servers(data);
        for name in strings(removed, usize::MAX) {
            categories.shift_remove(name);
        }
        if let Some(overrides) = self.0.get("category_overrides").and_then(Value::as_object) {
            for (name, value) in overrides {
                if value.is_object()
                    && value
                        .get("tools")
                        .is_none_or(|tools| tools.is_array() || tools.is_null())
                {
                    categories.insert(name.clone(), value.clone());
                }
            }
        }
        let mcp = categories
            .entry("mcp")
            .or_insert_with(|| schema().tool_categories["mcp"].clone());
        mcp["tools"] = json!(["list_mcp_servers"]);
        categories.retain(|name, _| {
            !name.starts_with("mcp_server__") || dynamic.iter().any(|(id, _)| id == name)
        });
        for (key, mut dynamic_category) in dynamic {
            if let Some(enabled) = categories
                .get(&key)
                .and_then(|category| category.get("default_enabled"))
            {
                dynamic_category["default_enabled"] = enabled.clone();
            }
            categories.insert(key, dynamic_category);
        }
        let forced = self.0.get("forced_category_states").unwrap_or(&Value::Null);
        let disabled = config.get("disabled_tool_categories")
            .map(|value| strings(value, usize::MAX)).unwrap_or_default();
        categories.into_iter().map(|(id, category)| {
            let locked = forced[&id].as_bool();
            let enabled = locked.unwrap_or_else(|| category.get("default_enabled").is_none_or(truthy) && !disabled.contains(&id.as_str()));
            let tools: Vec<_> = category["tools"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
            json!({"id": id, "label": category["label"].as_str().filter(|label| !label.is_empty()).unwrap_or(&id),
                "tools": tools, "enabled": enabled, "locked": locked.is_some(), "locked_state": locked})
        }).collect()
    }
}

pub(crate) fn default_admin_tool_categories(data: &Path, custom_ids: &[String]) -> Object {
    let mut categories = schema().tool_categories.clone();
    categories.insert("custom".into(), json!({"label":"自定义工具", "tools":custom_ids, "default_enabled":true}));
    categories.extend(mcp_servers(data));
    for category in categories.values_mut().filter_map(Value::as_object_mut) {
        category.entry("silent_when_disabled").or_insert(Value::Bool(false));
    }
    categories
}

pub(super) fn skills_catalog(global: &Path, private: &Path) -> Vec<Value> {
    let mut entries = Object::new();
    for (root, source) in [(global, "global"), (private, "private")] {
        if source == "private" && root == global {
            continue;
        }
        for path in child_directories(root) {
            let Some(id) = path
                .file_name()
                .and_then(|name| name.to_str())
                .filter(|id| resource_id(id))
            else {
                continue;
            };
            let skill = path.join("SKILL.md");
            let Ok(info) = fs::symlink_metadata(&skill) else {
                continue;
            };
            if !info.is_file() || info.len() > 1024 * 1024 {
                continue;
            }
            let frontmatter = fs::read_to_string(skill)
                .ok()
                .and_then(|text| parse_frontmatter(&text))
                .unwrap_or_default();
            let field = |name: &str| {
                frontmatter
                    .get(name)
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
            };
            entries.insert(id.into(), json!({"id":id,"label":field("name").unwrap_or(id),"description":field("description").unwrap_or(""),"source":source,"display_dir":".astrion/skills"}));
        }
    }
    entries.into_values().collect()
}

fn parse_frontmatter(text: &str) -> Option<Object> {
    let mut lines = text.trim_start_matches('\u{feff}').lines();
    if lines.next()?.trim() != "---" {
        return None;
    }
    let contents = lines
        .take_while(|line| line.trim() != "---")
        .collect::<Vec<_>>()
        .join("\n");
    serde_yaml_ng::from_str(&contents).ok()
}

pub(super) fn skill_selection<'a>(
    config: &Object,
    catalog: &'a [Value],
) -> (Vec<&'a str>, Vec<&'a str>) {
    let catalog_ids: Vec<_> = catalog
        .iter()
        .filter_map(|item| item["id"].as_str())
        .collect();
    let snapshot = strings(&config["skills_catalog_snapshot"], usize::MAX);
    let enabled = strings(&config["enabled_skills"], usize::MAX);
    let mut selected: Vec<_> = catalog_ids
        .iter()
        .copied()
        .filter(|id| config["enabled_skills"].is_null() || enabled.contains(id))
        .collect();
    if !snapshot.is_empty() {
        for id in &catalog_ids {
            if !snapshot.contains(id) && !selected.contains(id) {
                selected.push(id);
            }
        }
    }
    (selected, catalog_ids)
}

fn child_directories(root: &Path) -> Vec<PathBuf> {
    let mut children: Vec<_> = fs::read_dir(root)
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
        .map(|entry| entry.path())
        .collect();
    children.sort();
    children
}

fn resource_id(value: &str) -> bool {
    value
        .as_bytes()
        .first()
        .is_some_and(u8::is_ascii_alphanumeric)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
}

fn custom_tools(data: &Path) -> Vec<String> {
    let root = std::env::var_os("CUSTOM_TOOL_DIR")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| data.join("custom_tools"));
    if std::env::var("CUSTOM_TOOLS_ENABLED")
        .is_ok_and(|value| matches!(value.as_str(), "0" | "false" | "False"))
    {
        return Vec::new();
    }
    child_directories(&root)
        .into_iter()
        .filter_map(|path| {
            if !path.join("execution.py").is_file() {
                return None;
            }
            let id = read_object(&path.join("definition.json"))
                .ok()??
                .get("id")
                .and_then(Value::as_str)
                .map(str::to_owned)
                .or_else(|| {
                    path.file_name()
                        .and_then(|value| value.to_str())
                        .map(str::to_owned)
                })?;
            resource_id(&id).then_some(id)
        })
        .collect()
}

fn mcp_servers(data: &Path) -> Vec<(String, Value)> {
    if std::env::var("MCP_TOOLS_ENABLED")
        .is_ok_and(|value| matches!(value.as_str(), "0" | "false" | "False"))
    {
        return Vec::new();
    }
    let path = std::env::var_os("MCP_SERVERS_FILE")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| data.join("mcp_servers.json"));
    let servers = read_object(&path).ok().flatten().unwrap_or_default();
    servers
        .get("servers")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|server| {
            let id = server["id"]
                .as_str()
                .filter(|id| resource_id(id) && id.as_bytes()[0].is_ascii_alphabetic())?;
            if server["enabled"] == false {
                return None;
            }
            let name = server["name"]
                .as_str()
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .unwrap_or(id);
            let include = strings(&server["include_tools"], usize::MAX);
            let exclude = strings(&server["exclude_tools"], usize::MAX);
            let mut seen = HashSet::new();
            let tools: Vec<_> = server["tools_cache"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|tool| tool["name"].as_str())
                .map(str::trim)
                .filter(|tool| {
                    !tool.is_empty()
                        && (include.is_empty() || include.contains(tool))
                        && !exclude.contains(tool)
                })
                .map(|tool| mcp_alias(id, tool))
                .filter(|tool| seen.insert(tool.clone()))
                .collect();
            Some((
                format!("mcp_server__{}", &id[..id.len().min(52)]),
                json!({"label": format!("MCP · {name}"), "tools": tools, "default_enabled": true}),
            ))
        })
        .collect()
}

fn mcp_alias(server: &str, tool: &str) -> String {
    let mut slug = String::new();
    let mut inside_separator = false;
    for character in tool.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '_' | '-') {
            slug.push(character);
            inside_separator = false;
        } else if !inside_separator {
            slug.push('_');
            inside_separator = true;
        }
    }
    let slug = slug.trim_matches('_');
    let slug = if slug.is_empty() {
        "tool".into()
    } else if !slug.as_bytes()[0].is_ascii_alphabetic() {
        format!("x_{slug}")
    } else {
        slug.to_owned()
    };
    let prefix = format!("mcp__{server}__");
    let space = 64_usize.saturating_sub(prefix.len()).max(8);
    format!("{prefix}{}", &slug[..slug.len().min(space)])
}
