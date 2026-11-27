use std::{
    io::{self, ErrorKind, Read, Write},
    path::Path,
};

use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
use cap_std::{
    ambient_authority,
    fs::{Dir, OpenOptions},
};
use ring::digest::{SHA256, digest};
use serde_json::{Value, json};
use uuid::Uuid;

pub(super) const MAX_BYTES: u64 = 100 * 1024 * 1024;
const MAX_READ_CHARS: usize = 50_000;

#[derive(Debug, Eq, PartialEq)]
pub(super) struct Identity(u64, u64);

impl Identity {
    pub fn of(metadata: &impl MetadataExt) -> Self {
        Self(metadata.dev(), metadata.ino())
    }
}

pub(crate) fn open_canonical_dir(path: &Path) -> io::Result<Dir> {
    let anchor = path.ancestors().last().ok_or(ErrorKind::InvalidInput)?;
    let mut dir = Dir::open_ambient_dir(anchor, ambient_authority())?;
    let relative = path.strip_prefix(anchor).map_err(io::Error::other)?;
    for component in relative.components() {
        dir = dir.open_dir_nofollow(component.as_os_str())?;
    }
    Ok(dir)
}

pub(super) fn optional_dir(parent: &Dir, name: &str) -> io::Result<Option<Dir>> {
    match parent.open_dir_nofollow(name) {
        Ok(dir) => Ok(Some(dir)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

pub(crate) fn ensure_dir(parent: &Dir, name: &str) -> io::Result<Dir> {
    match parent.create_dir(name) {
        Ok(()) => {}
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error),
    }
    parent.open_dir_nofollow(name)
}

pub(crate) fn read_bytes(dir: &Dir, name: &str, limit: u64) -> io::Result<Vec<u8>> {
    let mut options = OpenOptions::new();
    options.read(true).follow(FollowSymlinks::No);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let file = dir.open_with(name, &options)?;
    let meta = file.metadata()?;
    if !meta.is_file() || meta.nlink() != 1 {
        return Err(io::Error::new(
            ErrorKind::PermissionDenied,
            "Expected an unlinked regular file",
        ));
    }
    if meta.len() > limit {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            "Knowledge file exceeds size limit",
        ));
    }
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            "Knowledge file exceeds size limit",
        ));
    }
    Ok(bytes)
}

pub(super) struct Document {
    pub text: String,
    pub(super) size: usize,
    pub version: String,
}

impl Document {
    pub fn open(dir: &Dir, name: &str) -> io::Result<Self> {
        Self::open_bounded(dir, name, MAX_BYTES)
    }

    pub fn open_bounded(dir: &Dir, name: &str, limit: u64) -> io::Result<Self> {
        let bytes = read_bytes(dir, name, limit)?;
        let size = bytes.len();
        let version = version(&bytes);
        let text = String::from_utf8(bytes)
            .map_err(|error| io::Error::new(ErrorKind::InvalidData, error))?;
        let text = if text.contains('\r') {
            text.replace("\r\n", "\n").replace('\r', "\n")
        } else {
            text
        };
        Ok(Self {
            text,
            size,
            version,
        })
    }

    pub fn read_result(&self, path: &str) -> Value {
        let total_lines = self.text.split_inclusive('\n').count();
        let end = self
            .text
            .char_indices()
            .nth(MAX_READ_CHARS)
            .map(|(index, _)| index);
        let content = &self.text[..end.unwrap_or(self.text.len())];
        let start = usize::from(total_lines != 0);
        json!({
            "success": true, "type": "read", "path": path, "encoding": "utf-8",
            "max_chars": MAX_READ_CHARS, "truncated": end.is_some(), "content": content,
            "line_start": start, "line_end": total_lines, "total_lines": total_lines,
            "file_size": self.size, "char_count": content.chars().count(),
            "message": format!("已读取 {path} 的内容（行 {start}~{total_lines}）")
        })
    }
}

pub(super) fn version(bytes: &[u8]) -> String {
    use std::fmt::Write as _;
    let mut result = String::with_capacity(64);
    for byte in digest(&SHA256, bytes).as_ref() {
        write!(result, "{byte:02x}").expect("writing to String");
    }
    result
}

/// Publish Markdown atomically in the anchored directory. When `exists` is false,
/// a concurrently created entry is preserved and reported as AlreadyExists.
pub(crate) fn publish(dir: &Dir, filename: &str, content: &[u8], exists: bool) -> io::Result<()> {
    let temp = format!(".astrion-memory-{}", Uuid::new_v4().simple());
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
    let mut file = dir.open_with(&temp, &options)?;
    let written = file.write_all(content).and_then(|()| file.sync_all());
    drop(file);
    let published = written.and_then(|()| {
        if exists {
            dir.rename(&temp, dir, filename)
        } else {
            dir.hard_link(&temp, dir, filename)
        }
    });
    if exists && published.is_ok() {
        return Ok(());
    }
    let removed = dir.remove_file(&temp);
    published.and(removed)
}

pub(super) fn display_io(error: io::Error) -> String {
    match error.kind() {
        ErrorKind::NotFound => "文件不存在".into(),
        ErrorKind::InvalidData => "文件过大或不是有效的 UTF-8 文本".into(),
        _ => "无法访问知识文件或目录，请检查路径和权限。".into(),
    }
}
