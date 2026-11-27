//! Net changes from successful native Agent workspace writes, not command diffs.

mod diff;

use std::{
    collections::BTreeMap,
    fmt::Write as _,
    io,
    path::{Path, PathBuf},
};

use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde_json::{Value, json};

use super::{RuntimeError, Store, store::Task, versioning::{files, open_data_directory}};
use crate::{conversation_db::Database, tools::write_file::{FileText, RecordedWrite}, web_assets::personalization};

#[derive(Default)]
pub(super) struct EditHistory {
    roots: BTreeMap<PathBuf, BTreeMap<String, Entry>>,
    size: usize,
}

struct Entry {
    path: PathBuf,
    before: FileText,
    after: FileText,
}

impl Entry {
    fn counts(&self) -> (usize, usize) {
        if let (Some(before), Some(after)) = (text(&self.before), text(&self.after)) {
            return diff::Diff::new(before, after).counts();
        }
        (line_count(&self.after), line_count(&self.before))
    }

    fn changed(&self) -> bool {
        self.before != self.after || text(&self.before).is_none()
    }
}

pub(super) fn record(
    store: &mut Store, task_id: &str, root: &Path, write: RecordedWrite, tool: &str,
) -> Result<(), RuntimeError> {
    let error = RuntimeError::storage;
    let task = store.tasks.get(task_id).ok_or_else(RuntimeError::task_not_found)?;
    let Some(message_id) = task.version_message_id.clone() else { return Ok(()) };
    let conversation = task.conversation_id.clone();
    let workspace = task.workspace_id.clone();
    let db = store.conversation_path(&workspace, &conversation)?;
    let enabled = personalization::modify_history_enabled(&store.data_dir);
    let task = store.tasks.get_mut(task_id).expect("task under runtime lock");
    let Some(path) = display_path(&write.path) else { return Ok(()) };
    // Record only ordinary workspace files; do not archive runtime metadata.
    if write.path.components().any(|c| c.as_os_str().eq_ignore_ascii_case(".astrion")) {
        return Ok(());
    }
    task.edit_history.add(root, &path, write);
    let timestamp = work_time(super::now());
    let summary = task.edit_history.summary(&timestamp, true);
    let edited = Database::open(db.parent().expect("conversation path has parent"))
        .map_err(error)?
        .record_edit_summary(&conversation, &message_id, &summary, &path, tool, &timestamp)
        .map_err(error)?;
    task.emit("edit_summary_updated", json!({"conversation_id":conversation,"message_id":message_id,"edit_summary":summary}));
    task.emit("edited_files_updated", json!({"conversation_id":conversation,"edited_files":edited}));
    if enabled {
        write_archives(task, None).map_err(RuntimeError::storage)?;
    }
    Ok(())
}

pub(super) fn finish(store: &mut Store, task_id: &str, timestamp: f64) {
    let task = &store.tasks[task_id];
    if task.edit_history.roots.is_empty() { return; }
    let timestamp = work_time(timestamp);
    let archive_error = if personalization::modify_history_enabled(&store.data_dir) {
        write_archives(task, Some(&timestamp)).err()
    } else {
        None
    };
    if let Some(message_id) = task.version_message_id.clone() {
        let id = task.conversation_id.clone();
        let summary = task.edit_history.summary(&timestamp, false);
        let saved = store.conversation_path(&task.workspace_id, &id)
            .map_err(|err| err.message)
            .and_then(|path| Database::open(path.parent().expect("conversation path has parent")))
            .and_then(|db| db.finish_edit_summary(&id, &message_id, &summary));
        if let Err(error) = saved {
            store.tasks.get_mut(task_id).expect("task under runtime lock")
                .emit("edit_history_error", json!({"phase":"summary","message":error}));
        } else {
            store.tasks.get_mut(task_id).expect("task under runtime lock").emit(
                "edit_summary_updated", json!({"conversation_id":id,"message_id":message_id,"edit_summary":summary}),
            );
        }
    }
    // Terminal Task objects stay in the runtime for replay, but original file bytes need not.
    let task = store.tasks.get_mut(task_id).expect("task under runtime lock");
    if let Some(error) = archive_error {
        task.emit("edit_history_error", json!({"phase":"archive","message":error.to_string()}));
    }
    task.edit_history = EditHistory::default();
}

impl EditHistory {
    fn add(&mut self, root: &Path, key: &str, write: RecordedWrite) {
        let root = self.roots.entry(root.to_owned()).or_default();
        let entry = root.entry(key.into()).or_insert_with(|| Entry {
            path: write.path, before: cap_text(write.before, &mut self.size), after: FileText::Missing,
        });
        self.size -= text_bytes(&entry.after);
        entry.after = cap_text(write.after, &mut self.size);
    }

