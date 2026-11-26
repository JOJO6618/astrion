use std::{
    collections::{BTreeMap, BTreeSet},
    env, io,
    io::ErrorKind,
    path::{Path, PathBuf},
    sync::Mutex,
};

use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde_json::{Value, json};

use crate::{
    knowledge::storage::ensure_dir, web_assets::personalization::default_admin_tool_categories,
};

use super::{AdminError, Result, store};

const DEFINITION: &str = "definition.json";
const EXECUTION: &str = "execution.py";
const RETURN: &str = "return.json";
const META: &str = "meta.json";

// Python's extra built-in aliases are reserved even when they are loaded lazily.
const RESERVED: &[&str] = &[
    "astrion_echo",
    "load_tools",
    "ask_user",
    "submit_plan",
    "view_video",
    "read_mediafile",
    "recall_project_memory",
    "search_project_memory",
    "update_project_memory",
    "stop_sub_agent",
    "send_message_to_sub_agent",
    "answer_sub_agent_question",
    "create_custom_agent",
    "list_agents",
    "list_active_sub_agents",
    "ask_master",
    "ask_other_agent",
    "answer_other_agent",
    "activate_workflow",
    "report_workflow_stage",
    "choose_workflow_branch",
    "get_workflow_status",
    "deactivate_workflow",
    "list_workflows",
    "save_workflow",
    "ocr_image",
    "manage_personalization",
];

pub(super) struct ToolRegistry {
    root: PathBuf,
    enabled: bool,
    reserved: BTreeSet<String>,
    tools: Mutex<Vec<Value>>,
}

impl ToolRegistry {
    pub(super) fn for_host(data: &Path) -> io::Result<Self> {
        let root = env::var_os("CUSTOM_TOOL_DIR")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .map(expand_home)
            .unwrap_or_else(|| data.join("custom_tools"));
        let enabled = !env::var("CUSTOM_TOOLS_ENABLED")
            .is_ok_and(|value| matches!(value.as_str(), "0" | "false" | "False"));
        Self::open(data, root, enabled)
    }

    pub(super) fn open(data: &Path, root: PathBuf, enabled: bool) -> io::Result<Self> {
        let categories = default_admin_tool_categories(data, &[]);
        let reserved = categories
            .values()
            .filter_map(|category| category["tools"].as_array())
            .flatten()
            .filter_map(Value::as_str)
            .chain(RESERVED.iter().copied())
            .map(str::to_owned)
            .collect();
        let mut registry = Self {
            root,
            enabled,
            reserved,
            tools: Mutex::default(),
        };
        if enabled {
            let loaded = registry.scan()?;
            *registry.tools.get_mut().expect("new registry") = loaded;
        }
        Ok(registry)
    }

    pub(super) fn model_tools(&self) -> io::Result<Vec<Value>> {
        if !self.enabled { return Ok(Vec::new()); }
        let tools = self.tools.lock().map_err(|_|io::Error::other("Custom tool registry is unavailable"))?;
        Ok(tools.iter().filter(|tool|tool["invalid_id"] == false).cloned().collect())
    }

    pub(super) fn ids(&self) -> Result<Vec<String>> {
        if !self.enabled {
            return Ok(Vec::new());
        }
        let tools = self.tools.lock().map_err(AdminError::lock)?;
        Ok(tools
            .iter()
            .filter_map(|tool| tool["id"].as_str())
            .map(str::to_owned)
            .collect())
    }

    pub(super) fn list(&self) -> Result<Value> {
        let tools = self.tools.lock().map_err(AdminError::lock)?;
        Ok(json!({"success": true, "data": *tools}))
    }

    pub(super) fn reload(&self) -> Result<Value> {
        let mut tools = self.tools.lock().map_err(AdminError::lock)?;
        *tools = self.scan()?;
        Ok(json!({"success": true}))
    }

