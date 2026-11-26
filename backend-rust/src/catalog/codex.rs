use std::path::Path;

use serde_json::Value;

use super::profiles::positive_limit;
use super::{
    Catalog, CatalogSource, Profile, PublicModel, RESPONSES, ReasoningLevel, array, nonempty,
    read_json, scalar_text, truthy,
};

const LEGACY: &str = "openai-codex";
const DISPLAY_NAME: &str = "OpenAI（ChatGPT 订阅）";

/// Read model metadata for registered accounts. OAuth tokens are only checked for presence.
pub(super) fn load(source: &CatalogSource, catalog: &mut Catalog) {
    let data = source.host_data_dir();
    let index = crate::providers::discover_codex_accounts(data);
    if connected(&data.join("codex_auth.json")) {
        let record = index.get(LEGACY).unwrap_or(&Value::Null);
        let mut models = read_json(&data.join("codex_models.json"));
        if array(&models["models"]).is_empty()
            && let Some(cli_cache) = source.codex_cli_cache()
        {
            models = read_json(cli_cache);
        }
        append_models(source, catalog, LEGACY, record, &models);
    }
    let mut accounts: Vec<_> = index.iter().filter(|(id, _)| id.as_str() != LEGACY).collect();
    accounts.sort_by_key(|(key, record)| (record["created_at"].as_str().unwrap_or_default(), *key));
    for (id, record) in accounts {
        let file = format!("{id}.json");
        if connected(&data.join("codex_accounts").join(&file)) {
            let models = read_json(&data.join("codex_models").join(file));
            append_models(source, catalog, id, record, &models);
        }
    }
}

fn connected(path: &Path) -> bool {
    let auth = read_json(path);
    let tokens = &auth["tokens"];
    truthy(&tokens["access_token"]) && truthy(&tokens["refresh_token"])
}

fn append_models(source: &CatalogSource, catalog: &mut Catalog, account: &str, record: &Value, cache: &Value) {
    let suffix = record["display_hash"].as_str().unwrap_or_default().trim();
    let provider = if suffix.is_empty() {
        DISPLAY_NAME.to_owned()
    } else {
        format!("{DISPLAY_NAME}-{suffix}")
    };
    for info in array(&cache["models"]) {
        let slug = scalar_text(&info["slug"]);
        let slug = slug.trim();
        if slug.is_empty() || info["visibility"] != "list" || !truthy(&info["supported_in_api"]) {
            continue;
        }
        let mut public = PublicModel::new(
            format!("{account}/{slug}"),
            nonempty(&info["display_name"]).unwrap_or(slug).into(),
        );
        public.description = scalar_text(&info["description"]);
        public.supports_thinking = true;
        public.thinking_only = true;
        public.supports_reasoning_effort = true;
        public.is_chatgpt_model = true;
        public.supports_chatgpt_fast_mode = crate::model::codex::supports_fast_mode(info);
        if array(&info["input_modalities"])
            .iter()
            .any(|mode| mode == "image")
        {
            public.multimodal = "image";
        }
        public.set_context_windows(
            positive_limit(&info["context_window"]),
            positive_limit(&info["max_context_window"]),
        );
        public.api_protocol = RESPONSES.into();
        public.provider_type = Some("provider");
        public.provider_id = Some(account.into());
        public.provider_name = Some(provider.clone());
        public.supported_reasoning_levels = Some(
            array(&info["supported_reasoning_levels"])
                .iter()
                .filter(|level| truthy(&level["effort"]))
                .map(|level| ReasoningLevel {
                    effort: scalar_text(&level["effort"]),
                    description: scalar_text(&level["description"]),
                })
                .collect(),
        );
        public.default_reasoning_level =
            info["default_reasoning_level"].as_str().map(str::to_owned);
        catalog.insert(Profile {
            public,
            hidden: false,
            connection: super::ProfileConnection::Subscription(super::CodexSelection {
                providers: source.providers().clone(),
                account_id: account.to_owned(),
                model_info: info.clone(),
                override_file: source.host_data_dir().parent().unwrap_or(source.host_data_dir()).join("config/codex_instructions.txt"),
            }),
            modelsdev_key: Some("openai".into()),
        });
    }
}
