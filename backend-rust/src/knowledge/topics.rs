use std::{
    io::{self, ErrorKind},
    path::Path,
    sync::{Mutex, MutexGuard},
};

use cap_std::fs::Dir;
use serde_json::{Value, json};

use super::{
    Knowledge, KnowledgeReads, RECALL_PROJECT_MEMORY, SEARCH_PROJECT_MEMORY, full_read_path,
    search::{Entry, Frontmatter, Hit, Kind, Query},
    storage::{Document, MAX_BYTES, display_io, ensure_dir, optional_dir, publish, version},
};

const MIGRATION_NOTICE: &str = "00_READ_ME_项目记忆已迁移.md";
// The version check and atomic publication share a lock across in-process tool instances.
static TOPIC_IO: Mutex<()> = Mutex::new(());

pub(crate) fn workspace_namespace(project: &Path) -> io::Result<String> {
    let name = project
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or(ErrorKind::InvalidData)?;
    let identity = project.to_str().ok_or(ErrorKind::InvalidData)?;
    #[cfg(windows)]
    let identity = if let Some(unc) = identity.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}").to_lowercase()
    } else {
        identity
            .strip_prefix(r"\\?\")
            .unwrap_or(identity)
            .to_lowercase()
    };
    let mut slug = String::new();
    let mut in_separator = false;
    for ch in name.chars() {
        if ch.is_alphanumeric() || matches!(ch, '_' | '.' | '-') {
            slug.push(ch);
            in_separator = false;
        } else if !in_separator {
            slug.push('-');
            in_separator = true;
        }
    }
    let slug: String = slug.trim_matches(['.', '-']).chars().take(48).collect();
    let slug = if slug.is_empty() { "workspace" } else { &slug };
    let hash = version(identity.as_bytes());
    Ok(format!("{slug}-{}", &hash[..16]))
}

pub(crate) fn lock() -> Result<MutexGuard<'static, ()>, String> {
    TOPIC_IO.lock().map_err(|_| "项目记忆暂时无法访问。".into())
}

fn directory(knowledge: &Knowledge, create: bool) -> io::Result<Option<Dir>> {
    let mut current = None;
    for name in ["memory", &knowledge.memory_namespace, "topics"] {
        let parent = current.as_ref().unwrap_or(&knowledge.data);
        if create {
            current = Some(ensure_dir(parent, name)?);
        } else {
            current = optional_dir(parent, name)?;
            if current.is_none() {
                return Ok(None);
            }
        }
    }
    Ok(current)
}

fn safe_topic_name(name: &str) -> bool {
    !name.trim().is_empty()
        && !matches!(name, "." | "..")
        && !name.contains(['/', '\\', ':'])
        && !name.chars().any(char::is_control)
        && !windows_alias(name)
}

#[cfg(windows)]
fn windows_alias(name: &str) -> bool {
    let stem = name
        .split('.')
        .next()
        .unwrap_or_default()
        .trim_end()
        .to_ascii_uppercase();
    let device = matches!(
        stem.as_str(),
        "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
    ) || stem
        .strip_prefix("COM")
        .or_else(|| stem.strip_prefix("LPT"))
        .is_some_and(|number| {
            matches!(
                number,
                "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
            )
        });
    device
        || stem.rsplit_once('~').is_some_and(|(_, digits)| {
            !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
        })
}

#[cfg(not(windows))]
fn windows_alias(_: &str) -> bool {
    false
}

fn topic_id(filename: &str) -> Option<&str> {
    filename
        .strip_suffix(".md")
        .filter(|name| filename != MIGRATION_NOTICE && safe_topic_name(name))
}

pub(super) fn index(knowledge: &Knowledge) -> Vec<String> {
    let Ok(_lock) = lock() else { return Vec::new() };
    directory(knowledge, false).ok().flatten()
        .map_or_else(Vec::new, |dir| super::prompt::topic_lines(&dir, topic_id))
}

fn name_and_filename(arguments: &Value) -> Result<(&str, String), String> {
    let name = arguments["name"].as_str().unwrap_or_default().trim();
    let filename = format!("{name}.md");
    if topic_id(&filename).is_none() {
        return Err(format!("记忆名称不合法: {name}"));
    }
    Ok((name, filename))
}

