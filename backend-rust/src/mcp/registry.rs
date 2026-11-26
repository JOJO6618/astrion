use std::{
    collections::{BTreeMap, BTreeSet},
    fs, io,
    path::{Path, PathBuf},
    sync::Mutex,
};

use serde::Serialize;
use serde_json::{Map, Value, json};

use crate::knowledge::storage::{open_canonical_dir, publish};

static WRITES: Mutex<()> = Mutex::new(());
const CONNECTION_FIELDS: [&str; 7] = [
    "transport",
    "command",
    "args",
    "cwd",
    "env",
    "url",
    "headers",
];
const CACHE_FIELDS: [&str; 3] = ["tools_cache", "tools_cache_updated_at", "last_error"];

#[derive(Clone)]
pub struct Registry {
    path: PathBuf,
    enabled: bool,
    protocol: String,
    timeout: u64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub(super) struct Server {
    pub id: String,
    pub name: String,
    pub description: String,
    pub enabled: bool,
    pub transport: String,
    pub command: String,
    pub args: Vec<String>,
    pub cwd: String,
    pub env: BTreeMap<String, String>,
    pub url: String,
    pub headers: BTreeMap<String, String>,
    pub timeout_seconds: u64,
    pub include_tools: Vec<String>,
    pub exclude_tools: Vec<String>,
    pub tools_cache: Vec<Value>,
    pub tools_cache_updated_at: String,
    pub last_error: String,
}

impl Server {
    fn normalize(raw: &Value, timeout: u64) -> Self {
        let text = |field: &str| scalar(&raw[field]);
        let id = text("id").trim().to_owned();
        let name = text("name");
        let transport = text("transport")
            .trim()
            .to_ascii_lowercase()
            .replace('-', "_");
        let timeout_seconds = scalar(&raw["timeout_seconds"])
            .parse::<f64>()
            .ok()
            .filter(|number| number.is_finite())
            .map(|number| number as u64)
            .filter(|number| *number > 0)
            .unwrap_or(timeout)
            .min(300);
        Self {
            name: if name.is_empty() { id.clone() } else { name },
            id,
            description: text("description"),
            enabled: is_enabled(&raw["enabled"]),
            transport: match transport.as_str() {
                "http" | "https" | "streamablehttp" => "streamable_http".into(),
                _ => transport,
            },
            command: text("command").trim().to_owned(),
            args: strings(&raw["args"]),
            cwd: text("cwd").trim().to_owned(),
            env: strings_map(&raw["env"]),
            url: text("url").trim().to_owned(),
            headers: strings_map(&raw["headers"]),
            timeout_seconds,
            include_tools: strings(&raw["include_tools"]),
            exclude_tools: strings(&raw["exclude_tools"]),
            tools_cache: raw["tools_cache"].as_array().cloned().unwrap_or_default(),
            tools_cache_updated_at: text("tools_cache_updated_at"),
            last_error: text("last_error"),
        }
    }

    fn validate(&self) -> Result<(), Error> {
        if !valid_id(&self.id) {
            return Err(Error::Invalid(
                "服务 ID 不合法：需以字母开头，可包含字母、数字、下划线、短横线".into(),
            ));
        }
        match self.transport.as_str() {
            "stdio" if self.command.is_empty() => {
                Err(Error::Invalid("stdio 传输必须提供 command".into()))
            }
            "streamable_http" if self.url.is_empty() => {
                Err(Error::Invalid("streamable_http 传输必须提供 url".into()))
            }
            "stdio" | "streamable_http" => Ok(()),
            _ => Err(Error::Invalid(format!(
                "不支持的 transport: {}（支持: ['stdio', 'streamable_http']）",
                self.transport
            ))),
        }
    }

    pub fn summary(&self) -> Value {
        json!({"id": self.id, "name": self.name, "description": self.description, "enabled": self.enabled,
            "transport": self.transport, "tools_cache_updated_at": self.tools_cache_updated_at,
            "last_error": self.last_error, "tools_cache_count": self.tools_cache.len()})
    }

    pub fn detail(&self) -> Value {
        let mut normalized = json!(self);
        normalized
            .as_object_mut()
            .unwrap()
            .shift_remove("tools_cache");
        normalized
    }

    pub fn same_connection(&self, other: &Self) -> bool {
        self.id == other.id
            && self.transport == other.transport
            && self.command == other.command
            && self.args == other.args
            && self.cwd == other.cwd
            && self.env == other.env
            && self.url == other.url
            && self.headers == other.headers
    }

    pub fn allows_tool(&self, name: &str) -> bool {
        (self.include_tools.is_empty() || self.include_tools.iter().any(|entry| entry == name))
            && !self.exclude_tools.iter().any(|entry| entry == name)
    }
}

#[derive(Debug)]
pub(super) enum Error {
    Invalid(String),
    Missing,
    Storage(io::Error),
}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Self::Storage(error)
    }
}

impl Registry {
    pub fn new(path: PathBuf, enabled: bool) -> Self {
        Self {
            path,
            enabled,
            protocol: "2025-06-18".into(),
            timeout: 25,
        }
    }

