use super::schema::{Object, normalize};
use crate::catalog::Catalog;
use serde_json::Value;
use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

const PERSONAL_FILE: &str = "personalization.json";
const SERVICE_KEYS: &[&str] = &[
    "search_provider",
    "webpage_extract_provider",
    "webpage_direct_extract_enabled",
    "webpage_direct_extract_domains",
    "searxng_base_url",
];
const SECRET_KEYS: &[&str] = &[
    "tavily_api_key",
    "bocha_api_key",
    "exa_api_key",
    "parallel_api_key",
    "jina_api_key",
];

pub(super) struct Store {
    personal: PathBuf,
    services: PathBuf,
    secrets: PathBuf,
}

impl Store {
    pub(super) fn open(data: &Path) -> io::Result<Self> {
        let mut personal = data.join(PERSONAL_FILE);
        if fs::symlink_metadata(&personal).is_ok_and(|metadata| metadata.is_symlink()) {
            // Keep the shared personal file and its service/secret stores together.
            personal = personal.canonicalize()?;
        }
        let directory = personal.parent().expect("personalization file parent");
        Ok(Self {
            services: directory.join("service_config.json"),
            secrets: directory.join("secrets.json"),
            personal,
        })
    }

    pub(super) fn read(&self, catalog: &Catalog) -> io::Result<Object> {
        let merged = self.merged()?;
        // Reading is pure: named stores take precedence, but a damaged document
        // or a legacy combined file is never rewritten by GET.
        normalize(&merged, &Object::new(), catalog, false).map_err(io::Error::other)
    }

    fn merged(&self) -> io::Result<Object> {
        let personal = read_strict(&self.personal)?.unwrap_or_default();
        let services = read_strict(&self.services)?.unwrap_or_default();
        let secrets = read_strict(&self.secrets)?.unwrap_or_default();
        let mut merged = personal.clone();
        for key in SERVICE_KEYS {
            if let Some(value) = services.get(*key) {
                merged.insert((*key).to_owned(), value.clone());
            }
        }
        for key in SECRET_KEYS {
            if let Some(value) = secrets
                .get(*key)
                .filter(|value| value.as_str().is_some_and(|key| !key.trim().is_empty()))
            {
                merged.insert((*key).to_owned(), value.clone());
            }
        }
        Ok(merged)
    }

    pub(super) fn title_preferences(&self) -> io::Result<(bool, String)> {
        let personal = read_strict(&self.personal)?.unwrap_or_default();
        Ok((
            personal.get("auto_generate_title").and_then(Value::as_bool).unwrap_or(true),
            personal.get("title_model").and_then(Value::as_str).unwrap_or_default().to_owned(),
        ))
    }

    pub(super) fn external_session_enabled(&self) -> io::Result<bool> {
        let personal = read_strict(&self.personal)?.unwrap_or_default();
        Ok(personal.get("external_session_header").and_then(Value::as_bool).unwrap_or(true))
    }

    pub(super) fn agent_creation_tools(&self) -> io::Result<Object> {
        let preferences = read_strict(&self.personal)?.unwrap_or_default();
        let mut tools = Object::new();
        if let Some(snapshot) = super::tool_loading_snapshot(&preferences) {
            tools.insert("tool_loading".into(), snapshot);
        }
        if preferences.get("git_worktree_enabled") == Some(&Value::Bool(true)) {
            tools.insert("git_worktree_enabled".into(), Value::Bool(true));
        }
        Ok(tools)
    }

    pub(super) fn network_settings(&self) -> io::Result<Object> {
        let mut merged = self.merged()?;
        merged.retain(|key, _| SERVICE_KEYS.contains(&key.as_str()) || SECRET_KEYS.contains(&key.as_str()));
        Ok(merged)
    }

    /// Read the ordinary preference document plus service selection, deliberately
    /// excluding API secrets. Chat's settings response must never echo secrets.
    pub(super) fn read_for_chat(&self) -> io::Result<Object> {
        let personal = read_strict(&self.personal)?.unwrap_or_default();
        let services = read_strict(&self.services)?.unwrap_or_default();
        let defaults = &super::schema::schema().defaults;
        // Public projection only: schema-known preferences plus service choice.
        // Historical inline secrets and private/unknown keys never leave this API;
        // the PATCH transaction still preserves them on disk.
        let mut public = defaults.clone();
        for (key, value) in personal {
            if defaults.contains_key(&key) && !SECRET_KEYS.contains(&key.as_str()) {
                public.insert(key, value);
            }
        }
        for key in SERVICE_KEYS {
            if let Some(value) = services.get(*key) {
                public.insert((*key).to_owned(), value.clone());
            }
        }
        for key in SECRET_KEYS {
            public.shift_remove(*key);
        }
        Ok(public)
    }

    /// Merge Chat-owned fields into the raw preference document without running
    /// Agent's schema normalization. Chat touches neither `service_config.json`
    /// nor `secrets.json`, and a corrupt document fails before any replacement.
    pub(super) fn write_ordinary_patch(&self, patch: &Object) -> io::Result<Object> {
        let mut personal = read_strict(&self.personal)?.unwrap_or_default();
        // Validate the service document even though Chat never rewrites it, so a
        // damaged file cannot make the returned merged snapshot silently wrong.
        let _ = read_strict(&self.services)?;
        for (key, value) in patch {
            personal.insert(key.clone(), value.clone());
        }
        write_if_changed(&self.personal, &personal)?;
        self.read_for_chat()
    }

    pub(super) fn write(&self, config: &Object) -> io::Result<()> {
        let mut ordinary = config.clone();
        let services = take(&mut ordinary, SERVICE_KEYS);
        let secrets = take(&mut ordinary, SECRET_KEYS);
        // `normalize` returns the schema projection; carry forward private,
        // non-secret keys already present on disk. Unknown keys submitted in an
        // update are deliberately not accepted into the public schema.
        let existing = read_strict(&self.personal)?.unwrap_or_default();
        let defaults = &super::schema::schema().defaults;
        for (key, value) in existing {
            if !defaults.contains_key(&key)
                && !SERVICE_KEYS.contains(&key.as_str())
                && !SECRET_KEYS.contains(&key.as_str())
            {
                ordinary.entry(key).or_insert(value);
            }
        }
        // Move the split values before replacing a legacy combined file.
        write_if_changed(&self.services, &services)?;
        write_if_changed(&self.secrets, &secrets)?;
        write_if_changed(&self.personal, &ordinary)
    }
}

pub(super) fn read_object(path: &Path) -> io::Result<Option<Object>> {
    match fs::read(path) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes).ok()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn read_strict(path: &Path) -> io::Result<Option<Object>> {
    match fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map(Some).map_err(io::Error::other),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

pub(super) fn english(data: &Path) -> bool {
    read_object(&data.join(PERSONAL_FILE))
        .ok()
        .flatten()
        .is_some_and(|value| value.get("ui_locale").and_then(Value::as_str) == Some("en-US"))
}

fn take(source: &mut Object, keys: &[&str]) -> Object {
    keys.iter()
        .filter_map(|key| source.remove(*key).map(|value| ((*key).to_owned(), value)))
        .collect()
}

fn write_if_changed(path: &Path, payload: &Object) -> io::Result<()> {
    if read_object(path)?.as_ref() == Some(payload) {
        #[cfg(unix)]
        if path.file_name().is_some_and(|name| name == "secrets.json") {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
        }
        return Ok(());
    }
    let temporary = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let result = (|| {
        let mut file = options.open(&temporary)?;
        serde_json::to_writer_pretty(&mut file, payload)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}
