use std::{
    io::{self, ErrorKind},
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, Instant},
};

use axum::http::StatusCode;
use cap_std::fs::Dir;
use serde_json::{Value, json};
use uuid::Uuid;

use crate::{
    conversation_transfer::assets,
    knowledge::storage::{open_canonical_dir, publish},
};

use super::{Result, UploadError};

pub(super) struct Scanner {
    enabled: bool,
    binary: String,
    arguments: Option<Vec<String>>,
    timeout: Duration,
    directory: Option<String>,
}

impl Scanner {
    pub(super) fn from_env() -> Self {
        let enabled = std::env::var("UPLOAD_CLAMAV_ENABLED")
            .ok()
            .is_some_and(|value| {
                !matches!(
                    value.trim().to_ascii_lowercase().as_str(),
                    "0" | "false" | "no" | "off"
                )
            });
        let arguments = std::env::var("UPLOAD_CLAMAV_ARGS")
            .unwrap_or_else(|_| "--fdpass --no-summary --stdout".into());
        let timeout = std::env::var("UPLOAD_CLAMAV_TIMEOUT_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(30)
            .max(1);
        Self {
            enabled,
            arguments: shlex::split(&arguments),
            timeout: Duration::from_secs(timeout),
            binary: std::env::var("UPLOAD_CLAMAV_BIN")
                .unwrap_or_else(|_| "clamdscan".into())
                .trim()
                .into(),
            directory: std::env::var("UPLOAD_QUARANTINE_SUBDIR").ok(),
        }
    }

    pub(super) async fn scan(&self, data: &Path, project: &Path, bytes: &[u8]) -> Result<Value> {
        if !self.enabled {
            return Ok(json!({"status":"skipped","engine":"clamdscan","message":"已跳过病毒扫描"}));
        }
        let arguments = self.arguments.as_ref().ok_or_else(scanner_unavailable)?;
        if self.binary.is_empty() {
            return Err(scanner_unavailable());
        }
        let staged = Stage::write(data, project, self.directory.as_deref(), bytes)
            .map_err(|_| UploadError::storage())?;
        let mut command = tokio::process::Command::new(&self.binary);
        command
            .args(arguments)
            .arg(&staged.path)
            .stdin(Stdio::null())
            .kill_on_drop(true);
        let started = Instant::now();
        let output = tokio::time::timeout(self.timeout, command.output())
            .await
            .map_err(|_| rejected())?
            .map_err(|error| {
                if error.kind() == ErrorKind::NotFound {
                    scanner_unavailable()
                } else {
                    rejected()
                }
            })?;
        if !output.status.success() {
            return Err(rejected());
        }
        Ok(
            json!({"status":"passed","engine":"clamdscan","duration_ms":started.elapsed().as_millis()}),
        )
    }
}

fn scanner_unavailable() -> UploadError {
    UploadError::new(
        StatusCode::INTERNAL_SERVER_ERROR,
        "scanner_missing",
        "文件扫描服务不可用",
    )
}

fn rejected() -> UploadError {
    UploadError::bad("scan_failed", "文件未通过安全扫描")
}

struct Stage {
    directory: Dir,
    path: PathBuf,
    name: String,
}

impl Stage {
    fn write(
        data: &Path,
        project: &Path,
        configured: Option<&str>,
        bytes: &[u8],
    ) -> io::Result<Self> {
        let (root, relative) = match configured {
            Some(directory) if Path::new(directory).is_absolute() => {
                // A deployment-configured absolute quarantine is an operator-owned path.
                std::fs::create_dir_all(directory)?;
                (Path::new(directory).canonicalize()?, None)
            }
            Some(directory) => (
                project
                    .parent()
                    .ok_or(ErrorKind::InvalidInput)?
                    .canonicalize()?,
                Some(directory),
            ),
            None => (data.canonicalize()?, Some("upload_quarantine")),
        };
        let directory = open_canonical_dir(&root)?;
        let (directory, path) = match relative {
            Some(relative) => (
                assets::subdir(&directory, relative, true)?,
                root.join(relative),
            ),
            None => (directory, root),
        };
        let name = format!("upload-{}", Uuid::new_v4().simple());
        publish(&directory, &name, bytes, false)?;
        Ok(Self {
            directory,
            path: path.join(&name),
            name,
        })
    }
}

impl Drop for Stage {
    fn drop(&mut self) {
        let _ = self.directory.remove_file(&self.name);
    }
}
