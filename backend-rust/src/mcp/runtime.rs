use std::{
    borrow::Cow,
    collections::{BTreeMap, BTreeSet, HashMap},
    sync::Arc,
    time::Instant,
};

use ring::digest::{SHA1_FOR_LEGACY_USE_ONLY, digest};
use serde_json::{Value, json};
use tokio::sync::{Mutex, RwLock};

use super::{
    access,
    registry::{Registry, Server, alias},
    scope::{Prepared, RuntimeScope},
    transport::Client,
};

pub const LIST_TOOL: &str = "list_mcp_servers";
const MAX_PAGES: usize = 20;

pub fn is_tool(name: &str) -> bool {
    name == LIST_TOOL || name.starts_with("mcp__")
}

/// Create one runtime per conversation or named child. Scope and overrides are
/// supplied by the trusted owner, and are never populated from tool arguments.
pub struct Runtime {
    registry: Registry,
    scope: Option<RuntimeScope>,
    overrides: RwLock<BTreeMap<String, bool>>,
    connections: Mutex<HashMap<String, Connection>>,
}

struct Connection {
    server: Server,
    client: Client,
}

#[derive(Clone)]
struct Binding {
    server: Arc<Server>,
    tool: String,
}

/// The set of aliases announced in a specific model request.
#[derive(Default)]
pub struct ToolSnapshot {
    schemas: Vec<Value>,
    bindings: BTreeMap<String, Binding>,
    can_list: bool,
}

impl ToolSnapshot {
    pub fn schemas(&self) -> &[Value] {
        &self.schemas
    }
    pub fn allows(&self, name: &str) -> bool {
        if name == LIST_TOOL {
            self.can_list
        } else {
            self.bindings.contains_key(name)
        }
    }
}

impl Runtime {
    pub fn new(registry: Registry, scope: RuntimeScope) -> Self {
        Self::with_scope(registry, Some(scope))
    }

    pub(super) fn management(registry: Registry) -> Self {
        Self::with_scope(registry, None)
    }

    fn with_scope(registry: Registry, scope: Option<RuntimeScope>) -> Self {
        Self {
            registry,
            scope,
            overrides: RwLock::default(),
            connections: Mutex::default(),
        }
    }

    /// A main conversation can temporarily narrow its persisted access.
    pub async fn set_category_overrides(&self, overrides: BTreeMap<String, bool>) {
        *self.overrides.write().await = overrides;
    }

    async fn available(&self) -> Vec<Server> {
        if !self.registry.enabled() {
            return Vec::new();
        }
        let (servers, settings) = self.registry.settings();
        let Some(scope) = &self.scope else {
            return servers
                .into_iter()
                .filter(|server| server.enabled)
                .collect();
        };
        let overrides = self.overrides.read().await;
        access::available(
            servers,
            &settings,
            scope.actor(),
            &scope.workspace_id,
            &scope.data,
            &overrides,
        )
    }

    /// Refresh before each model request. Discovery uses each role's effective
    /// allowlist and fills older or unsynchronized tool caches on demand.
    pub async fn prepare_tools(&self) -> ToolSnapshot {
        let servers = self.available().await;
        self.prune(&servers).await;
        let mut discovered = false;
        for server in &servers {
            if server.tools_cache.is_empty() && server.tools_cache_updated_at.is_empty() {
                let _ = self.discover(server, Some(12)).await;
                discovered = true;
            }
        }
        let servers = if discovered {
            self.available().await
        } else {
            servers
        };
        self.snapshot(&servers)
    }

