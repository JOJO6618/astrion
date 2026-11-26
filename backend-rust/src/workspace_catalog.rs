//! Read and update the Host workspace catalog in the existing Python format.

pub mod manage;

use serde_json::Value;
use std::{
    collections::HashSet,
    fs, io,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug)]
pub struct RegisteredWorkspace {
    pub id: String,
    pub label: String,
    pub path: PathBuf,
}

#[derive(Default)]
pub struct HostCatalog {
    pub workspaces: Vec<RegisteredWorkspace>,
    pub default_workspace_id: Option<String>,
}

impl HostCatalog {
    pub fn load(registry: &Path, resources: &Path) -> io::Result<Option<Self>> {
        let raw: Value = match fs::read(registry) {
            Ok(bytes) => serde_json::from_slice(&bytes)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
        };
        Self::from_raw(&raw, resources).map(Some)
    }

    fn from_raw(raw: &Value, resources: &Path) -> io::Result<Self> {
        if !raw.is_object() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Expected a workspace catalog object",
            ));
        }
        let mut seen = HashSet::new();
        let mut workspaces = Vec::new();
        if let Some(entries) = raw["workspaces"].as_array() {
            for (index, entry) in entries.iter().enumerate() {
                if let Some(workspace) = manage::normalize_entry(entry, index, resources)?
                    && seen.insert(workspace.id.clone())
                {
                    workspaces.push(workspace);
                }
            }
        }
        let mut catalog = Self {
            workspaces,
            default_workspace_id: None,
        };
        catalog.default_workspace_id = manage::text(raw, "default_workspace_id")
            .filter(|id| seen.contains(*id))
            .map(str::to_owned)
            .or_else(|| {
                catalog
                    .workspaces
                    .first()
                    .map(|workspace| workspace.id.clone())
            });
        Ok(catalog)
    }

    pub fn for_directory(&self, path: &Path) -> Option<&RegisteredWorkspace> {
        self.workspaces
            .iter()
            .filter(|workspace| path.starts_with(&workspace.path))
            .max_by_key(|workspace| workspace.path.components().count())
    }

    pub fn by_id(&self, id: &str) -> Option<&RegisteredWorkspace> {
        self.workspaces.iter().find(|workspace| workspace.id == id)
    }

    pub fn default_workspace(&self) -> Option<&RegisteredWorkspace> {
        self.by_id(self.default_workspace_id.as_deref()?)
    }
}
