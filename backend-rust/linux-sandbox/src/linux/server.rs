//! The authenticated client owns one worker and one cancellable transient unit.

use std::{
    collections::HashSet,
    fs, io,
    os::{
        fd::{AsRawFd, OwnedFd},
        unix::{
            fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt},
            process::{CommandExt, ExitStatusExt},
        },
    },
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use serde_json::{Value, json};

use crate::protocol::{Network, PROTOCOL_VERSION, Request, RunRequest, denied, invalid};

use super::{
    bind_parent_lifetime,
    config::{Config, DEFAULT_SOCKET, SERVICE_NAME, privileged_command, wait_timeout},
    install_signal_handlers,
    principal::Principal,
    random_token, reap_child,
    runner::{JOB_PREFIX, JobRef},
    sources::pin_sources,
    stopping,
    transport::{PacketSocket, active, event, poll, result, validate_stdio},
};

fn ready() -> io::Result<()> {
    let profiles = fs::read_to_string("/sys/kernel/security/apparmor/profiles")?;
    if !Network::ALL.iter().all(|network| {
        let label = format!("{} (enforce)", network.profile());
        profiles.lines().any(|actual| actual == label)
    }) {
        return Err(denied("An enforcing sandbox AppArmor profile is missing"));
    }
    if !Path::new("/sys/fs/cgroup/cgroup.controllers").is_file() {
        return Err(denied("cgroup v2 is required"));
    }
    Ok(())
}

pub fn serve(config_path: &str) -> io::Result<i32> {
    let config = Config::load(config_path)?;
    ready()?;
    let path = Path::new(&config.socket);
    let lock = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path.with_extension("lock"))?;
    result(unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) })?;
    match fs::symlink_metadata(path) {
        Ok(info) if info.file_type().is_socket() && info.uid() == 0 => {
            match PacketSocket::connect(path, Duration::from_secs(1)) {
                Err(error) if error.kind() == io::ErrorKind::ConnectionRefused => {
                    fs::remove_file(path)?
                }
                _ => return Err(denied("A sandbox broker is already listening")),
            }
        }
        Ok(_) => return Err(denied("Control socket path contains an unexpected file")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => (),
        Err(error) => return Err(error),
    }
    let listener = PacketSocket::new()?;
    listener.bind(path)?;
    let mut server = Server {
        path,
        children: HashSet::new(),
    };
    fs::set_permissions(path, fs::Permissions::from_mode(0o666))?;
    listener.listen(config.max_jobs)?;
    install_signal_handlers()?;
    let parent_pid = unsafe { libc::getpid() };
    while !stopping() {
        server
            .children
            .retain(|&pid| unsafe { libc::waitpid(pid, std::ptr::null_mut(), libc::WNOHANG) } == 0);
        let mut wait = [event(listener.as_raw_fd())];
        poll(&mut wait, 250)?;
        if wait[0].revents == 0 || stopping() {
            continue;
        }
        let connection = listener.accept()?;
        connection.timeout(Duration::from_secs(5))?;
        if server.children.len() >= config.max_jobs {
            let _ = connection.send(
                &json!({"error": "Sandbox helper is at its concurrency limit", "exitcode": 125}),
                &[],
            );
            continue;
        }
        let pid = result(unsafe { libc::fork() })?;
        if pid == 0 {
            drop(listener);
            drop(lock);
            let outcome = bind_parent_lifetime(parent_pid, libc::SIGTERM)
                .and_then(|()| worker(&connection, &config, config_path));
            if let Err(error) = outcome {
                let detail: String = error.to_string().chars().take(1000).collect();
                eprintln!("Linux sandbox worker failed: {:?}", detail);
                let _ = connection.send(&json!({"error": detail, "exitcode": 125}), &[]);
            }
            unsafe { libc::_exit(0) };
        }
        server.children.insert(pid);
    }
    drop(listener);
    Ok(0)
}

struct Server<'a> {
    path: &'a Path,
    children: HashSet<i32>,
}

