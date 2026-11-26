use std::{
    env, fs,
    io::{self, ErrorKind},
    path::{Path, PathBuf},
};

use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde_json::{Value, json};

use crate::knowledge::storage::{open_canonical_dir, publish, read_bytes};

const CONFIG_LIMIT: u64 = 8192;
const SNAPSHOT: &str = "astrion-state.json";
const FOREGROUND: &str = "astrion-host.json";
const HEARTBEAT: &str = "seen-astrion";

#[derive(Clone, Debug)]
pub struct CrossPetConfig {
    settings: PathBuf,
    integration: PathBuf,
    fallback: PathBuf,
    destination_override: Option<PathBuf>,
}

impl CrossPetConfig {
    pub fn for_host(deploy_config: &Path) -> Self {
        let home = env::home_dir().unwrap_or_default();
        let (support, fallback) = if cfg!(windows) {
            let local = variable("LOCALAPPDATA").unwrap_or_else(|| home.join("AppData/Local"));
            let support = local.join("CrossPet");
            let fallback = support.join("state");
            (support, fallback)
        } else {
            (
                home.join("Library/Application Support/CrossPet"),
                PathBuf::from("/tmp/crosspet"),
            )
        };
        let integration = variable("ASTRION_CROSSPET_CONFIG")
            .map(|path| expand_home(path, &home))
            .unwrap_or_else(|| {
                variable("CROSSPET_DATA_DIR")
                    .unwrap_or(support)
                    .join("astrion.json")
            });
        Self::with_paths(deploy_config.join("crosspet.json"), integration, fallback)
            .with_destination_override(
                variable("CROSSPET_STATE_DIR").map(|path| expand_home(path, &home)),
            )
    }

    pub fn with_paths(settings: PathBuf, integration: PathBuf, fallback: PathBuf) -> Self {
        Self {
            settings,
            integration,
            fallback,
            destination_override: None,
        }
    }

    pub fn with_destination_override(mut self, directory: Option<PathBuf>) -> Self {
        self.destination_override = directory;
        self
    }

    pub(super) fn enabled(&self) -> io::Result<bool> {
        let Some(value) = read_document(&self.settings)? else {
            return Ok(false);
        };
        if value["version"].as_u64() != Some(1) {
            return Err(invalid("Invalid CrossPet preference"));
        }
        value["enabled"]
            .as_bool()
            .ok_or_else(|| invalid("Invalid CrossPet preference"))
    }

    pub(super) fn save_enabled(&self, enabled: bool) -> io::Result<()> {
        let (parent, name) = document_path(&self.settings)?;
        let dir = directory(parent, true)?
            .ok_or_else(|| invalid("Invalid CrossPet preference directory"))?;
        write(
            &dir,
            name,
            &serde_json::to_vec(&json!({"version": 1, "enabled": enabled}))?,
        )
    }

    pub(super) fn destination(&self) -> io::Result<PathBuf> {
        let root = if let Some(override_directory) = &self.destination_override {
            override_directory.clone()
        } else if let Some(value) = read_document(&self.integration)? {
            if value["version"].as_u64() != Some(1) {
                return Err(invalid("Invalid CrossPet integration configuration"));
            }
            match &value["state_dir"] {
                Value::Null => self.fallback.clone(),
                Value::String(value) if value.is_empty() => self.fallback.clone(),
                Value::String(value) => {
                    expand_home(value.into(), &env::home_dir().unwrap_or_default())
                }
                _ => return Err(invalid("Invalid CrossPet state directory")),
            }
        } else {
            self.fallback.clone()
        };
        if !root.is_absolute() {
            return Err(invalid("Invalid CrossPet state directory"));
        }
        Ok(root)
    }
}

pub(super) fn check_targets(path: &Path) -> io::Result<()> {
    if let Some(dir) = directory(path, false)? {
        for name in [SNAPSHOT, FOREGROUND, HEARTBEAT] {
            exists(&dir, name)?;
        }
    }
    Ok(())
}

pub(super) fn publish_snapshot(path: &Path, snapshot: &Value) -> io::Result<()> {
    let payload = serde_json::to_vec(snapshot)?;
    if payload.len() > 1024 * 1024 {
        return Err(invalid("CrossPet snapshot exceeds the protocol byte limit"));
    }
    let foreground = serde_json::to_vec(&snapshot["foreground"])?;
    let ts = snapshot["ts"].as_f64().unwrap_or_default() as u64;
    let dir = directory(path, true)?.ok_or_else(|| invalid("Invalid CrossPet state directory"))?;
    let files = [
        (SNAPSHOT, payload.as_slice()),
        (FOREGROUND, foreground.as_slice()),
    ];
    // Validate all output names before replacing any file.
    let state_exists = exists(&dir, SNAPSHOT)?;
    let foreground_exists = exists(&dir, FOREGROUND)?;
    let heartbeat_exists = exists(&dir, HEARTBEAT)?;
    for ((name, contents), present) in files.into_iter().zip([state_exists, foreground_exists]) {
        publish(&dir, name, contents, present)?;
    }
    publish(&dir, HEARTBEAT, ts.to_string().as_bytes(), heartbeat_exists)
}

fn read_document(path: &Path) -> io::Result<Option<Value>> {
    let (parent, name) = document_path(path)?;
    let Some(dir) = directory(parent, false)? else {
        return Ok(None);
    };
    match read_bytes(&dir, name, CONFIG_LIMIT) {
        Ok(bytes) => Ok(Some(serde_json::from_slice(&bytes)?)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn document_path(path: &Path) -> io::Result<(&Path, &str)> {
    let parent = path
        .parent()
        .ok_or_else(|| invalid("Invalid CrossPet document path"))?;
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| invalid("Invalid CrossPet document path"))?;
    Ok((parent, name))
}

fn directory(path: &Path, create: bool) -> io::Result<Option<Dir>> {
    let absolute = std::path::absolute(if path.as_os_str().is_empty() {
        Path::new(".")
    } else {
        path
    })?;
    let Some((parent, name)) = absolute.parent().zip(absolute.file_name()) else {
        return open_canonical_dir(&absolute).map(Some);
    };
    if create {
        fs::create_dir_all(parent)?;
    }
    let ancestor = match parent.canonicalize() {
        Ok(path) => open_canonical_dir(&path)?,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    if create {
        match ancestor.create_dir(name) {
            Ok(()) => {}
            Err(error) if error.kind() == ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error),
        }
    }
    match ancestor.open_dir_nofollow(name) {
        Ok(dir) => Ok(Some(dir)),
        Err(error) if error.kind() == ErrorKind::NotFound && !create => Ok(None),
        Err(error) => Err(error),
    }
}

fn write(dir: &Dir, name: &str, contents: &[u8]) -> io::Result<()> {
    publish(dir, name, contents, exists(dir, name)?)
}

fn exists(dir: &Dir, name: &str) -> io::Result<bool> {
    match dir.symlink_metadata(name) {
        Ok(meta) if meta.is_file() => Ok(true),
        Ok(_) => Err(invalid("CrossPet target must be a regular file")),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

fn variable(name: &str) -> Option<PathBuf> {
    env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(Into::into)
}

fn expand_home(path: PathBuf, home: &Path) -> PathBuf {
    match path.strip_prefix("~") {
        Ok(suffix) if !home.as_os_str().is_empty() => home.join(suffix),
        _ => path,
    }
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(ErrorKind::InvalidData, message)
}