    pub fn for_data(data: &Path) -> Self {
        let path = std::env::var_os("MCP_SERVERS_FILE")
            .filter(|path| !path.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| data.join("mcp_servers.json"));
        let enabled = !std::env::var("MCP_TOOLS_ENABLED")
            .is_ok_and(|value| matches!(value.as_str(), "0" | "false" | "False"));
        let mut registry = Self::new(path, enabled);
        if let Ok(protocol) = std::env::var("MCP_PROTOCOL_VERSION")
            && !protocol.is_empty()
        {
            registry.protocol = protocol;
        }
        if let Ok(timeout) = std::env::var("MCP_DEFAULT_TIMEOUT_SECONDS")
            && let Ok(timeout) = timeout.parse::<u64>()
            && timeout > 0
        {
            registry.timeout = timeout.min(300);
        }
        registry
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }
    pub(super) fn protocol(&self) -> &str {
        &self.protocol
    }

    fn read(&self) -> io::Result<Value> {
        match fs::read(&self.path) {
            Ok(bytes) => {
                let data: Value = serde_json::from_slice(&bytes)?;
                if !data.is_object() {
                    return Err(invalid_data("Invalid MCP configuration"));
                }
                Ok(data)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(json!({"servers": []})),
            Err(error) => Err(error),
        }
    }

    fn update<T>(
        &self,
        action: impl FnOnce(&mut Value) -> Result<(T, bool), Error>,
    ) -> Result<T, Error> {
        let _lock = WRITES
            .lock()
            .map_err(|_| io::Error::other("MCP configuration lock poisoned"))?;
        let mut raw = self.read()?;
        let (result, changed) = action(&mut raw)?;
        if changed {
            let parent = self
                .path
                .parent()
                .filter(|path| !path.as_os_str().is_empty())
                .unwrap_or(Path::new("."));
            fs::create_dir_all(parent)?;
            let parent = open_canonical_dir(&parent.canonicalize()?)?;
            let filename = self
                .path
                .file_name()
                .and_then(|name| name.to_str())
                .ok_or_else(|| invalid_data("Invalid MCP configuration path"))?;
            let bytes = serde_json::to_vec_pretty(&raw).map_err(io::Error::from)?;
            publish(&parent, filename, &bytes, true)?;
        }
        Ok(result)
    }

    fn load_servers(&self, raw: &Value) -> Vec<Server> {
        raw["servers"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|row| row.is_object())
            .map(|row| Server::normalize(row, self.timeout))
            .filter(|row| !row.id.is_empty())
            .collect()
    }

    pub(super) fn settings(&self) -> (Vec<Server>, Value) {
        let raw = self.read().unwrap_or_default();
        (self.load_servers(&raw), raw["access"].clone())
    }

    pub(super) fn upsert(
        &self,
        id: Option<&str>,
        mut input: Map<String, Value>,
    ) -> Result<Server, Error> {
        let create = id.is_none();
        let id = id.map(str::to_owned).unwrap_or_else(|| {
            scalar(input.get("id").unwrap_or(&Value::Null))
                .trim()
                .to_owned()
        });
        input.insert("id".into(), json!(id));
        self.update(|raw| {
            if !raw["servers"].is_array() {
                raw["servers"] = json!([]);
            }
            let rows = raw["servers"].as_array_mut().unwrap();
            let index = rows.iter().position(|row| scalar(&row["id"]).trim() == id);
            let existing = match (create, index) {
                (true, Some(_)) => {
                    return Err(Error::Invalid("MCP server ID already exists".into()));
                }
                (false, None) => return Err(Error::Missing),
                (_, Some(index)) => rows[index].as_object().cloned().unwrap_or_default(),
                (_, None) => Map::new(),
            };
            let old = Server::normalize(&Value::Object(existing.clone()), self.timeout);
            let mut fields = existing;
            fields.extend(
                input
                    .into_iter()
                    .filter(|(key, _)| !CACHE_FIELDS.contains(&key.as_str())),
            );
            let mut server = Server::normalize(&Value::Object(fields.clone()), self.timeout);
            server.validate()?;
            if !create && !old.same_connection(&server) {
                server.tools_cache.clear();
                server.tools_cache_updated_at.clear();
                server.last_error.clear();
            }
            fields.extend(
                serde_json::to_value(&server)
                    .unwrap()
                    .as_object()
                    .unwrap()
                    .clone(),
            );
            match index {
                Some(index) => rows[index] = Value::Object(fields),
                None => rows.push(Value::Object(fields)),
            }
            rows.sort_by_cached_key(|row| scalar(&row["id"]));
            Ok((server, true))
        })
    }

    pub(super) fn delete(&self, id: &str) -> Result<(), Error> {
        self.update(|raw| {
            let rows = raw["servers"].as_array_mut().ok_or(Error::Missing)?;
            let old_len = rows.len();
            rows.retain(|row| scalar(&row["id"]).trim() != id);
            if old_len == rows.len() {
                return Err(Error::Missing);
            }
            for actor in ["main", "sub"] {
                remove(&mut raw["access"]["defaults"], actor, id);
                if let Some(scopes) = raw["access"]["workspaces"].as_object_mut() {
                    for selection in scopes.values_mut() {
                        if let Some(actor) = selection.get_mut(actor) {
                            if let Some(ids) =
                                actor.get_mut("server_ids").and_then(Value::as_array_mut)
                            {
                                ids.retain(|value| value != id);
                            }
                        }
                    }
                }
            }
            Ok(((), true))
        })
    }

    pub(super) fn save_access(
        &self,
        actor: &str,
        scope: Option<&str>,
        payload: &Value,
    ) -> Result<(), Error> {
        self.update(|raw| {
            let ids: BTreeSet<_> = self
                .load_servers(raw)
                .into_iter()
                .map(|row| row.id)
                .collect();
            let selection = payload["server_ids"]
                .as_array()
                .ok_or_else(invalid_selection)?;
            let mut selected = BTreeSet::new();
            for id in selection {
                let id = id.as_str().ok_or_else(invalid_selection)?;
                if ids.contains(id) {
                    selected.insert(id);
                }
            }
            let access = object_at(raw, "access")?;
            if let Some(scope) = scope {
                let inherit = payload["inherit"]
                    .as_bool()
                    .ok_or_else(|| Error::Invalid("Invalid workspace MCP selection".into()))?;
                let workspaces = object_at(access, "workspaces")?;
                object_at(workspaces, scope)?[actor] =
                    json!({"inherit": inherit, "server_ids": selected});
            } else {
                object_at(access, "defaults")?[actor] = json!(selected);
            }
            Ok(((), true))
        })
    }

    pub(super) fn update_cache(
        &self,
        expected: &Server,
        result: &Result<Vec<Value>, String>,
    ) -> Result<bool, Error> {
        self.update(|raw| {
            let Some(row) = raw["servers"]
                .as_array_mut()
                .into_iter()
                .flatten()
                .find(|row| scalar(&row["id"]).trim() == expected.id)
            else {
                return Ok((false, false));
            };
            let current = Server::normalize(row, self.timeout);
            if !current.enabled || !current.same_connection(expected) {
                return Ok((false, false));
            }
            if let Ok(tools) = result {
                row["tools_cache"] = json!(tools);
            }
            row["last_error"] = json!(
                result
                    .as_ref()
                    .err()
                    .map(String::as_str)
                    .unwrap_or_default()
            );
            row["tools_cache_updated_at"] =
                json!(jiff::Zoned::now().strftime("%Y-%m-%d %H:%M:%S").to_string());
            Ok((true, true))
        })
    }
}

fn object_at<'a>(object: &'a mut Value, key: &str) -> Result<&'a mut Value, Error> {
    let value = object
        .as_object_mut()
        .ok_or_else(|| invalid_data("Invalid MCP access configuration"))?
        .entry(key)
        .or_insert_with(|| json!({}));
    if value.is_object() {
        Ok(value)
    } else {
        Err(invalid_data("Invalid MCP access configuration").into())
    }
}

