use std::{
    collections::VecDeque,
    io,
    process::Command,
    sync::{Arc, Condvar, Mutex, MutexGuard},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use tokio::sync::watch;
use uuid::Uuid;

const COMMAND_BUFFER: usize = 50_000;
const TERMINAL_BUFFER: usize = 100_000;
pub(super) const CMD_READY: &str = "__ASTRION_TERMINAL_READY__";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Status {
    Running,
    Completed,
    Failed,
    Timeout,
    Cancelled,
}

impl Status {
    pub(super) fn text(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Timeout => "timeout",
            Self::Cancelled => "cancelled",
        }
    }
}

pub(super) struct Process {
    shared: Arc<Shared>,
    cmd_input: bool,
}

struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    revision: watch::Sender<u64>,
}

pub(super) struct State {
    pub(super) status: Status,
    pub(super) return_code: Option<i32>,
    pub(super) message: Option<String>,
    pub(super) created_at: f64,
    pub(super) updated_at: f64,
    pub(super) finished_at: Option<f64>,
    pub(super) last_output_at: Option<f64>,
    pub(super) last_command: String,
    pub(super) command_count: u64,
    pub(super) output: Output,
    cancelled: bool,
    input: VecDeque<u8>,
    marker: Option<Vec<u8>>,
    marker_tail: Vec<u8>,
}

pub(super) struct Output {
    bytes: VecDeque<u8>,
    end: u64,
    limit: usize,
}

impl Output {
    fn push(&mut self, bytes: &[u8]) {
        self.end += bytes.len() as u64;
        let excess = (self.bytes.len() + bytes.len()).saturating_sub(self.limit);
        let evict = excess.min(self.bytes.len());
        self.bytes.drain(..evict);
        self.bytes.extend(bytes[excess - evict..].iter().copied());
    }

    pub(super) fn len(&self) -> usize { self.bytes.len() }

    fn start(&self) -> u64 { self.end - self.bytes.len() as u64 }

    pub(super) fn truncated(&self) -> bool { self.start() > 0 }

    pub(super) fn text(&self, cursor: u64) -> String {
        let first = cursor.saturating_sub(self.start()).min(self.bytes.len() as u64) as usize;
        let bytes: Vec<_> = self.bytes.iter().skip(first).copied().collect();
        // Truncation may cut the first codepoint in half.
        let start = if first == 0 && self.truncated() {
            bytes.iter().take_while(|byte| **byte & 0xc0 == 0x80).count()
        } else { 0 };
        String::from_utf8_lossy(&bytes[start..]).into_owned()
    }
}

