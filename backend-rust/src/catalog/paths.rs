use std::{
    collections::HashMap,
    env, fs,
    path::{Path, PathBuf},
};

use serde_json::Value;

use super::{Catalog, Personality, array, codex, profiles, read_json, scalar_text, truthy};

/// Server-owned Host catalog paths and environment. Loading a snapshot never writes these paths.
#[derive(Clone)]
pub struct CatalogSource {
    repo_config: PathBuf,
    deploy_config: PathBuf,
    host_data: PathBuf,
    cli_cache: Option<PathBuf>,
    variables: HashMap<String, String>,
    providers: crate::providers::ProviderConfig,
}

impl CatalogSource {
    /// Resolve Python's Host data paths using the trusted Astrion source tree.
    pub fn from_environment(repo_root: &Path) -> Result<Self, String> {
        let repo_root = std::path::absolute(repo_root).map_err(|_| "无法解析项目配置目录")?;
        let home = env::home_dir().ok_or("无法解析用户主目录")?;
        Ok(Self::from_inputs(
            &repo_root,
            &home,
            process_variables(),
            || fs::read_to_string(repo_root.join(".env")).unwrap_or_default(),
        ))
    }

    /// Use explicit server paths, process variables, and settings from the selected data root.
    /// A dedicated data directory is its own root. The Host `host/data` layout uses its parent root.
    pub fn with_paths(repo_root: &Path, deploy_config: &Path, data_dir: &Path) -> Self {
        let root = if data_dir.ends_with("host/data") {
            data_dir.parent().and_then(Path::parent).unwrap_or(data_dir)
        } else {
            data_dir
        };
        let process = process_variables();
        let mut variables = process.clone();
        apply_settings(root, &process, &mut variables);
        Self {
            repo_config: repo_root.join("config"),
            deploy_config: deploy_config.to_owned(),
            host_data: data_dir.to_owned(),
            cli_cache: None,
            variables,
            providers: crate::providers::ProviderConfig::host(repo_root, deploy_config, data_dir),
        }
    }

    pub fn providers(&self) -> &crate::providers::ProviderConfig {
        &self.providers
    }

    pub fn with_providers(mut self, providers: crate::providers::ProviderConfig) -> Self {
        self.providers = providers;
        self
    }

    pub fn host_data_dir(&self) -> &Path {
        &self.host_data
    }

    pub fn deploy_config_dir(&self) -> &Path {
        &self.deploy_config
    }

    /// Refresh mutable catalog data from the same read-only paths used by Python.
    pub fn load(&self) -> Catalog {
        let mut catalog = Catalog {
            preferred: self
                .variable("AGENT_DEFAULT_MODEL")
                .or_else(|| self.variable("DEFAULT_MODEL_KEY"))
                .map(str::to_owned),
            chat_default_max_output_tokens: self.variable("AGENT_DEFAULT_RESPONSE_MAX_TOKENS")
                .map(|value| value.parse::<u64>().ok().filter(|limit| *limit > 0).unwrap_or(4_096)),
            personality: read_personality(&self.host_data),
            ..Catalog::default()
        };
        profiles::load_custom(self, &mut catalog);
        profiles::load_providers(self, &mut catalog);
        codex::load(self, &mut catalog);
        catalog
    }

    pub(super) fn variable(&self, name: &str) -> Option<&str> {
        variable(&self.variables, name)
    }

    pub(super) fn source_config(&self, file: &str) -> PathBuf {
        self.repo_config.join(file)
    }

    pub(super) fn custom_models_path(&self) -> PathBuf {
        let path = self.deploy_config.join("custom_models.json");
        if path.exists() {
            return path;
        }
        let seed = self.source_config("custom_models.json");
        if seed.exists() {
            return seed;
        }
        let example = self.source_config("custom_models.json.example");
        if example.exists() { example } else { path }
    }

    pub(super) fn codex_cli_cache(&self) -> Option<&Path> {
        self.cli_cache.as_deref()
    }

