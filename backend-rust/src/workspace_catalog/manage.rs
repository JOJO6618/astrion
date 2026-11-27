use std::{
    collections::HashSet,
    fmt,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Component, Path, PathBuf},
    sync::Mutex,
};

use serde_json::{Value, json};
use uuid::Uuid;

use super::{HostCatalog, RegisteredWorkspace};

static CATALOG_WRITES: Mutex<()> = Mutex::new(());

#[derive(Clone)]
pub struct WorkspaceRegistry {
    path: PathBuf,
    resources: PathBuf,
}

pub struct RegistryChange {
    pub catalog: HostCatalog,
    pub details: Value,
}

#[derive(Debug)]
pub enum RegistryError {
    Invalid(&'static str),
    Missing,
    Storage(io::Error),
}

impl fmt::Display for RegistryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Invalid(message) => formatter.write_str(message),
            Self::Missing => formatter.write_str("Workspace does not exist"),
            Self::Storage(error) => write!(formatter, "Workspace catalog unavailable: {error}"),
        }
    }
}

impl std::error::Error for RegistryError {}

impl From<io::Error> for RegistryError {
    fn from(error: io::Error) -> Self {
        Self::Storage(error)
    }
}

impl WorkspaceRegistry {
    pub fn new(path: PathBuf, resources: PathBuf) -> Self {
        Self { path, resources }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self) -> io::Result<HostCatalog> {
        Ok(HostCatalog::load(&self.path, &self.resources)?.unwrap_or_default())
    }

    /// Register Default on the first Web launch only. The marker and the
    /// registration are committed together, and deleting the last workspace
    /// sets the same marker so later launches respect that choice.
    pub fn ensure_default_workspace(&self) -> Result<(), RegistryError> {
        self.update(|raw| {
            if raw["auto_default_workspace"].as_bool() == Some(true)
                || raw.get("workspaces").is_some_and(|entries| {
                    entries.as_array().is_none_or(|entries| !entries.is_empty())
                })
            {
                return Ok((Value::Null, false));
            }
            let Some(directory) = super::default_workspace_path() else {
                return Ok((Value::Null, false));
            };
            let directory = directory.to_str().ok_or(RegistryError::Invalid(
                "Default workspace path is not valid Unicode",
            ))?;
            let directory = resolve_path(directory, &self.resources)?;
            fs::create_dir_all(&directory)?;
            let workspace = RegisteredWorkspace {
                id: "default".into(),
                label: "Default".into(),
                path: directory,
            };
            entries_mut(raw).push(workspace_json(&workspace));
            raw["default_workspace_id"] = workspace.id.into();
            raw["auto_default_workspace"] = true.into();
            Ok((Value::Null, true))
        })?;
        Ok(())
    }

    pub fn create(
        &self,
        path: &str,
        label: &str,
        set_default: bool,
    ) -> Result<RegistryChange, RegistryError> {
        let path = path.trim();
        if path.is_empty() {
            return Err(RegistryError::Invalid("Workspace path is required"));
        }
        self.update(|raw| {
            let directory = resolve_path(path, &self.resources)?;
            let catalog = HostCatalog::from_raw(raw, &self.resources)?;
            fs::create_dir_all(&directory)?;
            if let Some(previous) = catalog
                .workspaces
                .iter()
                .find(|entry| entry.path == directory)
            {
                return Ok((
                    json!({"created":false,"workspace":workspace_json(previous)}),
                    false,
                ));
            }
            let path_label = directory
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("");
            let label = label.trim();
            let seed = if label.is_empty() { path_label } else { label };
            let base = slugify(seed);
            let ids: HashSet<_> = catalog
                .workspaces
                .iter()
                .map(|entry| entry.id.as_str())
                .collect();
            let id = available_id(base, &ids);
            let label = if seed.is_empty() {
                id.clone()
            } else {
                seed.to_owned()
            };
            let workspace = workspace_json(&RegisteredWorkspace {
                id: id.clone(),
                label,
                path: directory,
            });
            entries_mut(raw).push(workspace.clone());
            if set_default
                || text(raw, "default_workspace_id").is_none()
                || catalog.workspaces.is_empty()
            {
                raw["default_workspace_id"] = id.into();
            }
            Ok((json!({"created":true,"workspace":workspace}), true))
        })
    }