impl Process {
    pub(super) fn spawn(command: Command, timeout: Option<Duration>, cmd_input: bool) -> io::Result<Arc<Self>> {
        let now = now();
        let (revision, _) = watch::channel(0);
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                status: Status::Running, return_code: None, message: None, created_at: now, updated_at: now,
                finished_at: None, last_output_at: None, last_command: String::new(), command_count: 0,
                output: Output { bytes: VecDeque::new(), end: 0, limit: if timeout.is_some() { COMMAND_BUFFER } else { TERMINAL_BUFFER } },
                cancelled: false, input: VecDeque::new(),
                marker: timeout.is_none().then(|| if cmd_input { CMD_READY.as_bytes().to_vec() }
                    else { b"\x1eASTRION_TERMINAL_READY\x1f".to_vec() }),
                marker_tail: Vec::new(),
            }),
            wake: Condvar::new(), revision,
        });
        platform::spawn(command, timeout, shared.clone(), timeout.is_none())?;
        let process = Arc::new(Self { shared, cmd_input });
        if timeout.is_none() { process.require_terminal_ready()?; }
        Ok(process)
    }

    fn require_terminal_ready(&self) -> io::Result<()> {
        let (state, wait) = self.shared.wake.wait_timeout_while(self.state(), Duration::from_secs(2), |state| {
            state.status == Status::Running && state.marker.is_some()
        }).unwrap_or_else(|error| error.into_inner());
        if !wait.timed_out() && state.status == Status::Running && state.marker.is_none() {
            Ok(())
        } else {
            Err(io::Error::other(format!("Trusted terminal did not start: {}", state.output.text(0).trim())))
        }
    }

    pub(super) fn state(&self) -> MutexGuard<'_, State> { self.shared.state() }

    pub(super) fn cancel(&self) {
        self.state().cancelled = true;
        self.shared.wake.notify_all();
    }

    pub(super) fn wait_blocking(&self) {
        let _state = self.shared.wake.wait_while(self.state(), |state| state.status == Status::Running)
            .unwrap_or_else(|error| error.into_inner());
    }

    pub(super) async fn wait(&self) {
        self.until(|state| state.status != Status::Running).await;
    }

    pub(super) fn input(&self, text: &str) -> io::Result<u64> {
        let mut state = self.state();
        if state.status != Status::Running || state.cancelled {
            return Err(io::Error::new(io::ErrorKind::BrokenPipe, "Terminal is closed"));
        }
        if !state.input.is_empty() {
            return Err(io::Error::new(io::ErrorKind::WouldBlock, "A terminal input is already queued"));
        }
        let cursor = state.output.end;
        state.last_command = text.to_owned();
        state.command_count += 1;
        state.updated_at = now();
        // A delimiter is only a UI completion hint. Shell authorization comes from
        // Seatbelt and the fixed conversation/process ownership, independent of output.
        let packet = if state.marker.is_some() {
            if self.cmd_input { format!("{text}\r\n") } else { format!("{text}\n") }
        } else {
            let id = Uuid::new_v4().simple().to_string();
            if self.cmd_input {
                let marker = format!("__ASTRION_{id}__");
                let text = text.replace("\r\n", "\n").replace('\n', "\r\n");
                let input = format!("{text}\r\necho {marker}\r\n");
                state.marker = Some(marker.into_bytes());
                input
            } else {
                state.marker = Some(format!("\x1e{id}\x1f").into_bytes());
                let quoted = text.replace('\'', "'\\''");
                format!("builtin eval '{quoted}'; builtin printf '\\036%s\\037' '{id}'\n")
            }
        };
        let echo = format!("{text}\n");
        state.output.push(echo.as_bytes());
        state.last_output_at = Some(state.updated_at);
        state.input.extend(packet.bytes());
        self.shared.changed();
        Ok(cursor + echo.len() as u64)
    }

    pub(super) async fn wait_input(&self) {
        self.until(|state| state.marker.is_none() || state.status != Status::Running).await;
    }

    async fn until(&self, done: impl Fn(&State) -> bool) {
        let mut updates = self.shared.revision.subscribe();
        while !done(&self.state()) {
            if updates.changed().await.is_err() { break; }
        }
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        self.cancel();
        self.wait_blocking();
    }
}

impl Shared {
    fn state(&self) -> MutexGuard<'_, State> { self.state.lock().unwrap_or_else(|error| error.into_inner()) }

    fn changed(&self) {
        self.revision.send_modify(|revision| *revision = revision.wrapping_add(1));
        self.wake.notify_all();
    }
}

impl State {
    pub(super) fn interactive(&self) -> bool { self.marker.is_some() && self.status == Status::Running }

    #[cfg(any(target_os = "macos", target_os = "linux", windows))]
    fn read_output(&mut self, stdout: &[u8], stderr: &[u8]) {
        if !stdout.is_empty() {
            if let Some(marker) = &self.marker {
                self.marker_tail.extend_from_slice(stdout);
                if let Some(first) = self.marker_tail.windows(marker.len()).position(|window| window == marker) {
                    self.output.push(&self.marker_tail[..first]);
                    self.output.push(&self.marker_tail[first + marker.len()..]);
                    self.marker_tail.clear();
                    self.marker = None;
                } else {
                    let matching = (1..marker.len()).rev().find(|&len| self.marker_tail.ends_with(&marker[..len])).unwrap_or(0);
                    let available = self.marker_tail.len() - matching;
                    self.output.push(&self.marker_tail[..available]);
                    self.marker_tail.drain(..available);
                }
            } else {
                self.output.push(stdout);
            }
        }
        self.output.push(stderr);
        self.updated_at = now();
        self.last_output_at = Some(self.updated_at);
    }
}

pub(super) fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()
}

#[cfg(not(any(target_os = "macos", target_os = "linux", windows)))]
mod platform {
    use super::*;
    pub(super) fn spawn(_: Command, _: Option<Duration>, _: Arc<Shared>, _: bool) -> io::Result<()> {
        Err(io::Error::new(io::ErrorKind::Unsupported, "Trusted Host process sandbox is unavailable"))
    }
}

