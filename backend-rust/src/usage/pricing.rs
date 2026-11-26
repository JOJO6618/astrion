use std::{fs, path::PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Number, Value};

use super::{money::Usd, token_count};

pub(super) const MODELS_DEV: &str = "https://models.dev/api.json";

/// A request-time snapshot. Rates are USD per million tokens.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct PriceSnapshot {
    pub model_key: String,
    pub provider_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub modelsdev_key: Option<String>,
    pub rates: Rates,
    pub source: Option<String>,
    pub fetched_at: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Rates {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input: Option<Number>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cache_read: Option<Number>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<Number>,
}

/// Reader for the shared models.dev capability/price cache and bundled snapshot.
/// The model catalog supplies the trusted provider's models.dev key, including
/// the explicit OpenAI provider mapping for Codex OAuth.
#[derive(Clone, Debug)]
pub struct PricingSource {
    pub cache_path: PathBuf,
    pub bundled_snapshot_path: PathBuf,
}

impl PricingSource {
    pub fn capture(
        &self,
        model_key: &str,
        provider_id: Option<&str>,
        modelsdev_key: Option<&str>,
        model_id: &str,
    ) -> PriceSnapshot {
        let mut snapshot = PriceSnapshot {
            model_key: model_key.into(),
            provider_id: provider_id.map(str::to_owned),
            model_id: Some(model_id.into()),
            modelsdev_key: modelsdev_key.map(str::to_owned),
            ..PriceSnapshot::default()
        };
        let Some(provider) = modelsdev_key.filter(|key| !key.is_empty()) else {
            return snapshot;
        };
        let read = |path: &PathBuf| {
            fs::read(path)
                .ok()
                .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
                .filter(|data| data["providers"].is_object())
        };
        let Some(registry) = read(&self.cache_path).or_else(|| read(&self.bundled_snapshot_path))
        else {
            return snapshot;
        };
        let cost = &registry["providers"][provider]["models"][model_id]["cost"];
        if cost.is_object() {
            snapshot.rates = rates(cost);
            snapshot.source = registry
                .get("source")
                .map_or(Some(MODELS_DEV.into()), |v| v.as_str().map(str::to_owned));
            snapshot.fetched_at = registry["fetched_at"].as_str().map(str::to_owned);
        }
        snapshot
    }
}

pub(super) fn rates(cost: &Value) -> Rates {
    Rates {
        input: rate(cost, "input"),
        cache_read: rate(cost, "cache_read"),
        output: rate(cost, "output"),
    }
}

fn rate(cost: &Value, key: &str) -> Option<Number> {
    let Value::Number(number) = &cost[key] else {
        return None;
    };
    clean_rate(number.clone())
}

