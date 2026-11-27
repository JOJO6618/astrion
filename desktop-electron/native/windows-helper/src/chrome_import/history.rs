use super::{origin, ImportResult, Preview, Stats};
use rusqlite::{types::ValueRef, Connection, ErrorCode, OpenFlags, Row};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs::{self, File, Metadata};
use std::io::{self, Read};
use std::path::Path;
use std::time::Duration;

const MAX_STATE_BYTES: u64 = 32 * 1024 * 1024;
const MAX_HISTORY_BYTES: u64 = 1024 * 1024 * 1024;
const MAX_HISTORY_ROWS: u32 = 100_000;
const CHROME_EPOCH_MS: i64 = 11_644_473_600_000;

#[derive(Serialize)]
pub(super) struct Source {
    id: String,
    name: String,
}

#[derive(Default, Deserialize)]
struct LocalState {
    #[serde(default)]
    profile: ProfileState,
}

#[derive(Default, Deserialize)]
struct ProfileState {
    #[serde(default)]
    info_cache: BTreeMap<String, ProfileInfo>,
}

#[derive(Deserialize)]
struct ProfileInfo {
    name: Option<String>,
}

fn profile_id(id: &str) -> bool {
    id == "Default"
        || id
            .strip_prefix("Profile ")
            .is_some_and(|number| !number.is_empty() && number.bytes().all(|b| b.is_ascii_digit()))
}

fn linked(metadata: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        // FILE_ATTRIBUTE_REPARSE_POINT includes junctions as well as symlinks.
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.is_symlink()
    }
}

fn directory_exists(path: &Path) -> ImportResult<bool> {
    // Walk from the root so an ancestor junction is rejected before reaching its contents.
    let ancestors: Vec<_> = path.ancestors().collect();
    for ancestor in ancestors
        .into_iter()
        .rev()
        .filter(|entry| !entry.as_os_str().is_empty())
    {
        match fs::symlink_metadata(ancestor) {
            Ok(metadata) if metadata.is_dir() && !linked(&metadata) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
            _ => return Err("invalid_source"),
        }
    }
    Ok(true)
}

fn regular_file(path: &Path, max_bytes: u64) -> ImportResult<bool> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if linked(&metadata) => Err("invalid_source"),
        Ok(metadata) if metadata.is_file() && metadata.len() <= max_bytes => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        _ => Err("database_unavailable"),
    }
}

fn display_names(root: &Path) -> LocalState {
    let path = root.join("Local State");
    if regular_file(&path, MAX_STATE_BYTES) != Ok(true) {
        return LocalState::default();
    }
    let mut state = Vec::new();
    if File::open(path)
        .and_then(|file| file.take(MAX_STATE_BYTES + 1).read_to_end(&mut state))
        .is_err()
        || state.len() as u64 > MAX_STATE_BYTES
    {
        return LocalState::default();
    }
    serde_json::from_slice(&state).unwrap_or_default()
}

pub(super) fn sources(root: &Path) -> ImportResult<Vec<Source>> {
    if !directory_exists(root)? {
        return Ok(Vec::new());
    }
    let state = display_names(root);
    let mut sources = Vec::new();
    for entry in fs::read_dir(root).map_err(|_| "chrome_read_failed")? {
        let entry = entry.map_err(|_| "chrome_read_failed")?;
        let Ok(id) = entry.file_name().into_string() else {
            continue;
        };
        if !profile_id(&id)
            || !fs::symlink_metadata(entry.path()).is_ok_and(|meta| meta.is_dir() && !linked(&meta))
        {
            continue;
        }
        let name = state
            .profile
            .info_cache
            .get(&id)
            .and_then(|info| info.name.as_deref())
            .filter(|name| !name.is_empty())
            .unwrap_or(&id)
            .chars()
            .take(200)
            .collect();
        sources.push(Source { id, name });
    }
    sources.sort_unstable_by(|a, b| a.id.cmp(&b.id));
    Ok(sources)
}

pub(super) fn preview(root: &Path, source: &str) -> ImportResult<Preview> {
    if !profile_id(source) {
        return Err("invalid_source");
    }
    let directory = root.join(source);
    if !directory_exists(&directory)? {
        return Err("invalid_source");
    }
    let mut preview = Preview::default();
    let stats = &mut preview.stats.history;
    match regular_file(&directory.join("History"), MAX_HISTORY_BYTES) {
        Ok(true) => {}
        Ok(false) => {
            stats.issue("missing_database", false);
            return Ok(preview);
        }
        Err(reason) => {
            stats.issue(reason, true);
            return Ok(preview);
        }
    }
    for name in ["History-wal", "History-shm", "History-journal"] {
        if let Err(reason) = regular_file(&directory.join(name), MAX_HISTORY_BYTES) {
            stats.issue(reason, true);
            return Ok(preview);
        }
    }
    match read_history(&directory.join("History")) {
        Ok((stats, history)) => {
            preview.stats.history = stats;
            preview.history = history;
        }
        Err(error) => {
            let reason = match error.sqlite_error_code() {
                Some(ErrorCode::DatabaseBusy | ErrorCode::DatabaseLocked) => "database_busy",
                _ => "database_unavailable",
            };
            stats.issue(reason, true);
        }
    }
    Ok(preview)
}

fn read_history(file: &Path) -> rusqlite::Result<(Stats, Vec<Value>)> {
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NOFOLLOW;
    let conn = Connection::open_with_flags(file, flags)?;
    conn.busy_timeout(Duration::from_secs(2))?;
    let mut statement = conn.prepare("SELECT url, title, last_visit_time FROM urls LIMIT ?1")?;
    let mut rows = statement.query([MAX_HISTORY_ROWS])?;
    let mut stats = Stats::default();
    let mut history = Vec::new();
    while let Some(row) = rows.next()? {
        stats.read += 1;
        match history_row(row) {
            Ok(entry) => {
                history.push(entry);
                stats.ready += 1;
            }
            Err(reason) => stats.issue(reason, false),
        }
    }
    Ok((stats, history))
}

fn history_row(row: &Row<'_>) -> ImportResult<Value> {
    let url = row
        .get_ref(0)
        .ok()
        .and_then(|value| value.as_str().ok())
        .ok_or("history_invalid_url")?;
    if !url.trim_start().split_once(':').is_some_and(|(scheme, _)| {
        scheme.eq_ignore_ascii_case("http") || scheme.eq_ignore_ascii_case("https")
    }) {
        return Err("history_non_web_url");
    }
    origin(url).map_err(|_| "history_invalid_url")?;
    let visited = match row.get_ref(2) {
        Ok(ValueRef::Integer(value)) => Some(value),
        Ok(ValueRef::Text(value)) => std::str::from_utf8(value)
            .ok()
            .and_then(|text| text.trim().parse().ok()),
        Ok(ValueRef::Real(value)) if value.abs() < i64::MAX as f64 => Some(value as i64),
        _ => None,
    };
    let at = visited
        .map(|value| value / 1_000 - CHROME_EPOCH_MS)
        .filter(|value| *value > 0)
        .ok_or("history_invalid_time")?;
    let title = row
        .get_ref(1)
        .ok()
        .and_then(|value| value.as_str().ok())
        .filter(|title| !title.is_empty())
        .unwrap_or(url);
    Ok(json!({ "url": url, "title": title, "at": at }))
}
