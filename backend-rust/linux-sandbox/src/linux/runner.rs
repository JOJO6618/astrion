//! Verify the transient service's real isolation before constructing mounts.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs, io,
    os::{
        fd::{AsRawFd, OwnedFd},
        unix::{fs::OpenOptionsExt, process::CommandExt},
    },
    path::Path,
    process::Command,
    time::Duration,
};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::protocol::{
    MAX_SOURCE_FDS, Network, RUNTIME_ROOT, RunRequest, SYSTEM_DIRECTORIES, denied, invalid,
    mount_path,
};

use super::{
    config::{Config, output_timeout, privileged_command},
    principal::Identity,
    sources::Mount,
    transport::{PacketSocket, result, stat},
};

pub const JOB_PREFIX: &str = "astrion-sandbox-job-";

#[derive(Serialize)]
pub struct JobRef<'a> {
    pub request: &'a RunRequest,
    pub principal: &'a Identity,
    pub mounts: &'a [Mount],
    pub lease: &'a Path,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Job {
    request: RunRequest,
    principal: Identity,
    mounts: Vec<Mount>,
    lease: String,
}

fn verify_filters(config: &Config, network: Network) -> io::Result<()> {
    let label = fs::read_to_string("/proc/self/attr/current")?;
    if label.trim() != format!("{} (enforce)", network.profile()) {
        return Err(denied("Required enforcing AppArmor profile is absent"));
    }
    if network == Network::Full {
        return Ok(());
    }
    let cgroup = fs::read_to_string("/proc/self/cgroup")?;
    let path = cgroup
        .lines()
        .find_map(|line| line.strip_prefix("0::"))
        .ok_or_else(|| denied("Required cgroup v2 membership is absent"))?;
    if !Path::new(path)
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with(JOB_PREFIX))
    {
        return Err(denied("Sandbox task is outside its dedicated cgroup"));
    }
    let mut command = privileged_command(&config.bpftool);
    command.args(["cgroup", "show", &format!("/sys/fs/cgroup{path}")]);
    let result = output_timeout(&mut command, Duration::from_secs(5))?;
    let attachments = String::from_utf8_lossy(&result.stdout);
    if !result.status.success()
        || !["cgroup_inet_ingress", "cgroup_inet_egress"]
            .iter()
            .all(|name| {
                attachments
                    .lines()
                    .any(|line| line.split_whitespace().any(|word| word == *name))
            })
    {
        return Err(denied("Required cgroup IP filtering is absent"));
    }
    Ok(())
}