fn clean_rate(number: Number) -> Option<Number> {
    number.as_f64().filter(|n| n.is_finite() && *n >= 0.0)?;
    if number.as_f64() == Some(0.0) && number.to_string().starts_with('-') {
        return Some(Number::from(0));
    }
    number.to_string().parse::<Usd>().ok()?;
    Some(number)
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Tokens {
    pub input_tokens: u64,
    pub cached_input_tokens: u64,
    pub output_tokens: u64,
    pub total_tokens: u64,
    pub current_context_tokens: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Priced,
    Unpriced,
}

#[derive(Clone, Debug, Serialize)]
pub struct CostEntry {
    #[serde(flatten)]
    pub tokens: Tokens,
    pub cost_usd: Option<String>,
    pub status: Status,
    pub price: PriceSnapshot,
}

/// Normalize provider usage and compute the charge against a frozen price.
pub fn calculate_cost(raw: Option<&Value>, mut price: PriceSnapshot) -> CostEntry {
    let usage = raw.and_then(normalize_usage);
    let tokens = usage.unwrap_or_default();
    price.rates.input = price.rates.input.and_then(clean_rate);
    price.rates.cache_read = price.rates.cache_read.and_then(clean_rate);
    price.rates.output = price.rates.output.and_then(clean_rate);
    let amount = usage.and_then(|_| price.rates.calculate(tokens));
    CostEntry {
        tokens,
        status: if amount.is_some() {
            Status::Priced
        } else {
            Status::Unpriced
        },
        cost_usd: amount.map(|amount| amount.to_string()),
        price,
    }
}

impl Rates {
    fn calculate(&self, tokens: Tokens) -> Option<Usd> {
        let mut cost = Usd::default();
        for (count, rate) in [
            (
                tokens.input_tokens - tokens.cached_input_tokens,
                &self.input,
            ),
            (tokens.cached_input_tokens, &self.cache_read),
            (tokens.output_tokens, &self.output),
        ] {
            if count == 0 {
                continue;
            }
            let rate: Usd = rate.as_ref()?.to_string().parse().ok()?;
            cost.add(&rate.charge(count));
        }
        Some(cost)
    }
}

fn first_count(raw: &Value, keys: &[&str]) -> Option<u64> {
    keys.iter().find_map(|key| token_count(&raw[key]))
}

pub(crate) fn normalize_usage(raw: &Value) -> Option<Tokens> {
    const INPUT: &[&str] = &[
        "prompt_tokens",
        "input_tokens",
        "inputTokens",
        "promptTokens",
        "prefill_tokens",
        "promptTokenCount",
    ];
    const OUTPUT: &[&str] = &[
        "completion_tokens",
        "output_tokens",
        "outputTokens",
        "completionTokens",
        "generated_tokens",
        "generatedTokens",
        "candidatesTokenCount",
    ];
    const TOTAL: &[&str] = &[
        "total_tokens",
        "totalTokens",
        "total_token_count",
        "totalTokenCount",
    ];
    const CACHE: &[&str] = &[
        "cached_input_tokens",
        "cached_tokens",
        "cachedTokens",
        "prompt_cache_hit_tokens",
        "promptCacheHitTokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
        "cached_content_token_count",
        "cachedContentTokenCount",
    ];
    const CONTEXT: &[&str] = &[
        "current_context_tokens",
        "currentContextTokens",
        "context_tokens",
        "contextTokens",
    ];

    let input = INPUT
        .iter()
        .find_map(|key| token_count(&raw[key]).map(|n| (*key, n)));
    let mut prompt = input.map(|(_, n)| n);
    let mut output = first_count(raw, OUTPUT);
    let total = first_count(raw, TOTAL);
    let cache = first_count(raw, CACHE).or_else(|| {
        [
            "prompt_tokens_details",
            "input_tokens_details",
            "promptTokensDetails",
            "inputTokensDetails",
        ]
        .iter()
        .find_map(|key| first_count(&raw[key], CACHE))
    });
    if let Some(("input_tokens" | "inputTokens", n)) = input {
        let read = first_count(raw, &["cache_read_input_tokens", "cacheReadInputTokens"])
            .unwrap_or_default();
        let write = first_count(
            raw,
            &["cache_creation_input_tokens", "cacheCreationInputTokens"],
        )
        .unwrap_or_default();
        prompt = Some(n.saturating_add(read).saturating_add(write));
    }
    let details = raw["completion_tokens_details"]
        .as_object()
        .filter(|obj| !obj.is_empty())
        .or_else(|| raw["output_tokens_details"].as_object());
    if output.is_none()
        && let Some(details) = details
    {
        output = first_count(raw, &["reasoning_tokens", "reasoningTokens"]).or_else(|| {
            ["reasoning_tokens", "reasoningTokens"]
                .iter()
                .find_map(|key| details.get(*key).and_then(token_count))
        });
    }
    if prompt.is_none() && output.is_none() && total.is_none() {
        return None;
    }
    let input_tokens = prompt.unwrap_or_else(|| {
        total
            .unwrap_or_default()
            .saturating_sub(output.unwrap_or_default())
    });
    let output_tokens =
        output.unwrap_or_else(|| total.unwrap_or_default().saturating_sub(input_tokens));
    Some(Tokens {
        input_tokens,
        cached_input_tokens: cache.unwrap_or_default().min(input_tokens),
        output_tokens,
        total_tokens: total.unwrap_or_else(|| input_tokens.saturating_add(output_tokens)),
        current_context_tokens: first_count(raw, CONTEXT).unwrap_or(input_tokens),
    })
}