    fn snapshot(&self, servers: &[Server]) -> ToolSnapshot {
        let mut snapshot = ToolSnapshot::default();
        for server in servers {
            let server = Arc::new(server.clone());
            let mut seen = BTreeSet::new();
            for item in &server.tools_cache {
                let Some(name) = item["name"]
                    .as_str()
                    .map(str::trim)
                    .filter(|name| !name.is_empty())
                else {
                    continue;
                };
                if !server.allows_tool(name) || !seen.insert(name) {
                    continue;
                }
                let mut alias = alias(&server.id, name);
                if snapshot.bindings.contains_key(&alias) {
                    let hash = digest(
                        &SHA1_FOR_LEGACY_USE_ONLY,
                        format!("{}:{name}", server.id).as_bytes(),
                    );
                    let suffix: String = hash.as_ref()[..3]
                        .iter()
                        .map(|byte| format!("{byte:02x}"))
                        .collect();
                    alias = format!("{}__{suffix}", &alias[..alias.len().min(56)]);
                    if snapshot.bindings.contains_key(&alias) {
                        continue;
                    }
                }
                let description = item["description"].as_str().unwrap_or_default().trim();
                let description = if description.is_empty() {
                    format!("通过 MCP 服务调用远程工具 {name}")
                } else {
                    description.to_owned()
                };
                snapshot
                    .schemas
                    .push(json!({"type": "function", "function": {
                        "name": alias, "description": format!("[MCP:{}] {description}", server.id),
                        "parameters": schema(&item["inputSchema"])
                    }}));
                snapshot.bindings.insert(
                    alias,
                    Binding {
                        server: server.clone(),
                        tool: name.into(),
                    },
                );
            }
        }
        if !servers.is_empty() {
            snapshot.can_list = true;
            let mut properties = json!({
                "refresh": {"type": "boolean", "description": "是否刷新可用 MCP 服务的工具缓存（默认 false）"},
                "server_id": {"type": "string", "description": "仅查看指定的可用 MCP 服务（可选）"}
            });
            if self
                .scope
                .as_ref()
                .is_some_and(|scope| scope.actor() == access::Actor::Main)
            {
                properties["intent"] =
                    json!({"type": "string", "description": "用不超过 15 个字说明操作目的"});
            }
            snapshot
                .schemas
                .push(json!({"type": "function", "function": {"name": LIST_TOOL,
                    "description": "列出当前智能体可用的 MCP 服务与工具别名。可选刷新工具缓存。",
                    "parameters": {"type": "object", "properties": properties, "required": []}
                }}));
        }
        snapshot
    }

    async fn prune(&self, servers: &[Server]) {
        let mut connections = self.connections.lock().await;
        let remove: Vec<_> = connections
            .iter()
            .filter_map(|(id, connection)| {
                let server = servers.iter().find(|server| &server.id == id);
                let keep = server.is_some_and(|server| {
                    let prepared = Prepared::new(server.clone(), self.scope.as_ref());
                    prepared.server.same_connection(&connection.server)
                });
                (!keep).then(|| id.clone())
            })
            .collect();
        for id in remove {
            // Client drop starts HTTP session cleanup and kills a local process.
            connections.remove(&id);
        }
    }

    pub async fn close(&self) {
        let entries = std::mem::take(&mut *self.connections.lock().await);
        for (_, connection) in entries {
            connection.client.close().await;
        }
    }

    async fn check(&self, expected: &Server, tool: Option<&str>) -> Result<Server, String> {
        let current = self
            .available()
            .await
            .into_iter()
            .find(|server| server.id == expected.id)
            .ok_or_else(|| format!("MCP 服务不可用：{}", expected.id))?;
        if !current.same_connection(expected)
            || tool.is_some_and(|name| {
                !current.allows_tool(name)
                    || !current.tools_cache.iter().any(|item| {
                        item["name"]
                            .as_str()
                            .is_some_and(|value| value.trim() == name)
                    })
            })
        {
            return Err(format!("MCP 工具或服务配置已变更：{}", expected.id));
        }
        Ok(current)
    }

