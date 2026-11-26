//! A durable undo log for changes spanning checkpoint records, workspace files and the Agent document.

use std::{
    io,
    path::{Component, Path, PathBuf},
};

use base64::{Engine, engine::general_purpose::STANDARD};
use cap_std::fs::Dir;
use ring::digest::{SHA256, digest};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::{RuntimeError, Store, files, open_data_directory, storage};
use crate::legacy::LegacyDocument;

const JOURNAL: &str = ".versioning-transaction.json";

#[derive(Clone, Deserialize, PartialEq, Eq, Serialize)]
pub(super) struct FileState {
    #[serde(with = "encoded")]
    pub bytes: Vec<u8>,
    pub mode: Option<u32>,
}

impl FileState {
    pub fn data(bytes: Vec<u8>) -> Self {
        Self { bytes, mode: None }
    }
    pub fn workspace(bytes: Vec<u8>, mode: Option<u32>) -> Self {
        Self { bytes, mode }
    }
}

mod encoded {
    use super::*;
    use serde::{Deserializer, Serializer, de::Error};

    pub fn serialize<S: Serializer>(bytes: &[u8], serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&STANDARD.encode(bytes))
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(deserializer: D) -> Result<Vec<u8>, D::Error> {
        STANDARD
            .decode(String::deserialize(deserializer)?)
            .map_err(D::Error::custom)
    }
}

#[derive(Clone, Copy, Deserialize, PartialEq, Eq, Serialize)]
pub(super) enum Scope {
    Records,
    Workspace,
}

#[derive(Deserialize, Serialize)]
struct Change {
    scope: Scope,
    path: String,
    before: Option<FileState>,
    after: Option<FileState>,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Journal {
    workspace_id: String,
    source_id: String,
    target_id: String,
    execution_root: Option<PathBuf>,
    before: Option<Vec<u8>>,
    after: Vec<u8>,
    #[serde(default)]
    versioning_only: bool,
    changes: Vec<Change>,
    created_workspace_dirs: Vec<String>,
}

impl Journal {
    pub fn affects_workspace(&self) -> bool {
        self.execution_root.is_some()
    }
}

pub(super) struct Transaction<'a> {
    marker: &'a Dir,
    records: &'a Dir,
    workspace: Option<Dir>,
    journal: Journal,
}

impl<'a> Transaction<'a> {
    pub fn new(
        marker: &'a Dir,
        records: &'a Dir,
        workspace_id: &str,
        source_id: &str,
        execution_root: Option<&Path>,
        previous: Option<&Value>,
        next: &LegacyDocument,
    ) -> Result<Self, RuntimeError> {
        let workspace = execution_root
            .map(open_data_directory)
            .transpose()
            .map_err(storage)?;
        Ok(Self {
            marker,
            records,
            workspace,
            journal: Journal {
                workspace_id: workspace_id.into(),
                source_id: source_id.into(),
                target_id: next.source_id().into(),
                execution_root: execution_root.map(Path::to_owned),
                before: previous.map(fingerprint).transpose().map_err(storage)?,
                after: fingerprint(next.raw()).map_err(storage)?,
                versioning_only: false,
                changes: Vec::new(),
                created_workspace_dirs: Vec::new(),
            },
        })
    }

    pub fn for_versioning(
        marker: &'a Dir, records: &'a Dir, workspace_id: &str, id: &str, before: &Value, after: &Value,
    ) -> Result<Self, RuntimeError> {
        Ok(Self { marker, records, workspace: None, journal: Journal {
            workspace_id: workspace_id.into(), source_id: id.into(), target_id: id.into(), execution_root: None,
            before: Some(fingerprint(before).map_err(storage)?), after: fingerprint(after).map_err(storage)?,
            versioning_only: true, changes: Vec::new(), created_workspace_dirs: Vec::new(),
        } })
    }

