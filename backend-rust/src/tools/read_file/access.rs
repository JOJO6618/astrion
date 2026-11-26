use std::io::{self, ErrorKind, Read};
use std::path::{Component, Path, PathBuf};

use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
use cap_std::ambient_authority;
use cap_std::fs::{Dir, File, OpenOptions};

use super::MAX_FILE_BYTES;

pub(in crate::tools) const ACCESS_DENIED: &str =
    "路径不在启动工作区内、属于私密路径或包含链接，无法读取。";
const NOT_A_FILE: &str = "不是文件";

// Workspace-local equivalents of the sensitive paths in host_sandbox_policy.py.
const PRIVATE_NAMES: &[&str] = &[
    ".ssh",
    ".aws",
    ".azure",
    ".gcp",
    ".google",
    ".kube",
    ".docker",
    ".gnupg",
    ".npmrc",
    ".netrc",
    ".pypirc",
    ".git-credentials",
    ".bash_history",
    ".zsh_history",
    ".psql_history",
    ".mysql_history",
    ".pgpass",
    "host_api_token",
    "rundata_settings.json",
];

#[derive(Debug, PartialEq, Eq)]
pub(in crate::tools) struct Identity {
    device: u64,
    inode: u64,
}

impl Identity {
    pub(in crate::tools) fn of(metadata: &impl MetadataExt) -> Self {
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }
}

#[derive(Debug)]
pub(in crate::tools) struct Workspace {
    directory: Dir,
    startup_path: PathBuf,
    canonical_path: PathBuf,
    private_path: Option<PathBuf>,
    private_identity: Identity,
    pub(in crate::tools) data_path: PathBuf,
}

impl Workspace {
    pub fn open(workspace: &Path, data_dir: &Path) -> io::Result<Self> {
        let startup_path = std::path::absolute(workspace)?;
        let canonical_path = startup_path.canonicalize()?;
        let data_path = data_dir.canonicalize()?;
        if canonical_path.parent().is_none()
            || canonical_path.starts_with(&data_path)
            || contains_private_component(&canonical_path)
        {
            return Err(io::Error::new(ErrorKind::PermissionDenied, ACCESS_DENIED));
        }
        let directory = open_startup_directory(&canonical_path)?;
        let private_identity = Identity::of(&open_startup_directory(&data_path)?.dir_metadata()?);
        if Identity::of(&directory.dir_metadata()?) == private_identity {
            return Err(io::Error::new(ErrorKind::PermissionDenied, ACCESS_DENIED));
        }
        let private_path = data_path
            .strip_prefix(&canonical_path)
            .ok()
            .map(Path::to_owned);
        Ok(Self {
            directory,
            startup_path,
            canonical_path,
            private_path,
            private_identity,
            data_path,
        })
    }

    pub fn read(&self, raw_path: &str) -> Result<(String, Vec<u8>, u64), String> {
        let path = self.relative_path(raw_path)?;
        let file = self.open_regular(&path).map_err(open_error)?;
        let size = file.metadata().map_err(read_error)?.len();
        if size > MAX_FILE_BYTES {
            return Err(too_large(size));
        }
        let mut bytes = Vec::new();
        file.take(MAX_FILE_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(read_error)?;
        if bytes.len() as u64 > MAX_FILE_BYTES {
            return Err(too_large(bytes.len() as u64));
        }
        Ok((path.to_string_lossy().into_owned(), bytes, size))
    }

    pub(in crate::tools) fn matches_root(&self, root: &Path) -> bool {
        (root == self.startup_path || root == self.canonical_path)
            && self.directory.dir_metadata().is_ok_and(|saved| {
                open_startup_directory(root).and_then(|dir| dir.dir_metadata())
                    .is_ok_and(|current| Identity::of(&saved) == Identity::of(&current))
            })
    }

    pub(in crate::tools) fn relative_path(&self, raw: &str) -> Result<PathBuf, String> {
        let raw = raw.trim();
        let path = Path::new(raw);
        let relative = if raw == "/workspace" {
            Path::new("")
        } else if let Some(alias) = raw.strip_prefix("/workspace/") {
            Path::new(alias)
        } else if path.has_root() || matches!(path.components().next(), Some(Component::Prefix(_)))
        {
            path.strip_prefix(&self.startup_path)
                .or_else(|_| path.strip_prefix(&self.canonical_path))
                .map_err(|_| ACCESS_DENIED)?
        } else {
            path
        };
        let mut path = PathBuf::new();
        for component in relative.components() {
            match component {
                Component::CurDir => {}
                Component::Normal(name) => path.push(name),
                _ => return Err(ACCESS_DENIED.into()),
            }
        }
        if path.as_os_str().is_empty() {
            return Err(NOT_A_FILE.into());
        }
        if contains_private_component(&path)
            || self
                .private_path
                .as_ref()
                .is_some_and(|private| path.starts_with(private))
        {
            return Err(ACCESS_DENIED.into());
        }
        Ok(path)
    }

    pub(super) fn open_regular(&self, path: &Path) -> io::Result<File> {
        let name = path.file_name().ok_or(ErrorKind::IsADirectory)?;
        let parent = self.open_parent(path, false)?;
        let file = parent.open_with(name, &file_options())?;
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err(ErrorKind::IsADirectory.into());
        }
        if metadata.nlink() != 1 {
            return Err(ErrorKind::PermissionDenied.into());
        }
        Ok(file)
    }

