use std::{
    ffi::OsStr,
    fs::{self, File},
    io::{self, Read},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

use ring::digest::{SHA256, digest};
use serde_json::{Value, json};

use super::{GitResult, Workspace, canonicalize, runner};

const TTL: Duration = Duration::from_secs(60);
const SKIP_DIRS: &[&str] = &[
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".astrion",
    ".codegraph",
    ".cache",
    ".next",
    ".nuxt",
];

pub(super) struct Discovery {
    scanned_at: Instant,
    result: Value,
    repositories: Vec<(String, PathBuf)>,
}

pub(super) struct Repository {
    pub id: String,
    pub path: PathBuf,
    pub git_dir: PathBuf,
}

impl Repository {
    pub(super) fn revalidate(&self, workspace_root: &Path) -> GitResult<()> {
        if validated_gitdir(workspace_root, &self.path)?.as_deref() == Some(self.git_dir.as_path())
        {
            return Ok(());
        }
        Err("仓库路径已变化，请刷新仓库列表。".into())
    }
}

impl Workspace {
    pub(super) fn repositories(&self, refresh: bool) -> GitResult<Value> {
        self.with_discovery(refresh, |cache| cache.result.clone())
    }

    pub(super) fn repository(&self, id: &str) -> GitResult<Repository> {
        let path = self
            .with_discovery(false, |cache| {
                cache
                    .repositories
                    .iter()
                    .find(|(key, _)| key == id)
                    .map(|(_, path)| path.clone())
            })?
            .ok_or("仓库不属于该工作区，请刷新仓库列表。")?;
        let git_dir =
            validated_gitdir(&self.root, &path)?.ok_or("仓库路径已变化，请刷新仓库列表。")?;
        Ok(Repository {
            id: id.to_owned(),
            path,
            git_dir,
        })
    }

    fn with_discovery<T>(
        &self,
        refresh: bool,
        project: impl FnOnce(&Discovery) -> T,
    ) -> GitResult<T> {
        let mut cache = self.discovery.lock().map_err(|_| "无法读取仓库索引。")?;
        if refresh
            || cache
                .as_ref()
                .is_none_or(|value| value.scanned_at.elapsed() >= TTL)
        {
            *cache = Some(self.discover());
        }
        Ok(project(
            cache.as_ref().expect("repository discovery populated"),
        ))
    }

    fn discover(&self) -> Discovery {
        let mut repositories = Vec::new();
        let mut warnings = Vec::new();
        let mut pending = Vec::new();
        if is_unchanged_directory(&self.root) {
            pending.push(self.root.clone());
        } else {
            warnings.push(self.warning("工作区目录不存在或不可访问。"));
        }
        while let Some(path) = pending.pop() {
            if !is_unchanged_directory(&path) {
                continue;
            }
            match validated_gitdir(&self.root, &path) {
                Ok(Some(_)) => repositories.push((
                    short_hash(path.as_os_str().as_encoded_bytes()),
                    path.clone(),
                )),
                Ok(None) => {}
                Err(error) => warnings.push(self.warning(&error.0)),
            }
            match fs::read_dir(&path) {
                Ok(entries) => {
                    for entry in entries {
                        match entry.and_then(|entry| Ok((entry.file_type()?, entry))) {
                            Ok((kind, entry))
                                if kind.is_dir()
                                    && !SKIP_DIRS.iter().any(|name| entry.file_name() == *name) =>
                            {
                                pending.push(entry.path());
                            }
                            Ok(_) => {}
                            Err(error) => warnings.push(self.warning(&error.to_string())),
                        }
                    }
                }
                Err(error) => warnings.push(self.warning(&error.to_string())),
            }
        }
        repositories.sort_by_cached_key(|(_, path)| path.to_string_lossy().to_lowercase());
        let rows: Vec<_> = repositories.iter().map(|(id, path)| {
            let relative = path.strip_prefix(&self.root).expect("scoped repository");
            let relative = if relative.as_os_str().is_empty() { Path::new(".") } else { relative };
            json!({
                "id": id,
                "name": path.file_name().unwrap_or_default().to_string_lossy(),
                "path": path.to_string_lossy(),
                "workspaces": [{"workspace_id": self.id, "label": self.label, "relative_path": relative.to_string_lossy()}],
            })
        }).collect();
        let result = json!({
            "repositories": rows,
            "workspaces": [{"workspace_id": self.id, "label": self.label, "path": self.root.to_string_lossy()}],
            "warnings": warnings,
        });
        Discovery {
            scanned_at: Instant::now(),
            result,
            repositories,
        }
    }

    fn warning(&self, error: &str) -> Value {
        json!({"workspace_id": self.id, "error": error})
    }
}

pub(super) fn short_hash(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let hash = digest(&SHA256, bytes);
    let mut short = String::with_capacity(24);
    for byte in &hash.as_ref()[..12] {
        short.push(HEX[(byte >> 4) as usize] as char);
        short.push(HEX[(byte & 0xf) as usize] as char);
    }
    short
}

fn is_unchanged_directory(path: &Path) -> bool {
    canonicalize(path).is_ok_and(|actual| actual == path) && path.is_dir()
}

fn validated_gitdir(root: &Path, path: &Path) -> GitResult<Option<PathBuf>> {
    if !path.starts_with(root) || !is_unchanged_directory(path) {
        return Err("仓库路径已变化，请刷新仓库列表。".into());
    }
    let marker = path.join(".git");
    let marker_kind = match fs::symlink_metadata(&marker) {
        Ok(metadata) => metadata.file_type(),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let gitdir = if marker_kind.is_dir() {
        canonicalize(&marker)?
    } else if marker_kind.is_file() {
        let raw = read_pointer(&marker)?;
        let destination = raw
            .strip_prefix(b"gitdir: ")
            .ok_or("无效的 Git 工作树记录。")?;
        canonicalize(&path.join(git_path(destination)?))?
    } else {
        return Ok(None);
    };
    let common = match fs::symlink_metadata(gitdir.join("commondir")) {
        Ok(_) => canonicalize(&gitdir.join(git_path(&read_pointer(&gitdir.join("commondir"))?)?))?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => gitdir.clone(),
        Err(error) => return Err(error.into()),
    };
    if !gitdir.starts_with(root) || !common.starts_with(root) {
        // Linked worktrees can have their primary metadata outside the workspace.
        // Recognize that layout only when the per-worktree gitdir points back here.
        if !marker_kind.is_file() || gitdir.parent() != Some(common.join("worktrees").as_path()) {
            return Err("Git 元数据超出已注册工作区范围。".into());
        }
        let backlink = git_path(&read_pointer(&gitdir.join("gitdir"))?)?;
        if canonicalize(&gitdir.join(backlink))? != marker {
            return Err("Git 工作树记录与当前目录不匹配。".into());
        }
    }
    let actual = runner::top_level(path)?;
    if canonicalize(&git_path(trim_newlines(&actual))?)? != path {
        return Err("仓库路径已变化，请刷新仓库列表。".into());
    }
    Ok(Some(gitdir))
}

fn read_pointer(path: &Path) -> GitResult<Vec<u8>> {
    const MAX_POINTER: u64 = 8192;
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.len() > MAX_POINTER {
        return Err("无效的 Git 工作树记录。".into());
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take(MAX_POINTER + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_POINTER || bytes.contains(&0) {
        return Err("无效的 Git 工作树记录。".into());
    }
    bytes.truncate(trim_newlines(&bytes).len());
    Ok(bytes)
}

fn trim_newlines(mut bytes: &[u8]) -> &[u8] {
    while bytes
        .last()
        .is_some_and(|byte| matches!(byte, b'\r' | b'\n'))
    {
        bytes = &bytes[..bytes.len() - 1];
    }
    bytes
}

pub(super) fn git_path(bytes: &[u8]) -> GitResult<PathBuf> {
    if bytes.is_empty() {
        return Err("无效的 Git 仓库路径。".into());
    }
    #[cfg(unix)]
    let path = {
        use std::os::unix::ffi::OsStrExt;
        OsStr::from_bytes(bytes)
    };
    #[cfg(not(unix))]
    let path = OsStr::new(std::str::from_utf8(bytes).map_err(|_| "无效的 Git 仓库路径。")?);
    Ok(PathBuf::from(path))
}