    async fn connect<'a>(
        &self,
        entries: &'a mut HashMap<String, Connection>,
        mut server: Server,
        timeout: Option<u64>,
    ) -> Result<&'a mut Client, String> {
        let id = server.id.clone();
        if let Some(timeout) = timeout {
            server.timeout_seconds = timeout;
        }
        let prepared = Prepared::new(server, self.scope.as_ref());
        let prepared_timeout = prepared.server.timeout_seconds;
        let usable = entries.get_mut(&id).is_some_and(|entry| {
            entry.server.same_connection(&prepared.server) && entry.client.alive()
        });
        if !usable {
            entries.remove(&id);
            prepared
                .prepare_launch()
                .map_err(|error| format!("Could not prepare MCP profile: {error}"))?;
            let client = Client::connect(&prepared.server, self.registry.protocol()).await?;
            entries.insert(
                id.clone(),
                Connection {
                    server: prepared.server,
                    client,
                },
            );
        }
        let client = &mut entries
            .get_mut(&id)
            .expect("MCP connection established")
            .client;
        client.set_timeout(prepared_timeout);
        Ok(client)
    }

    async fn discover(&self, expected: &Server, timeout: Option<u64>) -> Result<(), String> {
        let mut entries = self.connections.lock().await;
        let current = self.check(expected, None).await?;
        let result = async {
            let client = self.connect(&mut entries, current, timeout).await?;
            self.check(expected, None).await?;
            collect_tools(client).await
        }
        .await;
        if result.is_err() {
            entries.remove(&expected.id);
        }
        // A discovery from a scope revoked in flight must not publish its result.
        self.check(expected, None).await?;
        let updated = self
            .registry
            .update_cache(expected, &result)
            .map_err(|_| "Could not update MCP tools cache")?;
        if !updated {
            return Err("MCP settings changed during discovery".into());
        }
        result.map(|_| ())
    }

    pub(super) async fn sync(&self, id: Option<&str>) -> Value {
        let mut servers = self.available().await;
        self.prune(&servers).await;
        if let Some(id) = id {
            let result = if let Some(server) = servers.iter().find(|server| server.id == id) {
                self.discover(server, None).await
            } else {
                Err(format!("找不到可用的 MCP 服务：{id}"))
            };
            return json!({"success": result.is_ok(), "server_id": id, "error": result.err()});
        }
        servers.sort_by(|a, b| a.id.cmp(&b.id));
        let mut succeeded = 0;
        for server in &servers {
            if self.discover(server, None).await.is_ok() {
                succeeded += 1;
            }
        }
        json!({"success": succeeded == servers.len(), "servers_total": servers.len(),
            "servers_success": succeeded, "servers_failed": servers.len() - succeeded})
    }

    /// Dispatch using the aliases supplied to the model, and check current
    /// settings under the connection lock before sending each RPC.
    pub async fn execute(
        &self,
        snapshot: &ToolSnapshot,
        name: &str,
        mut arguments: Value,
    ) -> Value {
        let Some(fields) = arguments.as_object_mut() else {
            return error("MCP tool arguments must be an object");
        };
        fields.shift_remove("intent");
        if name == LIST_TOOL && snapshot.can_list {
            return self.list(&arguments).await;
        }
        let Some(binding) = snapshot.bindings.get(name) else {
            return error(format!("找不到 MCP 工具映射：{name}"));
        };
        let started = Instant::now();
        let mut entries = self.connections.lock().await;
        let result = async {
            let current = self.check(&binding.server, Some(&binding.tool)).await?;
            let client = self.connect(&mut entries, current, None).await?;
            self.check(&binding.server, Some(&binding.tool)).await?;
            client
                .request(
                    "tools/call",
                    json!({"name": binding.tool, "arguments": arguments}),
                )
                .await
        }
        .await;
        if result.is_err() {
            entries.remove(&binding.server.id);
        }
        let mut value = json!({"server_id": binding.server.id, "server_name": binding.server.name,
            "tool_alias": name, "tool_name": binding.tool, "transport": binding.server.transport});
        match result {
            Err(message) => {
                value["success"] = json!(false);
                value["error"] = json!(message);
            }
            Ok(result) => {
                let is_error = result["isError"].as_bool().unwrap_or(false);
                let content = result["content"].as_array().cloned().unwrap_or_default();
                value["success"] = json!(!is_error);
                value["is_error"] = json!(is_error);
                value["elapsed_ms"] = json!(started.elapsed().as_millis() as u64);
                value["message"] = json!(call_message(&content, &result["structuredContent"]));
                value["preview"] = json!(content_preview(&content));
                value["content"] = json!(content);
                value["structured_content"] = result["structuredContent"].clone();
                value["raw_result"] = result;
            }
        }
        value
    }

    async fn list(&self, arguments: &Value) -> Value {
        let id = arguments["server_id"]
            .as_str()
            .map(str::trim)
            .filter(|id| !id.is_empty());
        let sync = if arguments["refresh"] == true {
            let mut sync = self.sync(id).await;
            sync.as_object_mut().unwrap().shift_remove("error");
            sync
        } else {
            Value::Null
        };
        let servers = self.available().await;
        let snapshot = self.snapshot(&servers);
        let listed: Vec<_> = servers.iter().filter(|server| id.is_none_or(|id| server.id == id)).map(|server| {
            let visible = snapshot.bindings.iter().filter(|(_, binding)| binding.server.id == server.id);
            let names: BTreeSet<_> = visible.clone().map(|(_, binding)| &binding.tool).collect();
            let aliases: Vec<_> = visible.map(|(name, _)| name).collect();
            json!({"id": server.id, "name": server.name, "enabled": server.enabled, "transport": server.transport,
                "timeout_seconds": server.timeout_seconds, "tools_cache_count": names.len(), "tools_cache_names": names,
                "tool_aliases": aliases, "tools_cache_updated_at": server.tools_cache_updated_at,
                "last_error": if server.last_error.is_empty() { "" } else { "MCP sync failed" } })
        }).collect();
        json!({"success": true, "count": listed.len(), "servers": listed, "sync_result": sync})
    }
}

