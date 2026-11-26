use std::{
    ffi::{OsStr, OsString},
    io,
    path::Path,
};

use jiff::{SignedDuration, Timestamp, tz::TimeZone};
use serde::Serialize;

use super::{
    fs::{Dir, Kind, missing_or_unsafe_root, walk_files},
    is_backup,
    scanner::logs_in_scope,
    timestamps::backup_time,
};

#[derive(Default, Serialize)]
pub(super) struct LogsResult {
    freed_bytes: u64,
    removed: u64,
    truncated: u64,
    failed: Vec<Failure>,
}

#[derive(Serialize)]
pub(super) struct BackupsResult {
    freed_bytes: u64,
    removed_count: usize,
    removed: Vec<String>,
    failed: Vec<Failure>,
    keep_days: u32,
}

#[derive(Serialize)]
struct Failure {
    path: String,
    error: String,
}

fn failure(path: &Path, error: io::Error) -> Failure {
    Failure {
        path: path.to_string_lossy().into_owned(),
        error: error.to_string(),
    }
}

pub(super) fn clean_logs(data_path: &Path, logs_path: &Path) -> LogsResult {
    let mut result = LogsResult::default();
    let root = match Dir::open(logs_path) {
        Ok(root) => root,
        Err(error) if missing_or_unsafe_root(&error) => return result,
        Err(error) => {
            result.failed.push(failure(logs_path, error));
            return result;
        }
    };
    let data = Dir::open(data_path).ok();
    if !logs_in_scope(data_path, data.as_ref(), &root) {
        return result;
    }
    walk_files(
        &root,
        data.map(|data| data.identity),
        true,
        |parent, _, name, entry| {
            let active = name.as_encoded_bytes().ends_with(b".log");
            let operation = if active {
                parent.truncate(name, &entry)
            } else {
                parent.remove(name, &entry)
            };
            match operation {
                Ok(()) => {
                    result.freed_bytes = result.freed_bytes.saturating_add(entry.bytes);
                    if active {
                        result.truncated += 1;
                    } else {
                        result.removed += 1;
                    }
                }
                Err(error) => result.failed.push(failure(&parent.path.join(name), error)),
            }
        },
    );
    result
}

pub(super) fn clean_backups(data_path: &Path, keep_days: u32) -> BackupsResult {
    let mut result = BackupsResult {
        freed_bytes: 0,
        removed_count: 0,
        removed: Vec::new(),
        failed: Vec::new(),
        keep_days,
    };
    let save = OsStr::new("save");
    let (data, root) = match Dir::open(data_path).and_then(|data| {
        let root = data.child(save)?;
        Ok((data, root))
    }) {
        Ok(roots) => roots,
        Err(error) if missing_or_unsafe_root(&error) => return result,
        Err(error) => {
            result.failed.push(failure(&data_path.join(save), error));
            return result;
        }
    };
    let names = match root.names() {
        Ok(names) => names,
        Err(error) => {
            result.failed.push(failure(&root.path, error));
            return result;
        }
    };
    let cutoff = Timestamp::now() - SignedDuration::from_hours(i64::from(keep_days) * 24);
    let zone = TimeZone::system();
    for name in names.into_iter().filter(|name| is_backup(name)) {
        let dir = match root.child(&name) {
            Ok(dir) if root.same_device(&dir) => dir,
            Ok(_) => continue,
            Err(error) if missing_or_unsafe_root(&error) => continue,
            Err(error) => {
                result.failed.push(failure(&root.path.join(name), error));
                continue;
            }
        };
        if backup_time(&dir, &zone).is_none_or(|time| time.instant >= cutoff) {
            continue;
        }
        // Recheck both the configured data root and the direct save parent.
        // A moved or replaced directory must not authorize a deletion.
        if !data.is_current()
            || !data
                .child(save)
                .is_ok_and(|parent| parent.identity == root.identity)
            || !root
                .child(&name)
                .is_ok_and(|child| child.identity == dir.identity)
        {
            continue;
        }
        let operation = remove_contents(&dir).and_then(|bytes| {
            root.remove_dir(&name, dir)?;
            Ok(bytes)
        });
        match operation {
            Ok(bytes) => {
                result.freed_bytes = result.freed_bytes.saturating_add(bytes);
                result.removed_count += 1;
                result.removed.push(name.to_string_lossy().into_owned());
            }
            Err(error) => result.failed.push(failure(&root.path.join(name), error)),
        }
    }
    result
}

fn remove_contents(root: &Dir) -> io::Result<u64> {
    struct Frame {
        name: OsString,
        dir: Dir,
        names: std::vec::IntoIter<OsString>,
    }
    let mut root_names = root.names()?.into_iter();
    let mut frames = Vec::<Frame>::new();
    let mut bytes = 0_u64;
    loop {
        let (parent, next) = if let Some(frame) = frames.last_mut() {
            (&frame.dir, frame.names.next())
        } else {
            (root, root_names.next())
        };
        if let Some(name) = next {
            let entry = parent.entry(&name)?;
            if entry.kind == Kind::Directory {
                let child = parent.child(&name)?;
                if entry.identity != child.identity || !root.same_device(&child) {
                    return Err(io::Error::other(
                        "backup directory identity changed or crosses a filesystem boundary",
                    ));
                }
                frames.push(Frame {
                    name,
                    names: child.names()?.into_iter(),
                    dir: child,
                });
            } else {
                // Unlink terminal entries without following any symlinks inside
                // the expired backup. Only regular files contributed to scan.
                parent.remove(&name, &entry)?;
                if entry.kind == Kind::File {
                    bytes = bytes.saturating_add(entry.bytes);
                }
            }
        } else if let Some(frame) = frames.pop() {
            let parent = frames.last().map_or(root, |frame| &frame.dir);
            parent.remove_dir(&frame.name, frame.dir)?;
        } else {
            return Ok(bytes);
        }
    }
}
