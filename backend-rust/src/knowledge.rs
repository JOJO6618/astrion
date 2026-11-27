//! Skills, memories, and browser preferences stored in the existing Host format.

pub mod browser;
mod browser_retention;
mod browser_sites;
pub mod global_memory;
pub mod migration;
pub(crate) mod prompt;
mod search;
mod skills;
pub(crate) mod storage;
mod topics;
pub mod web;

use std::{
    collections::BTreeMap,
    io,
    path::{Path, PathBuf},
};

use cap_std::fs::Dir;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub use skills::SkillSettings;
pub(crate) use topics::{lock as lock_topics, workspace_namespace};
pub(crate) use skills::valid_id as valid_skill_id;
pub(crate) use skills::composer_messages as composer_skill_messages;

pub const SEARCH_SKILLS: &str = "search_skills";
pub const READ_SKILL: &str = "read_skill";
pub const SEARCH_PROJECT_MEMORY: &str = "search_project_memory";
pub const RECALL_PROJECT_MEMORY: &str = "recall_project_memory";
pub const UPDATE_PROJECT_MEMORY: &str = "update_project_memory";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KnowledgeAccess {
    ReadOnly,
    ReadWrite,
}

impl KnowledgeAccess {
    pub fn allows(self, name: &str) -> bool {
        match tool_access(name) {
            Some(Self::ReadOnly) => true,
            Some(Self::ReadWrite) => self == Self::ReadWrite,
            None => false,
        }
    }
}

pub fn tool_access(name: &str) -> Option<KnowledgeAccess> {
    match name {
        SEARCH_SKILLS | READ_SKILL | SEARCH_PROJECT_MEMORY | RECALL_PROJECT_MEMORY => {
            Some(KnowledgeAccess::ReadOnly)
        }
        UPDATE_PROJECT_MEMORY => Some(KnowledgeAccess::ReadWrite),
        _ => None,
    }
}

pub fn is_read_only_tool(name: &str) -> bool {
    KnowledgeAccess::ReadOnly.allows(name)
}

/// OpenAI Chat-style definitions suitable for extension into the ToolSet schema list.
pub fn definitions(access: KnowledgeAccess) -> Vec<Value> {
    let search_properties = || {
        json!({
            "keywords": {
                "type": "array", "minItems": 1, "maxItems": 5,
                "items": {"type": "string"},
                "description": "建议提供 2~5 个精确关键词，任一关键词命中即算匹配。"
            },
            "max_results": {"type": "integer", "description": "最多返回几条，默认 5，最大 10。"}
        })
    };
    let mut tools = vec![
        definition(
            SEARCH_SKILLS,
            "在 .astrion/skills/ 与 .agents/skills/ 的可用技能名称、描述和正文中按字面检索。请带上英文关键词。命中后用 read_skill 阅读全文，未命中时请按常规方式继续。",
            search_properties(),
            &["keywords"],
        ),
        definition(
            READ_SKILL,
            "按技能 ID 或名称读取 SKILL.md 全文。同名重复时用 read_file 按具体路径读取。",
            json!({"skill_name": {"type": "string", "description": "技能 ID 或名称"}}),
            &["skill_name"],
        ),
        definition(
            SEARCH_PROJECT_MEMORY,
            "按关键词检索当前工作区的索引式项目记忆。命中后用 recall_project_memory 阅读全文，未命中时继续当前任务。",
            search_properties(),
            &["keywords"],
        ),
        definition(
            RECALL_PROJECT_MEMORY,
            "按名称读取当前工作区的专题记忆全文（含 frontmatter）。",
            json!({"name": {"type": "string", "description": "专题名称，不带 .md 后缀"}}),
            &["name"],
        ),
    ];
    if access.allows(UPDATE_PROJECT_MEMORY) {
        tools.push(definition(UPDATE_PROJECT_MEMORY,
            "创建或覆盖当前工作区的专题记忆。覆盖之前请在本对话使用 recall_project_memory 读取全文。",
            json!({
                "name": {"type": "string", "description": "专题名称，不带 .md 后缀，建议用英文加下划线"},
                "description": {"type": "string", "description": "记忆索引使用的单行描述"},
                "content": {"type": "string", "description": "专题正文（Markdown）"}
            }), &["name", "description", "content"]));
    }
    tools
}

