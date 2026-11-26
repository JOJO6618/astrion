//! Deferred definitions remain in the checkpointed tool history.
use std::collections::HashSet;

use serde_json::{Value, json};

use crate::{conversation_db::Database, web_assets::personalization::deferred_registry};

use super::Runtime;

pub(super) const NAME: &str = "load_tools";

#[derive(Clone)]
pub(super) struct State {
    deferred: Vec<String>,
    exposed: Vec<String>,
    loaded: Vec<String>,
}

impl State {
    fn parse(raw: &Value) -> Option<Self> {
        if raw["enabled"] != true || !raw["deferred_set"].is_array() {
            return None;
        }
        let supported: HashSet<_> = deferred_registry().iter()
            .flat_map(|group| group["tools"].as_array().into_iter().flatten())
            .filter_map(Value::as_str).collect();
        let deferred: Vec<_> = names(&raw["deferred_set"]).into_iter()
            .filter(|name| supported.contains(name.as_str())).collect();
        let loaded = names(&raw["loaded"]).into_iter().filter(|name| deferred.contains(name)).collect();
        Some(Self { deferred, loaded, exposed: names(&raw["initial_exposed"]) })
    }

    fn value(&self) -> Value {
        let pending: Vec<_> = self.deferred.iter().filter(|name| !self.loaded.contains(name)).collect();
        json!({"enabled":true,"deferred_set":self.deferred,"initial_exposed":self.exposed,"loaded":self.loaded,"pending":pending})
    }

    pub(super) fn pending(&self, tool: &str) -> bool {
        self.deferred.iter().any(|name| name == tool) && !self.loaded.iter().any(|name| name == tool)
    }

    pub(super) fn active(&self) -> bool {
        !self.deferred.is_empty()
    }

    pub(super) fn schemas(&self, complete: &[Value]) -> Vec<Value> {
        let mut schemas: Vec<_> = complete.iter().map(|spec| {
            match name(spec) {
                Some(tool) if self.deferred.iter().any(|item| item == tool) => stub(tool, spec),
                _ => spec.clone(),
            }
        }).collect();
        if self.active() {
            schemas.push(schema());
        }
        schemas
    }

    pub(super) fn load(&mut self, arguments: &Value, complete: &[Value]) -> Value {
        let requested = names(&arguments["tool_names"]);
        if requested.is_empty() {
            return failure("tool_names 需要包含至少一个工具名称。");
        }
        if requested.iter().any(|tool| !self.deferred.contains(tool)) {
            return failure("只能加载当前对话按需加载目录中的工具。");
        }
        let mut new = Vec::new();
        let mut old = Vec::new();
        let mut unavailable = Vec::new();
        let mut definitions = Vec::new();
        for tool in &requested {
            if let Some(definition) = complete.iter().find(|spec| name(spec) == Some(tool)) {
                definitions.push(definition.clone());
                if self.pending(tool) { new.push(tool.clone()); } else { old.push(tool.clone()); }
            } else {
                unavailable.push(tool);
            }
        }
        if definitions.is_empty() {
            return json!({"success":false,"error":"这些工具当前不可用。","unavailable":unavailable});
        }
        if new.is_empty() {
            return failure("请求的工具已加载。请直接调用目标工具。");
        }
        self.loaded.extend(new.iter().cloned());
        json!({"success":true,"tools":definitions,"loaded_now":new,"already_loaded":old,
            "unavailable":unavailable,"message":"完整定义已返回。请直接调用目标工具。"})
    }
}

impl Runtime {
    pub(super) fn deferred_tools(&self, task_id: &str, complete: &[Value]) -> Result<Option<State>, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        let task = super::writable_task(&mut store, task_id)?;
        let id = task.conversation_id.clone();
        let workspace = task.workspace_id.clone();
        let path = store.conversation_path(&workspace, &id).map_err(|error| error.message)?;
        let mut db = Database::open(path.parent().ok_or("Missing conversation directory")?)?;
        let Some(mut state) = db.field(&id, "metadata", "tool_loading")?.as_ref().and_then(State::parse) else {
            return Ok(None);
        };
        if state.active() && state.exposed.is_empty() {
            state.exposed = complete.iter().filter_map(name)
                .filter(|name| !state.deferred.iter().any(|value| value == name)).map(str::to_owned).collect();
            state.exposed.push(NAME.into());
            db.set_field(&id, "metadata", "tool_loading", &state.value())?;
        }
        Ok(Some(state))
    }
}