async fn collect_tools(client: &mut Client) -> Result<Vec<Value>, String> {
    let mut output = Vec::new();
    let mut cursor = None::<String>;
    for _ in 0..MAX_PAGES {
        let params = cursor
            .as_ref()
            .map_or_else(|| json!({}), |cursor| json!({"cursor": cursor}));
        let result = client.request("tools/list", params).await?;
        for item in result["tools"].as_array().into_iter().flatten() {
            let Some(name) = item["name"]
                .as_str()
                .map(str::trim)
                .filter(|name| !name.is_empty())
            else {
                continue;
            };
            output.push(json!({"name": name, "description": item["description"].as_str().unwrap_or_default(),
                "title": item["title"].as_str().unwrap_or_default(),
                "inputSchema": item["inputSchema"].as_object().cloned().map(Value::Object).unwrap_or_else(|| schema(&Value::Null))}));
        }
        cursor = result["nextCursor"]
            .as_str()
            .filter(|cursor| !cursor.is_empty())
            .map(str::to_owned);
        if cursor.is_none() {
            break;
        }
    }
    Ok(output)
}

fn schema(input: &Value) -> Value {
    let mut schema = input
        .as_object()
        .cloned()
        .map(Value::Object)
        .unwrap_or_else(|| json!({}));
    schema["type"] = json!("object");
    if !schema["properties"].is_object() {
        schema["properties"] = json!({});
    }
    if schema
        .get("required")
        .is_some_and(|required| !required.is_array())
    {
        schema.as_object_mut().unwrap().shift_remove("required");
    }
    schema
}

fn error(message: impl Into<String>) -> Value {
    json!({"success": false, "error": message.into()})
}

fn text(item: &Value, key: &str) -> String {
    item[key].as_str().unwrap_or_default().trim().to_owned()
}

fn call_message(content: &[Value], structured: &Value) -> String {
    let parts: Vec<_> = content
        .iter()
        .enumerate()
        .filter_map(|(index, item)| {
            let kind = item["type"].as_str()?;
            let line = match kind {
                "text" => text(item, "text"),
                "image" | "audio" => {
                    let mime = item["mimeType"]
                        .as_str()
                        .or(item["mime_type"].as_str())
                        .unwrap_or(if kind == "image" {
                            "image/png"
                        } else {
                            "audio/wav"
                        });
                    format!(
                        "[MCP {kind} #{}] {mime} ({} base64 chars)",
                        index + 1,
                        text(item, "data").len()
                    )
                }
                "resource_link" | "resourcelink" | "resource" => {
                    let resource_text = text(&item["resource"], "text");
                    if !resource_text.is_empty() {
                        return Some(resource_text);
                    }
                    let uri = [&item["resource"]["uri"], &item["uri"], &item["url"]]
                        .into_iter()
                        .filter_map(Value::as_str)
                        .find(|uri| !uri.is_empty())?;
                    let name = item["name"]
                        .as_str()
                        .or(item["title"].as_str())
                        .filter(|name| !name.is_empty());
                    name.map_or_else(
                        || format!("[MCP 资源] {uri}"),
                        |name| format!("[MCP 资源] {name} -> {uri}"),
                    )
                }
                _ => return None,
            };
            (!line.is_empty()).then_some(line)
        })
        .collect();
    let message = if parts.is_empty() && !structured.is_null() {
        structured.to_string()
    } else {
        parts.join("\n")
    };
    message.chars().take(500).collect()
}