fn command(config: &Config, job: &Job, fds: &[OwnedFd], policy: &fs::File) -> io::Result<Command> {
    let mut command = privileged_command(&config.bwrap);
    command.args([
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--hostname",
        "astrion-sandbox",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--perms",
        "1777",
        "--tmpfs",
        "/tmp",
        "--perms",
        "1777",
        "--tmpfs",
        "/var/tmp",
    ]);
    let mut mounts: Vec<_> = job.mounts.iter().zip(fds).collect();
    mounts.sort_by_key(|(mount, _)| Path::new(&mount.target).components().count());
    let paths: BTreeSet<_> = mounts
        .iter()
        .map(|(mount, _)| Path::new(&mount.target))
        .collect();
    for (mount, fd) in mounts {
        mount_path(&mount.target)?;
        let option = if mount.writable {
            "--bind-fd"
        } else {
            "--ro-bind-fd"
        };
        command.args([option, &fd.as_raw_fd().to_string(), &mount.target]);
    }
    for &system_dir in SYSTEM_DIRECTORIES {
        let path = Path::new(system_dir);
        if !paths.contains(path) && path.is_symlink() && fs::canonicalize(path)?.starts_with("/usr")
        {
            command.arg("--symlink").arg(fs::read_link(path)?).arg(path);
        }
    }
    let mut parents = BTreeSet::new();
    for path in &paths {
        for parent in path.ancestors().skip(1) {
            if !["/", "/tmp", "/var/tmp"]
                .iter()
                .any(|virtual_root| parent == Path::new(virtual_root))
                && !paths.iter().any(|mount| parent.starts_with(mount))
            {
                parents.insert(parent);
            }
        }
    }
    let mut parents: Vec<_> = parents.into_iter().collect();
    parents.sort_by_key(|parent| parent.components().count());
    for parent in parents {
        command.args(["--chmod", "0755"]).arg(parent);
    }
    let home = format!("{RUNTIME_ROOT}/home");
    let executable = format!("{RUNTIME_ROOT}/uid-exec");
    command
        .args([
            "--perms",
            "0755",
            "--tmpfs",
            RUNTIME_ROOT,
            "--perms",
            "0700",
            "--tmpfs",
            &home,
            "--ro-bind",
        ])
        .arg(config.program())
        .arg(&executable)
        .args([
            "--ro-bind",
            &job.lease,
            &format!("{RUNTIME_ROOT}/lease"),
            "--remount-ro",
            RUNTIME_ROOT,
            "--remount-ro",
            "/",
            "--cap-drop",
            "ALL",
        ]);
    for cap in ["CAP_SETUID", "CAP_SETGID", "CAP_SETPCAP", "CAP_CHOWN"] {
        command.args(["--cap-add", cap]);
    }
    let mut environment = BTreeMap::from([
        ("PATH", "/usr/local/bin:/usr/bin:/bin"),
        ("LANG", "C.UTF-8"),
    ]);
    environment.extend(
        job.request
            .env
            .iter()
            .map(|(key, value)| (key.as_str(), value.as_str())),
    );
    environment.extend([
        ("HOME", home.as_str()),
        ("TMPDIR", "/tmp"),
        ("GIT_CONFIG_GLOBAL", "/dev/null"),
    ]);
    command.arg("--clearenv");
    for (key, value) in environment {
        command.args(["--setenv", key, value]);
    }

    let cwd_fd = fds
        .last()
        .ok_or_else(|| invalid("Missing pinned working directory"))?;
    let cwd_info = stat(cwd_fd.as_raw_fd())?;
    if cwd_info.st_mode & libc::S_IFMT != libc::S_IFDIR {
        return Err(denied("Invalid fixed working directory"));
    }
    let groups = job
        .principal
        .groups
        .iter()
        .map(u32::to_string)
        .collect::<Vec<_>>()
        .join(",");
    command
        .args([
            "--seccomp",
            &policy.as_raw_fd().to_string(),
            "--",
            &executable,
            "uid-exec",
            &job.principal.uid.to_string(),
            &job.principal.gid.to_string(),
            &job.request.umask.to_string(),
            &groups,
            &job.request.cwd,
            &cwd_info.st_dev.to_string(),
            &cwd_info.st_ino.to_string(),
        ])
        .args(&job.request.argv);
    Ok(command)
}

pub fn run(config_path: &str, rendezvous: &str) -> io::Result<i32> {
    let config = Config::load(config_path)?;
    let rendezvous = Path::new(rendezvous);
    if rendezvous.parent().and_then(Path::parent) != Some(Path::new(&config.runtime))
        || rendezvous.file_name().and_then(|name| name.to_str()) != Some("control")
    {
        return Err(denied("Invalid sandbox rendezvous path"));
    }
    let connection = PacketSocket::connect(rendezvous, Duration::from_secs(12))?;
    if connection.credentials()?.uid != 0 {
        return Err(denied("Untrusted sandbox broker worker"));
    }
    connection.send(&json!({"runner": true}), &[])?;
    let (job, fds) = connection.receive::<Job>(MAX_SOURCE_FDS)?;
    job.request.validate()?;
    if job.mounts.len() + 1 != fds.len()
        || Path::new(&job.lease) != rendezvous.with_file_name("lease")
    {
        return Err(denied("Incomplete pinned mount transfer or invalid lease"));
    }
    verify_filters(&config, job.request.network)?;
    let name = if job.request.network == Network::None {
        "policy-none.bpf"
    } else {
        "policy.bpf"
    };
    let policy = fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(Path::new(&config.install_root).join(name))?;
    let mut command = command(&config, &job, &fds, &policy)?;
    connection.send(&json!({"ready": true}), &[])?;
    if connection.receive::<Value>(0)?.0 != json!({"execute": true}) {
        return Err(denied("Sandbox execution was cancelled before start"));
    }
    drop(connection);
    for fd in fds
        .iter()
        .map(AsRawFd::as_raw_fd)
        .chain([policy.as_raw_fd()])
    {
        result(unsafe { libc::fcntl(fd, libc::F_SETFD, 0) })?;
    }
    Err(command.exec())
}
