use std::{
    io::{self, ErrorKind, Read, Write},
    path::{Path, PathBuf},
    time::SystemTime,
};

use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
use cap_std::fs::{Dir, File, Metadata, OpenOptions};
use ring::digest::{Context, Digest, SHA256, digest};
use uuid::Uuid;

use crate::knowledge::{
    storage::{Identity, ensure_dir, open_canonical_dir, optional_dir},
    topics::workspace_namespace,
};

pub(super) const NOTICE: &str = "00_READ_ME_项目记忆已迁移.md";

pub(super) struct Workspace {
    pub label: String,
    pub project: PathBuf,
    pub target: PathBuf,
    pub files: Vec<String>,
}

#[derive(PartialEq, Eq)]
struct FileIdentity {
    file: Identity,
    size: u64,
    modified: SystemTime,
    #[cfg(unix)]
    changed: (i64, i64),
}

impl FileIdentity {
    fn read(meta: &Metadata) -> io::Result<Self> {
        if !meta.is_file() || MetadataExt::nlink(meta) != 1 {
            return Err(io::Error::new(
                ErrorKind::PermissionDenied,
                "项目记忆必须是没有硬链接别名的普通文件",
            ));
        }
        #[cfg(unix)]
        use cap_std::fs::MetadataExt as UnixMetadataExt;
        Ok(Self {
            file: Identity::of(meta),
            size: meta.len(),
            modified: meta.modified()?.into_std(),
            #[cfg(unix)]
            changed: (
                UnixMetadataExt::ctime(meta),
                UnixMetadataExt::ctime_nsec(meta),
            ),
        })
    }
}

pub(super) struct Copy {
    pub source: String,
    pub target: String,
    digest: Digest,
    identity: FileIdentity,
}

pub(super) fn target_path(data: &Path, project: &Path) -> io::Result<PathBuf> {
    let target = data
        .join("memory")
        .join(workspace_namespace(project)?)
        .join("topics");
    #[cfg(windows)]
    if let Some(path) = target.to_str() {
        if let Some(share) = path.strip_prefix(r"\\?\UNC\") {
            return Ok(PathBuf::from(format!(r"\\{share}")));
        }
        if let Some(dos) = path.strip_prefix(r"\\?\") {
            return Ok(PathBuf::from(dos));
        }
    }
    Ok(target)
}

fn source_directory(project: &Path) -> io::Result<Option<Dir>> {
    let project = open_canonical_dir(project)?;
    let Some(astrion) = optional_dir(&project, ".astrion")? else {
        return Ok(None);
    };
    optional_dir(&astrion, "memory")
}

fn target_directory(data: &Path, workspace: &Workspace, create: bool) -> io::Result<Dir> {
    let mut current = open_canonical_dir(data)?;
    let namespace = workspace_namespace(&workspace.project)?;
    for name in ["memory", &namespace, "topics"] {
        current = if create {
            ensure_dir(&current, name)?
        } else {
            current.open_dir_nofollow(name)?
        };
    }
    Ok(current)
}

pub(super) fn list(project: &Path) -> io::Result<Vec<String>> {
    source_directory(project)?.map_or_else(|| Ok(Vec::new()), |dir| filenames(&dir))
}

fn filenames(dir: &Dir) -> io::Result<Vec<String>> {
    let mut names = Vec::new();
    for entry in dir.entries()? {
        let entry = entry?;
        let name = entry.file_name();
        if name.as_encoded_bytes().ends_with(b".md")
            && name != NOTICE
            && !entry.file_type()?.is_dir()
        {
            let name = name.into_string().map_err(|_| ErrorKind::InvalidData)?;
            names.push(name);
        }
    }
    names.sort_unstable();
    Ok(names)
}

fn checked_file(dir: &Dir, name: &str) -> io::Result<File> {
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
            "项目记忆必须是没有硬链接别名的普通文件",
        ));
    }
    Ok(file)
}

fn fingerprint(dir: &Dir, name: &str) -> io::Result<(Digest, FileIdentity)> {
    let mut file = checked_file(dir, name)?;
    let identity = FileIdentity::read(&file.metadata()?)?;
    let mut context = Context::new(&SHA256);
    let mut buffer = vec![0; 256 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        context.update(&buffer[..read]);
    }
    check_identity(&file.metadata()?, &identity, name)?;
    Ok((context.finish(), identity))
}

fn check_identity(meta: &Metadata, expected: &FileIdentity, name: &str) -> io::Result<()> {
    if FileIdentity::read(meta)? != *expected {
        return Err(io::Error::other(format!("旧文件在迁移时发生变化：{name}")));
    }
    Ok(())
}

fn publish(
    dir: &Dir,
    name: &str,
    mode: u32,
    modified: Option<SystemTime>,
    write: impl FnOnce(&mut File) -> io::Result<()>,
) -> io::Result<()> {
    let temporary = format!(".astrion-memory-{}", Uuid::new_v4().simple());
    let mut options = OpenOptions::new();
    options
        .write(true)
        .create_new(true)
        .follow(FollowSymlinks::No);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.mode(mode);
    }
    #[cfg(not(unix))]
    let _ = mode;
    let mut file = dir.open_with(&temporary, &options)?;
    let written = write(&mut file);
    let file = file.into_std();
    let written = written.and_then(|()| {
        if let Some(modified) = modified {
            file.set_times(
                std::fs::FileTimes::new()
                    .set_accessed(modified)
                    .set_modified(modified),
            )?;
        }
        file.sync_all()
    });
    drop(file);
    let published = written.and_then(|()| dir.hard_link(&temporary, dir, name));
    let removed = dir.remove_file(&temporary);
    published.and(removed)?;
    #[cfg(unix)]
    dir.open(".")?.sync_all()?;
    Ok(())
}