    pub fn rename(&self, id: &str, label: &str) -> Result<RegistryChange, RegistryError> {
        let label = label.trim();
        if label.is_empty() {
            return Err(RegistryError::Invalid("Workspace name is required"));
        }
        self.update(|raw| {
            let entries = entries_mut(raw);
            for (index, item) in entries.iter_mut().enumerate() {
                if let Some(entry) = normalize_entry(item, index, &self.resources)?
                    && entry.id == id
                {
                    item["workspace_id"] = entry.id.into();
                    item["label"] = label.into();
                    item["path"] = json!(entry.path.to_string_lossy());
                    return Ok((json!({"workspace":item}), true));
                }
            }
            Err(RegistryError::Missing)
        })
    }

    pub fn delete(&self, id: &str) -> Result<RegistryChange, RegistryError> {
        self.update(|raw| {
            let entries = entries_mut(raw);
            let mut matching = Vec::new();
            for (index, item) in entries.iter().enumerate() {
                if normalize_entry(item, index, &self.resources)?
                    .is_some_and(|entry| entry.id == id)
                {
                    matching.push(index);
                }
            }
            if matching.is_empty() {
                return Err(RegistryError::Missing);
            }
            for index in matching.into_iter().rev() {
                entries.remove(index);
            }
            let remaining = HostCatalog::from_raw(raw, &self.resources)?;
            if remaining.workspaces.is_empty() {
                raw["default_workspace_id"] = "".into();
                raw["auto_default_workspace"] = true.into();
            } else if text(raw, "default_workspace_id") == Some(id) {
                raw["default_workspace_id"] =
                    remaining.default_workspace_id.unwrap_or_default().into();
            }
            Ok((json!({"deleted_workspace_id":id}), true))
        })
    }

    pub fn set_default(&self, id: &str) -> Result<RegistryChange, RegistryError> {
        self.update(|raw| {
            if !HostCatalog::from_raw(raw, &self.resources)?
                .workspaces
                .iter()
                .any(|entry| entry.id == id)
            {
                return Err(RegistryError::Missing);
            }
            raw["default_workspace_id"] = id.into();
            Ok((json!({}), true))
        })
    }

    fn update(
        &self,
        edit: impl FnOnce(&mut Value) -> Result<(Value, bool), RegistryError>,
    ) -> Result<RegistryChange, RegistryError> {
        let _guard = CATALOG_WRITES
            .lock()
            .map_err(|_| io::Error::other("Workspace catalog lock unavailable"))?;
        let mut raw = match fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice::<Value>(&bytes).map_err(io::Error::from)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                json!({"default_workspace_id":"", "workspaces":[]})
            }
            Err(error) => return Err(error.into()),
        };
        if !raw.is_object() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Expected a workspace catalog object",
            )
            .into());
        }
        let (details, changed) = edit(&mut raw)?;
        let catalog = HostCatalog::from_raw(&raw, &self.resources)?;
        if changed {
            write_atomically(&self.path, &raw)?;
        }
        Ok(RegistryChange { catalog, details })
    }
}

pub(super) fn normalize_entry(
    item: &Value,
    index: usize,
    resources: &Path,
) -> io::Result<Option<RegisteredWorkspace>> {
    let Some(path) = text(item, "path") else {
        return Ok(None);
    };
    let id = text(item, "workspace_id")
        .or_else(|| text(item, "id"))
        .or_else(|| text(item, "name"))
        .filter(|id| valid_id(id))
        .map(str::to_owned)
        .unwrap_or_else(|| format!("workspace_{}", index + 1));
    let label = text(item, "label")
        .or_else(|| text(item, "name"))
        .unwrap_or(&id)
        .to_owned();
    Ok(Some(RegisteredWorkspace {
        id,
        label,
        path: resolve_path(path, resources)?,
    }))
}

