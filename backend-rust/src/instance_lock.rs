use std::fs::{self, File, OpenOptions};
use std::io;
use std::path::Path;

pub const LOCK_FILE_NAME: &str = "astrion-backend.lock";

/// The Python and Rust backends hold an exclusive lock on the same file.
/// Keep the file in place after exit so competing processes always lock the same inode.
pub struct InstanceLock {
    _file: File,
}

impl InstanceLock {
    pub fn acquire(data_dir: &Path) -> io::Result<Self> {
        fs::create_dir_all(data_dir)?;
        let path = data_dir.join(LOCK_FILE_NAME);
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options
                .mode(0o600)
                .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            use windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT;
            options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
        }
        let file = options.open(&path)?;
        let metadata = file.metadata()?;
        if !is_regular_lock_file(&metadata) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("Invalid Astrion backend lock file: {}", path.display()),
            ));
        }
        // fs2 uses flock on Unix and LockFileEx on Windows, where its locked range
        // overlaps the first byte locked by Python's msvcrt.locking.
        fs2::FileExt::try_lock_exclusive(&file).map_err(|error| {
            if error.kind() == io::ErrorKind::WouldBlock
                || error.raw_os_error() == fs2::lock_contended_error().raw_os_error()
            {
                io::Error::new(
                    io::ErrorKind::WouldBlock,
                    format!(
                        "Another Astrion backend is using this data directory: {}",
                        data_dir.display()
                    ),
                )
            } else {
                error
            }
        })?;
        Ok(Self { _file: file })
    }
}

fn is_regular_lock_file(metadata: &fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.nlink() != 1 {
            return false;
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return false;
        }
    }
    metadata.is_file()
}
