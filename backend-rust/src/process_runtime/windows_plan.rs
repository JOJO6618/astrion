use std::io;

use super::policy::{Network, SandboxAccess, denied};

pub(super) fn distro_name(value: Option<&str>) -> io::Result<String> {
    let name = value.map(str::trim).filter(|value| !value.is_empty()).unwrap_or("astrion-sandbox");
    if name.len() > 100 || name.starts_with('-')
        || !name.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return Err(denied("Invalid dedicated WSL sandbox distribution name"));
    }
    Ok(name.to_owned())
}

pub(super) fn wsl_path(raw: &str) -> io::Result<String> {
    let raw = raw.strip_prefix(r"\\?\").unwrap_or(raw);
    let bytes = raw.as_bytes();
    if bytes.len() < 4 || !bytes[0].is_ascii_alphabetic() || bytes[1] != b':'
        || !matches!(bytes[2], b'\\' | b'/') || raw.chars().any(char::is_control)
    {
        return Err(denied("WSL sandbox requires a local, non-root drive path"));
    }
    let tail = raw[3..].replace('\\', "/");
    if tail.trim_matches('/').is_empty() || tail.split('/').any(|part| matches!(part, "." | "..")) {
        return Err(denied("WSL sandbox path must be canonical"));
    }
    Ok(format!("/mnt/{}/{}", char::from(bytes[0].to_ascii_lowercase()), tail.trim_end_matches('/')))
}

pub(super) fn sandbox_args(
    distro: &str, workspace: &str, cwd: &str, access: SandboxAccess, script: &str,
    reads: &[String], writes: &[String],
) -> Vec<String> {
    let mut args = ["-d", distro, "-e", "/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-all"]
        .map(str::to_owned).to_vec();
    if access.network == Network::Full { args.push("--share-net".into()); }
    for path in ["/bin", "/sbin", "/usr", "/lib", "/etc"] {
        args.extend(["--ro-bind", path, path].map(str::to_owned));
    }
    for path in reads {
        args.extend(["--ro-bind", path, path].map(str::to_owned));
    }
    args.extend([if access.write { "--bind" } else { "--ro-bind" }, workspace, workspace].map(str::to_owned));
    if access.write {
        for path in writes {
            args.extend(["--bind", path, path].map(str::to_owned));
        }
    }
    args.extend([
        "--chdir", cwd, "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/var/tmp",
        "--dir", "/root", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin:/usr/sbin:/sbin",
        "--setenv", "HOME", "/root", "--remount-ro", "/", "--cap-drop", "ALL",
        "--", "/bin/bash", "--noprofile", "--norc", "-c", script,
    ].map(str::to_owned));
    args
}

#[cfg(test)]
#[path = "../../../test/2026-11-26_Rust运行资源/windows_plan.rs"]
mod tests;
