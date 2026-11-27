//! Read-only projection of the Python Host model registry.

mod codex;
mod paths;
mod profiles;

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/catalog_contract.rs"]
mod tests;

use std::{collections::HashMap, fs, path::Path};

use serde::Serialize;
use serde_json::{Map, Value, json};

use crate::usage::PriceIdentity;

pub use paths::CatalogSource;

const CHAT: &str = "chat_completions";
const RESPONSES: &str = "responses";
const NO_MODELS: &str = "尚未配置任何模型，请先在设置页的「提供商」中添加";

/// A snapshot used to list models and choose a connection consistently.
#[derive(Default)]
pub struct Catalog {
    entries: Vec<Profile>,
    indexes: HashMap<String, usize>,
    preferred: Option<String>,
    chat_default_max_output_tokens: Option<u64>,
    personality: Personality,
}

/// Private request configuration selected by a trusted server-side caller.
/// This type intentionally has no serialization or debug representation.
#[derive(Clone)]
pub struct SelectedModel {
    pub key: String,
    pub model_id: String,
    pub(crate) codex: Option<CodexSelection>,
    pub endpoint: String,
    pub api_key: Option<String>,
    pub api_protocol: String,
    pub headers: Map<String, Value>,
    pub extra_params: Map<String, Value>,
    pub max_output_tokens: Value,
    pub(crate) context_window: Option<u64>,
    pub(crate) chat_default_max_output_tokens: u64,
    pub price_identity: PriceIdentity,
    pub(crate) prompt_description: String,
    pub(crate) prompt_multimodal: &'static str,
}

#[derive(Default)]
struct Personality {
    default_model: Option<String>,
    hidden_models: Vec<String>,
}

struct Profile {
    public: PublicModel,
    hidden: bool,
    connection: ProfileConnection,
    modelsdev_key: Option<String>,
}

enum ProfileConnection {
    ApiKey(Connection),
    Subscription(CodexSelection),
}

#[derive(Clone)]
pub(crate) struct CodexSelection {
    pub(crate) providers: crate::providers::ProviderConfig,
    pub(crate) account_id: String,
    pub(crate) model_info: Value,
    override_file: std::path::PathBuf,
}

impl CodexSelection {
    pub(crate) fn instructions(&self) -> String {
        let custom = std::fs::read_to_string(&self.override_file).unwrap_or_default();
        let base = if custom.trim().is_empty() {
            self.model_info["base_instructions"].as_str().filter(|s| !s.is_empty())
                .unwrap_or(include_str!("../../prompts/codex_fallback.txt"))
        } else {
            custom.trim()
        };
        format!("{base}\n\n{}", include_str!("../../prompts/codex_bridge.txt").trim())
    }
}

struct Connection {
    base_url: String,
    api_key: String,
    headers: Map<String, Value>,
    fast: Mode,
    thinking: Option<Mode>,
}

struct Mode {
    id: String,
    extra_params: Map<String, Value>,
}

#[derive(Serialize)]
struct PublicModel {
    model_key: String,
    name: String,
    description: String,
    visible: bool,
    supports_thinking: bool,
    fast_only: bool,
    thinking_only: bool,
    supports_reasoning_effort: bool,
    is_chatgpt_model: bool,
    supports_chatgpt_fast_mode: bool,
    multimodal: &'static str,
    context_window: Value,
    default_context_window: Option<i64>,
    max_context_window: Option<i64>,
    max_input_tokens: Option<i64>,
    max_output_tokens: Value,
    api_protocol: String,
    provider_type: Option<&'static str>,
    provider_id: Option<String>,
    provider_name: Option<String>,
    supported_reasoning_levels: Option<Vec<ReasoningLevel>>,
    default_reasoning_level: Option<String>,
}

#[derive(Serialize)]
struct ReasoningLevel {
    effort: String,
    description: String,
}

impl PublicModel {
    fn new(model_key: String, name: String) -> Self {
        Self {
            model_key,
            name,
            description: String::new(),
            visible: true,
            supports_thinking: false,
            fast_only: false,
            thinking_only: false,
            supports_reasoning_effort: false,
            is_chatgpt_model: false,
            supports_chatgpt_fast_mode: false,
            multimodal: "none",
            context_window: Value::Null,
            default_context_window: None,
            max_context_window: None,
            max_input_tokens: None,
            max_output_tokens: Value::Null,
            api_protocol: CHAT.into(),
            provider_type: None,
            provider_id: None,
            provider_name: None,
            supported_reasoning_levels: None,
            default_reasoning_level: None,
        }
    }

