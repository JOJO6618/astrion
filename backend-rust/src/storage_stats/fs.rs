use std::{
    ffi::{OsStr, OsString},
    io,
    path::{Component, Path, PathBuf},
    time::SystemTime,
};

use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
use cap_std::{
    ambient_authority,
    fs::{self, File, Metadata, OpenOptions},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct Identity(u64, u64);

impl Identity {
    fn of(metadata: &Metadata) -> Self {
        Self(metadata.dev(), metadata.ino())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Kind {
    Directory,
    File,
    Other,
}

pub(super) struct Entry {
    pub kind: Kind,
    pub bytes: u64,
    pub identity: Identity,
}

impl Entry {
    fn of(metadata: Metadata) -> Self {
        let kind = if is_link(&metadata) {
            Kind::Other
        } else if metadata.is_dir() {
            Kind::Directory
        } else if metadata.is_file() {
            Kind::File
        } else {
            Kind::Other
        };
        Self {
            kind,
            bytes: allocated_bytes(&metadata),
            identity: Identity::of(&metadata),
        }
    }
}

pub(super) struct Dir {
    inner: fs::Dir,
    pub path: PathBuf,
    pub identity: Identity,
    #[cfg(windows)]
    ancestors: Option<std::sync::Arc<PinnedDir>>,
}

#[cfg(windows)]
struct PinnedDir {
    _handle: fs::Dir,
    _parent: Option<std::sync::Arc<PinnedDir>>,
}

impl Dir {
    pub fn open(path: &Path) -> io::Result<Self> {
        Self::open_path(path, false)?.configured()
    }

    pub fn ensure(path: &Path) -> io::Result<Self> {
        Self::open_path(path, true)?.configured()
    }

    fn configured(self) -> io::Result<Self> {
        if self.path.file_name().is_none() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "storage root must be a subdirectory",
            ));
        }
        Ok(self)
    }

    fn open_path(path: &Path, create: bool) -> io::Result<Self> {
        let absolute = std::path::absolute(path)?;
        let root = absolute
            .ancestors()
            .filter(|path| path.has_root())
            .last()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid storage root"))?;
        let relative = absolute
            .strip_prefix(root)
            .expect("path begins with its root");
        let mut names = Vec::new();
        for component in relative.components() {
            match component {
                Component::Normal(name) => names.push(name),
                Component::ParentDir => {
                    names.pop();
                }
                Component::CurDir => {}
                _ => {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "invalid storage root",
                    ));
                }
            }
        }
        let mut dir = Self::new(
            fs::Dir::open_ambient_dir(root, ambient_authority())?,
            root.to_owned(),
            #[cfg(windows)]
            None,
        )?;
        // A configured path is opened one component at a time. The same rule
        // applies when a first scan needs to create the data directory.
        for name in names {
            dir = match dir.child(name) {
                Err(error) if create && error.kind() == io::ErrorKind::NotFound => {
                    match dir.inner.create_dir(child_name(name)?) {
                        Ok(()) => {}
                        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
                        Err(error) => return Err(error),
                    }
                    dir.child(name)?
                }
                other => other?,
            };
        }
        Ok(dir)
    }

    fn new(
        inner: fs::Dir,
        path: PathBuf,
        #[cfg(windows)] ancestors: Option<std::sync::Arc<PinnedDir>>,
    ) -> io::Result<Self> {
        let metadata = inner.dir_metadata()?;
        if !metadata.is_dir() || is_link(&metadata) {
            return Err(io::Error::new(
                io::ErrorKind::NotADirectory,
                "invalid storage directory",
            ));
        }
        Ok(Self {
            inner,
            path,
            identity: Identity::of(&metadata),
            #[cfg(windows)]
            ancestors,
        })
    }

    pub fn child(&self, name: &OsStr) -> io::Result<Self> {
        Self::new(
            self.inner.open_dir_nofollow(child_name(name)?)?,
            self.path.join(name),
            // cap-std opens directory handles without FILE_SHARE_DELETE.
            // Keep the whole chain pinned for its Win32 path-based operations.
            #[cfg(windows)]
            Some(std::sync::Arc::new(PinnedDir {
                _handle: self.inner.try_clone()?,
                _parent: self.ancestors.clone(),
            })),
        )
    }

    pub fn same_device(&self, other: &Self) -> bool {
        self.identity.0 == other.identity.0
    }

    pub fn is_current(&self) -> bool {
        Self::open(&self.path).is_ok_and(|dir| dir.identity == self.identity)
    }

    pub fn relative_to(&self, ancestor: &Self) -> Option<&Path> {
        if self.identity == ancestor.identity {
            return Some(Path::new(""));
        }
        self.path.ancestors().skip(1).find_map(|path| {
            (path == ancestor.path
                || Self::open_path(path, false).is_ok_and(|dir| dir.identity == ancestor.identity))
            .then(|| self.path.strip_prefix(path).expect("directory ancestor"))
        })
    }

    pub fn names(&self) -> io::Result<Vec<OsString>> {
        self.inner
            .entries()?
            .map(|entry| entry.map(|entry| entry.file_name()))
            .collect()
    }

    pub fn entry(&self, name: &OsStr) -> io::Result<Entry> {
        self.inner
            .symlink_metadata(child_name(name)?)
            .map(Entry::of)
    }

    pub fn modified(&self) -> io::Result<SystemTime> {
        Ok(self.inner.dir_metadata()?.modified()?.into_std())
    }

    pub fn read_file(&self, name: &OsStr) -> io::Result<File> {
        let entry = self.entry(name)?;
        self.open_regular(name, &entry, false)
    }

    fn open_regular(&self, name: &OsStr, entry: &Entry, write: bool) -> io::Result<File> {
        if entry.kind != Kind::File {
            return Err(unsafe_file());
        }
        let mut options = OpenOptions::new();
        options.read(!write).write(write).follow(FollowSymlinks::No);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            // A FIFO substituted before open must fail the regular-file check
            // without blocking the scan or cleanup worker.
            options.custom_flags(libc::O_NONBLOCK);
        }
        let file = self.inner.open_with(child_name(name)?, &options)?;
        let metadata = file.metadata()?;
        if !metadata.is_file() || is_link(&metadata) || metadata.nlink() != 1 {
            return Err(unsafe_file());
        }
        if Identity::of(&metadata) != entry.identity {
            return Err(changed());
        }
        Ok(file)
    }

    pub fn create_file(&self, name: &OsStr) -> io::Result<File> {
        let mut options = OpenOptions::new();
        options
            .write(true)
            .create_new(true)
            .follow(FollowSymlinks::No);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        self.inner.open_with(child_name(name)?, &options)
    }

    pub fn replace_file(&self, from: &OsStr, to: &OsStr) -> io::Result<()> {
        self.inner
            .rename(child_name(from)?, &self.inner, child_name(to)?)
    }

    pub fn truncate(&self, name: &OsStr, entry: &Entry) -> io::Result<()> {
        self.open_regular(name, entry, true)?.set_len(0)
    }

    pub fn remove(&self, name: &OsStr, entry: &Entry) -> io::Result<()> {
        let current = self.entry(name)?;
        if current.kind != entry.kind || current.identity != entry.identity {
            return Err(changed());
        }
        self.inner.remove_file_or_symlink(child_name(name)?)
    }

    pub fn remove_dir(&self, name: &OsStr, dir: Self) -> io::Result<()> {
        let entry = self.entry(name)?;
        if entry.kind != Kind::Directory || entry.identity != dir.identity {
            return Err(changed());
        }
        drop(dir);
        self.inner.remove_dir(child_name(name)?)
    }
}

