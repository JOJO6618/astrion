//! Bounded, read-only sources for the Agent's initial system prompt.

use std::{io, path::Path, time::SystemTime};

use cap_fs_ext::DirExt;
use serde_json::{Value, json};

use super::{
    Knowledge,
    search::Frontmatter,
    storage::{MAX_BYTES, optional_dir, read_bytes, version},
};

fn text(dir: &cap_std::fs::Dir, file: &str, limit: u64) -> io::Result<Option<String>> {
    match read_bytes(dir, file, limit) {
        Ok(bytes) => String::from_utf8(bytes)
            .map(|text| Some(text.replace("\r\n", "\n").replace('\r', "\n")))
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

pub(crate) struct WorkspaceMemory {
    pub text: String,
    pub record: Value,
}

pub(crate) struct Rules {
    pub text: String,
    pub modified: Option<SystemTime>,
    pub children: Vec<String>,
}

impl Knowledge {
    pub(crate) fn prompt_preferences(&self) -> Value {
        let read = text(&self.data, "personalization.json", 4 * 1024 * 1024);
        read.ok()
            .flatten()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}))
    }

    pub(crate) fn prompt_global_memory(&self) -> String {
        text(&self.data, "memory.md", MAX_BYTES)
            .ok()
            .flatten()
            .unwrap_or_default()
    }

    pub(crate) fn prompt_project_memory(&self) -> Vec<String> {
        super::topics::index(self)
    }

    pub(crate) fn prompt_workspace_memory(&self) -> io::Result<WorkspaceMemory> {
        let saved = match optional_dir(&self.data, "memory")? {
            Some(dir) => match optional_dir(&dir, &self.memory_namespace)? {
                Some(dir) => text(&dir, "MEMORY.md", MAX_BYTES)?,
                None => None,
            },
            None => None,
        };
        let exists = saved.is_some();
        let text = saved.unwrap_or_default();
        let path = self.topics_path.with_file_name("MEMORY.md");
        let record = json!({"path":path, "exists":exists, "version":version(text.as_bytes())});
        Ok(WorkspaceMemory { text, record })
    }

    pub(crate) fn prompt_rules(&self, filename: &'static str) -> Option<Rules> {
        let text = text(&self.workspace, filename, 1024 * 1024).ok()??;
        if text.trim().is_empty() {
            return None;
        }
        let modified = self.workspace.symlink_metadata(filename).ok()
            .and_then(|meta| meta.modified().ok()).map(|time| time.into_std());
        let mut children: Vec<_> = self.workspace.entries().ok().into_iter().flatten()
            .filter_map(Result::ok)
            .filter_map(|entry| {
                let name = entry.file_name().into_string().ok()?;
                if name.starts_with('.') || matches!(name.as_str(), "node_modules" | "bower_components" | "venv" | "__pycache__" | "dist" | "build") {
                    return None;
                }
                let child = self.workspace.open_dir_nofollow(&name).ok()?;
                child.symlink_metadata(filename).ok()?.is_file().then(|| format!("{name}/{filename}"))
            })
            .collect();
        children.sort_unstable();
        Some(Rules { text, modified, children })
    }

    pub(crate) fn prompt_tree(&self) -> String {
        let mut names: Vec<_> = self.workspace.entries().ok().into_iter().flatten()
            .filter_map(Result::ok)
            .filter_map(|entry| {
                let name = entry.file_name().into_string().ok()?;
                if name.starts_with('.') || name.chars().any(char::is_control) {
                    return None;
                }
                let kind = entry.file_type().ok()?;
                if kind.is_dir() {
                    Some(format!("{name}/"))
                } else {
                    kind.is_file().then_some(name)
                }
            })
            .collect();
        names.sort_unstable();
        names.truncate(80);
        names.join("\n")
    }
}

pub(super) fn topic_lines(dir: &cap_std::fs::Dir, name_for: fn(&str) -> Option<&str>) -> Vec<String> {
    let mut topics: Vec<_> = dir.entries().ok().into_iter().flatten().filter_map(Result::ok)
        .filter_map(|file| {
            let filename = file.file_name().into_string().ok()?;
            let name = name_for(&filename)?;
            let metadata = file.metadata().ok()?;
            if !metadata.is_file() {
                return None;
            }
            let text = text(dir, &filename, MAX_BYTES).ok()??;
            let description = Frontmatter::parse(&text, false).description;
            let line = if description.is_empty() { name.into() } else { format!("{name}：{description}") };
            Some((metadata.modified().ok(), filename, line))
        }).collect();
    topics.sort_unstable_by(|(a_time, a_name, _), (b_time, b_name, _)| b_time.cmp(a_time).then(a_name.cmp(b_name)));
    topics.into_iter().map(|(_, _, line)| line).collect()
}

pub(crate) fn workspace_name(path: &Path) -> &str {
    path.file_name().and_then(|name| name.to_str()).unwrap_or_default()
}
