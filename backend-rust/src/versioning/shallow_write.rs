use std::{collections::BTreeMap, io, path::Path};

use cap_std::fs::Dir;
use ring::digest::{SHA256, digest};
use serde_json::{Map, json};

use super::{
    files, open_data_directory,
    shallow::{Backup, History, Snapshot, backup_name},
    transaction::{self, FileState, Scope, Transaction},
};

const STATE: &str = "state.jsonl";
pub(super) const DIRECTORY: &str = "shallow_backups";

pub(super) fn track(
    records: &Dir,
    root: &Path,
    data: &Path,
    raw: &str,
    message_id: &str,
    timestamp: &str,
) -> io::Result<()> {
    let path = relative_path(root, data, raw)?;
    let dir = files::child(records, DIRECTORY, true)?.expect("directory created");
    let mut snapshots = History::open(records)?
        .expect("directory created")
        .snapshots;
    if snapshots.iter().any(|snapshot| {
        snapshot
            .tracked_file_backups
            .keys()
            .any(|saved| relative_path(root, data, saved).is_ok_and(|saved| saved == path))
    }) {
        return Ok(());
    }
    let workspace = open_data_directory(root)?;
    let current = transaction::capture(&workspace, &path, true)?;
    let (backup, contents) = new_backup(&dir, root, &path, 1, current, timestamp)?;
    if let Some((name, bytes)) = contents {
        files::write(&dir, &name, &bytes)?;
    }
    if let Some(existing) = snapshots
        .iter_mut()
        .find(|snapshot| snapshot.message_id == message_id)
    {
        existing.tracked_file_backups.insert(path, backup);
    } else {
        snapshots.push(Snapshot {
            message_id: message_id.into(),
            tracked_file_backups: BTreeMap::from([(path, backup)]),
            other: Map::from_iter([("timestamp".into(), json!(timestamp))]),
        });
    }
    files::write_rows(&dir, STATE, &snapshots)
}

pub(super) fn checkpoint(
    records: &Dir,
    id: &str,
    root: &Path,
    data: &Path,
    message_id: &str,
    timestamp: &str,
    tx: &mut Transaction<'_>,
) -> io::Result<()> {
    let Some(mut history) = History::open(records)? else {
        return Ok(());
    };
    let workspace = open_data_directory(root)?;
    let mut paths = BTreeMap::<String, Backup>::new();
    for snapshot in &history.snapshots {
        for (stored, backup) in &snapshot.tracked_file_backups {
            let path = relative_path(root, data, stored)?;
            if paths
                .get(&path)
                .is_none_or(|latest| latest.version <= backup.version)
            {
                paths.insert(path, backup.clone());
            }
        }
    }
    for (path, previous) in &mut paths {
        let current = transaction::capture(&workspace, path, true)?;
        let saved = read_blob(&history.directory, previous.backup_file_name.as_deref())?;
        let mode = previous
            .other
            .get("file_mode")
            .and_then(serde_json::Value::as_u64)
            .map(|value| value as u32);
        if current.as_ref().map(|file| &file.bytes) == saved.as_ref()
            && mode.is_none_or(|mode| current.as_ref().and_then(|file| file.mode) == Some(mode))
        {
            continue;
        }
        let version = previous
            .version
            .unwrap_or(0)
            .checked_add(1)
            .ok_or_else(invalid_backup)?;
        let (backup, contents) =
            new_backup(&history.directory, root, path, version, current, timestamp)?;
        if let Some((name, bytes)) = contents {
            tx.put(&format!("{id}/{DIRECTORY}/{name}"), bytes)?;
        }
        *previous = backup;
    }
    history
        .snapshots
        .retain(|snapshot| snapshot.message_id != message_id);
    history.snapshots.push(Snapshot {
        message_id: message_id.into(),
        tracked_file_backups: paths,
        other: Map::from_iter([("timestamp".into(), json!(timestamp))]),
    });
    tx.put(
        &format!("{id}/{DIRECTORY}/{STATE}"),
        files::encode_rows(&history.snapshots)?,
    )
}

