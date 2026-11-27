pub mod client;
pub mod config;
mod identity;
pub mod install;
mod principal;
mod runner;
mod server;
mod sources;
mod transport;

#[cfg(test)]
#[path = "../../../../test/2026-11-27_Linux_Rust沙箱/native.rs"]
mod tests;

use std::{
    fs::File,
    io::{self, Read},
    sync::atomic::{AtomicBool, Ordering},
    thread,
    time::{Duration, Instant},
};

use crate::protocol::{denied, invalid};
use transport::result;

static STOPPING: AtomicBool = AtomicBool::new(false);

extern "C" fn stop(_: libc::c_int) {
    STOPPING.store(true, Ordering::Relaxed);
}

fn install_signal_handlers() -> io::Result<()> {
    let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
    action.sa_sigaction = stop as *const () as usize;
    result(unsafe { libc::sigemptyset(&mut action.sa_mask) })?;
    for signal in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
        result(unsafe { libc::sigaction(signal, &action, std::ptr::null_mut()) })?;
    }
    Ok(())
}

fn stopping() -> bool {
    STOPPING.load(Ordering::Relaxed)
}

fn bind_parent_lifetime(parent: i32, signal: i32) -> io::Result<()> {
    result(unsafe { libc::prctl(libc::PR_SET_PDEATHSIG, signal, 0, 0, 0) })?;
    if unsafe { libc::getppid() } != parent {
        return Err(denied("Cannot bind sandbox worker lifetime to its broker"));
    }
    Ok(())
}

fn reap_child(pid: i32) {
    if unsafe { libc::waitpid(pid, std::ptr::null_mut(), libc::WNOHANG) } != 0 {
        return;
    }
    unsafe { libc::kill(pid, libc::SIGKILL) };
    let deadline = Instant::now() + Duration::from_secs(1);
    while Instant::now() < deadline {
        if unsafe { libc::waitpid(pid, std::ptr::null_mut(), libc::WNOHANG) } != 0 {
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn random_token() -> io::Result<String> {
    let mut entropy = [0u8; 12];
    File::open("/dev/urandom")?.read_exact(&mut entropy)?;
    Ok(entropy.iter().map(|byte| format!("{byte:02x}")).collect())
}

pub fn cli(args: &[String]) -> io::Result<i32> {
    if unsafe { libc::getuid() != libc::geteuid() || libc::getgid() != libc::getegid() } {
        return Err(denied(
            "The sandbox helper rejects mixed account credentials",
        ));
    }
    match args {
        [mode, flag, request] if mode == "run" && flag == "--request" => client::run(request),
        [mode, config] if mode == "serve" => server::serve(config),
        [mode, config, rendezvous] if mode == "job" => runner::run(config, rendezvous),
        [mode, args @ ..] if mode == "uid-exec" => identity::execute(args),
        [mode, args @ ..] if mode == "check" => identity::check(args),
        [mode, args @ ..] if mode == "install" => install::install(args).map(|()| 0),
        [mode] if mode == "status" => {
            let state = status();
            println!("{}", serde_json::to_string_pretty(&state)?);
            Ok(if state["state"] == "ready" { 0 } else { 1 })
        }
        _ => Err(invalid(
            "Usage: astrion-linux-sandbox install [--user USER] [--dry-run] [--no-install-dependencies] | status",
        )),
    }
}

pub fn status() -> serde_json::Value {
    let (state, detail) = if !config::supported_platform() {
        (
            "unsupported",
            "The Linux Host sandbox is supported on Ubuntu 24.04.".to_owned(),
        )
    } else if !config::ordinary_user() {
        (
            "ordinary_user_required",
            "Run Astrion with an ordinary system account.".to_owned(),
        )
    } else {
        match client::probe() {
            Ok(()) => ("ready", String::new()),
            Err(error) => ("helper_missing", error.to_string()),
        }
    };
    serde_json::json!({
        "applicable": true, "platform": "linux", "state": state, "detail": detail, "setup_running": false,
        "can_install": false, "distro_name": "", "install_path": config::INSTALL_ROOT,
        "install_command": if config::ordinary_user() { "sudo bash scripts/setup-linux-sandbox.sh" } else { "" },
    })
}
