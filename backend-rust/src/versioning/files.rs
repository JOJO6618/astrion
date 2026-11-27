use std::io::{self, Read, Write};
use std::path::Path;

use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions};
use serde::{Serialize, de::DeserializeOwned};
use uuid::Uuid;

pub(in crate::runtime) fn child(parent: &Dir, name: &str, create: bool) -> io::Result<Option<Dir>> {
    match parent.open_dir_nofollow(name) {
        Err(error) if error.kind() == io::ErrorKind::NotFound && create => {
            match parent.create_dir(name) {
                Ok(()) => (),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
                Err(error) => return Err(error),
            }
            parent.open_dir_nofollow(name).map(Some)
        }
        result => optional(result),
    }
}

pub(super) fn read(parent: &Dir, name: impl AsRef<Path>) -> io::Result<Option<Vec<u8>>> {
    let name = name.as_ref();
    let Some(info) = optional(parent.symlink_metadata(name))? else {
        return Ok(None);
    };
    if !info.is_file() || info.nlink() != 1 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Invalid versioning storage file",
        ));
    }
    let mut options = options();
    options.read(true);
    let mut file = parent.open_with(name, &options)?;
    let info = file.metadata()?;
    if !info.is_file() || info.nlink() != 1 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Invalid versioning storage file",
        ));
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    Ok(Some(bytes))
}

pub(super) fn lock(parent: &Dir) -> io::Result<std::fs::File> {
    let mut options = options();
    options.read(true).write(true).create(true);
    let file = parent.open_with(".versioning.lock", &options)?;
    let info = file.metadata()?;
    if !info.is_file() || info.nlink() != 1 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Invalid versioning lock",
        ));
    }
    let file = file.into_std();
    fs2::FileExt::lock_exclusive(&file)?;
    Ok(file)
}

pub(in crate::runtime) fn write(parent: &Dir, name: &str, bytes: &[u8]) -> io::Result<()> {
    write_mode(parent, name, bytes, None)
}

pub(super) fn write_mode(
    parent: &Dir,
    name: &str,
    bytes: &[u8],
    mode: Option<u32>,
) -> io::Result<()> {
    let temp = format!(".versioning-{}.tmp", Uuid::new_v4());
    let result = (|| {
        let mut options = options();
        options.write(true).create_new(true);
        let mut file = parent.open_with(&temp, &options)?;
        #[cfg(unix)]
        if let Some(mode) = mode {
            use cap_std::fs::PermissionsExt;
            file.set_permissions(cap_std::fs::Permissions::from_mode(mode & 0o777))?;
        }
        #[cfg(not(unix))]
        let _ = mode;
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);
        #[cfg(windows)]
        for delay in [10, 20, 40, 80] {
            match parent.rename(&temp, parent, name) {
                Err(error) if matches!(error.raw_os_error(), Some(5 | 32 | 33)) => {
                    std::thread::sleep(std::time::Duration::from_millis(delay));
                }
                result => return result,
            }
        }
        parent.rename(&temp, parent, name)?;
        sync(parent)
    })();
    if result.is_err() {
        let _ = parent.remove_file(temp);
    }
    result
}

pub(super) fn write_json(parent: &Dir, name: &str, value: &impl Serialize) -> io::Result<()> {
    write(parent, name, &serde_json::to_vec_pretty(value)?)
}

pub(super) fn read_json<T: DeserializeOwned>(parent: &Dir, name: &str) -> io::Result<Option<T>> {
    read(parent, name)?
        .map(|bytes| serde_json::from_slice(&bytes).map_err(io::Error::from))
        .transpose()
}

pub(super) fn rows<T: DeserializeOwned>(parent: &Dir, name: &str) -> io::Result<Vec<T>> {
    Ok(read(parent, name)?
        .into_iter()
        .flat_map(|data| {
            data.split(|byte| *byte == b'\n')
                .filter_map(|line| serde_json::from_slice(line).ok())
                .collect::<Vec<_>>()
        })
        .collect())
}

pub(super) fn write_rows<T: Serialize>(parent: &Dir, name: &str, rows: &[T]) -> io::Result<()> {
    write(parent, name, &encode_rows(rows)?)
}

pub(super) fn encode_rows<T: Serialize>(rows: &[T]) -> io::Result<Vec<u8>> {
    let mut data = Vec::new();
    for row in rows {
        serde_json::to_writer(&mut data, row)?;
        data.push(b'\n');
    }
    Ok(data)
}

pub(super) fn sync(parent: &Dir) -> io::Result<()> {
    #[cfg(unix)]
    parent.open(".")?.sync_all()?;
    #[cfg(not(unix))]
    let _ = parent;
    Ok(())
}

fn options() -> OpenOptions {
    let mut options = OpenOptions::new();
    options.follow(FollowSymlinks::No);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NONBLOCK);
    }
    options
}

fn optional<T>(result: io::Result<T>) -> io::Result<Option<T>> {
    match result {
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        result => result.map(Some),
    }
}
