use std::{
    fmt, fs::{self, File, FileTimes, OpenOptions}, io::{self, Seek}, path::{Path, PathBuf},
};

use super::platform::{Platform, System};
use uuid::Uuid;

#[derive(Debug)]
pub enum SaveError {
    Unsupported,
    Dialog(io::Error),
    Copy(io::Error),
}

impl fmt::Display for SaveError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unsupported => formatter.write_str("Native saving is unavailable on this platform"),
            Self::Dialog(error) => write!(formatter, "The save dialog could not be opened: {error}"),
            Self::Copy(error) => write!(formatter, "Failed to save file: {error}"),
        }
    }
}

impl std::error::Error for SaveError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Unsupported => None,
            Self::Dialog(error) | Self::Copy(error) => Some(error),
        }
    }
}

/// Save an already-authorized regular file to a location selected in the native dialog.
/// Run on a blocking worker. `Ok(None)` means the user cancelled.
pub fn save_file(source: &Path, suggested_name: &str, english: bool) -> Result<Option<PathBuf>, SaveError> {
    if !System.has_dialogs() {
        return Err(SaveError::Unsupported);
    }
    let parent = source.parent().ok_or_else(|| SaveError::Copy(io::Error::other("File has no parent")))?;
    let name = source.file_name().ok_or_else(|| SaveError::Copy(io::Error::other("Missing file name")))?;
    let parent = super::scope::canonicalize(parent).map_err(SaveError::Copy)?;
    let source = parent.join(name);
    let file = open_regular(&source).map_err(SaveError::Copy)?;
    save_file_from(file, &source, suggested_name, english)
}

/// Keep the source handle pinned during the dialog. `original` identifies the source
/// path for the same-file check and supplies the default filename when necessary.
pub fn save_file_from(file: File, original: &Path, suggested_name: &str, english: bool) -> Result<Option<PathBuf>, SaveError> {
    save_with(&System, file, original, suggested_name, english)
}

pub(super) fn open_regular(source: &Path) -> io::Result<File> {
    use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};
    let parent = source.parent().ok_or_else(|| io::Error::other("File has no parent"))?;
    let name = source.file_name().ok_or_else(|| io::Error::other("Missing file name"))?;
    let directory = crate::knowledge::storage::open_canonical_dir(parent)?;
    let mut options = cap_std::fs::OpenOptions::new();
    options.read(true).follow(FollowSymlinks::No);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let file = directory.open_with(name, &options)?.into_std();
    if !file.metadata()?.is_file() {
        return Err(io::Error::other("Expected a regular file"));
    }
    Ok(file)
}

pub(super) fn save_with(
    platform: &dyn Platform, file: File, original: &Path, suggested_name: &str, english: bool,
) -> Result<Option<PathBuf>, SaveError> {
    if !platform.has_dialogs() {
        return Err(SaveError::Unsupported);
    }
    let prompt = if english { "Save file as…" } else { "保存文件到…" };
    let name = suggested_filename(original, suggested_name);
    let Some(target) = platform.save_dialog(prompt, &name).map_err(SaveError::Dialog)? else {
        return Ok(None);
    };
    copy_selected(file, original, &target).map_err(SaveError::Copy)?;
    Ok(Some(target))
}

fn suggested_filename(original: &Path, name: &str) -> String {
    let fallback = original.file_name().and_then(|name| name.to_str()).unwrap_or("download");
    let leaf = |name: &str| {
        let name = name.rsplit(['/', '\\']).next().unwrap_or_default().trim();
        if name.is_empty() || matches!(name, "." | "..") {
            return None;
        }
        Some(name.chars().filter(|ch| !ch.is_control()).map(|ch| if ch == ':' { '_' } else { ch }).take(200).collect::<String>())
    };
    leaf(name).filter(|name| !name.is_empty()).or_else(|| leaf(fallback)).unwrap_or_else(|| "download".into())
}

fn copy_selected(mut source: File, original: &Path, destination: &Path) -> io::Result<()> {
    if !destination.is_absolute() {
        return Err(io::Error::other("The save dialog did not return an absolute path"));
    }
    let parent = destination.parent().ok_or_else(|| io::Error::other("Destination has no parent"))?;
    let name = destination.file_name().ok_or_else(|| io::Error::other("Destination has no filename"))?;
    let parent = super::scope::canonicalize(parent)?;
    let destination = parent.join(name);
    let metadata = source.metadata()?;
    if !metadata.is_file() {
        return Err(io::Error::other("Expected a regular file"));
    }
    if let Ok(existing) = fs::symlink_metadata(&destination) {
        if existing.is_file() && same_source(&metadata, &existing, original, &destination) {
            return Ok(());
        }
        if !existing.is_file() && !existing.is_symlink() {
            return Err(io::Error::other("Destination is not a regular file"));
        }
    }
    let temp = parent.join(format!(".astrion-save-{}.tmp", Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut output = options.open(&temp)?;
    let result = (|| {
        source.rewind()?;
        io::copy(&mut source, &mut output)?;
        let mut times = FileTimes::new();
        if let Ok(modified) = metadata.modified() {
            times = times.set_modified(modified);
        }
        if let Ok(accessed) = metadata.accessed() {
            times = times.set_accessed(accessed);
        }
        output.set_times(times)?;
        output.set_permissions(metadata.permissions())?;
        output.sync_all()?;
        drop(output);
        // Replacing the selected entry also keeps symbolic links from redirecting the write.
        fs::rename(&temp, &destination)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}

#[cfg(unix)]
fn same_source(source: &fs::Metadata, dest: &fs::Metadata, _: &Path, _: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    source.dev() == dest.dev() && source.ino() == dest.ino()
}

#[cfg(not(unix))]
fn same_source(_: &fs::Metadata, _: &fs::Metadata, source: &Path, dest: &Path) -> bool {
    super::scope::canonicalize(source).is_ok_and(|source| source == dest)
}