    pub(super) fn upsert(&self, payload: &Value) -> Result<Value> {
        let id = payload["id"].as_str().unwrap_or_default().trim();
        id_required(id)?;
        if self.is_reserved(id) {
            return Err(AdminError::bad("自定义工具不能覆盖内置工具名称。"));
        }
        let definition = json!({"id": id, "description": payload["description"],
            "parameters": payload["parameters"], "required": payload["required"],
            "category": store::nonempty(&payload["category"]).unwrap_or("custom"),
            "icon": payload["icon"], "timeout": payload["timeout"]});
        let execution = store::nonempty(&payload["execution_code"])
            .or_else(|| store::nonempty(&payload["execution"]["code_template"]))
            .unwrap_or("# add python code here\n");
        let return_config = optional_bytes(payload, "return", "return_config")?;
        let meta = optional_bytes(payload, "meta", "notes")?;
        let changes = BTreeMap::from([
            (DEFINITION, Some(store::pretty(&definition)?)),
            (EXECUTION, Some(execution.as_bytes().to_vec())),
            (RETURN, return_config),
            (META, meta),
        ]);
        if changes
            .values()
            .flatten()
            .any(|bytes| bytes.len() as u64 > store::MAX_FILE_BYTES)
        {
            return Err(AdminError::bad("File too large"));
        }
        let mut tools = self.tools.lock().map_err(AdminError::lock)?;
        let root = store::directory(&self.root, true)?
            .ok_or_else(|| io::Error::other("Missing tool registry"))?;
        let dir = ensure_dir(&root, id)?;
        apply_changes(&dir, changes)?;
        *tools = self.scan()?;
        let saved = tools
            .iter()
            .find(|tool| tool["id"] == id)
            .ok_or_else(|| io::Error::other("Saved tool could not be registered"))?;
        Ok(json!({"success": true, "data": saved}))
    }

    pub(super) fn delete(&self, id: &str) -> Result<Value> {
        id_required(id)?;
        let mut tools = self.tools.lock().map_err(AdminError::lock)?;
        let root = store::directory(&self.root, false)?
            .ok_or_else(|| AdminError::missing("Tool not found"))?;
        open_tool(&root, id)?.ok_or_else(|| AdminError::missing("Tool not found"))?;
        root.remove_dir_all(id)?;
        *tools = self.scan()?;
        Ok(json!({"success": true, "data": {"deleted": id}}))
    }

    pub(super) fn file(
        &self,
        id: &str,
        name: &str,
        content: Option<&str>,
    ) -> Result<Option<String>> {
        id_required(id)?;
        if !matches!(name, DEFINITION | EXECUTION | RETURN | META) {
            return Err(AdminError::bad("Invalid custom tool file name"));
        }
        let _lock = self.tools.lock().map_err(AdminError::lock)?;
        let root = store::directory(&self.root, false)?
            .ok_or_else(|| AdminError::missing("Tool does not exist"))?;
        let dir =
            open_tool(&root, id)?.ok_or_else(|| AdminError::missing("Tool does not exist"))?;
        if let Some(content) = content {
            store::write(&dir, name, content.as_bytes())?;
            return Ok(None);
        }
        let bytes =
            store::read(&dir, name)?.ok_or_else(|| AdminError::missing("File not found"))?;
        let text = String::from_utf8(bytes)
            .map_err(|_| io::Error::new(ErrorKind::InvalidData, "Invalid tool file text"))?;
        Ok(Some(text))
    }

    fn is_reserved(&self, id: &str) -> bool {
        id.starts_with("mcp__") || self.reserved.contains(id)
    }

    fn scan(&self) -> io::Result<Vec<Value>> {
        let Some(root) = store::directory(&self.root, false)? else {
            return Ok(Vec::new());
        };
        let mut children: Vec<_> = root
            .entries()?
            .filter_map(|entry| {
                let entry = entry.ok()?;
                entry
                    .file_type()
                    .ok()?
                    .is_dir()
                    .then(|| entry.file_name().into_string().ok())
                    .flatten()
            })
            .collect();
        children.sort();
        let mut ids = BTreeSet::new();
        let mut tools = Vec::new();
        for child in children {
            let Ok(Some(dir)) = open_tool(&root, &child) else {
                continue;
            };
            if let Some(tool) = self.read_tool(&dir, &child)
                && ids.insert(tool["id"].as_str().unwrap_or_default().to_owned())
            {
                tools.push(tool);
            }
        }
        Ok(tools)
    }