    fn summary(&self, timestamp: &str, in_progress: bool) -> Value {
        let mut lines = 8_000;
        let mut bytes = 80_000;
        let has_several_roots = self.roots.len() > 1;
        let files = self.roots.iter().flat_map(|(root, entries)| {
            entries.iter().map(move |(path, entry)| (root, path, entry))
        }).filter(|(_, _, entry)| entry.changed()).map(|(root, path, entry)| {
            let path = if has_several_roots { root.join(path).to_string_lossy().into_owned() } else { path.clone() };
            let (added, removed, rows, truncated) = match (text(&entry.before), text(&entry.after)) {
                (Some(before), Some(after)) => {
                    let diff = diff::Diff::new(before, after);
                    let (added, removed) = diff.counts();
                    let (rows, truncated) = diff.card(&mut lines, &mut bytes);
                    (added, removed, rows, truncated)
                }
                _ => {
                    let (added, removed) = entry.counts();
                    (added, removed, Vec::new(), true)
                }
            };
            json!({"path":path,"status":if entry.before == FileText::Missing { "added" } else { "modified" },
                "added":added,"removed":removed,"lines":rows,"truncated":truncated,"updated_at":timestamp})
        }).collect::<Vec<_>>();
        json!({"version":1,"updated_at":timestamp,"in_progress":in_progress,"files":files})
    }
}

fn text(data: &FileText) -> Option<&str> {
    match data {
        FileText::Missing => Some(""),
        FileText::Text(text) => Some(text),
        FileText::Unavailable { .. } => None,
    }
}

fn text_bytes(data: &FileText) -> usize {
    text(data).map_or(0, str::len)
}

fn line_count(data: &FileText) -> usize {
    match data {
        FileText::Unavailable { lines } => *lines,
        other => text(other).unwrap_or_default().split_inclusive('\n').count(),
    }
}

fn cap_text(data: FileText, size: &mut usize) -> FileText {
    let next = size.saturating_add(text_bytes(&data));
    if next > 8_000_000 {
        return FileText::Unavailable { lines: line_count(&data) };
    }
    *size = next;
    data
}

fn display_path(path: &Path) -> Option<String> {
    path.components().map(|component| component.as_os_str().to_str())
        .collect::<Option<Vec<_>>>().map(|parts| parts.join("/"))
}

fn write_archives(task: &Task, finish: Option<&str>) -> io::Result<()> {
    if task.edit_history.roots.is_empty() { return Ok(()); }
    let started = work_time(task.start.created_at);
    let excerpt = task.start.message.split_whitespace().collect::<Vec<_>>().join(" ");
    let prefix = safe_excerpt(&excerpt);
    let safe_time = started.replace([':', '+'], "-");
    let filename = format!("{prefix}_{safe_time}_{}.diff", task.start.id);
    for (root, entries) in &task.edit_history.roots {
        let open_root = open_data_directory(root)?;
        let active = entries.values().any(Entry::changed);
        let Some(astrion) = files::child(&open_root, ".astrion", active)? else { continue };
        let Some(history) = files::child(&astrion, "modify_history", active)? else { continue };
        let Some(conversation) = files::child(&history, &task.conversation_id, active)? else { continue };
        if !active {
            match conversation.remove_file(&filename) {
                Ok(()) => (),
                Err(error) if error.kind() == io::ErrorKind::NotFound => (),
                Err(error) => return Err(error),
            }
            continue;
        }
        let excerpt = excerpt.chars().take(200).collect::<String>();
        let mut output = format!("# 修改记录（Astrion Modify History）\n# 对话: {}\n# 任务: {excerpt}\n# 时间: {started} → {}\n# 恢复: git apply；撤销: git apply -R\n\n",
            task.conversation_id, finish.unwrap_or("进行中"));
        for (path, entry) in entries.iter().filter(|(_, e)| e.changed()) {
            let (added, removed) = entry.counts();
            let _ = writeln!(output, "# {} · +{added} / -{removed}", path.escape_debug());
            let (Some(before), Some(after)) = (text(&entry.before), text(&entry.after)) else {
                output.push_str("# 文件内容超出限额或不是 UTF-8 文本，仅保留计数。\n\n");
                continue;
            };
            let missing = finish.is_some() && missing(&open_root, &entry.path);
            if missing {
                output.push_str("# 文件在任务结束时已不存在，以下补丁可恢复最后一次原生编辑的内容。\n");
            }
            let newly_added = entry.before == FileText::Missing || missing;
            let before = if missing { "" } else { before };
            diff::Diff::new(before, after).patch(path, newly_added, &mut output);
            output.push('\n');
        }
        files::write(&conversation, &filename, output.as_bytes())?;
    }
    Ok(())
}

fn missing(root: &Dir, path: &Path) -> bool {
    let Ok(mut current) = root.try_clone() else { return false; };
    let mut components = path.components().peekable();
    while let Some(component) = components.next() {
        if components.peek().is_none() {
            return current.symlink_metadata(component.as_os_str())
                .is_err_and(|error| error.kind() == io::ErrorKind::NotFound);
        }
        match current.open_dir_nofollow(component.as_os_str()) {
            Ok(next) => current = next,
            Err(error) => return error.kind() == io::ErrorKind::NotFound,
        }
    }
    false
}

fn safe_excerpt(excerpt: &str) -> String {
    let filtered = excerpt.chars().take(40).map(|c| {
        if c.is_control() || "/\\:*?\"<>|".contains(c) { '_' } else { c }
    }).collect::<String>();
    let filtered = filtered.trim_matches([' ', '.', '_']);
    if filtered.is_empty() { "task".into() } else { filtered.into() }
}

fn work_time(timestamp: f64) -> String {
    super::iso_time(timestamp).unwrap_or_default()
}

#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust修改留痕/modify_history_contract.rs"]
mod tests;