pub(super) fn restore(
    history: &History,
    root: &Path,
    data: &Path,
    message_id: &str,
    tx: &mut Transaction<'_>,
) -> io::Result<()> {
    let snapshot = history
        .snapshots
        .iter()
        .find(|snapshot| snapshot.message_id == message_id)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "Shallow checkpoint state is missing",
            )
        })?;
    let workspace = open_data_directory(root)?;
    for (path, backup) in &snapshot.tracked_file_backups {
        let path = relative_path(root, data, path)?;
        let saved = read_blob(&history.directory, backup.backup_file_name.as_deref())?;
        #[cfg(unix)]
        let mode = Some(
            match backup
                .other
                .get("file_mode")
                .and_then(serde_json::Value::as_u64)
            {
                Some(mode) => mode as u32 & 0o777,
                None => transaction::capture(&workspace, &path, true)?
                    .and_then(|file| file.mode)
                    .unwrap_or(0o644),
            },
        );
        #[cfg(not(unix))]
        let mode = None;
        tx.change(
            Scope::Workspace,
            &path,
            saved.map(|bytes| FileState::workspace(bytes, mode)),
        )?;
    }
    Ok(())
}

pub(super) fn save_branch(
    history: &History,
    id: &str,
    retained: &[String],
    copy: bool,
    tx: &mut Transaction<'_>,
) -> io::Result<()> {
    let snapshots: Vec<_> = history
        .snapshots
        .iter()
        .filter(|row| retained.contains(&row.message_id))
        .collect();
    tx.put(
        &format!("{id}/{DIRECTORY}/{STATE}"),
        files::encode_rows(&snapshots)?,
    )?;
    if copy {
        for entry in history.directory.entries()? {
            let entry = entry?;
            let name = entry.file_name();
            if let Some(name) = name.to_str().filter(|name| backup_name(name).is_some()) {
                let bytes =
                    read_blob(&history.directory, Some(name))?.ok_or_else(invalid_backup)?;
                tx.put(&format!("{id}/{DIRECTORY}/{name}"), bytes)?;
            }
        }
    }
    Ok(())
}

fn new_backup(
    dir: &Dir,
    root: &Path,
    path: &str,
    mut version: u64,
    file: Option<FileState>,
    timestamp: &str,
) -> io::Result<(Backup, Option<(String, Vec<u8>)>)> {
    let contents = if let Some(file) = file.as_ref() {
        let absolute = root.join(path);
        let digest = digest(&SHA256, absolute.to_string_lossy().as_bytes());
        let stem: String = digest.as_ref()[..8]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        loop {
            let name = format!("{stem}@v{version}");
            match files::read(dir, &name)? {
                None => break Some((name, file.bytes.clone())),
                Some(_) => version = version.checked_add(1).ok_or_else(invalid_backup)?,
            }
        }
    } else {
        None
    };
    let backup = Backup {
        backup_file_name: contents.as_ref().map(|(name, _)| name.clone()),
        version: Some(version),
        other: Map::from_iter([
            ("backup_time".into(), json!(timestamp)),
            ("file_mode".into(), json!(file.and_then(|file| file.mode))),
        ]),
    };
    Ok((backup, contents))
}

fn read_blob(dir: &Dir, name: Option<&str>) -> io::Result<Option<Vec<u8>>> {
    let Some(name) = name else { return Ok(None) };
    if backup_name(name).is_none() {
        return Err(invalid_backup());
    }
    files::read(dir, name)?.map(Some).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            "Shallow checkpoint backup is missing",
        )
    })
}

pub(super) fn relative_path(root: &Path, data: &Path, raw: &str) -> io::Result<String> {
    let native = if cfg!(windows) {
        raw.replace('\\', "/")
    } else {
        raw.to_owned()
    };
    let path = Path::new(&native);
    let relative = if path.is_absolute() {
        path.strip_prefix(root).map_err(|_| {
            io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Versioning file lies outside the current execution root",
            )
        })?
    } else {
        path
    };
    let relative = relative.to_str().ok_or_else(invalid_backup)?;
    transaction::validate_path(relative, true)?;
    if root.join(relative).starts_with(data) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Versioning file lies inside runtime storage",
        ));
    }
    Ok(relative.to_owned())
}

fn invalid_backup() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "Invalid shallow backup reference",
    )
}
