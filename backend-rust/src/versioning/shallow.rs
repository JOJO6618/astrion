use std::{
    collections::{BTreeMap, HashSet},
    io,
};

use cap_std::fs::Dir;
use ring::digest::{SHA256, digest};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::{diff, files};

#[derive(Clone, Deserialize, Serialize)]
pub(super) struct Snapshot {
    pub message_id: String,
    #[serde(default)]
    pub tracked_file_backups: BTreeMap<String, Backup>,
    #[serde(flatten)]
    pub other: serde_json::Map<String, Value>,
}

#[derive(Clone, Deserialize, Serialize)]
pub(super) struct Backup {
    pub backup_file_name: Option<String>,
    pub version: Option<u64>,
    #[serde(flatten)]
    pub other: serde_json::Map<String, Value>,
}

pub(super) struct History {
    pub directory: Dir,
    pub snapshots: Vec<Snapshot>,
}

#[derive(Serialize)]
pub(super) struct FileChange<'a> {
    path: &'a str,
    status: &'static str,
    pub insertions: usize,
    pub deletions: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    patch_lines: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    patch_truncated: Option<bool>,
}

impl History {
    pub fn open(directory: &Dir) -> io::Result<Option<Self>> {
        let Some(directory) = files::child(directory, "shallow_backups", false)? else {
            return Ok(None);
        };
        let mut snapshots = files::rows::<Snapshot>(&directory, "state.jsonl")?;
        let mut seen = HashSet::new();
        snapshots.reverse();
        snapshots.retain(|snapshot| seen.insert(snapshot.message_id.clone()));
        snapshots.reverse();
        Ok(Some(Self {
            directory,
            snapshots,
        }))
    }

    pub fn changes(
        &self,
        message_id: &str,
        origin: Option<&str>,
        workspace: &str,
        include_patch: bool,
    ) -> io::Result<Option<Vec<FileChange<'_>>>> {
        let Some(index) = self
            .snapshots
            .iter()
            .position(|snapshot| snapshot.message_id == message_id)
        else {
            return Ok(None);
        };
        let previous = index.checked_sub(1).map(|i| &self.snapshots[i]);
        let mut changes = Vec::new();
        for (path, target) in &self.snapshots[index].tracked_file_backups {
            let target_name = target.backup_file_name.as_deref();
            let after = self.read_backup(target_name)?.unwrap_or_default();
            let before = match previous.and_then(|snapshot| snapshot.tracked_file_backups.get(path))
            {
                Some(backup) => self.read_backup(backup.backup_file_name.as_deref())?,
                None => self.previous_version(target, &after, path, origin, workspace)?,
            };
            let delta = diff::compare(before.as_deref().unwrap_or_default(), &after, include_patch);
            if delta.insertions == 0 && delta.deletions == 0 {
                continue;
            }
            let status = match (before.is_some(), target_name.is_some()) {
                (_, false) => "deleted",
                (false, true) => "added",
                (true, true) => "modified",
            };
            changes.push(FileChange {
                path,
                status,
                insertions: delta.insertions,
                deletions: delta.deletions,
                patch_truncated: include_patch.then(|| delta.truncated()),
                patch_lines: include_patch.then(|| json!(delta.patch)),
            });
        }
        Ok(Some(changes))
    }

    fn previous_version(
        &self,
        target: &Backup,
        current_text: &str,
        path: &str,
        origin: Option<&str>,
        workspace: &str,
    ) -> io::Result<Option<String>> {
        let version = target.version.unwrap_or(1).max(1);
        if version == 1 {
            return Ok(target
                .backup_file_name
                .as_ref()
                .map(|_| current_text.to_owned()));
        }
        // When the final journal row replaced a pre-edit row, version N-1 is
        // the missing baseline. Prefer the saved digest so moved workspaces work.
        if let Some((stem, _)) = target.backup_file_name.as_deref().and_then(backup_name)
            && let Some(text) = self.read_backup(Some(&format!("{stem}@v{}", version - 1)))?
        {
            return Ok(Some(text));
        }
        for base in origin.into_iter().chain([workspace]) {
            let absolute = original_path(base, path);
            let hash = digest(&SHA256, absolute.as_bytes());
            let stem: String = hash.as_ref()[..8]
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect();
            if let Some(text) = self.read_backup(Some(&format!("{stem}@v{}", version - 1)))? {
                return Ok(Some(text));
            }
        }
        Ok(None)
    }

    fn read_backup(&self, name: Option<&str>) -> io::Result<Option<String>> {
        let Some(name) = name else { return Ok(None) };
        if backup_name(name).is_none() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Invalid shallow backup filename",
            ));
        }
        Ok(
            files::read(&self.directory, name)?.map(|bytes| match String::from_utf8(bytes) {
                Ok(text) => text,
                Err(error) => error
                    .as_bytes()
                    .utf8_chunks()
                    .map(|chunk| chunk.valid())
                    .collect(),
            }),
        )
    }
}

pub(super) fn backup_name(name: &str) -> Option<(&str, u64)> {
    let (stem, version) = name.split_once("@v")?;
    if stem.len() != 16
        || !stem
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        || !version.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    Some((stem, version.parse().ok()?))
}

fn original_path(base: &str, path: &str) -> String {
    let absolute = path.starts_with(['/', '\\']) || path.as_bytes().get(1) == Some(&b':');
    let windows = if absolute {
        path.as_bytes().get(1) == Some(&b':') || path.starts_with("\\\\")
    } else {
        base.as_bytes().get(1) == Some(&b':') || base.starts_with("\\\\")
    };
    let separator = if windows { '\\' } else { '/' };
    let mut result = if absolute {
        path.to_owned()
    } else {
        format!("{}{separator}{path}", base.trim_end_matches(['/', '\\']))
    };
    if windows {
        result = result.replace('/', "\\");
    }
    result
}
