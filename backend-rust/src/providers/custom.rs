//! `custom_models.json` UI CRUD.
//!
//! Reads resolve the deployment directory first, then the source seed and its
//! `.example` (matching `config/paths.py::resolve_deploy_config`). Writes always
//! land in the deployment directory, carrying existing seed entries over on the
//! first write. The file may contain plaintext keys, so writes are 0600 and use
//! a same-directory temporary file.

use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
};

use serde_json::{Map, Value};
use uuid::Uuid;

const ALLOWED: [&str; 15] = [
    "model_name",
    "display_name",
    "description",
    "model_description",
    "visible",
    "url",
    "apikey",
    "model_id",
    "multimodal",
    "reasoning_capability",
    "reasoning_effort",
    "context_window",
    "max_output_tokens",
    "thinkmode_status",
    "extra_parameter",
];

#[derive(Clone)]
pub(super) struct CustomModels {
    repo_config: PathBuf,
    deploy_config: PathBuf,
}

impl CustomModels {
    pub(super) fn new(repo_config: &Path, deploy_config: &Path) -> Self {
        Self {
            repo_config: repo_config.to_owned(),
            deploy_config: deploy_config.to_owned(),
        }
    }

    pub(super) fn effective(&self) -> PathBuf {
        let deploy = self.deploy_config.join("custom_models.json");
        if deploy.exists() {
            return deploy;
        }
        let seed = self.repo_config.join("custom_models.json");
        if seed.exists() {
            return seed;
        }
        let example = self.repo_config.join("custom_models.json.example");
        if example.exists() {
            return example;
        }
        deploy
    }

    pub(super) fn read(&self) -> Result<(Vec<Value>, PathBuf), String> {
        let effective = self.effective();
        let raw: Value = match fs::read(&effective) {
            Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or(Value::Null),
            Err(_) => Value::Null,
        };
        let items = if raw.is_array() { &raw } else { &raw["models"] };
        let items = items
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .filter(|item| item.is_object())
                    .cloned()
                    .collect()
            })
            .unwrap_or_default();
        Ok((items, effective))
    }

    pub(super) fn write(&self, items: &[Value]) -> Result<(), String> {
        let target = self.deploy_config.join("custom_models.json");
        fs::create_dir_all(&self.deploy_config)
            .map_err(|error| format!("write_failed: {error}"))?;
        let document = serde_json::json!({"models": items});
        let temporary = self
            .deploy_config
            .join(format!(".custom-models-{}.tmp", Uuid::new_v4()));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options
            .open(&temporary)
            .map_err(|error| format!("write_failed: {error}"))?;
        let result = (|| -> io::Result<()> {
            let mut writer = io::BufWriter::new(&mut file);
            serde_json::to_writer_pretty(&mut writer, &document)?;
            writer.flush()?;
            drop(writer);
            file.sync_all()?;
            replace_file(&temporary, &target)
        })();
        if let Err(error) = result {
            let _ = fs::remove_file(&temporary);
            return Err(format!("write_failed: {error}"));
        }
        Ok(())
    }
}

fn replace_file(from: &Path, to: &Path) -> io::Result<()> {
    #[cfg(windows)]
    for delay in [10, 20, 40, 80] {
        match fs::rename(from, to) {
            Err(error) if matches!(error.raw_os_error(), Some(5 | 32 | 33)) => {
                std::thread::sleep(std::time::Duration::from_millis(delay));
            }
            result => return result,
        }
    }
    fs::rename(from, to)
}

/// Whitelist fields and validate required ones. Returns the cleaned entry and
/// the Python error code (`model_name_required`, `model_name_invalid`, ...).
pub(super) fn sanitize(payload: &Map<String, Value>) -> Result<Value, &'static str> {
    let mut item = Map::new();
    for key in ALLOWED {
        if let Some(value) = payload.get(key) {
            item.insert(key.into(), value.clone());
        }
    }
    let model_name = field(&item, "model_name");
    if model_name.is_empty() {
        return Err("model_name_required");
    }
    if model_name
        .chars()
        .any(|character| matches!(character, '/' | '\\' | ' ' | '\t' | '\n'))
    {
        return Err("model_name_invalid");
    }
    if field(&item, "url").is_empty() {
        return Err("url_required");
    }
    if field(&item, "model_id").is_empty() {
        return Err("model_id_required");
    }
    if field(&item, "apikey").is_empty() {
        return Err("apikey_required");
    }
    item.insert("model_name".into(), Value::String(model_name));
    Ok(Value::Object(item))
}

fn field(item: &Map<String, Value>, key: &str) -> String {
    super::store::text(item.get(key).unwrap_or(&Value::Null))
        .trim()
        .to_owned()
}

pub(super) fn find_index(items: &[Value], model_name: &str) -> Option<usize> {
    items
        .iter()
        .position(|item| item["model_name"].as_str() == Some(model_name))
}
