//! UTF-8 read, literal search, and line extraction within the startup workspace.

pub(super) mod access;
mod text;

pub(super) use text::normalize_newlines;

use std::io;
use std::path::Path;

use serde_json::{Value, json};

use access::Workspace;
use text::{Extraction, LineRange, Search, TextFile};

pub const NAME: &str = "read_file";
const MAX_CHARS: usize = 50_000;
const MAX_FILE_BYTES: u64 = 100 * 1024 * 1024;

/// Open a workspace download using the same directory handles and privacy rules as the read tool.
pub(crate) fn open_download(root: &Path, data: &Path, path: &str) -> io::Result<std::fs::File> {
    let workspace = Workspace::open(root, data)?;
    let relative = workspace.relative_path(path)
        .map_err(|error| io::Error::new(io::ErrorKind::PermissionDenied, error))?;
    Ok(workspace.open_regular(&relative)?.into_std())
}

/// Chat tool definition. The Responses adapter flattens its function wrapper.
pub fn schema() -> Value {
    json!({
        "type": "function",
        "function": {
            "name": NAME,
            "description": "读取、搜索或按行抽取启动工作区内的 UTF-8 文本文件。通过 type 选择模式，支持限制返回字符数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "description": "用不超过 15 个字向用户说明这次读取的目的"},
                    "path": {"type": "string", "description": "工作区相对路径或工作区内的绝对路径"},
                    "type": {"type": "string", "enum": ["read", "search", "extract"], "description": "读取模式：read=阅读、search=搜索、extract=按行抽取"},
                    "max_chars": {"type": "integer", "description": "返回内容的最大字符数（默认及最大 50000）"},
                    "start_line": {"type": "integer", "description": "[read] 可选的起始行号（从 1 开始）"},
                    "end_line": {"type": "integer", "description": "[read] 可选的结束行号（>= start_line）"},
                    "query": {"type": "string", "description": "[search] 按字面匹配的搜索关键词"},
                    "max_matches": {"type": "integer", "description": "[search] 最多返回多少个命中窗口（默认 5，最大 50）"},
                    "context_before": {"type": "integer", "description": "[search] 命中行向上追加的行数（默认 1，最大 3）"},
                    "context_after": {"type": "integer", "description": "[search] 命中行向下追加的行数（默认 1，最大 5）"},
                    "case_sensitive": {"type": "boolean", "description": "[search] 是否区分大小写，默认 false"},
                    "segments": {
                        "type": "array",
                        "description": "[extract] 需要抽取的行区间",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "片段的可选标签"},
                                "start_line": {"type": "integer", "description": "起始行号（>= 1）"},
                                "end_line": {"type": "integer", "description": "结束行号（>= start_line）"}
                            },
                            "required": ["start_line", "end_line"]
                        },
                        "minItems": 1
                    }
                },
                "required": ["path", "type"]
            }
        }
    })
}

/// A read-only capability bound to one workspace at startup.
#[derive(Debug)]
pub struct ReadFile(pub(super) Workspace);

impl ReadFile {
    /// Open the trusted startup workspace and exclude the initialized runtime data directory.
    pub fn open(workspace: &Path, data_dir: &Path) -> io::Result<Self> {
        Workspace::open(workspace, data_dir).map(Self)
    }

    pub(crate) fn checked_relative_path(&self, path: &str) -> Result<std::path::PathBuf, String> {
        self.0.relative_path(path)
    }

    pub(crate) fn read_bytes(&self, path: &str) -> Result<Vec<u8>, String> {
        self.0.read(path).map(|(_, bytes, _)| bytes)
    }

    /// Execute a tool invocation. Call from a blocking thread when used in an async runtime.
    pub fn execute(&self, arguments: &Value) -> Value {
        self.run(arguments)
            .unwrap_or_else(|error| json!({"success": false, "error": error}))
    }

