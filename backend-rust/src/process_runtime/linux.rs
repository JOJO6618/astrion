//! Ordinary-user bridge to the existing administrator-installed broker.

use std::{fs, io, os::unix::fs::{FileTypeExt, MetadataExt}, path::Path, process::Command};

use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::{Value, json};

use super::policy::{Network, SandboxAccess, denied};

const INSTALL_ROOT: &str = "/usr/local/libexec/astrion-sandbox";
const CLIENT: &str = "/usr/local/libexec/astrion-sandbox/client.py";
const SOCKET: &str = "/run/astrion-sandbox/control.sock";
const PYTHON: &str = "/usr/bin/python3";

fn supported_platform() -> bool {
    fs::read_to_string("/etc/os-release").is_ok_and(|release| {
        let value = |key: &str| release.lines().find_map(|line| {
            line.split_once('=').filter(|(name, _)| *name == key).map(|(_, value)| value.trim_matches('"'))
        });
        value("ID") == Some("ubuntu") && value("VERSION_ID") == Some("24.04")
    })
}

pub(super) fn ordinary_user() -> bool {
    // Match the installed broker, which rejects root principals.
    unsafe { libc::getuid() != 0 && libc::geteuid() == libc::getuid() }
}

fn trusted(path: &Path, file: bool) -> bool {
    fs::symlink_metadata(path).is_ok_and(|meta| {
        meta.uid() == 0 && meta.mode() & 0o022 == 0 && if file { meta.is_file() } else { meta.is_dir() }
    })
}

pub(super) fn helper_installed() -> bool {
    supported_platform() && ordinary_user()
        && Path::new(INSTALL_ROOT).ancestors().all(|path| trusted(path, false))
        && trusted(Path::new(CLIENT), true)
        && fs::canonicalize(PYTHON).is_ok_and(|path| trusted(&path, true))
        && fs::symlink_metadata(SOCKET).is_ok_and(|meta| meta.uid() == 0 && meta.file_type().is_socket())
}

pub(super) fn command_argv(
    workspace: &Path, cwd: &Path, access: SandboxAccess, argv: &[&str], reads: Vec<String>, writes: Vec<String>,
    fixed_write_root: Option<&Path>,
) -> io::Result<Command> {
    if !helper_installed() { return Err(denied("The administrator-installed Linux sandbox is unavailable")); }
    let network = match access.network { Network::None => "none", Network::Restricted => "restricted", Network::Full => "full" };
    let mut environment = serde_json::Map::new();
    for key in ["PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM"] {
        if let Ok(value) = std::env::var(key) { environment.insert(key.into(), json!(value)); }
    }
    environment.entry("PATH").or_insert(json!("/usr/bin:/bin"));
    let mut request = json!({"operation":"run","workspace":workspace,"cwd":cwd,
        "reads":reads,"writes":if access.write { writes } else { Vec::new() },"readonly":!access.write,
        "workspace_only":fixed_write_root.is_some(),"network":network,"argv":argv,
        "env":environment,"umask":0o022});
    if let Some(root) = fixed_write_root { request["workspace_write_root"] = json!(root); }
    let payload = serde_json::to_vec(&request)?;
    if payload.len() > 65_536 { return Err(denied("Linux sandbox request exceeds the broker packet limit")); }
    let mut command = Command::new(PYTHON);
    command.args(["-I", CLIENT, "--request"]).arg(STANDARD.encode(payload)).env_clear().current_dir(cwd);
    Ok(command)
}

pub(crate) fn sandbox_status() -> Value {
    let mut state = "helper_missing";
    let mut detail = "Install the administrator-managed Astrion sandbox helper on this machine.".to_owned();
    if !supported_platform() {
        state = "unsupported";
        detail = "The Linux Host sandbox is supported on Ubuntu 24.04.".into();
    } else if !ordinary_user() {
        state = "ordinary_user_required";
        detail = "Run Astrion with an ordinary system account.".into();
    } else if helper_installed() {
        let code = "import sys; sys.path.insert(0, '/usr/local/libexec/astrion-sandbox'); from client import probe; probe()";
        if Command::new(PYTHON).args(["-I", "-c", code]).env_clear().output().is_ok_and(|output| output.status.success()) {
            state = "ready";
            detail.clear();
        } else {
            detail = "The installed sandbox helper did not complete its authenticated status check.".into();
        }
    }
    json!({"applicable":true,"platform":"linux","state":state,"detail":detail,"setup_running":false,"can_install":false,
        "distro_name":"","install_path":INSTALL_ROOT,"install_command":if ordinary_user() {"sudo bash scripts/setup-linux-sandbox.sh"} else {""}})
}
