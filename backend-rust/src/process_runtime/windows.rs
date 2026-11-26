//! WSL sandbox plans use the dedicated user-installed distribution only.

use std::{io, os::windows::process::CommandExt, path::{Path, PathBuf}, process::{Command, Stdio}, time::{Duration, Instant}};

use serde_json::{Value, json};
use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

use super::{policy::{SandboxAccess, denied}, windows_plan};

const NO_INTEROP: &str = "test ! -e /proc/sys/fs/binfmt_misc/WSLInterop || /bin/grep -qx disabled /proc/sys/fs/binfmt_misc/WSLInterop";

pub(crate) fn system_program(file: &str) -> Option<PathBuf> {
    let root = PathBuf::from(std::env::var_os("SystemRoot")?);
    if !root.is_absolute() { return None; }
    [root.join("System32").join(file), root.join("Sysnative").join(file)].into_iter().find(|path| path.is_file())
}

pub(crate) fn distro() -> io::Result<String> {
    windows_plan::distro_name(std::env::var("HOST_SANDBOX_WSL_DISTRO").ok().as_deref())
}

pub(super) fn helper_installed() -> bool {
    system_program("wsl.exe").is_some() && distro().is_ok()
}

fn probe(wsl: &Path, distro: &str, argv: &[&str]) -> bool {
    let mut command = Command::new(wsl);
    command.args(["-d", distro, "-e"]).args(argv);
    probe_command(command)
}

fn probe_command(mut command: Command) -> bool {
    let mut child = match command.env("WSL_UTF8", "1").creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn()
    {
        Ok(child) => child,
        Err(_) => return false,
    };
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(40)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

pub(crate) fn host_usable() -> bool {
    let Some(wsl) = system_program("wsl.exe") else { return false; };
    let mut command = Command::new(wsl);
    command.args(["-l", "-q"]);
    probe_command(command)
}

pub(crate) fn virtual_machine_platform_ready() -> bool {
    let Some(sc) = system_program("sc.exe") else { return false; };
    let mut command = Command::new(sc);
    command.args(["query", "vmcompute"]);
    probe_command(command)
}

pub(super) fn command(
    workspace: &Path, cwd: &Path, access: SandboxAccess, script: &str, reads: Vec<String>, writes: Vec<String>,
    fixed_write_root: Option<&Path>,
) -> io::Result<Command> {
    command_argv(workspace,cwd,access,&["/bin/bash","--noprofile","--norc","-c",script],reads,writes,fixed_write_root)
}

pub(super) fn plugin_interpreter() -> io::Result<&'static str> {
    let wsl = system_program("wsl.exe").ok_or_else(||denied("缺少自定义工具所需的 WSL"))?;
    let name = distro()?;
    if probe(&wsl,&name,&["/usr/bin/test","-x","/usr/bin/python3"]) { Ok("/usr/bin/python3") }
    else { Err(denied("Astrion WSL 沙箱缺少 /usr/bin/python3")) }
}

pub(super) fn command_argv(
    workspace: &Path, cwd: &Path, access: SandboxAccess, argv: &[&str], reads: Vec<String>, writes: Vec<String>,
    fixed_write_root: Option<&Path>,
) -> io::Result<Command> {
    let wsl = system_program("wsl.exe").ok_or_else(|| denied("WSL is not installed"))?;
    let distro = distro()?;
    if !probe(&wsl, &distro, &["/bin/sh", "-c", NO_INTEROP]) {
        return Err(denied("The dedicated WSL sandbox is unavailable or Windows interop is still enabled"));
    }
    let map = |path: &Path| windows_plan::wsl_path(path.to_str().ok_or_else(|| denied("WSL path must be UTF-8"))?);
    let map_list = |list: Vec<String>| list.iter().map(|path| windows_plan::wsl_path(path)).collect::<io::Result<Vec<_>>>();
    let fixed_write_root = fixed_write_root.map(map).transpose()?;
    let args = windows_plan::sandbox_argv(&distro, &map(workspace)?, &map(cwd)?, access, argv,
        &map_list(reads)?, &map_list(writes)?, fixed_write_root.as_deref());
    let mut command = Command::new(wsl);
    command.args(args).env("WSL_UTF8", "1").current_dir(cwd).creation_flags(CREATE_NO_WINDOW);
    Ok(command)
}

pub(super) fn direct(cwd: &Path, script: Option<&str>) -> io::Result<Command> {
    let cmd = system_program("cmd.exe").ok_or_else(|| denied("Windows command interpreter is unavailable"))?;
    let mut command = Command::new(cmd);
    command.current_dir(cwd).creation_flags(CREATE_NO_WINDOW);
    if let Some(script) = script {
        command.args(["/D", "/S", "/C"]).raw_arg(format!("\"{script}\""));
    } else {
        command.args(["/D", "/Q", "/V:OFF", "/K"]).raw_arg(format!("echo {}", super::process::CMD_READY));
    }
    Ok(command)
}

pub(crate) fn sandbox_status() -> Value {
    let distro = distro().unwrap_or_else(|_| "astrion-sandbox".into());
    let (state, detail) = match system_program("wsl.exe") {
        None => ("wsl_missing", "Windows Subsystem for Linux is unavailable."),
        Some(_) if !host_usable() => ("wsl_missing", "The Windows Subsystem for Linux features are not ready."),
        Some(_) if !virtual_machine_platform_ready() => ("vm_platform_missing", "The Windows Virtual Machine Platform feature is not ready."),
        Some(wsl) if !probe(&wsl, &distro, &["/bin/true"]) => ("distro_missing", "The dedicated WSL sandbox distribution is not ready."),
        Some(wsl) if !probe(&wsl, &distro, &["/usr/bin/bwrap", "--version"]) => ("bwrap_missing", "The WSL sandbox distribution needs bubblewrap."),
        Some(wsl) if !probe(&wsl, &distro, &["/bin/sh", "-c", NO_INTEROP]) => ("unsupported", "Windows interop must be disabled in the sandbox distribution."),
        Some(_) => ("ready", ""),
    };
    json!({"applicable":true,"platform":"win32","state":state,"detail":detail,"distro_name":distro,
        "setup_running":false,"can_install":false})
}
