//! Resumable child-agent execution and authenticated activity views.
mod activity;
mod casefold;
mod communication;
mod execution;
mod mcp;
mod migration;
pub(crate) mod protocol;
mod registry;
mod routes;
mod scope;
mod worker;

pub(crate) use execution::{Create, Parent};
pub(crate) use migration::{legacy_pending, migrate_legacy};
pub(crate) use scope::ParentAccess;

use std::{
    io,
    path::{Path, PathBuf},
    sync::Arc,
};

use serde_json::{Value, json};

use crate::{runtime::Runtime, workspace_catalog::RegisteredWorkspace};
use registry::{Record, Records, Registry};
use routes::RouteError;

/// Browser and Gateway readers share the same registry and Runtime binding.
#[derive(Clone)]
pub struct SubAgents {
    runtime: Runtime,
    registry: Arc<Registry>,
    execution: Arc<execution::Execution>,
    live: Arc<LiveRecords>,
}

type LiveRecords = dyn Fn(&RegisteredWorkspace, &str) -> io::Result<Vec<Value>> + Send + Sync;

impl SubAgents {
    pub fn new(data_dir: impl AsRef<Path>, runtime: Runtime) -> Self {
        Self {
            runtime,
            registry: Arc::new(Registry::new(data_dir.as_ref().to_owned())),
            execution: Arc::default(),
            live: Arc::new(|_, _| Ok(Vec::new())),
        }
    }

    /// Supply task records from the active supervisor, scoped to the registered
    /// workspace and canonical parent ID. Owned live records take precedence.
    pub fn with_live_records(
        mut self,
        read: impl Fn(&RegisteredWorkspace, &str) -> io::Result<Vec<Value>> + Send + Sync + 'static,
    ) -> Self {
        self.live = Arc::new(read);
        self
    }

    /// Report only live children owned by the registered workspace and conversation.
    /// The persisted registry can retain a stale running status after a restart.
    pub fn has_running(&self, workspace: &RegisteredWorkspace, parent: &str) -> io::Result<bool> {
        let parent = normalize_conversation(parent).ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidInput, "Invalid parent conversation")
        })?;
        let bound = self
            .check_workspace(workspace)
            .map_err(|_| io::Error::other("Child activity workspace is unavailable"))?;
        Ok(self
            .live_records(workspace, &parent, &bound)?
            .values()
            .any(running))
    }

    /// The caller already holds the runtime lock and verified the workspace.
    pub(crate) fn has_running_for_bound(&self, workspace: &RegisteredWorkspace, parent: &str) -> io::Result<bool> {
        Ok(self.live_records(workspace, parent, &workspace.path)?.values().any(running))
    }

    pub(crate) fn activity_for_bound_parent(
        &self, workspace: &RegisteredWorkspace, parent: &Parent,
    ) -> io::Result<(bool, bool)> {
        let running = self.live_records(workspace, &parent.conversation_id, &parent.root)?
            .values().any(running);
        let pending = self.pending_notices(parent).map_err(io::Error::other)?;
        Ok((running, !pending.is_empty()))
    }

    fn check_workspace(&self, workspace: &RegisteredWorkspace) -> Result<PathBuf, RouteError> {
        let bound = self
            .runtime
            .bound_workspace(&workspace.id)
            .map_err(RouteError::storage)?
            .ok_or_else(RouteError::workspace_missing)?;
        let current = workspace
            .path
            .canonicalize()
            .map_err(|_| RouteError::workspace_missing())?;
        if current != bound || !current.is_dir() {
            return Err(RouteError::workspace_missing());
        }
        Ok(bound)
    }

    fn records(
        &self,
        workspace: &RegisteredWorkspace,
        parent: &str,
    ) -> Result<Records, RouteError> {
        let bound = self.check_workspace(workspace)?;
        if self
            .runtime
            .navigation_session(&workspace.id, parent)
            .map_err(RouteError::storage)?
            .is_none()
        {
            return Err(RouteError::parent_missing());
        }
        let stored = self.registry.snapshot(parent).map_err(RouteError::storage)?;
        let mut records = Records::new();
        if let Some(rows) = stored.get(parent) {
            records.extend(
                rows.iter()
                    .filter(|(_, record)| owns_path(record, &bound))
                    .map(|(id, record)| (id.clone(), disk_record(record))),
            );
        }
        records.extend(
            self.live_records(workspace, parent, &bound)
                .map_err(RouteError::storage)?,
        );
        Ok(records)
    }

    fn live_records(
        &self,
        workspace: &RegisteredWorkspace,
        parent: &str,
        bound: &Path,
    ) -> io::Result<Records> {
        let mut records: Records = (self.live)(workspace, parent)?
            .into_iter()
            .filter_map(|row| {
                let Value::Object(mut record) = row else {
                    return None;
                };
                let id = string(&record, "task_id")
                    .filter(|id| valid_task_id(id))?
                    .to_owned();
                if !owns_path(&record, bound)
                    || string(&record, "conversation_id")
                        .and_then(normalize_conversation)
                        .as_deref()
                        != Some(parent)
                {
                    return None;
                }
                record.insert("conversation_id".into(), json!(parent));
                Some((id, record))
            })
            .collect();
        let live = self.lock().map_err(io::Error::other)?;
        if !live.workers.is_empty() {
            let stored = self.registry.snapshot(parent)?;
            if let Some(rows) = stored.get(parent) {
                records.extend(rows.iter().filter(|(id, record)| {
                    live.workers.contains_key(*id) && owns_path(record, bound)
                }).map(|(id, record)| (id.clone(), record.clone())));
            }
        }
        Ok(records)
    }
}

pub(crate) fn historical_registry(data: &Path) -> io::Result<Value> {
    Registry::new(data.to_owned()).document()
}

fn string<'a>(record: &'a Record, key: &str) -> Option<&'a str> {
    record.get(key).and_then(Value::as_str)
}

fn running(record: &Record) -> bool {
    matches!(
        string(record, "status"),
        Some("running" | "pending" | "starting" | "cancel_requested" | "in_progress")
    )
}

fn disk_record(record: &Record) -> Record {
    let mut record = record.clone();
    if running(&record) {
        record.insert("status".into(), json!("stopped"));
    }
    record
}

fn owns_path(record: &Record, bound: &Path) -> bool {
    string(record, "workspace_root").is_some_and(|path| same_path(Path::new(path), bound))
}

#[cfg(not(windows))]
fn same_path(recorded: &Path, bound: &Path) -> bool {
    recorded == bound
}

#[cfg(windows)]
fn same_path(recorded: &Path, bound: &Path) -> bool {
    fn windows_path(path: &Path) -> String {
        let path = path.to_string_lossy();
        if let Some(unc) = path.strip_prefix(r"\\?\UNC\") {
            format!(r"\\{unc}").to_lowercase()
        } else {
            path.strip_prefix(r"\\?\").unwrap_or(&path).to_lowercase()
        }
    }
    windows_path(recorded) == windows_path(bound)
}

fn normalize_conversation(raw: &str) -> Option<String> {
    let raw = raw.trim();
    if raw.is_empty() || raw.len() > 180 || !safe_id(raw) {
        return None;
    }
    Some(if raw.starts_with("conv_") {
        raw.into()
    } else {
        format!("conv_{raw}")
    })
}

fn valid_task_id(id: &str) -> bool {
    id.len() <= 100
        && id
            .strip_prefix("sub_")
            .is_some_and(|suffix| !suffix.is_empty() && safe_id(suffix))
}

fn safe_id(id: &str) -> bool {
    id.bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/sub_agents_contract.rs"]
mod tests;
