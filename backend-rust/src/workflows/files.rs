use super::{Result, WorkflowError};
use std::{
    fs::{self, Metadata, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
};

pub(super) const MARKDOWN_FILE: &str = "WORKFLOW.md";

/// Resolve a trusted configuration path, including a directory not yet created.
pub(super) fn resolve_root(path: &Path) -> io::Result<PathBuf> {
    match path.canonicalize() {
        Ok(resolved) => Ok(resolved),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let name = path.file_name().ok_or(error)?;
            let parent = path
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .unwrap_or(Path::new("."));
            Ok(resolve_root(parent)?.join(name))
        }
        Err(error) => Err(error),
    }
}

pub(super) fn directory(path: &Path) -> Result<bool> {
    entry(path, Metadata::is_dir)
}

pub(super) fn file(path: &Path) -> Result<bool> {
    entry(path, Metadata::is_file)
}

fn entry(path: &Path, expected: fn(&Metadata) -> bool) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(meta) if expected(&meta) && !is_link(&meta) => Ok(true),
        Ok(_) => Err(invalid_path()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
    }
}

fn is_link(meta: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        // Directory junctions and other reparse points may also redirect lookups.
        use_windows_reparse_point(meta.file_attributes())
    }
    #[cfg(not(windows))]
    meta.file_type().is_symlink()
}

#[cfg(windows)]
fn use_windows_reparse_point(attributes: u32) -> bool {
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

pub(super) fn invalid_path() -> WorkflowError {
    WorkflowError::new(400, "非法路径")
}

pub(super) fn checked_root(path: &Path) -> Result<bool> {
    if !directory(path)? {
        return Ok(false);
    }
    if path.canonicalize()? != path {
        return Err(invalid_path());
    }
    Ok(true)
}

pub(super) fn read(path: &Path) -> Result<Option<String>> {
    if !file(path)? {
        return Ok(None);
    }
    fs::read_to_string(path).map(Some).map_err(Into::into)
}

pub(super) fn write_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let temp = path.with_file_name(format!(".workflow-{}.tmp", uuid::Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temp)?;
    let result = file.write_all(contents).and_then(|()| file.sync_all());
    drop(file);
    let result = result.and_then(|()| fs::rename(&temp, path));
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}

pub(super) fn now_epoch() -> f64 {
    time::OffsetDateTime::now_utc().unix_timestamp_nanos() as f64 / 1e9
}

pub(super) fn now_minute() -> String {
    use time::OffsetDateTime;
    let now = OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc());
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}",
        now.year(),
        now.month() as u8,
        now.day(),
        now.hour(),
        now.minute()
    )
}
