use serde_json::{Map, Value};

use super::{
    CHAT, Catalog, CatalogSource, Connection, Mode, Profile, PublicModel, array, enabled, nonempty,
    read_json, scalar_text, truthy,
};

pub(super) fn load_custom(source: &CatalogSource, catalog: &mut Catalog) {
    let raw = read_json(&source.custom_models_path());
    let items = if raw.is_array() { &raw } else { &raw["models"] };
    for item in array(items) {
        if let Some(profile) = custom_profile(source, item) {
            catalog.insert(profile);
        }
    }
}

fn custom_profile(source: &CatalogSource, item: &Value) -> Option<Profile> {
    let key = scalar_text(&item["model_name"]).trim().to_owned();
    if key.is_empty() {
        return None;
    }
    let base_url = env_ref(source, &item["url"])?
        .trim_end_matches('/')
        .to_owned();
    let api_key = env_ref(source, &item["apikey"])?;

    let settings = &item["thinkmode_status"];
    let mode_name = nonempty(&settings["type"])
        .or_else(|| nonempty(&settings["mode"]))
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    let (mut fast_id, mut thinking_id) = match mode_name.as_str() {
        "switch_model" => (
            model_id(&settings["fast_model_id"]),
            model_id(&settings["thinking_model_id"]),
        ),
        "param_toggle" => {
            let id = model_id(&settings["model_id"]);
            (id.clone(), id)
        }
        _ => {
            let id = model_id(&settings["model_id"]).or_else(|| model_id(&item["model_id"]));
            (id.clone(), id)
        }
    };
    if fast_id.is_none() {
        fast_id.clone_from(&thinking_id);
    }
    if thinking_id.is_none() {
        thinking_id.clone_from(&fast_id);
    }
    let fast_id = fast_id?;

    let name = nonempty(&item["display_name"]).unwrap_or(&key);
    let mut public = PublicModel::new(key.clone(), name.into());
    public.description = scalar_text(&item["description"]);
    public.visible = enabled(item, "visible");
    public.context_window = integer(&item["context_window"]);
    public.max_context_window = public.context_window.as_i64();
    public.max_output_tokens = integer(&item["max_output_tokens"]);
    public.multimodal = match item["multimodal"]
        .as_str()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "image" => "image",
        "video" => "video",
        "image,video" | "video,image" => "image,video",
        _ => "none",
    };
    let reasoning = scalar_text(&item["reasoning_capability"])
        .replace(' ', "")
        .to_ascii_lowercase();
    public.thinking_only = reasoning == "thinking";
    public.supports_thinking = public.thinking_only || reasoning == "fast,thinking";
    public.fast_only = !public.supports_thinking;
    public.supports_reasoning_effort =
        public.supports_thinking || truthy(&item["reasoning_effort"]);

    let extras = |mode| {
        let current_name = format!("{mode}_extra_parameter");
        let legacy_name = format!("{mode}_parameter");
        let overrides = settings[current_name]
            .as_object()
            .filter(|fields| !fields.is_empty())
            .or_else(|| settings[legacy_name].as_object());
        let mut params = item["extra_parameter"]
            .as_object()
            .cloned()
            .unwrap_or_default();
        if let Some(overrides) = overrides {
            params.extend(overrides.clone());
        }
        params
    };
    let thinking = public.supports_thinking.then(|| Mode {
        id: thinking_id.unwrap_or_else(|| fast_id.clone()),
        extra_params: extras("thinking"),
    });
    let hidden = !public.visible;
    Some(Profile {
        public,
        hidden,
        modelsdev_key: None,
        connection: super::ProfileConnection::ApiKey(Connection {
            base_url,
            api_key,
            headers: Map::new(),
            fast: Mode {
                id: fast_id,
                extra_params: extras("fast"),
            },
            thinking,
        }),
    })
}

fn model_id(value: &Value) -> Option<String> {
    let id = scalar_text(value).trim().to_owned();
    (!id.is_empty()).then_some(id)
}

