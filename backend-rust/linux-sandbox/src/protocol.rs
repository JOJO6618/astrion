//! The broker protocol shares its type and size limits with command planners.

use std::{
    collections::{BTreeMap, HashSet},
    io,
    path::Path,
};

use serde::{Deserialize, Deserializer, Serialize};

pub const PROTOCOL_VERSION: u32 = 2;
pub const DEFAULT_SOCKET: &str = "/run/astrion-sandbox/control.sock";
pub const INSTALL_ROOT: &str = "/usr/local/libexec/astrion-sandbox";
pub const PROFILE_PREFIX: &str = "astrion-linux-sandbox-";
pub const RUNTIME_ROOT: &str = "/.astrion-runtime";
pub const MAX_PACKET: usize = 65_536;
pub const MAX_PATHS: usize = 128;
pub const MAX_ARGUMENTS: usize = 256;
pub const SYSTEM_DIRECTORIES: &[&str] = &["/usr", "/bin", "/sbin", "/lib", "/lib64"];
pub const SYSTEM_FILES: &[&str] = &[
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/group",
    "/etc/gai.conf",
    "/etc/localtime",
    "/etc/os-release",
    "/etc/ssl/certs",
    "/etc/ssl/openssl.cnf",
];
pub const MAX_SOURCE_FDS: usize = MAX_PATHS + SYSTEM_DIRECTORIES.len() + SYSTEM_FILES.len() + 2;
pub const SAFE_ENVIRONMENT: &[&str] = &[
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "COLORTERM",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE",
    "PIP_DISABLE_PIP_VERSION_CHECK",
    "PIP_NO_CACHE_DIR",
    "NO_COLOR",
    "FORCE_COLOR",
];

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Network {
    Full,
    Restricted,
    None,
}

impl Network {
    pub const ALL: [Self; 3] = [Self::Full, Self::Restricted, Self::None];

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::Restricted => "restricted",
            Self::None => "none",
        }
    }

    pub fn profile(self) -> String {
        format!("{PROFILE_PREFIX}{}", self.as_str())
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Status {},
    Run(RunRequest),
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RunRequest {
    pub workspace: String,
    pub cwd: String,
    pub reads: Vec<String>,
    pub writes: Vec<String>,
    pub readonly: bool,
    pub workspace_only: bool,
    pub network: Network,
    pub argv: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub umask: u32,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "present_write_root"
    )]
    pub workspace_write_root: Option<String>,
}

fn present_write_root<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<String>, D::Error> {
    String::deserialize(deserializer).map(Some)
}

impl RunRequest {
    pub fn validate(&self) -> io::Result<()> {
        mount_path(&self.workspace)?;
        mount_path(&self.cwd)?;
        let workspace = Path::new(&self.workspace);
        if !Path::new(&self.cwd).starts_with(workspace) {
            return Err(invalid("Working directory is outside the workspace"));
        }
        if let Some(root) = &self.workspace_write_root {
            mount_path(root)?;
            if !Path::new(root).starts_with(workspace)
                || self.readonly
                || !self.workspace_only
                || !self.writes.is_empty()
            {
                return Err(invalid("Invalid fixed workspace write root"));
            }
        }
        if self.reads.len() > MAX_PATHS || self.writes.len() > MAX_PATHS {
            return Err(invalid("Invalid sandbox path list"));
        }
        let paths: HashSet<_> = self
            .reads
            .iter()
            .chain(&self.writes)
            .chain([&self.workspace])
            .collect();
        if paths.len() > MAX_PATHS {
            return Err(invalid("Too many combined sandbox grants"));
        }
        for path in paths {
            mount_path(path)?;
        }
        if (self.readonly || self.workspace_only) && !self.writes.is_empty() {
            return Err(invalid(
                "Extra persistent writes are forbidden for this scope",
            ));
        }
        if self.argv.is_empty()
            || self.argv.len() > MAX_ARGUMENTS
            || self
                .argv
                .iter()
                .any(|arg| arg.len() > MAX_PACKET || arg.contains('\0'))
        {
            return Err(invalid("Invalid command arguments"));
        }
        if self.env.iter().any(|(name, value)| {
            !SAFE_ENVIRONMENT.contains(&name.as_str()) || value.len() > 8192 || value.contains('\0')
        }) {
            return Err(invalid("Invalid command environment"));
        }
        if self.umask > 0o777 {
            return Err(invalid("Invalid command creation mask"));
        }
        Ok(())
    }
}

pub fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

pub fn denied(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, message.into())
}

pub fn mount_path(path: &str) -> io::Result<()> {
    let Some(components) = path.strip_prefix('/') else {
        return Err(invalid("Expected an absolute Linux path"));
    };
    if path.len() > 4096
        || path.contains('\0')
        || components
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err(invalid(
            "Root, relative, and non-normalized paths are not sandbox grants",
        ));
    }
    if [RUNTIME_ROOT, "/proc", "/dev", "/sys"]
        .iter()
        .any(|root| Path::new(path).starts_with(root))
    {
        return Err(denied(
            "Kernel and internal runtime directories cannot be host mount grants",
        ));
    }
    Ok(())
}
