//! Pin allowed mount sources as the real caller before running bwrap.

use std::{
    collections::{HashMap, HashSet},
    ffi::CString,
    fs, io,
    os::fd::{AsRawFd, OwnedFd, RawFd},
    path::Path,
    time::Duration,
};

use serde::{Deserialize, Serialize};

use crate::protocol::{
    MAX_SOURCE_FDS, RunRequest, SYSTEM_DIRECTORIES, SYSTEM_FILES, denied, invalid,
};

use super::{
    principal::Identity,
    transport::{PacketSocket, owned, result, stat},
};

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Mount {
    pub target: String,
    pub writable: bool,
}

struct Source {
    path: String,
    mount: Mount,
}

pub struct Pinned {
    pub mounts: Vec<Mount>,
    pub descriptors: Vec<OwnedFd>,
}

pub fn open_path(path: &Path, directory: bool) -> io::Result<OwnedFd> {
    let mut descriptor = owned(unsafe {
        libc::open(
            c"/".as_ptr(),
            libc::O_PATH | libc::O_DIRECTORY | libc::O_CLOEXEC,
        )
    })?;
    let relative = path
        .strip_prefix("/")
        .map_err(|_| invalid("An absolute mount source is required"))?;
    descriptor = walk(descriptor, relative, directory)?;
    Ok(descriptor)
}

fn walk(mut descriptor: OwnedFd, path: &Path, directory: bool) -> io::Result<OwnedFd> {
    use std::os::unix::ffi::OsStrExt;
    let mut components = path.components().peekable();
    while let Some(component) = components.next() {
        let std::path::Component::Normal(name) = component else {
            return Err(invalid("Invalid sandbox path component"));
        };
        let name = CString::new(name.as_bytes()).map_err(|_| invalid("NUL in sandbox path"))?;
        let mut flags = libc::O_PATH | libc::O_NOFOLLOW | libc::O_CLOEXEC;
        if directory || components.peek().is_some() {
            flags |= libc::O_DIRECTORY;
        }
        descriptor = owned(unsafe { libc::openat(descriptor.as_raw_fd(), name.as_ptr(), flags) })?;
    }
    let mode = stat(descriptor.as_raw_fd())?.st_mode & libc::S_IFMT;
    if !matches!(mode, libc::S_IFDIR | libc::S_IFREG | libc::S_IFSOCK) {
        return Err(denied(
            "Symlink, device, FIFO, and special sandbox grants are refused",
        ));
    }
    Ok(descriptor)
}

fn specifications(request: &RunRequest) -> io::Result<Vec<Source>> {
    let mut sources = Vec::new();
    let mut targets = HashMap::new();
    let mut insert = |path: String, target: &str, writable: bool, replace: bool| {
        let item = Source {
            path,
            mount: Mount {
                target: target.to_owned(),
                writable,
            },
        };
        if let Some(&index) = targets.get(target) {
            if replace {
                sources[index] = item;
            }
        } else {
            targets.insert(target.to_owned(), sources.len());
            sources.push(item);
        }
    };
    for &name in SYSTEM_DIRECTORIES.iter().chain(SYSTEM_FILES) {
        let path = Path::new(name);
        if path.exists() && !path.is_symlink() {
            insert(name.to_owned(), name, false, false);
        } else if path.exists() && SYSTEM_FILES.contains(&name) {
            let canonical = fs::canonicalize(path)?;
            let canonical = canonical
                .to_str()
                .ok_or_else(|| invalid("Invalid system mount path"))?;
            insert(canonical.to_owned(), name, false, false);
        }
    }
    let writes: HashSet<_> = request.writes.iter().collect();
    for path in request.reads.iter().chain(&request.writes) {
        insert(path.clone(), path, writes.contains(path), false);
    }
    let write_root = request
        .workspace_write_root
        .as_deref()
        .unwrap_or(&request.workspace);
    insert(
        request.workspace.clone(),
        &request.workspace,
        !request.readonly && write_root == request.workspace,
        true,
    );
    if !request.readonly && write_root != request.workspace {
        insert(write_root.to_owned(), write_root, true, true);
    }
    Ok(sources)
}

fn open_sources(
    identity: &Identity,
    request: &RunRequest,
    sources: &[Source],
    supervisor: i32,
) -> io::Result<Vec<OwnedFd>> {
    identity.drop_privilege()?;
    // A uid/gid change clears PDEATHSIG. Rebind it before accessing the filesystem.
    super::bind_parent_lifetime(supervisor, libc::SIGKILL)?;
    let workspace = open_path(Path::new(&request.workspace), true)?;
    let from_workspace = |path: &Path, directory: bool| -> io::Result<OwnedFd> {
        let relative = path
            .strip_prefix(&request.workspace)
            .map_err(|_| denied("Path is outside the pinned workspace"))?;
        let base = owned(unsafe { libc::fcntl(workspace.as_raw_fd(), libc::F_DUPFD_CLOEXEC, 3) })?;
        walk(base, relative, directory)
    };
    let cwd = from_workspace(Path::new(&request.cwd), true)?;
    let mut descriptors = Vec::new();
    for source in sources {
        let path = Path::new(&source.path);
        let needs_directory = source.mount.target == request.workspace
            || request.workspace_write_root.as_deref() == Some(&source.mount.target);
        let fd = if path.starts_with(&request.workspace) {
            from_workspace(path, needs_directory)?
        } else {
            open_path(path, needs_directory)?
        };
        let information = stat(fd.as_raw_fd())?;
        if source.mount.writable
            && information.st_mode & libc::S_IFMT == libc::S_IFREG
            && information.st_nlink != 1
        {
            return Err(denied("Multi-link writable file grants are refused"));
        }
        descriptors.push(fd);
    }
    descriptors.push(cwd);
    Ok(descriptors)
}

pub fn pin_sources(identity: &Identity, request: &RunRequest) -> io::Result<Pinned> {
    let sources = specifications(request)?;
    let (parent, child) = PacketSocket::pair()?;
    parent.timeout(Duration::from_secs(5))?;
    child.timeout(Duration::from_secs(4))?;
    let supervisor = unsafe { libc::getpid() };
    let pid = result(unsafe { libc::fork() })?;
    if pid == 0 {
        drop(parent);
        unsafe {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0) < 0
                || libc::getppid() != supervisor
            {
                libc::_exit(125);
            }
        }
        match open_sources(identity, request, &sources, supervisor) {
            Ok(fds) => {
                let fds: Vec<_> = fds.iter().map(AsRawFd::as_raw_fd).collect::<Vec<RawFd>>();
                let _ = child.send(&serde_json::json!({"ok": true}), &fds);
            }
            Err(error) => {
                let _ = child.send(
                    &serde_json::json!({"ok": false, "error": error.to_string()}),
                    &[],
                );
            }
        }
        unsafe { libc::_exit(0) };
    }
    drop(child);
    let received = parent.receive::<serde_json::Value>(MAX_SOURCE_FDS);
    drop(parent);
    super::reap_child(pid);
    let (reply, descriptors) = received?;
    if reply["ok"] != true {
        return Err(denied(
            reply["error"]
                .as_str()
                .unwrap_or("Sandbox source verification failed"),
        ));
    }
    if descriptors.len() != sources.len() + 1 {
        return Err(denied("Incomplete verified sandbox sources"));
    }
    Ok(Pinned {
        mounts: sources.into_iter().map(|source| source.mount).collect(),
        descriptors,
    })
}