fn env_ref(source: &CatalogSource, value: &Value) -> Option<String> {
    let value = scalar_text(value);
    let text = value.trim();
    if text.is_empty() {
        return None;
    }
    if let Some(key) = text
        .strip_prefix("${")
        .and_then(|rest| rest.strip_suffix('}'))
        .filter(|key| !key.is_empty())
    {
        return source.variable(key.trim()).map(str::to_owned);
    }
    if text
        .get(..4)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("env:"))
        && text.len() > 4
    {
        return source.variable(text[4..].trim()).map(str::to_owned);
    }
    if let Some(key) = text.strip_prefix('$').filter(|key| !key.is_empty()) {
        return source.variable(key.trim()).map(str::to_owned);
    }
    let letters = text.replace('_', "");
    if !letters.is_empty()
        && letters.chars().all(char::is_alphanumeric)
        && text == text.to_uppercase()
        && let Some(value) = source.variable(text)
    {
        return Some(value.to_owned());
    }
    Some(text.to_owned())
}

fn integer(value: &Value) -> Value {
    match value {
        Value::Bool(value) => Value::from(u8::from(*value)),
        Value::Number(value) if value.is_i64() || value.is_u64() => Value::Number(value.clone()),
        Value::Number(value) => value
            .as_f64()
            .filter(|number| *number >= i64::MIN as f64 && *number < i64::MAX as f64)
            .map(|number| Value::from(number as i64))
            .unwrap_or_default(),
        Value::String(text) => text
            .trim()
            .parse::<i64>()
            .map(Value::from)
            .unwrap_or_default(),
        _ => Value::Null,
    }
}

pub(super) fn positive_limit(value: &Value) -> Option<i64> {
    let tokens = integer(value).as_i64().filter(|&tokens| tokens > 0)?;
    match value {
        Value::Bool(_) => None,
        Value::Number(number) if number.is_f64() && number.as_f64() != Some(tokens as f64) => None,
        _ => Some(tokens),
    }
}

pub(super) fn load_providers(source: &CatalogSource, catalog: &mut Catalog) {
    let stored = read_json(&source.host_data_dir().join("providers.json"));
    let Some(records) = stored["providers"]
        .as_object()
        .filter(|records| !records.is_empty())
    else {
        return;
    };
    let definitions = read_json(&source.source_config("providers_catalog.json"));
    let cache = read_json(&source.host_data_dir().join("modelsdev_cache.json"));
    let snapshot = if cache["providers"].is_object() {
        cache
    } else {
        read_json(&source.source_config("modelsdev_snapshot.json"))
    };
    for (provider_id, record) in records {
        if !enabled(record, "enabled") {
            continue;
        }
        let Some(base_url) = nonempty(&record["base_url"]) else {
            continue;
        };
        let base_url = base_url.trim_end_matches('/');
        if base_url.is_empty() {
            continue;
        }
        let catalog_id = record["catalog_id"].as_str().unwrap_or_default();
        let definition = array(&definitions["providers"])
            .iter()
            .rev()
            .find(|definition| definition["id"].as_str() == Some(catalog_id))
            .unwrap_or(&Value::Null);
        let metadata_key = definition["modelsdev_key"].as_str().unwrap_or_default();
        let metadata = &snapshot["providers"][metadata_key];
        let name = provider_name(provider_id, record, definition);
        let api_key = scalar_text(&record["api_key"]);
        let headers = record["headers"].as_object().cloned().unwrap_or_default();
        for model in array(&record["models"]) {
            let Some(id) = model_id(&model["id"]) else {
                continue;
            };
            let mut public = PublicModel::new(
                format!("{provider_id}/{id}"),
                nonempty(&model["name"]).unwrap_or(&id).to_owned(),
            );
            let model_meta = &metadata["models"][&id];
            public.api_protocol = protocol(definition, model_meta, metadata, &id).to_owned();
            public.description.clone_from(&name);
            public.provider_type = Some("provider");
            public.provider_id = Some(provider_id.clone());
            public.provider_name = Some(name.clone());
            public.supports_thinking =
                model_meta["reasoning"].is_null() || truthy(&model_meta["reasoning"]);
            public.supports_reasoning_effort = public.supports_thinking;
            public.fast_only = !public.supports_thinking;
            public.multimodal = match model_meta["modalities_input"].as_array() {
                Some(modalities) => modalities_name(modalities),
                None => "image,video",
            };
            apply_provider_limits(&mut public, model, model_meta);
            catalog.insert(Profile {
                public,
                hidden: false,
                modelsdev_key: (!metadata_key.is_empty()).then(|| metadata_key.to_owned()),
                connection: super::ProfileConnection::ApiKey(Connection {
                    base_url: base_url.into(),
                    api_key: api_key.clone(),
                    headers: headers.clone(),
                    fast: Mode {
                        id,
                        extra_params: Map::new(),
                    },
                    thinking: None,
                }),
            });
        }
    }
}