/// Apply loaded state only in the same database write as the tool definition receipt.
pub(super) fn checkpoint(document: &Value, batch: &[Value]) -> Option<Value> {
    let mut state = State::parse(&document["metadata"]["tool_loading"])?;
    let assistant = batch.first()?.get("tool_calls")?.as_array()?;
    let receipts: HashSet<_> = assistant.iter().filter(|call| call["function"]["name"] == NAME)
        .filter_map(|call| call["id"].as_str()).collect();
    let prior = state.loaded.len();
    for result in batch.iter().skip(1).filter(|row| row["role"] == "tool"
        && row["tool_call_id"].as_str().is_some_and(|id| receipts.contains(id)))
    {
        let Some(value) = result["content"].as_str().and_then(|raw| serde_json::from_str::<Value>(raw).ok())
            .filter(|value| value["success"] == true && value["tools"].is_array()) else { continue; };
        for tool in names(&value["loaded_now"]) {
            if state.pending(&tool) {
                state.loaded.push(tool);
            }
        }
    }
    (prior != state.loaded.len()).then(|| state.value())
}

pub(super) fn catalog(raw: &Value) -> String {
    let Some(state) = State::parse(raw) else { return String::new(); };
    deferred_registry().iter().filter_map(|group| {
        let tools: Vec<_> = group["tools"].as_array()?.iter().filter_map(Value::as_str)
            .filter(|name| state.deferred.iter().any(|value| value == name)).collect();
        if tools.is_empty() { return None; }
        let (label, hint) = match group["key"].as_str()? {
            "schedules" => ("定时任务", "管理定时 Agent 计划"),
            "git_worktree" => ("Git worktree", "进入工作树或返回工作区"),
            "workflow" => ("工作流", "查询、创建或推进工作流"),
            "sub_agent" => ("子智能体", "委派、查询和沟通独立任务"),
            "conversation" => ("对话回顾", "查询或回顾历史对话"),
            "memory_write" => ("记忆写入", "维护用户和项目记忆"),
            "personalization" => ("个性化", "维护个人偏好"),
            "skill_create" => ("技能", "创建可复用技能"),
            "mcp" => ("MCP", "查看 MCP 服务"),
            _ => ("其它", "按工具描述使用"),
        };
        Some(format!("- {label}（{}）：{hint}", tools.join(", ")))
    }).collect::<Vec<_>>().join("\n")
}

pub(super) fn blocked(tool: &str) -> Value {
    json!({"success":false,"tool_not_loaded":true,
        "error":format!("工具 {tool} 未加载。请先调用 load_tools 获取完整定义。")})
}

pub(super) fn failure(message: &str) -> Value {
    json!({"success":false,"error":message})
}

fn name(spec: &Value) -> Option<&str> {
    spec["function"]["name"].as_str()
}

fn names(value: &Value) -> Vec<String> {
    let mut seen = HashSet::new();
    value.as_array().into_iter().flatten().filter_map(Value::as_str).map(str::trim)
        .filter(|name| !name.is_empty() && seen.insert(*name)).map(str::to_owned).collect()
}

fn schema() -> Value {
    json!({"type":"function","function":{
        "name":NAME,"description":"按名加载目录内尚未加载的工具定义。拿到定义后直接调用；压缩后须重新加载。",
        "parameters":{"type":"object","properties":{
            "tool_names":{"type":"array","items":{"type":"string"},"minItems":1},
            "intent":{"type":"string","description":"操作目的"}
        },"required":["tool_names"]}
    }})
}

fn stub(name: &str, schema: &Value) -> Value {
    let description = schema["function"]["description"].as_str().unwrap_or_default();
    let summary = description.split(['\n', '。']).next().unwrap_or_default().trim();
    let short: String = summary.chars().take(60).collect();
    let description = format!("{short}（调用前通过 load_tools 加载完整定义）");
    json!({"type":"function","function":{"name":name,"description":description,
        "parameters":{"type":"object","properties":{}}
    }})
}