fn definition(name: &str, description: &str, mut properties: Value, required: &[&str]) -> Value {
    properties["intent"] = json!({"type": "string", "description": "用不超过 15 个字说明操作目的"});
    json!({"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}
    }})
}

/// The runtime owns one instance per conversation. Serialize alongside session state when
/// update_project_memory is enabled. Full recalls and successful writes record known versions.
#[derive(Debug, Default, Deserialize, Serialize)]
pub struct KnowledgeReads(BTreeMap<PathBuf, String>);

/// Returns the path a generic file editor may treat as read after a knowledge tool call.
pub fn full_read_path<'a>(name: &str, result: &'a Value) -> Option<&'a str> {
    if matches!(name, READ_SKILL | RECALL_PROJECT_MEMORY)
        && result["success"] == true
        && result["truncated"] == false
    {
        result["path"].as_str()
    } else {
        None
    }
}

#[derive(Debug)]
pub struct Knowledge {
    workspace: Dir,
    data: Dir,
    data_identity: storage::Identity,
    memory_namespace: String,
    topics_path: PathBuf,
    skill_settings: Option<SkillSettings>,
}

impl Knowledge {
    /// Bind to the startup workspace and existing application data directory chosen by trusted code.
    /// Opening never creates the memory namespace or scans the workspace.
    pub fn open(workspace: &Path, data_dir: &Path) -> io::Result<Self> {
        let workspace_path = workspace.canonicalize()?;
        let data_path = data_dir.canonicalize()?;
        if workspace_path.parent().is_none() || workspace_path.starts_with(&data_path) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Invalid knowledge roots",
            ));
        }
        let workspace = storage::open_canonical_dir(&workspace_path)?;
        let data = storage::open_canonical_dir(&data_path)?;
        let data_identity = storage::Identity::of(&data.dir_metadata()?);
        if data_identity == storage::Identity::of(&workspace.dir_metadata()?) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Invalid knowledge roots",
            ));
        }
        let memory_namespace = topics::workspace_namespace(&workspace_path)?;
        let topics_path = data_path
            .join("memory")
            .join(&memory_namespace)
            .join("topics");
        Ok(Self {
            workspace,
            data,
            data_identity,
            memory_namespace,
            topics_path,
            skill_settings: None,
        })
    }

    /// Supply settings from an existing trusted binding in place of loading personalization.json.
    pub fn with_skill_settings(mut self, settings: SkillSettings) -> Self {
        self.skill_settings = Some(settings);
        self
    }

    pub fn skill_list_lines(&self) -> Result<Vec<String>, String> {
        skills::list_lines(self)
    }

    pub fn global_memory(&self) -> io::Result<global_memory::GlobalMemory> {
        global_memory::GlobalMemory::from_data(&self.data)
    }

    /// Read-only ToolSet adapter. Call on a blocking thread.
    pub fn execute_read_only(&self, name: &str, arguments: &Value) -> Option<Value> {
        self.execute(
            name,
            arguments,
            KnowledgeAccess::ReadOnly,
            &mut KnowledgeReads::default(),
        )
    }

    /// Dispatch a known tool with caller-authorized access and conversation-owned state.
    /// A rejected write is a tool error. Unknown names are left to the next dispatcher.
    pub fn execute(
        &self,
        name: &str,
        arguments: &Value,
        access: KnowledgeAccess,
        reads: &mut KnowledgeReads,
    ) -> Option<Value> {
        tool_access(name)?;
        if !access.allows(name) {
            return Some(
                json!({"success": false, "error_code": "permission_denied", "error": "当前任务没有项目记忆写入权限。"}),
            );
        }
        if !arguments.is_object() {
            return Some(json!({"success": false, "error": "工具参数必须是对象。"}));
        }
        let result = match name {
            SEARCH_SKILLS => skills::search(self, arguments),
            READ_SKILL => skills::read(self, arguments),
            SEARCH_PROJECT_MEMORY => topics::search(self, arguments),
            RECALL_PROJECT_MEMORY => topics::read(self, arguments, reads),
            UPDATE_PROJECT_MEMORY => topics::update(self, arguments, reads),
            _ => unreachable!("known knowledge tool"),
        };
        Some(result.unwrap_or_else(|error| json!({"success": false, "error": error})))
    }
}

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/knowledge_contract.rs"]
mod contract_tests;
#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/knowledge_rest_contract.rs"]
mod rest_contract_tests;