fn content_preview(content: &[Value]) -> String {
    content
        .iter()
        .filter_map(|item| match item["type"].as_str()? {
            "text" => {
                let text = text(item, "text");
                (!text.is_empty()).then(|| text.chars().take(300).collect())
            }
            kind => Some(format!("[{kind}]")),
        })
        .take(4)
        .collect::<Vec<_>>()
        .join("\n")
}

/// Convert MCP output to the existing model content form. This keeps the
/// structured value and attaches MCP images when the selected model accepts them.
pub fn model_content(result: &Value, images: bool) -> Value {
    let message = result["message"]
        .as_str()
        .filter(|value| !value.is_empty())
        .or(result["error"].as_str())
        .unwrap_or_default();
    let mut blocks = vec![json!({"type": "text", "text": message})];
    if !result["structured_content"].is_null() {
        blocks.push(json!({"type": "text", "text": result["structured_content"].to_string()}));
    }
    if images {
        for item in result["content"].as_array().into_iter().flatten() {
            if item["type"] == "image"
                && let Some(data) = item["data"].as_str().filter(|data| !data.is_empty())
            {
                let mime = item["mimeType"].as_str().unwrap_or("image/png");
                blocks.push(json!({"type": "image_url", "image_url": {"url": format!("data:{mime};base64,{data}")}}));
            }
        }
    }
    if blocks.len() == 1 {
        json!(message)
    } else {
        json!(blocks)
    }
}

/// Keep tool-call pairs in their durable form and give vision models the MCP
/// images in a user content message immediately after the complete tool batch.
pub(crate) fn model_messages(messages: &[Value], images: bool) -> Cow<'_, [Value]> {
    let is_image_result = |message: &Value| {
        message["role"] == "tool" && message["content"].as_str().is_some_and(|content| {
            content.contains("\"tool_alias\"") && content.contains("\"image\"")
        })
    };
    if !messages.iter().any(&is_image_result) {
        return Cow::Borrowed(messages);
    }
    let mut projected = Vec::with_capacity(messages.len());
    let mut pending_images = Vec::new();
    for message in messages {
        if message["role"] != "tool" {
            push_image_message(&mut projected, &mut pending_images);
        }
        let mut message = message.clone();
        if is_image_result(&message)
            && let Ok(mut result) = serde_json::from_str::<Value>(message["content"].as_str().unwrap_or_default())
            && result["tool_alias"].as_str().is_some_and(|alias| alias.starts_with("mcp__"))
            && result["content"].as_array().is_some_and(|items| items.iter().any(|item| item["type"] == "image"))
        {
            if images && let Value::Array(blocks) = model_content(&result, true) {
                pending_images.extend(blocks.into_iter().filter(|block| block["type"] == "image_url"));
            }
            discard_image_bytes(&mut result["content"]);
            discard_image_bytes(&mut result["raw_result"]["content"]);
            message["content"] = json!(result.to_string());
        }
        projected.push(message);
    }
    push_image_message(&mut projected, &mut pending_images);
    Cow::Owned(projected)
}

fn discard_image_bytes(content: &mut Value) {
    for item in content.as_array_mut().into_iter().flatten() {
        if item["type"] == "image" && let Some(fields) = item.as_object_mut() {
            fields.shift_remove("data");
        }
    }
}

fn push_image_message(messages: &mut Vec<Value>, images: &mut Vec<Value>) {
    if images.is_empty() { return; }
    let content = std::iter::once(json!({"type":"text","text":"Images from the preceding MCP tool results."}))
        .chain(std::mem::take(images)).collect::<Vec<_>>();
    messages.push(json!({"role":"user","content":content}));
}
