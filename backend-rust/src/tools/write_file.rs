//! Approved UTF-8 writes and exact edits using the startup workspace capability.

mod edit;
mod transaction;

use std::borrow::Cow;
use std::collections::HashSet;
use std::path::Path;
use std::sync::{Arc, Mutex};

use serde_json::{Map, Value, json};

use crate::approval::{ExecutionAccess, ExecutionScope, TaskScope, ToolRequest};

use super::read_file::{ReadFile, access::Workspace, normalize_newlines};
use transaction::Destination;

pub const WRITE_NAME: &str = "write_file";
pub const EDIT_NAME: &str = "edit_file";

const NO_AUTHORITY: &str = "文件修改需要当前对话和启动工作区内的有效授权。";

pub fn write_file_tool() -> Value {
    json!({
        "type": "function",
        "function": {
            "name": WRITE_NAME,
            "description": "在启动工作区内写入 UTF-8 文件。append 为 false 时覆盖，为 true 时追加。启用编辑前预读检查时，覆盖已有文件前需先读取。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "要写入的工作区相对路径"},
                    "content": {"type": "string", "description": "要写入文件的内容"},
                    "append": {"type": "boolean", "default": false, "description": "是否追加到文件末尾"},
                    "intent": {"type": "string", "description": "用不超过 15 个字向用户说明这次写入的目的"}
                },
                "required": ["file_path", "content"]
            }
        }
    })
}

pub fn edit_file_tool() -> Value {
    json!({
        "type": "function",
        "function": {
            "name": EDIT_NAME,
            "description": "在启动工作区内依次执行多组精确字符串替换。启用编辑前预读检查时，修改前需先读取。任意一组失败时不会写入文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "要修改的工作区相对路径"},
                    "replacements": {
                        "type": "array",
                        "description": "按数组顺序执行的替换组",
                        "minItems": 1,
                        "maxItems": edit::MAX_GROUPS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_string": {"type": "string", "description": "从文件内容精确复制，至少包含目标行及上下文共 3 行"},
                                "new_string": {"type": "string", "description": "用于替换的新文本，必须不同于 old_string"},
                                "replace_all": {"type": "boolean", "enum": [true, false], "description": "true 替换全部匹配，false 或省略时仅替换首个匹配"}
                            },
                            "required": ["old_string", "new_string"]
                        }
                    },
                    "intent": {"type": "string", "description": "用不超过 15 个字向用户说明这次编辑的目的"}
                },
                "required": ["file_path", "replacements"]
            }
        }
    })
}

/// Keep one shared writer per startup workspace to serialize mutations.
pub struct WorkspaceFileWriter {
    reader: Arc<ReadFile>,
    mutations: Mutex<()>,
}

impl WorkspaceFileWriter {
    pub fn new(reader: Arc<ReadFile>) -> Self {
        Self {
            reader,
            mutations: Mutex::new(()),
        }
    }

    /// Consume the scope from the launch-time approval check. Use only server-owned
    /// conversation messages, including successful results from earlier calls in the batch.
    /// Runs synchronous filesystem I/O and belongs on a blocking worker.
    pub fn execute(
        &self,
        scope: ExecutionScope,
        conversation_id: &str,
        messages: &[Value],
    ) -> Value {
        self.execute_with_host_root(scope, conversation_id, messages, None, false)
    }

    pub(crate) fn execute_with_host_root(
        &self, scope: ExecutionScope, conversation_id: &str, messages: &[Value],
        host_root: Option<&super::read_file::HostFileRoot>, injected_memory: bool,
    ) -> Value {
        if !matches!(scope.access(), ExecutionAccess::SandboxWrite | ExecutionAccess::Direct) {
            return failure(NO_AUTHORITY);
        }
        if let Some(root) = host_root {
            let task = scope.task();
            if task.conversation_id() != conversation_id || !self.reader.0.matches_root(task.workspace_root()) {
                return failure(NO_AUTHORITY);
            }
            let reader = match root.open(&self.reader.0.data_path, true) {
                Ok(reader) => reader,
                Err(error) => return failure(error.to_string()),
            };
            let mut result = self.mutate(&reader.0, &scope.call().tool_name, &scope.call().arguments, messages, injected_memory)
                .unwrap_or_else(failure);
            if matches!(root, super::read_file::HostFileRoot::ProjectMemory { .. }) && result["success"] == true {
                result["path"] = scope.call().arguments["file_path"].clone();
            }
            return result;
        }
        self.execute_call(scope.task(), scope.call(), conversation_id, messages)
    }

    fn execute_call(
        &self,
        task: &TaskScope,
        call: &ToolRequest,
        conversation_id: &str,
        messages: &[Value],
    ) -> Value {
        let workspace = &self.reader.0;
        if task.conversation_id() != conversation_id
            || !workspace.matches_root(task.workspace_root())
        {
            return failure(NO_AUTHORITY);
        }
        self.mutate(workspace, &call.tool_name, &call.arguments, messages, false)
            .unwrap_or_else(failure)
    }

    pub(crate) fn requires_read(&self)->Result<bool,String>{
        crate::web_assets::personalization::require_read_before_edit(&self.reader.0.data_path).map_err(|e|e.to_string())
    }