impl Drop for Server<'_> {
    fn drop(&mut self) {
        let _ = fs::remove_file(self.path);
        for &pid in &self.children {
            unsafe { libc::kill(pid, libc::SIGTERM) };
        }
        let deadline = Instant::now() + Duration::from_secs(12);
        while !self.children.is_empty() && Instant::now() < deadline {
            self.children.retain(
                |&pid| unsafe { libc::waitpid(pid, std::ptr::null_mut(), libc::WNOHANG) } == 0,
            );
            thread::sleep(Duration::from_millis(50));
        }
        for &pid in &self.children {
            reap_child(pid);
        }
    }
}

fn worker(connection: &PacketSocket, config: &Config, config_path: &str) -> io::Result<()> {
    let principal = Principal::authenticate(connection, &config.allowed_uids)?;
    let (request, mut fds) = connection.receive::<Request>(3)?;
    ready()?;
    match request {
        Request::Status {} if fds.is_empty() => connection.send(
            &json!({"protocol": PROTOCOL_VERSION, "ready": true, "network": Network::ALL}),
            &[],
        ),
        Request::Status {} => Err(invalid("Status takes no descriptors")),
        Request::Run(request) => {
            request.validate()?;
            validate_stdio(&fds)?;
            run_job(
                config,
                config_path,
                connection,
                &principal,
                &request,
                &mut fds,
            )
        }
    }
}

fn unit_command(config: &Config, config_path: &str, job: &Job<'_>, network: Network) -> Command {
    let mut command = privileged_command(&config.systemd_run);
    command.args([
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--service-type=exec",
        &format!("--unit={}", job.name),
        "--property=KillMode=control-group",
        "--property=TimeoutStopSec=2",
        &format!("--property=RuntimeMaxSec={}", config.runtime_max_sec),
        "--property=TasksMax=512",
        "--property=MemoryMax=4G",
        "--property=UMask=0077",
        &format!("--property=AppArmorProfile={}", network.profile()),
    ]);
    if config.socket == DEFAULT_SOCKET {
        command.args([
            "--property=BindsTo=astrion-sandbox.service",
            "--property=After=astrion-sandbox.service",
        ]);
    }
    match network {
        Network::Full => (),
        Network::None => {
            command.arg("--property=IPAddressDeny=any");
        }
        Network::Restricted => {
            command.args([
                "--property=IPAddressDeny=any",
                "--property=IPAddressAllow=localhost",
            ]);
        }
    }
    command
        .arg(config.program())
        .args(["job", config_path])
        .arg(job.directory.join("control"));
    command
}

fn cancelled(connection: &PacketSocket, peer: &Principal) -> io::Result<bool> {
    Ok(active(connection.as_raw_fd())? || active(peer.pidfd.as_raw_fd())? || stopping())
}