    pub(in crate::tools) fn open_parent(&self, path: &Path, create: bool) -> io::Result<Dir> {
        let mut parent = None;
        for component in path.parent().expect("validated file path").components() {
            let directory = parent.as_ref().unwrap_or(&self.directory);
            let name = component.as_os_str();
            let child = match directory.open_dir_nofollow(name) {
                Err(error) if create && error.kind() == ErrorKind::NotFound => {
                    match directory.create_dir(name) {
                        Err(error) if error.kind() != ErrorKind::AlreadyExists => {
                            return Err(error);
                        }
                        _ => directory.open_dir_nofollow(name)?,
                    }
                }
                child => child?,
            };
            if Identity::of(&child.dir_metadata()?) == self.private_identity {
                return Err(io::Error::new(ErrorKind::PermissionDenied, ACCESS_DENIED));
            }
            parent = Some(child);
        }
        parent.map_or_else(|| self.directory.try_clone(), Ok)
    }
}

pub(in crate::tools) fn file_options() -> OpenOptions {
    let mut options = OpenOptions::new();
    options.read(true).follow(FollowSymlinks::No);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        // A FIFO substituted before the open must fail the regular-file check without blocking.
        options.custom_flags(libc::O_NONBLOCK);
    }
    options
}

fn open_startup_directory(path: &Path) -> io::Result<Dir> {
    let anchor = path.ancestors().last().expect("absolute startup path");
    let mut directory = Dir::open_ambient_dir(anchor, ambient_authority())?;
    let relative = path
        .strip_prefix(anchor)
        .expect("path anchored at filesystem root");
    for component in relative.components() {
        directory = directory.open_dir_nofollow(component.as_os_str())?;
    }
    Ok(directory)
}

fn contains_private_component(path: &Path) -> bool {
    let mut previous = String::new();
    for component in path.components() {
        let Component::Normal(name) = component else {
            continue;
        };
        let name = name.to_string_lossy().to_uppercase().to_lowercase();
        if PRIVATE_NAMES.contains(&name.as_str())
            || name.ends_with(".env")
            || name.contains(".env.")
            || (previous == "library" && name == "keychains")
            || (previous == ".astrion" && matches!(name.as_str(), "astrion" | "astrion-desktop"))
            || windows_path_alias(&name)
        {
            return true;
        }
        previous = name;
    }
    false
}

#[cfg(windows)]
fn windows_path_alias(name: &str) -> bool {
    if name.ends_with([' ', '.']) || name.contains(':') {
        return true;
    }
    let stem = name.split('.').next().unwrap_or_default();
    // A short NTFS alias must not bypass the spelling checks for a private file.
    stem.rsplit_once('~').is_some_and(|(_, digits)| {
        !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
    })
}

#[cfg(not(windows))]
fn windows_path_alias(_: &str) -> bool {
    false
}

fn open_error(error: io::Error) -> String {
    match error.kind() {
        ErrorKind::NotFound => "文件不存在".into(),
        ErrorKind::IsADirectory => NOT_A_FILE.into(),
        _ => ACCESS_DENIED.into(),
    }
}

fn read_error(_: io::Error) -> String {
    "读取文件失败".into()
}

fn too_large(size: u64) -> String {
    format!(
        "文件太大 ({:.2}MB > 100.0MB)",
        size as f64 / 1024.0 / 1024.0
    )
}