    fn mutate(
        &self,
        workspace: &Workspace,
        tool: &str,
        args: &Map<String, Value>,
        messages: &[Value],
        injected_memory: bool,
    ) -> Result<Value, String> {
        let operation = Operation::parse(tool, args)?;
        let raw = args
            .get("file_path")
            .and_then(Value::as_str)
            .filter(|path| !path.trim().is_empty())
            .ok_or("缺少文件路径参数")?;
        let path = workspace
            .relative_path(raw)
            .map_err(transaction::write_path_error)?;
        let _lock = self.mutations.lock().map_err(|_| "文件写入状态不可用")?;
        let append = matches!(operation, Operation::Write { append: true, .. });
        let is_edit = matches!(operation, Operation::Edit(_));
        let require_read = !injected_memory && self.requires_read()?;
        if require_read && is_edit && !was_visited(workspace, &path, messages) {
            return Ok(pre_read(tool, raw));
        }
        let destination = Destination::open(workspace, &path, !is_edit)?;
        if require_read && !append && !is_edit && destination.exists() && !was_visited(workspace, &path, messages) {
            return Ok(pre_read(tool, raw));
        }
        let before = destination.read(is_edit.then_some(edit::MAX_INPUT_BYTES))?;

        let path_display = path.to_string_lossy();
        match operation {
            Operation::Write { content, append } => {
                let mode = if append { "a" } else { "w" };
                let encoded = native_bytes(content);
                let output = if append && let Some(previous) = &before {
                    let mut bytes = Vec::new();
                    bytes
                        .try_reserve_exact(previous.len().saturating_add(encoded.len()))
                        .map_err(|_| transaction::TOO_LARGE)?;
                    bytes.extend_from_slice(previous);
                    bytes.extend_from_slice(&encoded);
                    Cow::Owned(bytes)
                } else {
                    encoded
                };
                destination.commit(workspace, &path, &output)?;
                let after = decoded(output.into_owned()).unwrap_or_else(|| content.to_owned());
                Ok(write_result(
                    &path_display,
                    content,
                    mode,
                    before.and_then(decoded),
                    after,
                ))
            }
            Operation::Edit(replacements) => {
                let original = decoded(before.ok_or("文件不存在")?)
                    .ok_or("文件不是 UTF-8 文本，无法直接读取。")?;
                let edited = match edit::apply(&path_display, &original, replacements) {
                    Ok(edited) => edited,
                    Err(result) => return Ok(result),
                };
                let bytes = native_bytes(&edited.content);
                destination.commit(workspace, &path, &bytes)?;
                let after = decoded(bytes.into_owned()).expect("UTF-8 text written by edit_file");
                let mut result =
                    write_result(&path_display, &edited.content, "w", Some(original), after);
                result
                    .as_object_mut()
                    .expect("write result object")
                    .extend(edited.fields);
                Ok(result)
            }
        }
    }
}

enum Operation<'a> {
    Write { content: &'a str, append: bool },
    Edit(&'a [Value]),
}

impl<'a> Operation<'a> {
    fn parse(tool: &str, args: &'a Map<String, Value>) -> Result<Self, String> {
        match tool {
            WRITE_NAME => {
                let content = args
                    .get("content")
                    .map_or(Some(""), Value::as_str)
                    .ok_or("content 必须是字符串")?;
                let append = args
                    .get("append")
                    .map_or(Some(false), Value::as_bool)
                    .ok_or("append 必须是 true 或 false")?;
                Ok(Self::Write { content, append })
            }
            EDIT_NAME => {
                let replacements = args
                    .get("replacements")
                    .and_then(Value::as_array)
                    .filter(|items| !items.is_empty())
                    .ok_or("replacements 必须是非空数组")?;
                if replacements.len() > edit::MAX_GROUPS {
                    return Err("replacements 数量过多，最多支持 100 组".into());
                }
                Ok(Self::Edit(replacements))
            }
            _ => Err(NO_AUTHORITY.into()),
        }
    }
}

fn was_visited(workspace: &Workspace, target: &Path, messages: &[Value]) -> bool {
    let mut calls = HashSet::new();
    for message in messages {
        match message["role"].as_str() {
            Some("tool") => {
                let Some(id) = message["tool_call_id"].as_str() else {
                    continue;
                };
                if !calls.remove(id) {
                    continue;
                }
                let Some(result) = message["content"]
                    .as_str()
                    .and_then(|text| serde_json::from_str::<Value>(text).ok())
                else {
                    continue;
                };
                if result["success"] == true
                    && let Some(path) = result["path"].as_str()
                    && workspace.matches_read_result(path, target)
                {
                    return true;
                }
            }
            Some("assistant") => {
                calls.clear();
                calls.extend(
                    message["tool_calls"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(|call| {
                            let name = call["function"]["name"].as_str()?;
                            matches!(
                                name,
                                "read_file"
                                    | "read_skill"
                                    | "recall_project_memory"
                                    | WRITE_NAME
                                    | EDIT_NAME
                            )
                            .then_some(call["id"].as_str()?)
                        }),
                );
            }
            _ => calls.clear(),
        }
    }
    false
}

fn pre_read(tool: &str, path: &str) -> Value {
    json!({
        "success": false,
        "error": format!("调用 {tool} 前必须先使用 read_file 阅读目标文件"),
        "message": format!("文件 {path} 尚未在当前对话中通过 read_file 阅读。请先使用 read_file（read/search/extract 任一模式）读取后再编辑。"),
        "required_tool": "read_file",
        "required_path": path,
        "enforcement": "read_before_edit_required"
    })
}

fn write_result(
    path: &str,
    written: &str,
    mode: &str,
    before: Option<String>,
    after: String,
) -> Value {
    json!({"success": true, "path": path, "size": written.chars().count(),
        "mode": mode, "original_file": before, "new_file": after})
}

fn failure(error: impl Into<String>) -> Value {
    json!({"success": false, "error": error.into()})
}

fn decoded(mut bytes: Vec<u8>) -> Option<String> {
    normalize_newlines(&mut bytes);
    String::from_utf8(bytes).ok()
}

fn native_bytes(content: &str) -> Cow<'_, [u8]> {
    if cfg!(windows) {
        Cow::Owned(content.replace('\n', "\r\n").into_bytes())
    } else {
        Cow::Borrowed(content.as_bytes())
    }
}