#[cfg(any(target_os = "macos", target_os = "linux"))]
mod platform {
    use super::*;
    use std::{
        io::{Read, Write}, os::{fd::{AsRawFd, FromRawFd, OwnedFd}, unix::{net::UnixStream, process::{CommandExt, ExitStatusExt}}},
        process::{Child, Stdio},
    };

    const POLL: Duration = Duration::from_millis(15);

    pub(super) fn spawn(command: Command, timeout: Option<Duration>, shared: Arc<Shared>, input: bool) -> io::Result<()> {
        let (ready, wait) = std::sync::mpsc::sync_channel(0);
        std::thread::Builder::new().name("astrion-host-process".into()).spawn(move || {
            let mut child = match start(command, input) {
                Ok(child) => child,
                Err(error) => { let _ = ready.send(Err(error)); return; }
            };
            if ready.send(Ok(())).is_err() { return; }
            let launched = Instant::now();
            let mut stdout = child.0.stdout.take();
            let mut stderr = child.0.stderr.take();
            let mut stdin = child.0.stdin.take();
            let mut out = Vec::with_capacity(8192);
            let mut err = Vec::with_capacity(8192);
            let finish = loop {
                out.clear();
                err.clear();
                read(&mut stdout, &mut out);
                read(&mut stderr, &mut err);
                let mut state = shared.state();
                if !(out.is_empty() && err.is_empty()) {
                    state.read_output(&out, &err);
                    shared.changed();
                }
                if state.cancelled { break Ok(Status::Cancelled); }
                if timeout.is_some_and(|timeout| launched.elapsed() >= timeout) { break Ok(Status::Timeout); }
                match child.exited() {
                    Ok(true) => break Ok(Status::Completed),
                    Ok(false) => (),
                    Err(error) => break Err(error),
                }
                if let Some(pipe) = &mut stdin && !state.input.is_empty() {
                    match pipe.write(state.input.as_slices().0) {
                        Ok(count) => { state.input.drain(..count); }
                        Err(error) if matches!(error.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted) => (),
                        Err(error) => break Err(error),
                    }
                }
                let _state = shared.wake.wait_timeout(state, POLL).unwrap_or_else(|error| error.into_inner());
            };
            let code = child.reap();
            drop(stdin);
            // All remaining reads are non-blocking. Escaped descriptors cannot
            // hold the worker, HTTP cancellation, or backend shutdown open.
            out.clear();
            err.clear();
            read(&mut stdout, &mut out);
            read(&mut stderr, &mut err);
            drop((stdout, stderr));
            let (status, message) = match (&finish, &code) {
                (_, Err(error)) | (Err(error), _) => (Status::Failed, Some(format!("Process supervision failed: {error}"))),
                (Ok(Status::Completed), Ok(0)) => (Status::Completed, None),
                (Ok(Status::Completed), Ok(code)) => (Status::Failed, Some(format!("Command exited with return code {code}"))),
                (Ok(Status::Timeout), _) => (Status::Timeout, Some("Command timed out".into())),
                (Ok(Status::Cancelled), _) => (Status::Cancelled, Some("Command cancelled".into())),
                (Ok(status), _) => (*status, None),
            };
            let mut state = shared.state();
            if !(out.is_empty() && err.is_empty()) { state.read_output(&out, &err); }
            let unfinished = std::mem::take(&mut state.marker_tail);
            state.output.push(&unfinished);
            state.marker = None;
            state.input.clear();
            state.status = status;
            state.message = message;
            state.return_code = code.ok();
            state.updated_at = now();
            state.finished_at = Some(state.updated_at);
            shared.changed();
        })?;
        wait.recv().map_err(io::Error::other)?
    }

    fn read<R: Read>(pipe: &mut Option<R>, output: &mut Vec<u8>) {
        let mut block = [0; 8192];
        // Check cancellation between bounded drain batches even for a busy pipe.
        for _ in 0..16 {
            let Some(reader) = pipe.as_mut() else { return; };
            match reader.read(&mut block) {
                Ok(0) => { *pipe = None; return; }
                Ok(count) => output.extend_from_slice(&block[..count]),
                Err(error) if error.kind() == io::ErrorKind::Interrupted => (),
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => return,
                Err(_) => { *pipe = None; return; }
            }
        }
    }

