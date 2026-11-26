use std::{
    ffi::OsString,
    fs, io,
    path::{Component, Path, PathBuf},
};

use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};
use cap_std::{
    ambient_authority,
    fs::{Dir, OpenOptions},
};
use uuid::Uuid;

use crate::project_git::runner::object_id;

use super::{Error, MANAGED, Result, git};

pub(super) struct Carry {
    source: PathBuf,
    staged: Vec<u8>,
    unstaged: Vec<u8>,
    untracked: Vec<PathBuf>,
}

impl Carry {
    pub(super) fn prepare(source: &Path) -> Result<Self> {
        let filters = git::safe_filters(source)?;
        let diff = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "--full-index",
            "--no-color",
            "--no-relative",
            "--submodule=short",
        ];
        let staged = git::run_edit(
            source,
            &filters,
            diff.into_iter().chain(["--cached", "HEAD", "--"]),
        )?;
        let unstaged = git::run_edit(source, &filters, diff.into_iter().chain(["--"]))?;
        let raw = git::read(
            source,
            ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        )?;
        let mut untracked = Vec::new();
        let checkout = Dir::open_ambient_dir(source, ambient_authority())?;
        for bytes in raw
            .split(|byte| *byte == 0)
            .filter(|bytes| !bytes.is_empty())
        {
            let path = git::local_path(bytes)?;
            if path.starts_with(MANAGED)
                || !path
                    .components()
                    .all(|part| matches!(part, Component::Normal(_)))
            {
                return Err(Error::invalid("未跟踪文件路径无效。"));
            }
            let parent = parent_dir(&checkout, &path, false)?;
            let metadata = parent.symlink_metadata(path.file_name().expect("Git file path"))?;
            if !metadata.is_file() {
                return Err(Error::invalid(format!(
                    "无法安全携带文件：{}",
                    path.display()
                )));
            }
            untracked.push(path);
        }
        Ok(Self {
            source: source.to_owned(),
            staged,
            unstaged,
            untracked,
        })
    }

    pub(super) fn apply(self, target: &Path, scratch: &Path, filters: &[String]) -> Result<()> {
        if !self.staged.is_empty() {
            apply_patch(target, scratch, filters, &self.staged)?;
        }
        if !self.unstaged.is_empty() {
            let staged_tree = object_id(git::run_edit(target, filters, ["write-tree"])?)?;
            apply_patch(target, scratch, filters, &self.unstaged)?;
            // Applying a three-way patch may stage its result. Restore exactly
            // the index produced by the staged patch.
            git::run_edit(target, filters, ["read-tree", &staged_tree])?;
        }
        let source = Dir::open_ambient_dir(&self.source, ambient_authority())?;
        let destination = Dir::open_ambient_dir(target, ambient_authority())?;
        for path in self.untracked {
            copy_untracked(&source, &destination, &path)?;
        }
        Ok(())
    }
}

fn apply_patch(target: &Path, scratch: &Path, filters: &[String], patch: &[u8]) -> Result<()> {
    use std::io::Write;
    let path = scratch.join(format!(".carry-{}.patch", Uuid::new_v4()));
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&path)?;
    let result = file.write_all(patch);
    drop(file);
    let result = result.map_err(Error::from).and_then(|()| {
        let args = [
            OsString::from("apply"),
            "--3way".into(),
            "--binary".into(),
            "--".into(),
            path.as_os_str().to_owned(),
        ];
        git::run_edit(target, filters, args).map(|_| ())
    });
    let cleanup = fs::remove_file(&path);
    match (result, cleanup) {
        (result, Ok(())) => result,
        (Ok(()), Err(error)) => Err(Error::invalid(format!(
            "补丁已应用，但无法清理 {}：{error}",
            path.display()
        ))),
        (Err(error), Err(cleanup)) => Err(Error::invalid(format!(
            "{error}；无法清理临时补丁 {}：{cleanup}",
            path.display()
        ))),
    }
}

fn copy_untracked(source: &Dir, target: &Dir, path: &Path) -> Result<()> {
    let src = parent_dir(source, path, false)?;
    let dst = parent_dir(target, path, true)?;
    let name = path.file_name().expect("validated Git filename");
    let mut input_options = OpenOptions::new();
    input_options.read(true).follow(FollowSymlinks::No);
    let mut input = src.open_with(name, &input_options)?;
    let metadata = input.metadata()?;
    if !metadata.is_file() {
        return Err(Error::invalid(format!(
            "无法安全携带文件：{}",
            path.display()
        )));
    }
    let mut output_options = OpenOptions::new();
    output_options
        .write(true)
        .create_new(true)
        .follow(FollowSymlinks::No);
    let mut output = dst.open_with(name, &output_options).map_err(|error| {
        Error::invalid(format!(
            "携带文件与目标冲突或无法写入 {}：{error}",
            path.display()
        ))
    })?;
    let copied = io::copy(&mut input, &mut output)?;
    if copied != metadata.len() || input.metadata()?.modified()? != metadata.modified()? {
        return Err(Error::invalid(format!(
            "携带期间文件已变化：{}",
            path.display()
        )));
    }
    output.set_permissions(metadata.permissions())?;
    Ok(())
}

fn parent_dir(root: &Dir, path: &Path, create: bool) -> Result<Dir> {
    let mut working = root.try_clone()?;
    for segment in path.parent().expect("validated Git path").components() {
        let name = segment.as_os_str();
        working = match working.open_dir_nofollow(name) {
            Ok(directory) => directory,
            Err(error) if create && error.kind() == io::ErrorKind::NotFound => {
                match working.create_dir(name) {
                    Ok(()) => {}
                    Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
                    Err(error) => return Err(error.into()),
                }
                working.open_dir_nofollow(name)?
            }
            Err(error) => return Err(error.into()),
        };
    }
    Ok(working)
}
