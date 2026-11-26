use std::{
    collections::HashMap,
    ffi::{OsStr, OsString},
    path::Path,
    time::Instant,
};

use jiff::{Timestamp, tz::TimeZone};
use serde::{Deserialize, Serialize};

use super::{
    CACHE_VERSION,
    fs::{Dir, Kind, walk_files},
    is_backup,
    timestamps::{backup_time, format_in_local_time},
};

#[derive(Clone, Copy)]
enum Category {
    Backups,
    Logs,
    McpProfiles,
    AgentConversations,
    Media,
    SubAgents,
    Chat,
    Other,
}

const CATEGORY_IDS: [&str; 8] = [
    "backups",
    "logs",
    "mcp_profiles",
    "agent_conversations",
    "media",
    "sub_agents",
    "chat",
    "other",
];
const MAX_BACKUP_ENTRIES: usize = 2000;

#[derive(Deserialize, Serialize)]
pub(super) struct Snapshot {
    pub version: u32,
    pub platform: Option<String>,
    pub data_root: Option<String>,
    pub logs_root: Option<String>,
    pub scanned_at: Option<String>,
    #[serde(default)]
    pub duration_ms: u64,
    #[serde(default)]
    pub total_bytes: u64,
    #[serde(default)]
    pub categories: Vec<Bucket>,
    #[serde(default)]
    pub backups: Vec<Backup>,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Bucket {
    id: String,
    bytes: u64,
    files: u64,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Backup {
    id: String,
    bytes: u64,
    updated_at: Option<String>,
}

fn classify(dirs: &[OsString], filename: &OsStr) -> Category {
    let first = dirs.first().map_or(filename, OsString::as_os_str).to_str();
    let second = (!dirs.is_empty()).then(|| dirs.get(1).map_or(filename, OsString::as_os_str));
    match first {
        Some("save") => Category::Backups,
        Some("mcp_profiles") => Category::McpProfiles,
        Some("conversations") if second == Some(OsStr::new("media_store")) => Category::Media,
        Some("conversations") => Category::AgentConversations,
        Some("sub_agent_tasks") => Category::SubAgents,
        Some("sub_agent_runtime.json" | "sub_agents.json" | "sub_agents.sqlite3" | "sub_agents.sqlite3-wal" | "sub_agents.sqlite3-shm") if dirs.is_empty() => {
            Category::SubAgents
        }
        Some("chat") => Category::Chat,
        _ => Category::Other,
    }
}

pub(super) fn logs_in_scope(data_path: &Path, data: Option<&Dir>, logs: &Dir) -> bool {
    // A log override must not include the data root or reclassify one of its
    // reserved categories as an unrestricted log directory.
    if std::path::absolute(data_path).is_ok_and(|path| path.starts_with(&logs.path)) {
        return false;
    }
    data.is_none_or(|data| {
        data.relative_to(logs).is_none()
            && !logs
                .relative_to(data)
                .and_then(|path| path.components().next())
                .is_some_and(|component| {
                    let name = component.as_os_str().to_ascii_lowercase();
                    !matches!(classify(&[], &name), Category::Other)
                })
    })
}

pub(super) fn scan(data_path: &Path, logs_path: &Path) -> Snapshot {
    let start = Instant::now();
    let data = Dir::open(data_path).ok();
    let logs = Dir::open(logs_path)
        .ok()
        .filter(|logs| logs_in_scope(data_path, data.as_ref(), logs));
    let mut categories = CATEGORY_IDS.map(|id| Bucket {
        id: id.into(),
        bytes: 0,
        files: 0,
    });
    let mut backup_bytes = HashMap::<OsString, u64>::new();

    if let Some(data) = &data {
        walk_files(
            data,
            logs.as_ref().map(|dir| dir.identity),
            false,
            |_, dirs, name, entry| {
                let category = classify(dirs, name);
                let bucket = &mut categories[category as usize];
                bucket.bytes = bucket.bytes.saturating_add(entry.bytes);
                bucket.files += 1;
                if let (Category::Backups, Some(id)) = (category, dirs.get(1)) {
                    let bytes = backup_bytes.entry(id.clone()).or_default();
                    *bytes = bytes.saturating_add(entry.bytes);
                }
            },
        );
    }
    if let Some(logs) = &logs {
        let bucket = &mut categories[Category::Logs as usize];
        walk_files(
            logs,
            data.as_ref().map(|dir| dir.identity),
            false,
            |_, _, _, entry| {
                bucket.bytes = bucket.bytes.saturating_add(entry.bytes);
                bucket.files += 1;
            },
        );
    }

    let zone = TimeZone::system();
    let backups = data
        .as_ref()
        .map(|data| backup_entries(data, backup_bytes, &zone))
        .unwrap_or_default();
    Snapshot {
        version: CACHE_VERSION,
        platform: Some(
            match std::env::consts::OS {
                "macos" => "darwin",
                "windows" => "win32",
                other => other,
            }
            .into(),
        ),
        data_root: Some(data_path.to_string_lossy().into_owned()),
        logs_root: Some(logs_path.to_string_lossy().into_owned()),
        scanned_at: Some(format_in_local_time(Timestamp::now(), &zone, true)),
        duration_ms: start.elapsed().as_millis().try_into().unwrap_or(u64::MAX),
        total_bytes: categories
            .iter()
            .fold(0_u64, |total, bucket| total.saturating_add(bucket.bytes)),
        categories: categories.into(),
        backups,
    }
}

fn backup_entries(data: &Dir, bytes: HashMap<OsString, u64>, zone: &TimeZone) -> Vec<Backup> {
    let Ok(save) = data.child(OsStr::new("save")) else {
        return Vec::new();
    };
    let mut entries = save.names().unwrap_or_default();
    entries.retain(|name| {
        is_backup(name)
            && save
                .entry(name)
                .is_ok_and(|entry| entry.kind == Kind::Directory)
    });
    entries.sort_by_key(|name| std::cmp::Reverse(bytes.get(name).copied().unwrap_or(0)));
    entries
        .into_iter()
        .filter_map(|name| {
            let dir = save.child(&name).ok()?;
            Some(Backup {
                bytes: bytes.get(&name).copied().unwrap_or(0),
                id: name.to_string_lossy().into_owned(),
                updated_at: backup_time(&dir, zone).map(|time| time.text),
            })
        })
        .take(MAX_BACKUP_ENTRIES)
        .collect()
}
