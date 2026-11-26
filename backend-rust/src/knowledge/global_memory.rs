//! The Agent's numbered global memory files in the existing Host DATA_DIR.

use std::{
    io::{self, ErrorKind},
    path::Path,
    sync::Mutex,
};

use cap_std::fs::Dir;
use serde_json::{Value, json};

use super::storage::{MAX_BYTES, open_canonical_dir, publish, read_bytes};

pub const UPDATE_TOOL: &str = "update_memory";
static WRITES: Mutex<()> = Mutex::new(());

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MemoryKind {
    Main,
    Task,
}

impl MemoryKind {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "main" => Some(Self::Main),
            "task" => Some(Self::Task),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Main => "main",
            Self::Task => "task",
        }
    }

    fn filename(self) -> &'static str {
        match self {
            Self::Main => "memory.md",
            Self::Task => "task_memory.md",
        }
    }
}

/// The policy applies to Agent global memory independently of the task's workspace file scope.
/// The dispatcher grants `Manage` only after its normal tool availability and approval checks.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum GlobalMemoryAccess {
    Read,
    Manage,
}

pub fn definition(access: GlobalMemoryAccess) -> Option<Value> {
    (access == GlobalMemoryAccess::Manage).then(|| json!({
        "type": "function", "function": {
            "name": UPDATE_TOOL,
            "description": "按条目管理跨项目的全局长期记忆。项目私人约定优先维护工作区的 MEMORY.md，项目专题细节用 update_project_memory。",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["append", "replace", "delete"], "description": "操作类型"},
                    "content": {"type": "string", "description": "条目内容，追加或替换时填写"},
                    "index": {"type": "integer", "description": "替换或删除的序号，从 1 开始"},
                    "intent": {"type": "string", "description": "用不超过 15 个字说明操作目的"}
                },
                "required": ["operation"]
            }
        }
    }))
}

#[derive(Debug)]
pub struct GlobalMemory(Dir);

impl GlobalMemory {
    /// `data_dir` comes from trusted Host configuration. Reads do not initialize memory files.
    pub fn open(data_dir: &Path) -> io::Result<Self> {
        open_canonical_dir(&data_dir.canonicalize()?).map(Self)
    }

    pub(super) fn from_data(data: &Dir) -> io::Result<Self> {
        data.try_clone().map(Self)
    }

    pub fn entries(&self, kind: MemoryKind) -> io::Result<Vec<String>> {
        let _read = WRITES
            .lock()
            .map_err(|_| io::Error::other("Global memory lock is unavailable"))?;
        Ok(read(&self.0, kind)?.unwrap_or_default())
    }

    /// The conversation ID is supplied by the trusted task, never by model arguments.
    /// Call on a blocking thread when dispatched inside the async runtime.
    pub fn execute(
        &self,
        arguments: &Value,
        access: GlobalMemoryAccess,
        conversation_id: Option<&str>,
    ) -> Value {
        if access != GlobalMemoryAccess::Manage {
            return json!({"success": false, "error_code": "permission_denied", "error": "当前任务没有全局记忆写入权限。"});
        }
        let operation = match Operation::parse(arguments) {
            Ok(operation) => operation,
            Err(error) => return json!({"success": false, "error": error}),
        };
        self.update(operation, conversation_id)
            .unwrap_or_else(|_| json!({"success": false, "error": "无法写入全局记忆。"}))
    }

    fn update(&self, operation: Operation<'_>, conversation_id: Option<&str>) -> io::Result<Value> {
        let _write = WRITES
            .lock()
            .map_err(|_| io::Error::other("Global memory lock is unavailable"))?;
        let original = read(&self.0, MemoryKind::Main)?;
        let exists = original.is_some();
        let mut entries = original.unwrap_or_default();
        let index = operation.index();
        if let Some(index) = index
            && index > entries.len()
        {
            return Ok(
                json!({"success": false, "error": format!("序号 {index} 超出当前记忆条目数 {}", entries.len())}),
            );
        }
        match operation {
            Operation::Append(content) => entries.push(enrich(content, conversation_id)),
            Operation::Replace(index, content) => {
                entries[index - 1] = enrich(content, conversation_id)
            }
            Operation::Delete(index) => {
                entries.remove(index - 1);
            }
        }
        let mut markdown = String::new();
        for (index, entry) in entries.iter().enumerate() {
            use std::fmt::Write as _;
            writeln!(markdown, "{}. {}", index + 1, entry.trim()).expect("writing to String");
        }
        if markdown.len() as u64 > MAX_BYTES {
            return Err(io::Error::new(
                ErrorKind::InvalidData,
                "Global memory exceeds size limit",
            ));
        }
        publish(
            &self.0,
            MemoryKind::Main.filename(),
            markdown.as_bytes(),
            exists,
        )?;
        let mut result = json!({"success": true, "operation": operation.name(), "memory_type": "main", "count": entries.len()});
        if let Some(index) = index {
            result["index"] = json!(index);
        }
        Ok(result)
    }
}

fn read(data: &Dir, kind: MemoryKind) -> io::Result<Option<Vec<String>>> {
    let bytes = match read_bytes(data, kind.filename(), MAX_BYTES) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let content = std::str::from_utf8(&bytes)
        .map_err(|error| io::Error::new(ErrorKind::InvalidData, error))?;
    let entries = content
        .split([
            '\n', '\r', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\u{85}', '\u{2028}', '\u{2029}',
        ])
        .filter_map(numbered_entry)
        .map(str::to_owned)
        .collect();
    Ok(Some(entries))
}

fn numbered_entry(line: &str) -> Option<&str> {
    let (number, content) = line.trim_start().split_once('.')?;
    (!number.is_empty() && number.bytes().all(|byte| byte.is_ascii_digit())).then(|| content.trim())
}

#[derive(Clone, Copy)]
enum Operation<'a> {
    Append(&'a str),
    Replace(usize, &'a str),
    Delete(usize),
}

impl<'a> Operation<'a> {
    fn parse(arguments: &'a Value) -> Result<Self, String> {
        let action = arguments["operation"]
            .as_str()
            .ok_or("operation 不能为空")?;
        let content = arguments["content"]
            .as_str()
            .map(str::trim)
            .filter(|value| !value.is_empty());
        let index = arguments["index"]
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .filter(|n| *n != 0);
        match action.to_ascii_lowercase().as_str() {
            "append" => content
                .map(Self::Append)
                .ok_or_else(|| "append 需要 content".into()),
            "replace" => index
                .zip(content)
                .map(|(index, content)| Self::Replace(index, content))
                .ok_or_else(|| "replace 需要有效的 index 和 content（从1开始）".into()),
            "delete" => index
                .map(Self::Delete)
                .ok_or_else(|| "delete 需要有效的 index（从1开始）".into()),
            _ => Err(format!("未知操作: {action}")),
        }
    }

    fn index(self) -> Option<usize> {
        match self {
            Self::Append(_) => None,
            Self::Replace(index, _) | Self::Delete(index) => Some(index),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Append(_) => "append",
            Self::Replace(_, _) => "replace",
            Self::Delete(_) => "delete",
        }
    }
}

fn enrich(content: &str, conversation_id: Option<&str>) -> String {
    if content.starts_with('[') {
        return content.into();
    }
    let now = jiff::Zoned::now();
    let time = now.strftime("%Y-%m-%d %H:%M");
    let conversation_id = conversation_id.unwrap_or("unknown");
    format!("[{time}][{conversation_id}] {content}")
}