    pub fn change(&mut self, scope: Scope, path: &str, after: Option<FileState>) -> io::Result<()> {
        if self
            .journal
            .changes
            .iter()
            .any(|change| change.scope == scope && change.path == path)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Duplicate versioning destination",
            ));
        }
        let workspace = scope == Scope::Workspace;
        let base = self.base(scope)?;
        validate_path(path, workspace)?;
        let before = capture(base, path, workspace)?;
        if before == after {
            return Ok(());
        }
        if workspace && after.is_some() {
            for missing in missing_parents(base, path)? {
                if !self.journal.created_workspace_dirs.contains(&missing) {
                    self.journal.created_workspace_dirs.push(missing);
                }
            }
        }
        self.journal.changes.push(Change {
            scope,
            path: path.into(),
            before,
            after,
        });
        Ok(())
    }

    pub fn put(&mut self, path: &str, bytes: Vec<u8>) -> io::Result<()> {
        self.change(Scope::Records, path, Some(FileState::data(bytes)))
    }

    pub fn commit(self, store: &Store, document: LegacyDocument) -> Result<(), RuntimeError> {
        self.commit_with(store, |journal| store.persist(&journal.workspace_id, document, journal.before.is_none()))
    }

    pub fn commit_versioning(self, store: &Store, config: Value) -> Result<(), RuntimeError> {
        self.commit_with(store, |journal| {
            store.update_agent_metadata(&journal.workspace_id, &journal.target_id, |metadata| {
                let current = fingerprint(&metadata["versioning"]).map_err(storage)?;
                if journal.before.as_ref() != Some(&current) {
                    return Err(RuntimeError::new(409, "The conversation version setting changed during checkpointing"));
                }
                metadata["versioning"] = config;
                Ok(())
            })?
        })
    }

    fn commit_with(self, store: &Store, publish: impl FnOnce(&Journal) -> Result<(), RuntimeError>) -> Result<(), RuntimeError> {
        self.record_intent()?;
        let written = self
            .install(true)
            .and_then(|()| {
                if !self.matches_after()? {
                    return Err(io::Error::other(
                        "Versioning destinations could not be verified",
                    ));
                }
                Ok(())
            })
            .map_err(storage).and_then(|()| publish(&self.journal));
        match written {
            Ok(()) => self.clear(),
            Err(error) => {
                if let Err(recovery) = self.recover(store) {
                    return Err(RuntimeError::new(
                        500,
                        format!("{error}. Versioning recovery remains pending: {recovery}"),
                    ));
                }
                Err(error)
            }
        }
    }

    fn record_intent(&self) -> Result<(), RuntimeError> {
        if pending(self.marker)?.is_some() {
            return Err(RuntimeError::new(409, "Versioning recovery is pending"));
        }
        if self.journal.affects_workspace() {
            if pending_workspace(self.records, &self.journal.workspace_id)?.is_some() {
                return Err(RuntimeError::new(
                    409,
                    "Workspace versioning recovery is pending",
                ));
            }
            files::write_json(
                self.records,
                &workspace_marker(&self.journal.workspace_id),
                &self.journal.source_id,
            )
            .map_err(storage)?;
        }
        files::write_json(self.marker, JOURNAL, &self.journal).map_err(storage)
    }

    #[cfg(test)]
    pub fn interrupt_after(&self, steps: usize) -> Result<(), RuntimeError> {
        self.record_intent()?;
        for change in self.journal.changes.iter().take(steps) {
            self.apply(change, true).map_err(storage)?;
        }
        Ok(())
    }

    fn recover(&self, store: &Store) -> Result<(), RuntimeError> {
        let value = if self.journal.versioning_only {
            store.header(&self.journal.workspace_id, &self.journal.target_id)
                .map(|(header, _)| header["metadata"]["versioning"].clone())
        } else {
            store.read(&self.journal.workspace_id, &self.journal.target_id).map(LegacyDocument::into_raw)
        };
        let current = match value {
            Ok(value) => Some(fingerprint(&value).map_err(storage)?),
            Err(error) if error.status == 404 => None,
            Err(error) => return Err(error),
        };
        let after = current.as_ref() == Some(&self.journal.after);
        let before = current == self.journal.before;
        if !before && !after {
            return Err(RuntimeError::new(
                409,
                "The conversation changed during versioning recovery",
            ));
        }
        // If an operation leaves the conversation identical, the files determine whether
        // it reached the commit boundary. A partial operation is rolled back in that case.
        let committed = after && (!before || self.matches_after().map_err(storage)?);
        self.install(committed).map_err(storage)?;
        if !committed && let Some(base) = &self.workspace {
            for path in self.journal.created_workspace_dirs.iter().rev() {
                validate_path(path, true).map_err(storage)?;
                remove_empty_dir(base, path).map_err(storage)?;
            }
        }
        self.clear()
    }

    fn matches_after(&self) -> io::Result<bool> {
        for change in &self.journal.changes {
            let base = self.base(change.scope)?;
            if capture(base, &change.path, change.scope == Scope::Workspace)? != change.after {
                return Ok(false);
            }
        }
        Ok(true)
    }

    fn install(&self, forward: bool) -> io::Result<()> {
        if forward {
            for change in &self.journal.changes {
                self.apply(change, true)?;
            }
        } else {
            for change in self.journal.changes.iter().rev() {
                self.apply(change, false)?;
            }
        }
        Ok(())
    }

    fn apply(&self, change: &Change, forward: bool) -> io::Result<()> {
        let base = self.base(change.scope)?;
        let workspace = change.scope == Scope::Workspace;
        let (old, new) = if forward {
            (&change.before, &change.after)
        } else {
            (&change.after, &change.before)
        };
        let current = capture(base, &change.path, workspace)?;
        if &current == new {
            return Ok(());
        }
        if &current != old {
            return Err(io::Error::other(
                "A versioning destination changed during recovery",
            ));
        }
        let (parent, name) = open_parent(base, &change.path, new.is_some())?.ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "Versioning destination disappeared",
            )
        })?;
        match new {
            Some(file) => files::write_mode(&parent, &name, &file.bytes, file.mode),
            None => {
                parent.remove_file(&name)?;
                files::sync(&parent)
            }
        }
    }

    fn base(&self, scope: Scope) -> io::Result<&Dir> {
        match scope {
            Scope::Records => Ok(self.records),
            Scope::Workspace => self
                .workspace
                .as_ref()
                .ok_or_else(|| io::Error::other("No workspace scope for versioning")),
        }
    }

    fn clear(&self) -> Result<(), RuntimeError> {
        self.marker.remove_file(JOURNAL).map_err(storage)?;
        files::sync(self.marker).map_err(storage)?;
        if self.journal.affects_workspace() {
            clear_workspace(self.records, &self.journal.workspace_id)?;
        }
        Ok(())
    }
}