    fn set_context_windows(&mut self, default: Option<i64>, maximum: Option<i64>) {
        self.default_context_window = default;
        self.max_context_window = maximum;
        self.context_window = maximum.or(default).into();
    }
}

impl Catalog {
    pub(crate) fn registered_model_keys(&self) -> Vec<String> {
        self.entries.iter().map(|entry| entry.public.model_key.clone()).collect()
    }

    /// The exact Python GET /api/v1/models envelope and field allowlist.
    pub fn models_response(&self) -> Value {
        let items: Vec<_> = self
            .entries
            .iter()
            .filter(|p| !p.hidden)
            .map(|p| &p.public)
            .collect();
        json!({"success": true, "items": items})
    }

    /// User-level model visibility is applied by clients, independently of the public catalog.
    pub fn hidden_models(&self) -> &[String] {
        &self.personality.hidden_models
    }

    /// New-conversation preference, then the environment preference, then the first visible key.
    pub fn default_key(&self) -> Option<&str> {
        self.personality
            .default_model
            .as_deref()
            .filter(|key| self.profile(key).is_some_and(|p| !p.hidden))
            .or_else(|| self.registry_default(true))
    }

    /// Registry semantics for callers that need Python's resolve_model_key behavior.
    pub fn resolve_key(&self, candidate: Option<&str>, visible_only: bool) -> Result<&str, String> {
        let Some(candidate) = candidate.filter(|key| !key.is_empty()) else {
            return self
                .registry_default(visible_only)
                .ok_or_else(|| NO_MODELS.into());
        };
        self.profile(candidate)
            .filter(|p| !visible_only || !p.hidden)
            .map(|p| p.public.model_key.as_str())
            .ok_or_else(|| format!("未知模型 key: {candidate}"))
    }

    pub(crate) fn auxiliary_key(&self, configured: &str) -> Option<&str> {
        let configured = configured.trim();
        if !configured.is_empty() {
            return self.resolve_key(Some(configured), false).ok();
        }
        let mut visible = self.entries.iter().filter(|profile| !profile.hidden);
        let first = visible.next()?;
        if matches!(first.connection, ProfileConnection::Subscription(_))
            && visible.clone().all(|profile| matches!(profile.connection, ProfileConnection::Subscription(_)))
        {
            return std::iter::once(first).chain(visible)
                .find(|profile| profile.public.model_key.contains("-luna"))
                .or(Some(first))
                .map(|profile| profile.public.model_key.as_str());
        }
        Some(&first.public.model_key)
    }

    pub(crate) fn supports_chatgpt_fast_mode(&self, candidate: Option<&str>) -> Result<bool, String> {
        let key = self.resolve_key(candidate, false)?;
        let profile = self.profile(key).expect("a resolved model is registered");
        Ok(profile.public.is_chatgpt_model && profile.public.supports_chatgpt_fast_mode)
    }

    /// Resolve an explicit key or the new-conversation default to a usable upstream connection.
    pub fn select(&self, candidate: Option<&str>, thinking: bool) -> Result<SelectedModel, String> {
        self.select_for_turn(candidate, thinking, None)
    }

