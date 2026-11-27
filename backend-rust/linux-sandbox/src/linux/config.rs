//! Only immutable administrator-owned paths may supply privileged inputs.

use std::{
    fs::{self, File},
    io::{self, Read, Seek, SeekFrom},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{fs::MetadataExt, process::CommandExt},
    },
    path::{Component, Path},
    process::{Command, ExitStatus, Output, Stdio},
    thread,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};

pub use crate::protocol::{DEFAULT_SOCKET, INSTALL_ROOT};
use crate::protocol::{PROTOCOL_VERSION, denied, invalid};

pub const CONFIG_PATH: &str = "/etc/astrion-sandbox/helper.json";
pub const EXECUTABLE_NAME: &str = "astrion-linux-sandbox";
pub const SERVICE_NAME: &str = "astrion-sandbox.service";
pub const RUNTIME_DIR: &str = "/run/astrion-sandbox/jobs";
const SYSTEM_PATH: &str = "/usr/sbin:/usr/bin:/sbin:/bin";

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub protocol: u32,
    pub allowed_uids: Vec<u32>,
    pub socket: String,
    pub runtime: String,
    pub install_root: String,
    pub bwrap: String,
    pub systemd_run: String,
    pub systemctl: String,
    pub bpftool: String,
    pub max_jobs: usize,
    pub runtime_max_sec: u64,
}

impl Config {
    pub fn load(path: &str) -> io::Result<Self> {
        if unsafe { libc::getuid() != 0 || libc::geteuid() != 0 } {
            return Err(denied(
                "The sandbox broker must be launched by the administrator service",
            ));
        }
        trusted_path(Path::new(path), false)?;
        let info = fs::metadata(path)?;
        if info.mode() & 0o7777 != 0o600 || info.len() > 65_536 {
            return Err(denied("Helper configuration must be root-owned mode 0600"));
        }
        let config: Self = serde_json::from_reader(File::open(path)?.take(65_537))?;
        if config.protocol != PROTOCOL_VERSION
            || config.allowed_uids.is_empty()
            || config
                .allowed_uids
                .iter()
                .any(|uid| *uid == 0 || *uid == u32::MAX)
            || !(1..=64).contains(&config.max_jobs)
            || !(10..=86_400).contains(&config.runtime_max_sec)
        {
            return Err(invalid("Invalid sandbox helper configuration"));
        }
        trusted_path(Path::new(&config.runtime), true)?;
        trusted_path(Path::new(&config.install_root), true)?;
        let socket = Path::new(&config.socket);
        if !normalized(socket) || socket.parent().is_none() {
            return Err(invalid("Invalid broker socket path"));
        }
        trusted_path(socket.parent().unwrap(), true)?;
        for bin in [
            &config.bwrap,
            &config.systemd_run,
            &config.systemctl,
            &config.bpftool,
        ] {
            trusted_path(Path::new(bin), false)?;
        }
        for name in [EXECUTABLE_NAME, "policy.bpf", "policy-none.bpf"] {
            trusted_path(&Path::new(&config.install_root).join(name), false)?;
        }
        let executable_mode = fs::metadata(config.program())?.mode();
        if executable_mode & 0o6000 != 0 || executable_mode & 0o100 == 0 {
            return Err(denied("Invalid sandbox helper executable mode"));
        }
        Ok(config)
    }

    pub fn program(&self) -> std::path::PathBuf {
        Path::new(&self.install_root).join(EXECUTABLE_NAME)
    }
}

pub fn supported_platform() -> bool {
    fs::read_to_string("/etc/os-release").is_ok_and(|release| {
        let value = |key: &str| {
            release.lines().find_map(|line| {
                let (name, value) = line.split_once('=')?;
                (name == key).then(|| value.trim_matches('"'))
            })
        };
        value("ID") == Some("ubuntu") && value("VERSION_ID") == Some("24.04")
    })
}

pub fn ordinary_user() -> bool {
    unsafe { libc::getuid() != 0 && libc::getuid() == libc::geteuid() }
}

fn normalized(path: &Path) -> bool {
    let rebuilt: std::path::PathBuf = path.components().collect();
    path.is_absolute()
        && rebuilt.as_os_str() == path.as_os_str()
        && !path
            .components()
            .any(|component| matches!(component, Component::ParentDir))
}

pub fn trusted_path(path: &Path, directory: bool) -> io::Result<()> {
    if !normalized(path) {
        return Err(denied(
            "Privileged installation paths must be absolute and normalized",
        ));
    }
    for ancestor in path.ancestors() {
        let info = fs::symlink_metadata(ancestor)?;
        let is_directory = directory || ancestor != path;
        let sticky_ancestor = ancestor != path && info.is_dir() && info.mode() & libc::S_ISVTX != 0;
        if info.uid() != 0
            || (info.mode() & 0o022 != 0 && !sticky_ancestor)
            || (is_directory && !info.is_dir())
            || (!is_directory && !info.is_file())
        {
            return Err(denied(format!(
                "Untrusted privileged installation path: {}",
                ancestor.display()
            )));
        }
    }
    Ok(())
}

pub fn privileged_command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut command = Command::new(program);
    command
        .env_clear()
        .current_dir("/")
        .env("PATH", SYSTEM_PATH)
        .env("LANG", "C.UTF-8");
    command
}

pub fn wait_timeout(command: &mut Command, timeout: Duration) -> io::Result<ExitStatus> {
    let mut child = command.process_group(0).spawn()?;
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            // An unreaped child cannot lose its PID to another process.
            unsafe { libc::kill(-(child.id() as i32), libc::SIGKILL) };
            super::reap_child(child.id() as i32);
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "Sandbox system operation timed out",
            ));
        }
        thread::sleep(Duration::from_millis(20));
    }
}

pub fn output_timeout(command: &mut Command, timeout: Duration) -> io::Result<Output> {
    fn capture_file() -> io::Result<File> {
        let fd = unsafe {
            libc::memfd_create(
                c"astrion-sandbox-command-output".as_ptr(),
                libc::MFD_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(unsafe { File::from_raw_fd(fd) })
    }
    fn read(mut file: File) -> io::Result<Vec<u8>> {
        file.seek(SeekFrom::Start(0))?;
        let mut data = Vec::new();
        file.take(1_048_577).read_to_end(&mut data)?;
        if data.len() > 1_048_576 {
            return Err(invalid("Sandbox system command output exceeded its limit"));
        }
        Ok(data)
    }
    let stdout = capture_file()?;
    let stderr = capture_file()?;
    // Reopen each memfd for the child so seeking the reader never changes the
    // writer's position. All control descriptors remain close-on-exec.
    let reopen = |file: &File| {
        fs::OpenOptions::new()
            .write(true)
            .open(format!("/proc/self/fd/{}", file.as_raw_fd()))
    };
    command
        .stdin(Stdio::null())
        .stdout(reopen(&stdout)?)
        .stderr(reopen(&stderr)?);
    let status = wait_timeout(command, timeout)?;
    Ok(Output {
        status,
        stdout: read(stdout)?,
        stderr: read(stderr)?,
    })
}
