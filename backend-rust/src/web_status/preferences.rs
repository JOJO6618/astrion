use crate::catalog::Catalog;
use serde::Serialize;
use serde_json::{Map, Value, json};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::Path;

const MAX_SETTINGS_BYTES: u64 = 8 * 1024 * 1024;
const UI_BLOCKS: &[&str] = &[
    "collapse_workspace",
    "block_file_manager",
    "block_personal_space",
    "block_upload",
    "block_conversation_review",
    "block_tool_toggle",
    "block_realtime_terminal",
    "block_focus_panel",
    "block_token_panel",
    "block_compress_conversation",
    "block_virtual_monitor",
];

pub(super) struct Personalization {
    model: Option<String>,
    pub run_mode: &'static str,
    pub reasoning_effort: Option<&'static str>,
    pub compress_limit: i64,
    work_mode: &'static str,
    permission_mode: &'static str,
    browser_tools: bool,
    git_worktree: bool,
}

impl Personalization {
    pub fn load(data_dir: &Path) -> Self {
        let document = read_object(&data_dir.join("personalization.json")).unwrap_or_default();
        Self {
            model: document["default_model"]
                .as_str()
                .map(str::trim)
                .filter(|key| !key.is_empty())
                .map(str::to_owned),
            run_mode: if std::env::var_os("ASTRION_HOST_THINKING_DEFAULT")
                .is_some_and(|value| value == "1")
            {
                "thinking"
            } else {
                run_mode(&document["default_run_mode"], "thinking")
            },
            reasoning_effort: reasoning_effort(&document["default_reasoning_effort"]),
            compress_limit: compress_limit(&document["deep_compress_trigger_tokens"])
                .unwrap_or(250_000)
                .max(
                    compress_limit(&document["shallow_compress_trigger_tokens"]).unwrap_or(80_000)
                        + 1,
                ),
            work_mode: work_mode(&document["default_work_mode"], "plan"),
            permission_mode: permission_mode(&document["default_permission_mode"], "approval"),
            browser_tools: document["browser_tools_default"] == true,
            git_worktree: document["git_worktree_enabled"] == true,
        }
    }

    pub fn model_key<'a>(&'a self, catalog: &'a Catalog) -> Option<&'a str> {
        self.model.as_deref().or_else(|| catalog.default_key())
    }

    pub fn modes(&self, metadata: &Value) -> Modes {
        Modes::resolve(
            work_mode(&metadata["work_mode"], self.work_mode),
            permission_mode(&metadata["permission_mode"], self.permission_mode),
            execution_mode(&metadata["execution_mode"]),
            network_mode(&metadata["network_permission"]),
        )
    }

    pub fn draft_settings(&self, catalog: &Catalog, previous: &Value) -> Value {
        let model_key = self.model_key(catalog).unwrap_or_default();
        let run = model_run_mode(catalog, model_key, self.run_mode);
        let previous_execution = execution_mode(&previous["execution_mode"]);
        let modes = Modes::resolve(
            self.work_mode,
            self.permission_mode,
            previous_execution,
            network_mode(&previous["network_permission"]),
        );
        let mut settings = json!({
            "model_key": model_key,
            "run_mode": run,
            "thinking_mode": run == "thinking",
            "reasoning_effort": self.reasoning_effort,
            "chatgpt_fast_mode": previous["chatgpt_fast_mode"] == true,
            "browser_tools": self.browser_tools,
            "git_worktree_enabled": self.git_worktree,
            "restore_modes": {
                "pre_plan_permission_mode": (self.work_mode == "plan").then_some(self.permission_mode),
                "pre_plan_execution_mode": (self.work_mode == "plan" && self.permission_mode != "readonly" && previous_execution == "direct").then_some("direct"),
                "pre_readonly_execution_mode": (self.permission_mode == "readonly" && previous_execution == "direct").then_some("direct"),
            },
        });
        modes.write_fields(&mut settings);
        settings
    }
}

#[derive(Serialize)]
pub(super) struct Modes {
    pub work_mode: &'static str,
    pub permission_mode: &'static str,
    pub execution_mode: &'static str,
    pub network_permission: &'static str,
}

impl Modes {
    fn resolve(
        work_mode: &'static str,
        mut permission_mode: &'static str,
        mut execution_mode: &'static str,
        network_permission: &'static str,
    ) -> Self {
        if work_mode == "plan" {
            permission_mode = "readonly";
        }
        if permission_mode == "readonly" {
            execution_mode = "sandbox";
        }
        Self {
            work_mode,
            permission_mode,
            execution_mode,
            network_permission,
        }
    }

    pub fn write_fields(&self, destination: &mut Value) {
        destination["work_mode"] = json!(self.work_mode);
        destination["permission_mode"] = json!(self.permission_mode);
        destination["execution_mode"] = json!(self.execution_mode);
        destination["network_permission"] = json!(self.network_permission);
    }
}