    pub(super) fn from_inputs(
        repo_root: &Path,
        home: &Path,
        process: HashMap<String, String>,
        dotenv: impl FnOnce() -> String,
    ) -> Self {
        let ignored = variable(&process, "ASTRION_IGNORE_DOTENV").is_some_and(|value| {
            matches!(
                value.to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        });
        let mut variables = if ignored {
            HashMap::new()
        } else {
            parse_dotenv(&dotenv())
        };
        let dotenv_root = variables.get("ASTRION_DATA_ROOT").cloned();
        variables.extend(
            process
                .iter()
                .map(|(key, value)| (key.clone(), value.clone())),
        );
        if let Some(root) = dotenv_root {
            variables.insert("ASTRION_DATA_ROOT".into(), root);
        }
        let settings_root = resolve_path(
            repo_root,
            home,
            variable(&variables, "ASTRION_DATA_ROOT"),
            "~/.astrion/astrion",
        );
        apply_settings(&settings_root, &process, &mut variables);
        let root = resolve_path(
            repo_root,
            home,
            variable(&variables, "ASTRION_DATA_ROOT"),
            "~/.astrion/astrion",
        );
        let host_data = variable(&variables, "DATA_DIR")
            .map(|path| resolve_path(repo_root, home, Some(path), ""))
            .unwrap_or_else(|| root.join("host/data"));
        let deploy_config = variable(&variables, "DEPLOY_CONFIG_DIR")
            .map(|path| resolve_path(repo_root, home, Some(path), ""))
            .unwrap_or_else(|| root.join("config"));
        Self {
            providers: crate::providers::ProviderConfig::host(repo_root, &deploy_config, &host_data),
            repo_config: repo_root.join("config"),
            deploy_config,
            host_data,
            cli_cache: Some(home.join(".codex/models_cache.json")),
            variables,
        }
    }
}

fn process_variables() -> HashMap<String, String> {
    env::vars_os()
        .filter_map(|(key, value)| Some((key.into_string().ok()?, value.into_string().ok()?)))
        .collect()
}

fn apply_settings(root: &Path, process: &HashMap<String, String>, variables: &mut HashMap<String, String>) {
    let settings = read_json(&root.join("settings.json"));
    if let Some(env_vars) = settings["env_vars"].as_object() {
        for (key, value) in env_vars {
            if !process.contains_key(key) && truthy(value) {
                variables.insert(key.clone(), scalar_text(value));
            }
        }
    }
    let output_setting = &settings["models"]["default_response_max_tokens"];
    let output_variable = "AGENT_DEFAULT_RESPONSE_MAX_TOKENS";
    if !process.contains_key(output_variable) && !output_setting.is_null() {
        variables.insert(output_variable.into(), scalar_text(output_setting));
    }
}

fn variable<'a>(variables: &'a HashMap<String, String>, key: &str) -> Option<&'a str> {
    variables
        .get(key)
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
}

fn parse_dotenv(content: &str) -> HashMap<String, String> {
    content
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            if line.starts_with('#') {
                return None;
            }
            let (key, value) = line.split_once('=')?;
            let key = key.trim();
            if key.is_empty() {
                return None;
            }
            Some((
                key.into(),
                value.trim().trim_matches('"').trim_matches('\'').into(),
            ))
        })
        .collect()
}

fn resolve_path(repo_root: &Path, home: &Path, configured: Option<&str>, default: &str) -> PathBuf {
    let raw = configured.unwrap_or(default);
    let path = if raw == "~" {
        home.into()
    } else if let Some(relative) = raw.strip_prefix("~/").or_else(|| raw.strip_prefix("~\\")) {
        home.join(relative)
    } else {
        PathBuf::from(raw)
    };
    let path = if path.is_absolute() {
        path
    } else {
        repo_root.join(path)
    };
    path.canonicalize().unwrap_or(path)
}

fn read_personality(host_data: &Path) -> Personality {
    let raw = read_json(&host_data.join("personalization.json"));
    let mut hidden_models = Vec::new();
    for key in array(&raw["hidden_models"])
        .iter()
        .filter_map(Value::as_str)
        .map(str::trim)
    {
        if !key.is_empty() && !hidden_models.iter().any(|hidden| hidden == key) {
            hidden_models.push(key.to_owned());
            if hidden_models.len() == 500 {
                break;
            }
        }
    }
    Personality {
        default_model: raw["default_model"].as_str().map(str::to_owned),
        hidden_models,
    }
}