    fn read_tool(&self, dir: &Dir, child: &str) -> Option<Value> {
        let definition = store::read_object(dir, DEFINITION).ok()??;
        let id = store::nonempty(&definition["id"]).unwrap_or(child);
        if self.is_reserved(id) {
            return None;
        }
        let code = String::from_utf8(store::read(dir, EXECUTION).ok()??).ok()?;
        let meta = store::read_object(dir, META)
            .ok()
            .flatten()
            .unwrap_or_else(|| json!({}));
        let return_config = store::read_object(dir, RETURN)
            .ok()
            .flatten()
            .unwrap_or_else(|| json!({}));
        let valid = valid_id(id) && child == id;
        Some(
            json!({"id": id, "description": store::nonempty(&definition["description"])
                .map(str::to_owned).unwrap_or_else(|| format!("自定义工具 {id}")),
                "parameters": fallback(&definition["parameters"], json!({"type":"object","properties":{}})),
                "required": fallback(&definition["required"], json!([])),
                "category": store::nonempty(&definition["category"]).unwrap_or("custom"),
                "icon": fallback(&definition["icon"], meta["icon"].clone()),
                "execution": {"type": "python", "timeout": definition["timeout"], "code_template": code,
                    "file": self.root.join(child).join(EXECUTION)},
                "execution_code": code, "return": return_config, "meta": meta,
                "invalid_id": !valid, "validation_error": (!valid).then_some("工具 ID 无效或与目录名不一致")
            }),
        )
    }
}

fn fallback(value: &Value, fallback: Value) -> Value {
    if store::truthy(value) {
        value.clone()
    } else {
        fallback
    }
}

fn valid_id(id: &str) -> bool {
    let mut bytes = id.bytes();
    bytes.next().is_some_and(|byte| byte.is_ascii_alphabetic())
        && bytes.all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn id_required(id: &str) -> Result<()> {
    if id.is_empty() {
        return Err(AdminError::bad("Missing id"));
    }
    if !valid_id(id) {
        return Err(AdminError::bad("工具ID需以字母开头，可含字母/数字/_/-"));
    }
    Ok(())
}

fn open_tool(root: &Dir, id: &str) -> io::Result<Option<Dir>> {
    match root.open_dir_nofollow(id) {
        Ok(dir) => Ok(Some(dir)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn optional_bytes(payload: &Value, field: &str, alias: &str) -> io::Result<Option<Vec<u8>>> {
    let value = [&payload[field], &payload[alias]]
        .into_iter()
        .find(|value| store::truthy(value));
    value.map(store::pretty).transpose()
}

fn apply_changes(dir: &Dir, changes: BTreeMap<&str, Option<Vec<u8>>>) -> io::Result<()> {
    let mut backups = BTreeMap::new();
    for name in changes.keys() {
        backups.insert(*name, store::read(dir, name)?);
    }
    let change = |name, content: Option<&[u8]>| match content {
        Some(content) => store::write(dir, name, content),
        None => store::remove(dir, name),
    };
    let mut applied = Vec::new();
    for (name, content) in changes {
        if let Err(error) = change(name, content.as_deref()) {
            for name in applied.into_iter().rev() {
                if change(name, backups[name].as_deref()).is_err() {
                    return Err(io::Error::other("Tool file rollback failed"));
                }
            }
            return Err(error);
        }
        applied.push(name);
    }
    Ok(())
}

fn expand_home(path: PathBuf) -> PathBuf {
    path.strip_prefix("~")
        .ok()
        .and_then(|tail| env::home_dir().map(|home| home.join(tail)))
        .unwrap_or(path)
}