    fn start(plan: Command, input: bool) -> io::Result<OwnedChild> {
        let (watch, control) = UnixStream::pair()?;
        // Reserve an owned descriptor before spawning. It stays close-on-exec in
        // the backend and is inherited deliberately only by the fixed guard script.
        let descriptor = unsafe { libc::fcntl(watch.as_raw_fd(), libc::F_DUPFD_CLOEXEC, 10) };
        if descriptor == -1 { return Err(io::Error::last_os_error()); }
        let watch = unsafe { OwnedFd::from_raw_fd(descriptor) };
        let watcher = format!(concat!(
            "(IFS= builtin read -r closed <&{fd}; builtin kill -KILL -- -\"$$\") </dev/null >/dev/null 2>&1 &\n",
            "exec {fd}<&-\n",
            "exec \"$@\"\n",
        ), fd=descriptor);
        let mut command = Command::new("/bin/bash");
        // This fixed supervisor handles only backend liveness. The supplied
        // command remains an opaque argv element of the OS sandbox invocation.
        command.args(["--noprofile", "--norc", "-c", &watcher, "astrion-process-guard"])
            .arg(plan.get_program()).args(plan.get_args()).env_clear();
        for (key, value) in plan.get_envs() {
            if let Some(value) = value { command.env(key, value); }
        }
        command.current_dir(plan.get_current_dir().ok_or_else(|| io::Error::other("Missing process cwd"))?)
            .stdin(if input { Stdio::piped() } else { Stdio::null() }).stdout(Stdio::piped()).stderr(Stdio::piped());
        // SAFETY: setsid and fcntl are async-signal-safe. The descriptor is owned
        // by watch across spawn, and only the child descriptor's flag changes.
        unsafe {
            command.pre_exec(move || {
                if libc::setsid() == -1 || libc::fcntl(descriptor, libc::F_SETFD, 0) == -1 {
                    return Err(io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = OwnedChild(command.spawn()?, false, control);
        drop(watch);
        for fd in [child.0.stdin.as_ref().map(AsRawFd::as_raw_fd), child.0.stdout.as_ref().map(AsRawFd::as_raw_fd), child.0.stderr.as_ref().map(AsRawFd::as_raw_fd)].into_iter().flatten() {
            // SAFETY: each fd is held by child for the duration of the calls.
            let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
            if flags == -1 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } == -1 {
                return Err(io::Error::last_os_error());
            }
        }
        Ok(child)
    }

    struct OwnedChild(Child, bool, UnixStream);

    impl OwnedChild {
        fn exited(&self) -> io::Result<bool> {
            let mut status = std::mem::MaybeUninit::<libc::siginfo_t>::zeroed();
            // SAFETY: waitid only observes our child. WNOWAIT retains the PID
            // until we kill its process group, preventing PID reuse during cleanup.
            let result = unsafe { libc::waitid(libc::P_PID, self.0.id() as libc::id_t, status.as_mut_ptr(), libc::WEXITED | libc::WNOHANG | libc::WNOWAIT) };
            if result == 0 {
                Ok(unsafe { status.assume_init().si_pid() } != 0)
            } else {
                let error = io::Error::last_os_error();
                if error.kind() == io::ErrorKind::Interrupted { Ok(false) } else { Err(error) }
            }
        }

        fn reap(&mut self) -> io::Result<i32> {
            let pid = self.0.id() as libc::pid_t;
            // SAFETY: only this thread waits/reaps the process and its PID is
            // retained here. A successful setsid made pid its isolated group ID.
            let sent = unsafe { libc::kill(-pid, libc::SIGKILL) };
            let termination = if sent == -1 {
                let error = io::Error::last_os_error();
                if error.raw_os_error() == Some(libc::ESRCH) { Ok(()) } else {
                    let _ = self.0.kill();
                    Err(error)
                }
            } else { Ok(()) };
            let result = self.0.wait();
            self.1 = true;
            let _ = self.2.shutdown(std::net::Shutdown::Both);
            termination?;
            let status = result?;
            Ok(status.code().or_else(|| status.signal().map(|signal| -signal)).unwrap_or(-1))
        }
    }

    impl Drop for OwnedChild {
        fn drop(&mut self) {
            if !self.1 { let _ = self.reap(); }
        }
    }
}

#[cfg(windows)]
#[path = "process_windows.rs"]
mod platform;