pub(super) fn text<'a>(item: &'a Value, field: &str) -> Option<&'a str> {
    item[field]
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn valid_id(id: &str) -> bool {
    id.len() <= 64
        && !id.is_empty()
        && !matches!(id, "." | "..")
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
}

fn slugify(label: &str) -> String {
    let mut slug = String::new();
    for character in label.chars() {
        let next = if character.is_ascii_alphanumeric() || matches!(character, '.' | '_') {
            character.to_ascii_lowercase()
        } else {
            '-'
        };
        if next != '-' || !slug.ends_with('-') {
            slug.push(next);
        }
    }
    let trimmed = slug.trim_matches(['-', '.', '_']);
    if valid_id(trimmed) {
        trimmed.to_owned()
    } else {
        "workspace".into()
    }
}

fn available_id(base: String, taken: &HashSet<&str>) -> String {
    if !taken.contains(base.as_str()) {
        return base;
    }
    for sequence in 2.. {
        let suffix = format!("-{sequence}");
        let candidate = format!("{}{}", &base[..base.len().min(64 - suffix.len())], suffix);
        if !taken.contains(candidate.as_str()) {
            return candidate;
        }
    }
    unreachable!("the workspace ID space cannot be exhausted in memory")
}

fn workspace_json(workspace: &RegisteredWorkspace) -> Value {
    json!({"workspace_id":workspace.id, "label":workspace.label, "path":workspace.path.to_string_lossy()})
}

fn entries_mut(raw: &mut Value) -> &mut Vec<Value> {
    if !raw["workspaces"].is_array() {
        raw["workspaces"] = Value::Array(Vec::new());
    }
    raw["workspaces"]
        .as_array_mut()
        .expect("workspace entries array")
}

fn resolve_path(path: &str, resources: &Path) -> io::Result<PathBuf> {
    let path = if path == "~" || path.starts_with("~/") || cfg!(windows) && path.starts_with("~\\")
    {
        std::env::home_dir()
            .ok_or_else(|| io::Error::other("Cannot resolve workspace home directory"))?
            .join(path[1..].trim_start_matches(['/', '\\']))
    } else {
        resources.join(path)
    };
    let absolute = std::path::absolute(path)?;
    for ancestor in absolute.ancestors() {
        match ancestor.canonicalize() {
            Ok(mut normalized) => {
                let relative = absolute
                    .strip_prefix(ancestor)
                    .expect("an ancestor is a path prefix");
                for part in relative.components() {
                    match part {
                        Component::ParentDir => {
                            normalized.pop();
                        }
                        Component::Normal(value) => normalized.push(value),
                        _ => {}
                    }
                }
                return Ok(normalized);
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "Cannot resolve workspace path",
    ))
}

fn write_atomically(path: &Path, raw: &Value) -> io::Result<()> {
    let path = std::path::absolute(path)?;
    let directory = path
        .parent()
        .ok_or_else(|| io::Error::other("Workspace catalog parent is missing"))?;
    fs::create_dir_all(directory)?;
    let scratch = directory.join(format!(".host-workspaces-{}.tmp", Uuid::new_v4()));
    let result = (|| {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(&scratch)?;
        let mut writer = io::BufWriter::new(file);
        serde_json::to_writer_pretty(&mut writer, raw)?;
        writer.write_all(b"\n")?;
        writer.flush()?;
        writer.get_ref().sync_all()?;
        drop(writer);
        replace_file(&scratch, &path)?;
        #[cfg(unix)]
        File::open(directory)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(scratch);
    }
    result
}

fn replace_file(source: &Path, target: &Path) -> io::Result<()> {
    #[cfg(windows)]
    for delay in [10, 20, 40, 80] {
        match fs::rename(source, target) {
            Err(error) if matches!(error.raw_os_error(), Some(5 | 32 | 33)) => {
                std::thread::sleep(std::time::Duration::from_millis(delay));
            }
            result => return result,
        }
    }
    fs::rename(source, target)
}