fn remove(object: &mut Value, key: &str, id: &str) {
    if let Some(list) = object.get_mut(key).and_then(Value::as_array_mut) {
        list.retain(|value| value != id);
    }
}

fn invalid_data(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn invalid_selection() -> Error {
    Error::Invalid("MCP selection must be a list of server IDs".into())
}

fn valid_id(id: &str) -> bool {
    id.as_bytes().first().is_some_and(u8::is_ascii_alphabetic)
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

pub(super) fn scalar(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::String(value) => value.clone(),
        Value::Bool(value) => if *value { "True" } else { "False" }.into(),
        _ => value.to_string(),
    }
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .map(scalar)
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .collect()
}

fn strings_map(value: &Value) -> BTreeMap<String, String> {
    value
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(key, _)| !key.trim().is_empty())
        .map(|(key, value)| (key.trim().to_owned(), scalar(value)))
        .collect()
}

fn is_enabled(value: &Value) -> bool {
    !matches!(
        scalar(value).trim().to_ascii_lowercase().as_str(),
        "0" | "false" | "no" | "off"
    )
}

pub(super) fn slug(value: &str, fallback: &str) -> String {
    let mut output = String::new();
    let mut separating = false;
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-') {
            output.push(ch);
            separating = false;
        } else if !separating {
            output.push('_');
            separating = true;
        }
    }
    let output = output.trim_matches('_');
    if output.is_empty() {
        fallback.to_owned()
    } else if output.as_bytes()[0].is_ascii_alphabetic() {
        output.to_owned()
    } else {
        format!("x_{output}")
    }
}

pub(super) fn category(id: &str) -> String {
    let id = slug(id, "server");
    format!("mcp_server__{}", &id[..id.len().min(52)])
}

pub(super) fn alias(id: &str, name: &str) -> String {
    let prefix = format!("mcp__{}__", slug(id, "server"));
    let name = slug(name, "tool");
    let length = 64usize.saturating_sub(prefix.len()).max(8);
    format!("{prefix}{}", &name[..name.len().min(length)])
}