fn normalize(value: &Value, allowed: &[&'static str]) -> Option<&'static str> {
    let text = value.as_str()?.trim();
    allowed
        .iter()
        .copied()
        .find(|candidate| candidate.eq_ignore_ascii_case(text))
}

fn work_mode(value: &Value, fallback: &'static str) -> &'static str {
    normalize(value, &["plan", "ask", "execute"]).unwrap_or(fallback)
}

fn permission_mode(value: &Value, fallback: &'static str) -> &'static str {
    normalize(
        value,
        &["readonly", "approval", "auto_approval", "unrestricted"],
    )
    .unwrap_or(fallback)
}

fn execution_mode(value: &Value) -> &'static str {
    normalize(value, &["sandbox", "direct"]).unwrap_or("sandbox")
}

fn network_mode(value: &Value) -> &'static str {
    normalize(value, &["restricted", "full", "none"]).unwrap_or("restricted")
}

pub(super) fn run_mode(value: &Value, fallback: &'static str) -> &'static str {
    match normalize(value, &["fast", "thinking", "deep"]) {
        Some("deep") => "thinking",
        Some(other) => other,
        None => fallback,
    }
}

pub(super) fn model_run_mode<'a>(catalog: &Catalog, key: &str, fallback: &'a str) -> &'a str {
    let models = catalog.models_response();
    let profile = models["items"]
        .as_array()
        .and_then(|items| items.iter().find(|item| item["model_key"] == key));
    match profile {
        Some(profile) if profile["thinking_only"] == true => "thinking",
        Some(profile) if profile["fast_only"] == true => "fast",
        _ => fallback,
    }
}

pub(super) fn compress_limit(value: &Value) -> Option<i64> {
    let tokens = match value {
        Value::String(text) => text.trim().parse().ok()?,
        Value::Number(number) => number.as_i64().or_else(|| {
            let n = number.as_f64()?;
            (n.fract() == 0.0).then_some(n as i64)
        })?,
        _ => return None,
    };
    (1_000..=2_000_000).contains(&tokens).then_some(tokens)
}

pub(super) fn reasoning_effort(value: &Value) -> Option<&'static str> {
    normalize(value, &["low", "medium", "high", "xhigh", "max"])
}

pub(super) fn pending_modes(metadata: &Value) -> Value {
    json!({
        "permission_mode": normalize(&metadata["pending_permission_mode"], &["readonly", "approval", "auto_approval", "unrestricted"]),
        "execution_mode": normalize(&metadata["pending_execution_mode"], &["sandbox", "direct"]),
        "network_permission": normalize(&metadata["pending_network_permission"], &["restricted", "full", "none"]),
    })
}

/// Load the effective administrator UI and model policy for the Host identity.
pub fn admin_policy(data_dir: &Path, catalog: &Catalog) -> io::Result<Value> {
    let policy = read_object(&data_dir.join("admin_policy.json"))?;
    let mut ui_blocks = Map::new();
    let mut forced = Map::new();
    let mut disabled_models = BTreeSet::new();
    for scope in [
        &policy["global"],
        &policy["roles"]["admin"],
        &policy["invites"][""],
        &policy["users"]["host"],
    ] {
        if let Some(values) = scope["ui_blocks"].as_object() {
            for (key, value) in values
                .iter()
                .filter(|(key, _)| UI_BLOCKS.contains(&key.as_str()))
            {
                ui_blocks.insert(key.clone(), json!(truthy(value)));
            }
        }
        if let Some(values) = scope["forced_category_states"].as_object() {
            for (key, value) in values
                .iter()
                .filter(|(key, _)| !key.is_empty() && key.as_str() != "custom")
            {
                forced.insert(key.clone(), json!(value.as_bool()));
            }
        }
        if let Some(values) = scope["disabled_models"].as_array() {
            disabled_models.extend(
                values
                    .iter()
                    .filter_map(Value::as_str)
                    .filter(|key| catalog.resolve_key(Some(key), false).is_ok()),
            );
        }
    }
    Ok(
        json!({ "ui_blocks": ui_blocks, "disabled_models": disabled_models, "forced_category_states": forced, "version": policy["updated_at"].as_str() }),
    )
}

pub(super) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

pub(super) fn memory_count(data_dir: &Path, filename: &str) -> usize {
    let Ok(content) = read_small_file(&data_dir.join(filename)) else {
        return 0;
    };
    let Ok(content) = std::str::from_utf8(&content) else {
        return 0;
    };
    content
        .lines()
        .filter(|line| {
            let Some((number, _)) = line.trim_start().split_once('.') else {
                return false;
            };
            !number.is_empty() && number.chars().all(|digit| digit.is_ascii_digit())
        })
        .count()
}

fn read_object(path: &Path) -> io::Result<Value> {
    let contents = match read_small_file(path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(json!({})),
        Err(error) => return Err(error),
    };
    let value: Value = serde_json::from_slice(&contents)?;
    if !value.is_object() {
        return Err(io::Error::other("Expected a settings object"));
    }
    Ok(value)
}

fn read_small_file(path: &Path) -> io::Result<Vec<u8>> {
    let metadata = fs::metadata(path)?;
    if !metadata.is_file() || metadata.len() > MAX_SETTINGS_BYTES {
        return Err(io::Error::other("Invalid settings file"));
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take(MAX_SETTINGS_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_SETTINGS_BYTES {
        return Err(io::Error::other("Settings file is too large"));
    }
    Ok(bytes)
}
