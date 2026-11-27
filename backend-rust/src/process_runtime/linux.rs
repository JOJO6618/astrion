//! Ordinary-user command planning for the administrator-installed broker.

use std::{
    fs, io,
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::{Path, PathBuf},
    process::Command,
};

use astrion_linux_sandbox::{
    linux::config::{EXECUTABLE_NAME, supported_platform, trusted_path},
    protocol::{
        DEFAULT_SOCKET, INSTALL_ROOT, MAX_PACKET, Network as BrokerNetwork, Request, RunRequest,
    },
};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::{Value, json};

use super::policy::{Network, SandboxAccess, denied};

pub(super) use astrion_linux_sandbox::linux::config::ordinary_user;

fn installed_program() -> Option<PathBuf> {
    let program = Path::new(INSTALL_ROOT).join(EXECUTABLE_NAME);
    trusted_path(&program, false).ok()?;
    let mode = fs::metadata(&program).ok()?.mode();
    if mode & 0o6000 != 0 || mode & 0o111 == 0 {
        return None;
    }
    let socket = Path::new(DEFAULT_SOCKET);
    trusted_path(socket.parent()?, true).ok()?;
    let info = fs::symlink_metadata(socket).ok()?;
    (info.uid() == 0 && info.file_type().is_socket()).then_some(program)
}

pub(super) fn helper_installed() -> bool {
    supported_platform() && ordinary_user() && installed_program().is_some()
}

pub(super) fn command_argv(
    workspace: &Path,
    cwd: &Path,
    access: SandboxAccess,
    argv: &[&str],
    reads: Vec<String>,
    writes: Vec<String>,
    fixed_write_root: Option<&Path>,
) -> io::Result<Command> {
    let program = installed_program()
        .filter(|_| supported_platform() && ordinary_user())
        .ok_or_else(|| denied("Install or upgrade the administrator-installed Linux sandbox"))?;
    let network = match access.network {
        Network::None => BrokerNetwork::None,
        Network::Restricted => BrokerNetwork::Restricted,
        Network::Full => BrokerNetwork::Full,
    };
    let mut env: std::collections::BTreeMap<_, _> =
        ["PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM"]
            .into_iter()
            .filter_map(|key| std::env::var(key).ok().map(|value| (key.to_owned(), value)))
            .collect();
    env.entry("PATH".into())
        .or_insert_with(|| "/usr/bin:/bin".into());
    let path_text = |path: &Path| {
        path.to_str()
            .map(str::to_owned)
            .ok_or_else(|| denied("Linux sandbox paths must be valid UTF-8"))
    };
    let request = RunRequest {
        workspace: path_text(workspace)?,
        cwd: path_text(cwd)?,
        reads,
        writes: if access.write { writes } else { Vec::new() },
        readonly: !access.write,
        workspace_only: fixed_write_root.is_some(),
        network,
        argv: argv.iter().map(|arg| (*arg).to_owned()).collect(),
        env,
        umask: 0o022,
        workspace_write_root: fixed_write_root.map(path_text).transpose()?,
    };
    request.validate()?;
    let payload = serde_json::to_vec(&Request::Run(request))?;
    if payload.len() > MAX_PACKET {
        return Err(denied(
            "Linux sandbox request exceeds the broker packet limit",
        ));
    }
    let mut command = Command::new(program);
    command
        .args(["run", "--request"])
        .arg(STANDARD.encode(payload))
        .env_clear()
        .current_dir(cwd);
    Ok(command)
}

pub(crate) fn sandbox_status() -> Value {
    if supported_platform() && ordinary_user() && installed_program().is_none() {
        return json!({
            "applicable": true, "platform": "linux", "state": "helper_missing",
            "detail": "Install or upgrade the administrator-managed Astrion sandbox helper on this machine.",
            "setup_running": false, "can_install": false, "distro_name": "", "install_path": INSTALL_ROOT,
            "install_command": "sudo bash scripts/setup-linux-sandbox.sh",
        });
    }
    astrion_linux_sandbox::linux::status()
}