fn run_job(
    config: &Config,
    config_path: &str,
    connection: &PacketSocket,
    peer: &Principal,
    request: &RunRequest,
    stdio: &mut Vec<OwnedFd>,
) -> io::Result<()> {
    let pinned = pin_sources(&peer.identity, request)?;
    if cancelled(connection, peer)? {
        return Ok(());
    }
    let mut job = Job::new(config)?;
    let address = job.directory.join("control");
    let listener = PacketSocket::new()?;
    listener.bind(&address)?;
    listener.listen(1)?;
    let mut command = unit_command(config, config_path, &job, request.network);
    let mut stdio = stdio.drain(..);
    command
        .stdin(Stdio::from(stdio.next().unwrap()))
        .stdout(Stdio::from(stdio.next().unwrap()))
        .stderr(Stdio::from(stdio.next().unwrap()));
    // Start a separate session so terminal group signals affect the client,
    // which owns cancellation, and never the privileged service launcher.
    unsafe {
        command.pre_exec(|| {
            result(libc::setsid())?;
            Ok(())
        });
    }
    if cancelled(connection, peer)? {
        return Ok(());
    }
    job.launcher = Some(command.spawn()?);
    drop(command);
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut runner = None;
    let mut started = false;
    loop {
        if let Some(status) = job.launcher.as_mut().unwrap().try_wait()? {
            if !started && status.success() {
                return Err(denied(
                    "The sandbox unit exited before the authenticated start handshake",
                ));
            }
            let code = status
                .code()
                .unwrap_or_else(|| -status.signal().unwrap_or(1));
            drop(job);
            return connection.send(&json!({"exitcode": code}), &[]);
        }
        if stopping() {
            return Ok(());
        }
        if !started && Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "Sandbox unit creation timed out",
            ));
        }
        let handshake_fd = if started {
            -1
        } else {
            runner
                .as_ref()
                .map_or(listener.as_raw_fd(), PacketSocket::as_raw_fd)
        };
        let mut wait = [
            event(connection.as_raw_fd()),
            event(peer.pidfd.as_raw_fd()),
            event(handshake_fd),
        ];
        poll(&mut wait, 100)?;
        if wait[0].revents != 0 || wait[1].revents != 0 || stopping() {
            return Ok(());
        }
        if wait[2].revents == 0 {
            continue;
        }
        if let Some(socket) = runner.take() {
            let (ready, _) = socket.receive::<Value>(0)?;
            if ready != json!({"ready": true}) {
                return Err(denied("Unit runner did not confirm its filters"));
            }
            if cancelled(connection, peer)? {
                return Ok(());
            }
            socket.send(&json!({"execute": true}), &[])?;
            started = true;
            connection.send(&json!({"started": true, "unit": job.name}), &[])?;
            continue;
        }
        let socket = listener.accept()?;
        socket.timeout(Duration::from_secs(2))?;
        if socket.credentials()?.uid != 0
            || socket.label()? != format!("{} (enforce)", request.network.profile())
        {
            return Err(denied("Untrusted unit runner"));
        }
        if socket.receive::<Value>(0)?.0 != json!({"runner": true}) {
            return Err(denied("Invalid unit runner handshake"));
        }
        let fds: Vec<_> = pinned.descriptors.iter().map(AsRawFd::as_raw_fd).collect();
        socket.send(
            &JobRef {
                request,
                principal: &peer.identity,
                mounts: &pinned.mounts,
                lease: &job.lease,
            },
            &fds,
        )?;
        runner = Some(socket);
    }
}

struct Job<'a> {
    config: &'a Config,
    name: String,
    directory: PathBuf,
    lease: PathBuf,
    launcher: Option<Child>,
}

impl<'a> Job<'a> {
    fn new(config: &'a Config) -> io::Result<Self> {
        let token = random_token()?;
        let directory = Path::new(&config.runtime).join(&token);
        fs::DirBuilder::new().mode(0o700).create(&directory)?;
        let job = Self {
            config,
            name: format!("{JOB_PREFIX}{token}"),
            lease: directory.join("lease"),
            directory,
            launcher: None,
        };
        fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&job.lease)?;
        Ok(job)
    }
}

impl Drop for Job<'_> {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.lease);
        if let Some(launcher) = self.launcher.as_mut() {
            let name = format!("{}.service", self.name);
            let mut command = privileged_command(&self.config.systemctl);
            command
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            if !wait_timeout(command.args(["stop", &name]), Duration::from_secs(6))
                .is_ok_and(|status| status.success())
            {
                let mut kill = privileged_command(&self.config.systemctl);
                kill.stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null());
                let _ = wait_timeout(
                    kill.args(["kill", "--kill-whom=all", "--signal=KILL", &name]),
                    Duration::from_secs(4),
                );
            }
            if launcher.try_wait().is_ok_and(|status| status.is_none()) {
                reap_child(launcher.id() as i32);
            }
        }
        if let Err(error) = fs::remove_dir_all(&self.directory) {
            eprintln!("Linux sandbox cleanup for {} failed: {error}", SERVICE_NAME);
        }
    }
}