pub(super) fn search(knowledge: &Knowledge, arguments: &Value) -> Result<Value, String> {
    let query = Query::parse(arguments, SEARCH_PROJECT_MEMORY)?;
    let _lock = lock()?;
    let Some(dir) = directory(knowledge, false).map_err(display_io)? else {
        return Ok(query.result(Kind::Topic, Vec::new(), true));
    };
    let mut filenames = Vec::new();
    for item in dir.entries().map_err(display_io)? {
        let item = item.map_err(display_io)?;
        let Ok(filename) = item.file_name().into_string() else {
            continue;
        };
        if topic_id(&filename).is_some() && item.file_type().map_err(display_io)?.is_file() {
            filenames.push(filename);
        }
    }
    filenames.sort_unstable();
    let mut hits = Vec::new();
    for filename in filenames {
        let id = filename
            .strip_suffix(".md")
            .expect("indexed Markdown filename");
        let Ok(doc) = Document::open(&dir, &filename) else {
            continue;
        };
        let meta = Frontmatter::parse(&doc.text, false);
        let Some(matches) = query.matches(id, &meta, &doc.text) else {
            continue;
        };
        let name = if meta.name.is_empty() { id } else { meta.name };
        hits.push(Hit {
            entry: Entry::Topic {
                id: id.into(),
                name: name.into(),
                file: filename,
            },
            description: meta.description.into(),
            matches,
        });
    }
    Ok(query.result(Kind::Topic, hits, false))
}

pub(super) fn read(
    knowledge: &Knowledge,
    arguments: &Value,
    reads: &mut KnowledgeReads,
) -> Result<Value, String> {
    let (name, filename) = name_and_filename(arguments)?;
    let _lock = lock()?;
    let dir = directory(knowledge, false)
        .map_err(display_io)?
        .ok_or("文件不存在")?;
    let doc = Document::open(&dir, &filename).map_err(display_io)?;
    let path = knowledge.topics_path.join(&filename);
    let mut result = doc.read_result(&path.to_string_lossy());
    result["memory_name"] = json!(name);
    if full_read_path(RECALL_PROJECT_MEMORY, &result).is_some() {
        reads.0.insert(path, doc.version);
    }
    Ok(result)
}

pub(super) fn update(
    knowledge: &Knowledge,
    arguments: &Value,
    reads: &mut KnowledgeReads,
) -> Result<Value, String> {
    let (name, filename) = name_and_filename(arguments)?;
    let description = text_argument(arguments, "description")?;
    if description.contains(['\r', '\n']) {
        return Err("记忆描述必须为单行。".into());
    }
    let content = text_argument(arguments, "content")?;
    let markdown = format!("---\nname: {name}\ndescription: {description}\n---\n\n{content}\n");
    if markdown.len() as u64 > MAX_BYTES {
        return Err("项目记忆超过大小限制。".into());
    }
    let _lock = lock()?;
    let dir = directory(knowledge, true)
        .map_err(display_io)?
        .expect("created namespace");
    let path = knowledge.topics_path.join(&filename);
    let exists = match Document::open(&dir, &filename) {
        Ok(current) => {
            match reads.0.get(&path) {
                None => {
                    return Ok(json!({"success": false, "error_code": "read_before_edit",
                    "error": "覆盖已有专题前，请在本对话使用 recall_project_memory 读取全文。"}));
                }
                Some(known) if *known != current.version => {
                    return Ok(json!({"success": false,
                    "error_code": "project_memory_conflict", "error": "专题记忆已更新，请重新读取全文并合并后重试。"}));
                }
                Some(_) => {}
            }
            true
        }
        Err(error) if error.kind() == ErrorKind::NotFound => false,
        Err(error) => return Err(display_io(error)),
    };
    publish(&dir, &filename, markdown.as_bytes(), exists).map_err(display_io)?;
    reads.0.insert(path.clone(), version(markdown.as_bytes()));
    let action = if exists { "已更新" } else { "已创建" };
    Ok(json!({"success": true, "memory_name": name, "path": path,
        "summary": format!("{action}项目记忆: {name}")}))
}

fn text_argument<'a>(arguments: &'a Value, field: &str) -> Result<&'a str, String> {
    arguments[field]
        .as_str()
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| format!("{field} 不能为空"))
}
