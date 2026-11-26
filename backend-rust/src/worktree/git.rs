use std::{
    ffi::{OsStr, OsString},
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

use crate::project_git::{
    canonicalize,
    runner::{Git, object_id},
};

use super::{Error, Result};

pub(super) fn command(path: &Path) -> Git {
    Git::new(path).with_timeout(Duration::from_secs(120))
}

pub(super) fn read(
    path: &Path,
    args: impl IntoIterator<Item = impl AsRef<OsStr>>,
) -> Result<Vec<u8>> {
    Ok(command(path).read(args)?)
}

pub(super) fn text(
    path: &Path,
    args: impl IntoIterator<Item = impl AsRef<OsStr>>,
) -> Result<String> {
    String::from_utf8(read(path, args)?).map_err(|_| Error::invalid("Git 返回了无效的文本。"))
}

pub(super) fn verify_ref(path: &Path, reference: &str) -> Result<String> {
    if reference.is_empty()
        || reference.starts_with('-')
        || reference.chars().count() > 512
        || reference.contains(['\0', '\r', '\n'])
    {
        return Err(Error::invalid("必须明确提供有效的起始分支或版本。"));
    }
    let expr = format!("{reference}^{{commit}}");
    Ok(object_id(read(
        path,
        ["rev-parse", "--verify", "--end-of-options", &expr],
    )?)?)
}

pub(super) fn branch(path: &Path) -> Result<String> {
    let result = command(path).read_optional(["symbolic-ref", "--short", "-q", "HEAD"])?;
    Ok(
        String::from_utf8_lossy(result.as_deref().unwrap_or_default())
            .trim()
            .to_owned(),
    )
}

pub(super) fn local_path(raw: &[u8]) -> Result<PathBuf> {
    if raw.is_empty() || raw.contains(&0) {
        return Err(Error::invalid("Git 返回了无效的路径。"));
    }
    #[cfg(unix)]
    let path = {
        use std::os::unix::ffi::OsStrExt;
        OsStr::from_bytes(raw)
    };
    #[cfg(not(unix))]
    let path =
        OsStr::new(std::str::from_utf8(raw).map_err(|_| Error::invalid("Git 返回了无效的路径。"))?);
    Ok(PathBuf::from(path))
}

pub(super) fn line(mut raw: &[u8]) -> &[u8] {
    if let Some(value) = raw.strip_suffix(b"\n") {
        raw = value;
        #[cfg(windows)]
        if let Some(value) = raw.strip_suffix(b"\r") {
            raw = value;
        }
    }
    raw
}

pub(super) fn canonical_git_path(path: &Path, arg: &str) -> Result<PathBuf> {
    let raw = read(path, ["rev-parse", "--path-format=absolute", arg])?;
    let target = local_path(line(&raw))?;
    if !target.is_absolute() {
        return Err(Error::invalid("Git 返回了非绝对路径。"));
    }
    Ok(canonicalize(&target)?)
}

pub(super) fn require_checkout(path: &Path) -> Result<()> {
    if canonicalize(path)? != path || canonical_git_path(path, "--show-toplevel")? != path {
        return Err(Error::invalid("当前工作区必须是 Git 仓库根目录。"));
    }
    let marker = fs::symlink_metadata(path.join(".git"))?;
    if !marker.is_file() && !marker.is_dir() {
        return Err(Error::invalid("Git 工作目录不能被重定向。"));
    }
    Ok(())
}

/// Git can run checkout and clean filters from local repository config. Disable
/// these on the trusted process interface before working-tree reads or writes.
pub(super) fn safe_filters(path: &Path) -> Result<Vec<String>> {
    let result = command(path).read_optional([
        "config",
        "--includes",
        "--null",
        "--name-only",
        "--get-regexp",
        "^filter\\.",
    ])?;
    let mut settings = Vec::new();
    for raw in result
        .as_deref()
        .unwrap_or_default()
        .split(|byte| *byte == 0)
        .filter(|key| !key.is_empty())
    {
        let key = std::str::from_utf8(raw)
            .map_err(|_| Error::invalid("无法安全关闭自定义 Git 过滤器。"))?;
        let Some((_, property)) = key.rsplit_once('.') else {
            continue;
        };
        if !matches!(property, "process" | "clean" | "smudge" | "required") {
            continue;
        }
        if key.contains(['=', '\r', '\n']) {
            return Err(Error::invalid("无法安全关闭自定义 Git 过滤器。"));
        }
        let value = if property == "required" { "false" } else { "" };
        settings.push(format!("{key}={value}"));
    }
    Ok(settings)
}

pub(super) fn run_edit(
    path: &Path,
    filters: &[String],
    args: impl IntoIterator<Item = impl AsRef<OsStr>>,
) -> Result<Vec<u8>> {
    let command = command(path).with_config(filters);
    Ok(command.read(args)?)
}

pub(super) fn argument(prefix: &str, value: &Path) -> OsString {
    let mut arg = OsString::from(prefix);
    arg.push(value);
    arg
}
