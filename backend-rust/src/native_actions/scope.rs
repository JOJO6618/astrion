use std::{ffi::OsStr, fs, io, path::{Component, Path, PathBuf}, time::Duration};

pub(super) use crate::project_git::canonicalize;
use crate::{project_git::runner::Git, runtime::{Runtime, RuntimeError}, workspace_catalog::RegisteredWorkspace, worktree::Worktree};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum FileError {
    Invalid,
    Missing,
}

pub(super) struct Scope {
    workspace: PathBuf,
    execution: Result<PathBuf, FileError>,
}

impl Scope {
    pub(super) fn for_request(runtime: &Runtime, workspace: &RegisteredWorkspace, conversation: &str) -> Result<Self, RuntimeError> {
        let root = registered_root(workspace).map_err(|_| RuntimeError::new(404, "Workspace not found"))?;
        if conversation.is_empty() {
            return Ok(Self { execution: Ok(root.clone()), workspace: root });
        }
        let id = if conversation.starts_with("conv_") { conversation.to_owned() } else { format!("conv_{conversation}") };
        let not_found = || RuntimeError::new(404, "Conversation not found");
        if runtime.bound_workspace(&workspace.id)?.as_deref() != Some(root.as_path()) {
            return Err(not_found());
        }
        let document = runtime.session_history(&workspace.id, &id).map_err(|error| {
            if matches!(error.status, 400 | 404) { not_found() } else { error }
        })?;
        let metadata = &document["conversation"]["metadata"];
        if let Some(saved) = metadata["project_path"].as_str().filter(|value| !value.is_empty())
            && Path::new(saved) != root
            && canonicalize(Path::new(saved)).is_ok_and(|saved| saved != root)
        {
            return Err(not_found());
        }
        let execution = if metadata["git_worktree_enabled"] == true && !metadata["git_worktree"].is_null() {
            Worktree::new(&root).and_then(|tree| tree.execution_root(metadata)).map_err(|_| FileError::Invalid)
        } else {
            Ok(root.clone())
        };
        Ok(Self { workspace: root, execution })
    }

    pub(super) fn project_directory(&self) -> PathBuf {
        let git = git_root(&self.workspace);
        if git.starts_with(&self.workspace) { git } else { self.workspace.clone() }
    }

    pub(super) fn git_file(&self, path: &str) -> Result<PathBuf, FileError> {
        let raw = Path::new(path);
        validate_raw(path)?;
        if !raw.is_absolute() && !is_relative(raw) {
            return Err(FileError::Invalid);
        }
        locate_file(&git_root(&self.workspace).join(raw), &[&self.workspace])
    }

    pub(super) fn workspace_file(&self, path: &str, restore_root: bool) -> Result<PathBuf, FileError> {
        validate_raw(path)?;
        let execution = self.execution.as_ref().map_err(|error| *error)?;
        let allowed = [self.workspace.as_path(), execution.as_path()];
        if path == "/workspace" {
            return Err(FileError::Missing);
        }
        if let Some(relative) = path.strip_prefix("/workspace/") {
            if !is_relative(Path::new(relative)) {
                return Err(FileError::Invalid);
            }
            return locate_file(&self.workspace.join(relative), &allowed);
        }
        let raw = Path::new(path);
        if raw.is_absolute() {
            return locate_file(raw, &allowed);
        }
        if !is_relative(raw) {
            return Err(FileError::Invalid);
        }
        let result = locate_file(&execution.join(raw), &allowed);
        #[cfg(unix)]
        if restore_root && result == Err(FileError::Missing) {
            let absolute = Path::new("/").join(raw);
            if let Ok(resolved) = canonicalize(&absolute)
                && resolved.is_file()
            {
                return locate_file(&resolved, &allowed);
            }
        }
        #[cfg(not(unix))]
        let _ = restore_root;
        result
    }
}

pub(super) fn registered_root(workspace: &RegisteredWorkspace) -> io::Result<PathBuf> {
    let root = canonicalize(&workspace.path)?;
    if root != workspace.path || !root.is_dir() {
        return Err(io::Error::other("Workspace directory has changed"));
    }
    Ok(root)
}

fn git_root(root: &Path) -> PathBuf {
    Git::new(root).with_timeout(Duration::from_secs(2)).read(["rev-parse", "--show-toplevel"])
        .ok()
        .and_then(|bytes| {
            let end = bytes.iter().rposition(|byte| !matches!(byte, b'\r' | b'\n'))? + 1;
            #[cfg(unix)]
            let git_path = {
                use std::os::unix::ffi::OsStrExt;
                OsStr::from_bytes(&bytes[..end])
            };
            #[cfg(not(unix))]
            let git_path = OsStr::new(std::str::from_utf8(&bytes[..end]).ok()?);
            let path = Path::new(git_path);
            path.is_absolute().then(|| canonicalize(path).ok()).flatten()
        })
        .filter(|directory| directory.is_dir() && root.starts_with(directory))
        .unwrap_or_else(|| root.to_owned())
}

fn validate_raw(path: &str) -> Result<(), FileError> {
    if path.is_empty() || path.contains('\0') || (cfg!(windows) && path.strip_prefix("/workspace/").unwrap_or(path).starts_with("//./")) {
        return Err(FileError::Invalid);
    }
    #[cfg(windows)]
    if matches!(Path::new(path).components().next(), Some(Component::Prefix(prefix)) if matches!(prefix.kind(), std::path::Prefix::DeviceNS(_))) {
        return Err(FileError::Invalid);
    }
    Ok(())
}

fn is_relative(path: &Path) -> bool {
    !matches!(path.components().next(), Some(Component::RootDir | Component::Prefix(_)))
}

fn locate_file(path: &Path, allowed: &[&Path]) -> Result<PathBuf, FileError> {
    let contains = |path: &Path| allowed.iter().any(|root| path.starts_with(root));
    match canonicalize(path) {
        Ok(target) if contains(&target) => {
            let metadata = fs::metadata(&target).map_err(|_| FileError::Missing)?;
            if !metadata.is_file() { return Err(FileError::Missing); }
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if metadata.nlink() != 1 { return Err(FileError::Invalid); }
            }
            Ok(target)
        }
        Ok(_) => Err(FileError::Invalid),
        Err(error) if error.kind() == io::ErrorKind::NotFound || error.kind() == io::ErrorKind::NotADirectory => {
            // Canonicalize the deepest existing ancestor so a missing child of an
            // escaping link is classified as invalid even though it does not exist.
            let mut cursor = path;
            let mut rest = Vec::new();
            loop {
                if let Ok(mut ancestor) = canonicalize(cursor) {
                    for component in rest.into_iter().rev() {
                        if component == OsStr::new("..") { ancestor.pop(); }
                        else if component != OsStr::new(".") { ancestor.push(component); }
                    }
                    return Err(if contains(&ancestor) { FileError::Missing } else { FileError::Invalid });
                }
                if fs::symlink_metadata(cursor).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
                    return Err(FileError::Invalid);
                }
                let mut parts = cursor.components();
                let Some(part) = parts.next_back() else { return Err(FileError::Invalid); };
                rest.push(part.as_os_str());
                cursor = parts.as_path();
            }
        }
        Err(_) => Err(FileError::Invalid),
    }
}

#[cfg(test)]
impl Scope {
    pub(super) fn local(root: PathBuf) -> Self {
        Self { execution: Ok(root.clone()), workspace: root }
    }
}