fn apply_provider_limits(public: &mut PublicModel, model: &Value, metadata: &Value) {
    let reported_max = [
        "max_context_window_tokens",
        "max_context_window",
        "max_context_length",
    ]
    .iter()
    .find_map(|key| positive_limit(&model[*key]));
    let default = positive_limit(&model["default_context_window"])
        .or_else(|| positive_limit(&model["default_context_length"]))
        .or_else(|| reported_max.and_then(|_| positive_limit(&model["context_window"])));
    // Older discovery caches collapsed several upstream fields into context_length.
    let legacy = positive_limit(&model["context_length"]).or_else(|| {
        default
            .is_none()
            .then_some(&model["context_window"])
            .and_then(positive_limit)
    });
    let maximum = reported_max
        .or_else(|| positive_limit(&metadata["context"]))
        .or(legacy);
    public.set_context_windows(default, maximum);
    public.max_input_tokens = positive_limit(&model["max_input_tokens"])
        .or_else(|| positive_limit(&model["max_prompt_tokens"]))
        .or_else(|| positive_limit(&metadata["max_input"]));
    public.max_output_tokens = positive_limit(&model["max_output_tokens"])
        .or_else(|| positive_limit(&model["max_completion_tokens"]))
        .or_else(|| positive_limit(&model["max_tokens"]))
        .or_else(|| positive_limit(&metadata["max_output"]))
        .into();
}

fn provider_name(provider_id: &str, record: &Value, definition: &Value) -> String {
    let base = if record.get("base_name").is_some() {
        nonempty(&record["base_name"]).unwrap_or(provider_id)
    } else {
        nonempty(&definition["name"])
            .or_else(|| nonempty(&record["name"]))
            .unwrap_or(provider_id)
    };
    match record["display_index"].as_u64().filter(|index| *index > 0) {
        Some(index) => format!("{base}-{index}"),
        None => base.into(),
    }
}

fn protocol<'a>(definition: &'a Value, model: &Value, provider: &Value, model_id: &str) -> &'a str {
    if let Some(mapped) = nonempty(&definition["protocol_map"][model_id]) {
        return mapped;
    }
    if definition["protocol_source"] == "models.dev" {
        match nonempty(&model["npm"]).or_else(|| nonempty(&provider["npm"])) {
            Some("@ai-sdk/openai") => return "responses",
            Some("@ai-sdk/anthropic") => return "anthropic_messages",
            Some("@ai-sdk/google") => return "google",
            _ => {}
        }
    }
    CHAT
}

fn modalities_name(items: &[Value]) -> &'static str {
    match (
        items.iter().any(|item| item == "image"),
        items.iter().any(|item| item == "video"),
    ) {
        (true, true) => "image,video",
        (true, false) => "image",
        (false, true) => "video",
        (false, false) => "none",
    }
}