fn workspace_marker(workspace_id: &str) -> String {
    format!(".versioning-workspace-{workspace_id}.json")
}

pub(super) fn pending_workspace(
    records: &Dir,
    workspace_id: &str,
) -> Result<Option<String>, RuntimeError> {
    files::read_json(records, &workspace_marker(workspace_id)).map_err(storage)
}

pub(super) fn clear_workspace(records: &Dir, workspace_id: &str) -> Result<(), RuntimeError> {
    match records.remove_file(workspace_marker(workspace_id)) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => (),
        result => result.map_err(storage)?,
    }
    files::sync(records).map_err(storage)
}

pub(super) fn pending(marker: &Dir) -> Result<Option<Journal>, RuntimeError> {
    files::read_json(marker, JOURNAL).map_err(storage)
}

pub(super) fn recover(
    store: &Store,
    marker: &Dir,
    records: &Dir,
    workspace_id: &str,
    source_id: &str,
    bound_root: &Path,
    journal: Journal,
) -> Result<(), RuntimeError> {
    if workspace_id != journal.workspace_id
        || source_id != journal.source_id
        || journal
            .execution_root
            .as_deref()
            .is_some_and(|root| root != bound_root)
    {
        return Err(RuntimeError::new(
            409,
            "Versioning recovery belongs to a different workspace binding",
        ));
    }
    store.conversation_path(workspace_id, &journal.target_id)?;
    let workspace = journal
        .execution_root
        .as_deref()
        .map(open_data_directory)
        .transpose()
        .map_err(storage)?;
    Transaction {
        marker,
        records,
        workspace,
        journal,
    }
    .recover(store)
}

pub(super) fn validate_path(path: &str, workspace: bool) -> io::Result<()> {
    let invalid_part = |part: Component<'_>| match part {
        Component::Normal(part) => {
            let text = part.to_string_lossy();
            workspace
                && (text.eq_ignore_ascii_case(".git") || text.eq_ignore_ascii_case(".astrion"))
        }
        _ => true,
    };
    if path.is_empty()
        || path.contains(['\\', ':', '\0'])
        || path.split('/').any(|part| matches!(part, "" | "." | ".."))
        || Path::new(path).components().any(invalid_part)
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Invalid versioning destination",
        ));
    }
    Ok(())
}

pub(super) fn capture(base: &Dir, path: &str, workspace: bool) -> io::Result<Option<FileState>> {
    validate_path(path, workspace)?;
    let Some((parent, name)) = open_parent(base, path, false)? else {
        return Ok(None);
    };
    let Some(bytes) = files::read(&parent, &name)? else {
        return Ok(None);
    };
    #[cfg(unix)]
    let mode = if workspace {
        use cap_std::fs::MetadataExt;
        Some(parent.symlink_metadata(&name)?.mode() & 0o777)
    } else {
        None
    };
    #[cfg(not(unix))]
    let mode = None;
    Ok(Some(FileState { bytes, mode }))
}

fn open_parent(base: &Dir, path: &str, create: bool) -> io::Result<Option<(Dir, String)>> {
    let mut parts = path.rsplitn(2, '/');
    let name = parts.next().expect("path has filename").to_owned();
    let mut parent = base.try_clone()?;
    if let Some(path) = parts.next() {
        for component in path.split('/') {
            let Some(child) = files::child(&parent, component, create)? else {
                return Ok(None);
            };
            parent = child;
        }
    }
    Ok(Some((parent, name)))
}

fn missing_parents(base: &Dir, path: &str) -> io::Result<Vec<String>> {
    let mut missing = Vec::new();
    let Some((parent, _)) = path.rsplit_once('/') else {
        return Ok(missing);
    };
    let mut directory = Some(base.try_clone()?);
    let mut relative = String::new();
    for part in parent.split('/') {
        if !relative.is_empty() {
            relative.push('/');
        }
        relative.push_str(part);
        if let Some(base) = directory.take() {
            directory = files::child(&base, part, false)?;
        }
        if directory.is_none() {
            missing.push(relative.clone());
        }
    }
    Ok(missing)
}

fn remove_empty_dir(base: &Dir, path: &str) -> io::Result<()> {
    if let Some((parent, name)) = open_parent(base, path, false)? {
        match parent.remove_dir(&name) {
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::NotFound | io::ErrorKind::DirectoryNotEmpty
                ) =>
            {
                ()
            }
            result => result?,
        }
        files::sync(&parent)?;
    }
    Ok(())
}

fn fingerprint(raw: &Value) -> io::Result<Vec<u8>> {
    Ok(digest(&SHA256, &serde_json::to_vec(raw)?).as_ref().to_vec())
}