pub(super) fn walk_files(
    root: &Dir,
    exclude: Option<Identity>,
    stay_on_device: bool,
    mut visit: impl FnMut(&Dir, &[OsString], &OsStr, Entry),
) {
    if exclude == Some(root.identity) {
        return;
    }

    let mut stack = vec![(None, root.names().unwrap_or_default().into_iter())];
    let mut path = Vec::new();
    while let Some((dir, names)) = stack.last_mut() {
        let Some(name) = names.next() else {
            stack.pop();
            path.pop();
            continue;
        };
        let parent = dir.as_ref().unwrap_or(root);
        let Ok(entry) = parent.entry(&name) else {
            continue;
        };
        if stay_on_device && root.identity.0 != entry.identity.0 {
            continue;
        }
        match entry.kind {
            Kind::Directory => {
                let Ok(child) = parent.child(&name) else {
                    continue;
                };
                if entry.identity != child.identity || exclude == Some(child.identity) {
                    continue;
                }
                if let Ok(names) = child.names() {
                    stack.push((Some(child), names.into_iter()));
                    path.push(name);
                }
            }
            Kind::File => visit(parent, &path, &name, entry),
            _ => {}
        }
    }
}

fn changed() -> io::Error {
    io::Error::other("storage entry changed during operation")
}

fn unsafe_file() -> io::Error {
    io::Error::other("storage entry is not a private regular file")
}

fn child_name(name: &OsStr) -> io::Result<&Path> {
    let path = Path::new(name);
    let mut components = path.components();
    if matches!(components.next(), Some(Component::Normal(part)) if part == name)
        && components.next().is_none()
    {
        Ok(path)
    } else {
        Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid storage entry name",
        ))
    }
}

fn is_link(metadata: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use cap_std::fs::MetadataExt;
        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    metadata.is_symlink()
}

fn allocated_bytes(metadata: &Metadata) -> u64 {
    #[cfg(unix)]
    {
        use cap_std::fs::MetadataExt;
        if metadata.blocks() > 0 {
            return metadata.blocks().saturating_mul(512);
        }
    }
    metadata.len()
}

pub(super) fn missing_or_unsafe_root(error: &io::Error) -> bool {
    #[cfg(unix)]
    if error.raw_os_error() == Some(libc::ELOOP) {
        return true;
    }
    #[cfg(windows)]
    if error.raw_os_error() == Some(windows_sys::Win32::Foundation::ERROR_STOPPED_ON_SYMLINK as i32)
    {
        return true;
    }
    matches!(
        error.kind(),
        io::ErrorKind::NotFound | io::ErrorKind::NotADirectory
    )
}
