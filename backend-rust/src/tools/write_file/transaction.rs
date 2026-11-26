use std::ffi::OsStr;
use std::io::{self, ErrorKind, Read, Write};
use std::path::Path;

use cap_fs_ext::{DirExt, MetadataExt};
use cap_std::fs::{Dir, DirBuilder, File, Metadata};
use uuid::Uuid;

use crate::tools::read_file::access::{ACCESS_DENIED, Identity, Workspace, file_options};

pub(super) const TOO_LARGE: &str = "文件太大，无法写入或生成文件修改结果。";
const DENIED: &str = "路径不在启动工作区内、属于私密路径或包含链接，无法写入。";
const CHANGED: &str = "文件或上级目录在修改期间发生变化，请重新读取后重试。";
const STAGED_FILE: &str = "content";

pub(super) struct Destination<'a> {
    directory: Dir,
    name: &'a OsStr,
    original: Option<(File, Metadata)>,
}

impl<'a> Destination<'a> {
    pub fn open(workspace: &Workspace, path: &'a Path, create: bool) -> Result<Self, String> {
        let directory = workspace.open_parent(path, create).map_err(io_error)?;
        let name = path.file_name().ok_or("不是文件")?;
        let original = existing(&directory, name)?;
        Ok(Self {
            directory,
            name,
            original,
        })
    }

    pub fn exists(&self) -> bool {
        self.original.is_some()
    }

    pub fn read(&self, limit: Option<u64>) -> Result<Option<Vec<u8>>, String> {
        let Some((file, metadata)) = &self.original else {
            return Ok(None);
        };
        let mut bytes = Vec::new();
        if let Some(limit) = limit {
            if metadata.len() > limit {
                return Err(too_large(metadata.len(), limit));
            }
            file.take(limit + 1)
                .read_to_end(&mut bytes)
                .map_err(io_error)?;
            if bytes.len() as u64 > limit {
                return Err(too_large(bytes.len() as u64, limit));
            }
        } else {
            let mut file = file;
            file.read_to_end(&mut bytes).map_err(io_error)?;
        }
        if !unchanged(metadata, &file.metadata().map_err(io_error)?) {
            return Err(CHANGED.into());
        }
        Ok(Some(bytes))
    }

    pub fn commit(self, workspace: &Workspace, path: &Path, bytes: &[u8]) -> Result<(), String> {
        let staged = Staged::open(&self.directory).map_err(io_error)?;
        let source = staged.directory();
        let mut options = file_options();
        options.write(true).create_new(true);
        let mut output = source.open_with(STAGED_FILE, &options).map_err(io_error)?;
        output.write_all(bytes).map_err(io_error)?;
        if let Some((_, metadata)) = &self.original {
            output
                .set_permissions(metadata.permissions())
                .map_err(io_error)?;
        }
        output.sync_all().map_err(io_error)?;
        let output_metadata = output.metadata().map_err(io_error)?;
        let destination_parent = workspace.open_parent(path, false).map_err(|_| CHANGED)?;
        let original_parent = self.directory.dir_metadata().map_err(io_error)?;
        if Identity::of(&destination_parent.dir_metadata().map_err(io_error)?)
            != Identity::of(&original_parent)
        {
            return Err(CHANGED.into());
        }
        let current = existing(&destination_parent, self.name).map_err(|_| CHANGED)?;
        let (_, staged_metadata) = existing(source, OsStr::new(STAGED_FILE))?.ok_or(CHANGED)?;
        if !unchanged(&output_metadata, &staged_metadata) {
            return Err(CHANGED.into());
        }
        match (&self.original, current) {
            (Some((_, before)), Some((_, now))) if unchanged(before, &now) => {
                // Rename replaces the directory entry without following its final component.
                source
                    .rename(STAGED_FILE, &destination_parent, self.name)
                    .map_err(io_error)
            }
            (None, None) => {
                // Publish a new file atomically with no clobber if the name appears while staging.
                source
                    .hard_link(STAGED_FILE, &destination_parent, self.name)
                    .map_err(io_error)
            }
            _ => Err(CHANGED.into()),
        }
    }
}

fn existing(directory: &Dir, name: &OsStr) -> Result<Option<(File, Metadata)>, String> {
    let mut options = file_options();
    // Test the file's own write permissions as well as directory permissions.
    options.write(true);
    let file = match directory.open_with(name, &options) {
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        opened => opened.map_err(io_error)?,
    };
    let metadata = file.metadata().map_err(io_error)?;
    if !metadata.is_file() {
        return Err("不是文件".into());
    }
    if metadata.nlink() != 1 {
        return Err(DENIED.into());
    }
    Ok(Some((file, metadata)))
}

fn unchanged(before: &Metadata, after: &Metadata) -> bool {
    Identity::of(before) == Identity::of(after)
        && after.is_file()
        && after.nlink() == 1
        && before.len() == after.len()
        && before.permissions() == after.permissions()
        && before.modified().ok() == after.modified().ok()
}

struct Staged(Option<Dir>);

impl Staged {
    fn open(parent: &Dir) -> io::Result<Self> {
        let name = format!(".astrion-write-{}", Uuid::new_v4().simple());
        let mut builder = DirBuilder::new();
        #[cfg(unix)]
        {
            use cap_std::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        #[cfg(not(unix))]
        builder.recursive(false);
        parent.create_dir_with(&name, &builder)?;
        parent.open_dir_nofollow(&name).map(|dir| Self(Some(dir)))
    }

    fn directory(&self) -> &Dir {
        self.0.as_ref().expect("staging directory owned until drop")
    }
}

impl Drop for Staged {
    fn drop(&mut self) {
        if let Some(directory) = self.0.take() {
            let _ = directory.remove_file(STAGED_FILE);
            let _ = directory.remove_open_dir();
        }
    }
}

pub(super) fn write_path_error(error: String) -> String {
    if error == ACCESS_DENIED {
        DENIED.into()
    } else {
        error
    }
}

fn too_large(size: u64, limit: u64) -> String {
    let mib = |bytes: u64| bytes as f64 / 1024.0 / 1024.0;
    format!("文件太大 ({:.2}MB > {:.1}MB)", mib(size), mib(limit))
}

fn io_error(error: io::Error) -> String {
    match error.kind() {
        ErrorKind::NotFound => "文件不存在".into(),
        ErrorKind::IsADirectory => "不是文件".into(),
        ErrorKind::AlreadyExists => CHANGED.into(),
        ErrorKind::WriteZero | ErrorKind::StorageFull => "文件写入失败。".into(),
        _ => DENIED.into(),
    }
}