pub(super) fn copy(
    data: &Path,
    workspace: &Workspace,
    mut progress: impl FnMut(),
) -> io::Result<Vec<Copy>> {
    let source = source_directory(&workspace.project)?.ok_or(ErrorKind::NotFound)?;
    let target = target_directory(data, workspace, true)?;
    let mut copies = Vec::new();
    for name in &workspace.files {
        let (digest, identity) = fingerprint(&source, name)?;
        let digest_prefix: String = digest.as_ref()[..6]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let stem: String = Path::new(name)
            .file_stem()
            .and_then(|stem| stem.to_str())
            .unwrap_or(name)
            .chars()
            .take(45)
            .collect();
        let destination = (0..1000)
            .find_map(|attempt| {
                let destination = match attempt {
                    0 => name.clone(),
                    1 => format!("{stem}_migrated_{digest_prefix}.md"),
                    _ => format!("{stem}_migrated_{digest_prefix}_{attempt}.md"),
                };
                let result = match target.symlink_metadata(&destination) {
                    Ok(meta) if !meta.is_file() || meta.nlink() != 1 => return None,
                    Ok(_) => fingerprint(&target, &destination)
                        .map(|(existing, _)| existing.as_ref() == digest.as_ref()),
                    Err(error) if error.kind() == ErrorKind::NotFound => publish(
                        &target,
                        &destination,
                        0o600,
                        Some(identity.modified),
                        |out| {
                            let mut input = checked_file(&source, name)?;
                            check_identity(&input.metadata()?, &identity, name)?;
                            io::copy(&mut input, out)?;
                            check_identity(&input.metadata()?, &identity, name)
                        },
                    )
                    .map(|()| true),
                    Err(error) => Err(error),
                };
                match result {
                    Ok(true) => Some(Ok(destination)),
                    Ok(false) => None,
                    Err(error) if error.kind() == ErrorKind::AlreadyExists => None,
                    Err(error) => Some(Err(error)),
                }
            })
            .transpose()?
            .ok_or_else(|| io::Error::other(format!("找不到可用的目标文件名：{name}")))?;
        copies.push(Copy {
            source: name.clone(),
            target: destination,
            digest,
            identity,
        });
        progress();
    }
    Ok(copies)
}

fn verify_one(source: &Dir, target: &Dir, copy: &Copy) -> io::Result<()> {
    let (digest, identity) = fingerprint(source, &copy.source)?;
    if identity != copy.identity || digest.as_ref() != copy.digest.as_ref() {
        return Err(io::Error::other(format!(
            "旧文件在迁移时发生变化：{}",
            copy.source
        )));
    }
    let (destination, _) = fingerprint(target, &copy.target)?;
    if destination.as_ref() != digest.as_ref() {
        return Err(io::Error::other(format!(
            "目标文件内容核验失败：{}",
            copy.target
        )));
    }
    Ok(())
}

pub(super) fn verify_and_clean(
    data: &Path,
    workspace: &Workspace,
    copies: &[Copy],
    mut progress: impl FnMut(),
) -> io::Result<()> {
    let source = source_directory(&workspace.project)?.ok_or(ErrorKind::NotFound)?;
    let target = target_directory(data, workspace, false)?;
    if filenames(&source)? != workspace.files {
        return Err(io::Error::other("工作区的待迁移文件列表发生变化，请重试。"));
    }
    for copy in copies {
        verify_one(&source, &target, copy)?;
        progress();
    }
    let notice = format!(
        "# Astrion 项目记忆已迁移\n\n本工作区的专题项目记忆已迁至 `{}`。\n\n查找专题请用 `search_project_memory`，知道名称时用 `recall_project_memory`，新增或更新用 `update_project_memory`。这些工具会定位当前工作区的记忆。请在新位置维护后续专题记忆。\n",
        workspace.target.display()
    );
    match publish(&source, NOTICE, 0o644, None, |file| {
        file.write_all(notice.as_bytes())
    }) {
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {
            let (actual, _) = fingerprint(&source, NOTICE)?;
            if digest(&SHA256, notice.as_bytes()).as_ref() != actual.as_ref() {
                return Err(io::Error::new(
                    ErrorKind::AlreadyExists,
                    format!(
                        "已有同名迁移说明，请检查：{}",
                        workspace
                            .project
                            .join(".astrion/memory")
                            .join(NOTICE)
                            .display()
                    ),
                ));
            }
        }
        result => result?,
    }
    for copy in copies {
        verify_one(&source, &target, copy)?;
        check_identity(
            &source.symlink_metadata(&copy.source)?,
            &copy.identity,
            &copy.source,
        )?;
        source.remove_file(&copy.source)?;
    }
    Ok(())
}

#[cfg(test)]
#[path = "../../../tests/fixtures/migration_files.rs"]
mod tests;