    /// Select the upstream connection and apply the conversation's reasoning preference.
    pub fn select_for_turn(
        &self,
        candidate: Option<&str>,
        thinking: bool,
        reasoning_effort: Option<&str>,
    ) -> Result<SelectedModel, String> {
        let key = match candidate.filter(|key| !key.is_empty()) {
            Some(key) => self.resolve_key(Some(key), false)?,
            None => self.default_key().ok_or(NO_MODELS)?,
        };
        let profile = self.profile(key).expect("a resolved model is registered");
        let protocol = profile.public.api_protocol.as_str();
        let suffix = match protocol {
            CHAT => "chat/completions",
            RESPONSES => "responses",
            _ => return Err("当前 Rust 后端不支持此模型的 API 协议".into()),
        };
        let thinking = profile.public.thinking_only || (thinking && !profile.public.fast_only);
        let (model_id, endpoint, api_key, headers, extra_params, codex) = match &profile.connection {
            ProfileConnection::ApiKey(connection) => {
                if connection.api_key.is_empty() {
                    return Err("模型缺少 API Key 配置".into());
                }
                let endpoint = format!("{}/{suffix}", connection.base_url);
                let url = reqwest::Url::parse(&endpoint).map_err(|_| "模型 API 地址无效")?;
                if !matches!(url.scheme(), "http" | "https") || url.host().is_none()
                    || !url.username().is_empty() || url.password().is_some()
                    || url.fragment().is_some() || !url.path().ends_with(suffix) {
                    return Err("模型 API 地址无效".into());
                }
                let mode = if thinking { connection.thinking.as_ref().unwrap_or(&connection.fast) } else { &connection.fast };
                (mode.id.clone(), endpoint, Some(connection.api_key.clone()), connection.headers.clone(),
                 request_parameters(&profile.public, mode, thinking, reasoning_effort), None)
            }
            ProfileConnection::Subscription(selected) => {
                let mut params = Map::new();
                if let Some(effort) = reasoning_effort { params.insert("reasoning".into(), json!({"effort":effort})); }
                (scalar_text(&selected.model_info["slug"]).trim().to_owned(),
                 "https://chatgpt.com/backend-api/codex/responses".to_owned(), None, Map::new(), params, Some(selected.clone()))
            }
        };
        Ok(SelectedModel {
            key: key.into(),
            model_id,
            endpoint,
            api_key,
            api_protocol: protocol.into(),
            headers,
            codex,
            extra_params,
            max_output_tokens: profile.public.max_output_tokens.clone(),
            context_window: profile.public.context_window.as_u64().filter(|limit| *limit > 0),
            chat_default_max_output_tokens: self.chat_default_max_output_tokens.unwrap_or(32_768),
            prompt_description: profile.public.description.clone(),
            prompt_multimodal: profile.public.multimodal,
            price_identity: PriceIdentity {
                provider_id: profile.public.provider_id.clone(),
                modelsdev_key: profile.modelsdev_key.clone(),
            },
        })
    }

    pub(crate) fn status_context_window(&self, key: &str) -> Option<u64> {
        self.profile(key)?.public.context_window.as_u64().filter(|limit| *limit > 0)
    }

    fn profile(&self, key: &str) -> Option<&Profile> {
        self.indexes.get(key).map(|index| &self.entries[*index])
    }

    fn registry_default(&self, visible_only: bool) -> Option<&str> {
        self.preferred
            .as_deref()
            .filter(|key| {
                self.profile(key)
                    .is_some_and(|p| !visible_only || !p.hidden)
            })
            .or_else(|| {
                self.entries
                    .iter()
                    .find(|p| !visible_only || !p.hidden)
                    .map(|p| p.public.model_key.as_str())
            })
    }

    fn insert(&mut self, profile: Profile) {
        let key = profile.public.model_key.clone();
        if let Some(index) = self.indexes.get(&key) {
            self.entries[*index] = profile;
        } else {
            self.indexes.insert(key, self.entries.len());
            self.entries.push(profile);
        }
    }
}

fn request_parameters(
    profile: &PublicModel,
    mode: &Mode,
    thinking: bool,
    effort: Option<&str>,
) -> Map<String, Value> {
    let mut parameters = mode.extra_params.clone();
    if profile.api_protocol == CHAT
        && !thinking
        && mode
            .id
            .rsplit('/')
            .next()
            .is_some_and(|name| name.to_ascii_lowercase().starts_with("qwen"))
    {
        parameters
            .entry("enable_thinking")
            .or_insert(Value::Bool(false));
    }
    let Some(effort) = effort
        .map(|value| value.trim().to_ascii_lowercase())
        .filter(|value| matches!(value.as_str(), "low" | "medium" | "high" | "xhigh" | "max"))
    else {
        return parameters;
    };
    match profile.api_protocol.as_str() {
        CHAT if thinking && profile.supports_reasoning_effort => {
            parameters.insert("reasoning_effort".into(), Value::String(effort));
        }
        RESPONSES => {
            let mut reasoning = parameters
                .get("reasoning")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_else(|| Map::from_iter([("summary".into(), json!("detailed"))]));
            reasoning.insert("effort".into(), Value::String(effort));
            parameters.insert("reasoning".into(), Value::Object(reasoning));
        }
        _ => {}
    }
    parameters
}

fn read_json(path: &Path) -> Value {
    fs::read(path)
        .ok()
        .and_then(|data| serde_json::from_slice(&data).ok())
        .unwrap_or_default()
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn enabled(value: &Value, key: &str) -> bool {
    value.get(key).is_none_or(truthy)
}

fn nonempty(value: &Value) -> Option<&str> {
    value.as_str().filter(|text| !text.is_empty())
}

fn scalar_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Number(number) if truthy(value) => number.to_string(),
        Value::Bool(true) => "True".into(),
        _ => String::new(),
    }
}

fn array(value: &Value) -> &[Value] {
    value.as_array().map(Vec::as_slice).unwrap_or_default()
}