    fn run(&self, arguments: &Value) -> Result<Value, String> {
        let path = arguments["path"]
            .as_str()
            .filter(|path| !path.is_empty())
            .ok_or("缺少文件路径参数")?;
        let mode = Mode::parse(arguments)?;
        let max_chars = clamped_int(&arguments["max_chars"], MAX_CHARS, 1, MAX_CHARS);
        let (path, bytes, size) = self.0.read(path)?;
        let file = TextFile::new(bytes)?;
        let mut result = json!({
            "success": true,
            "type": mode.name(),
            "path": path,
            "encoding": "utf-8",
            "max_chars": max_chars,
            "truncated": false,
        });
        let fields = match mode {
            Mode::Read(range) => file.read(&path, size, range, max_chars)?,
            Mode::Search(search) => file.search(&path, size, search, max_chars),
            Mode::Extract(segments) => file.extract(&path, size, segments, max_chars)?,
        };
        result
            .as_object_mut()
            .expect("read_file result object")
            .extend(fields);
        Ok(result)
    }
}

enum Mode<'a> {
    Read(LineRange),
    Search(Search<'a>),
    Extract(Extraction<'a>),
}

impl<'a> Mode<'a> {
    fn parse(arguments: &'a Value) -> Result<Self, String> {
        let value = &arguments["type"];
        let mode = if truthy(value) {
            value.as_str().ok_or("type 必须是字符串")?.to_lowercase()
        } else {
            "read".to_owned()
        };
        match mode.as_str() {
            "read" => {
                let start = optional_line(&arguments["start_line"], "start_line")?.unwrap_or(1);
                let end = optional_line(&arguments["end_line"], "end_line")?.unwrap_or(usize::MAX);
                if end < start {
                    return Err("end_line 必须大于等于 start_line".into());
                }
                Ok(Self::Read(LineRange { start, end }))
            }
            "search" => {
                let query = arguments["query"]
                    .as_str()
                    .filter(|query| !query.is_empty())
                    .ok_or("搜索模式需要提供 query 参数")?;
                Ok(Self::Search(Search {
                    query,
                    max_matches: clamped_int(&arguments["max_matches"], 5, 1, 50),
                    before: clamped_int(&arguments["context_before"], 1, 0, 3),
                    after: clamped_int(&arguments["context_after"], 1, 0, 5),
                    case_sensitive: truthy(&arguments["case_sensitive"]),
                }))
            }
            "extract" => {
                let segments = arguments["segments"]
                    .as_array()
                    .filter(|items| !items.is_empty())
                    .ok_or("extract 模式需要提供 segments 数组")?;
                Ok(Self::Extract(Extraction { segments }))
            }
            _ => Err(format!("未知的读取类型: {mode}")),
        }
    }

    fn name(&self) -> &'static str {
        match self {
            Self::Read(_) => "read",
            Self::Search(_) => "search",
            Self::Extract(_) => "extract",
        }
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(value) => Some(i64::from(*value)),
        Value::Number(number) => number
            .as_i64()
            .or_else(|| number.as_f64().map(|value| value as i64)),
        Value::String(text) => {
            let text = text.trim();
            text.parse::<i64>().ok().or_else(|| {
                // Line numbers and limits beyond i64 have the same observable effect as the bound.
                let (negative, digits) = match text.as_bytes().first() {
                    Some(b'-') => (true, &text[1..]),
                    Some(b'+') => (false, &text[1..]),
                    _ => (false, text),
                };
                (!digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit()))
                    .then_some(if negative { i64::MIN } else { i64::MAX })
            })
        }
        _ => None,
    }
}

fn clamped_int(value: &Value, default: usize, min: usize, max: usize) -> usize {
    integer(value).map_or(default, |value| {
        value.clamp(min as i64, max as i64) as usize
    })
}

fn optional_line(value: &Value, field: &str) -> Result<Option<usize>, String> {
    if value.is_null() {
        return Ok(None);
    }
    let number = integer(value).ok_or_else(|| format!("{field} 必须是整数"))?;
    if number < 1 {
        return Err(format!("{field} 必须大于等于1"));
    }
    Ok(Some(usize::try_from(number).unwrap_or(usize::MAX)))
}
