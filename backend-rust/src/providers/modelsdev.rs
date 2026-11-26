//! models.dev snapshot/cache projection and the best-effort online refresh.
//!
//! The providers API only needs protocol resolution here (to report
//! `unsupported_protocol_count`). Capability enrichment still belongs to the
//! model catalog, but the cache file itself is shared with that catalog, so the
//! refresh keeps Python's v3 slim schema, 6-hour TTL, and field filtering.
//!
//! Refresh semantics (`modules/modelsdev_registry.py::refresh_cache`):
//! - a cached `version=3` document younger than 6 hours is reused as-is;
//! - otherwise fetch the full `api.json`, keep only the catalog's
//!   `modelsdev_key` providers and the protocol/enrichment fields, then
//!   atomically replace `<DATA_DIR>/modelsdev_cache.json` (0600);
//! - any request/parse/write error leaves the previous cache untouched and is
//!   ignored by `connect`/`refresh`.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    io::{self, Write},
    path::Path,
    time::Duration,
};

use serde_json::{Map, Value, json};
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time};

use super::store::replace_file;

pub(super) const API_URL: &str = "https://models.dev/api.json";
const CACHE_FILE: &str = "modelsdev_cache.json";
const CACHE_VERSION: i64 = 3;
const CACHE_TTL_SECONDS: i64 = 6 * 60 * 60;
const FETCH_TIMEOUT: Duration = Duration::from_secs(60);
const SUPPORTED: [&str; 2] = ["chat_completions", "responses"];

#[derive(Default)]
pub(super) struct Registry {
    providers: BTreeMap<String, Value>,
}

impl Registry {
    pub(super) fn load(repo_config: &Path, data_dir: &Path) -> Self {
        let cache = data_dir.join(CACHE_FILE);
        let snapshot = repo_config.join("modelsdev_snapshot.json");
        let active = if cache.exists() {
            cache
        } else {
            snapshot.clone()
        };
        let data = read_providers(&active)
            .or_else(|| read_providers(&snapshot))
            .unwrap_or_default();
        Self { providers: data }
    }

    /// Catalog `protocol_map` wins, then models.dev `npm` for entries that opt
    /// into `protocol_source="models.dev"`, then Chat Completions.
    pub(super) fn protocol(&self, entry: &Value, model_id: &str) -> &'static str {
        if let Some(hit) = entry["protocol_map"]
            .get(model_id)
            .and_then(Value::as_str)
            .filter(|hit| !hit.is_empty())
        {
            return match hit {
                "responses" => "responses",
                "anthropic_messages" => "anthropic_messages",
                "google" => "google",
                _ => "chat_completions",
            };
        }
        if entry["protocol_source"].as_str() == Some("models.dev") {
            let key = entry["modelsdev_key"].as_str().unwrap_or_default();
            if let Some(provider) = self.providers.get(key) {
                let npm = provider["models"][model_id]["npm"]
                    .as_str()
                    .filter(|npm| !npm.is_empty())
                    .or_else(|| provider["npm"].as_str());
                match npm {
                    Some("@ai-sdk/openai") => return "responses",
                    Some("@ai-sdk/anthropic") => return "anthropic_messages",
                    Some("@ai-sdk/google") => return "google",
                    _ => {}
                }
            }
        }
        "chat_completions"
    }

    /// Count discovered models whose protocol this request layer cannot call.
    pub(super) fn unsupported_count(&self, entry: &Value, models: &[Value]) -> usize {
        models
            .iter()
            .filter_map(|model| model.get("id").and_then(Value::as_str))
            .filter(|id| !id.trim().is_empty())
            .filter(|id| !SUPPORTED.contains(&self.protocol(entry, id)))
            .count()
    }
}

/// Best-effort refresh; `Ok(false)` means the cache is still fresh.
pub(super) async fn refresh_if_stale(
    client: &reqwest::Client,
    url: &str,
    keys: &BTreeSet<String>,
    data_dir: &Path,
) -> Result<bool, String> {
    let path = data_dir.join(CACHE_FILE);
    if cache_is_fresh(&path) {
        return Ok(false);
    }
    let response = client
        .get(url)
        .timeout(FETCH_TIMEOUT)
        .send()
        .await
        .map_err(|error| format!("request_error: {error}"))?;
    if response.status() != reqwest::StatusCode::OK {
        return Err(format!("http_{}", response.status().as_u16()));
    }
    let full: Value = response
        .json()
        .await
        .map_err(|error| format!("request_error: {error}"))?;
    if !full.is_object() {
        return Err("bad_payload".into());
    }
    let slim = slim_payload(&full, keys);
    let write_path = path.clone();
    tokio::task::spawn_blocking(move || write_cache(&write_path, &slim))
        .await
        .map_err(|error| format!("write_error: {error}"))?
        .map_err(|error| format!("write_error: {error}"))?;
    Ok(true)
}

/// Python only skips when `version == 3` and the strict
/// `%Y-%m-%dT%H:%M:%SZ` timestamp is younger than six hours (a future
/// timestamp also counts as fresh).
fn cache_is_fresh(path: &Path) -> bool {
    let Ok(bytes) = fs::read(path) else {
        return false;
    };
    let Ok(cached) = serde_json::from_slice::<Value>(&bytes) else {
        return false;
    };
    if cached["version"].as_i64() != Some(CACHE_VERSION) {
        return false;
    }
    let Some(fetched_at) = cached["fetched_at"].as_str().and_then(parse_fetched_at) else {
        return false;
    };
    OffsetDateTime::now_utc().unix_timestamp() - fetched_at < CACHE_TTL_SECONDS
}

fn parse_fetched_at(text: &str) -> Option<i64> {
    let bytes = text.as_bytes();
    if bytes.len() != 20
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
        || bytes[19] != b'Z'
    {
        return None;
    }
    let year: i32 = text.get(0..4)?.parse().ok()?;
    let month = Month::try_from(text.get(5..7)?.parse::<u8>().ok()?).ok()?;
    let day: u8 = text.get(8..10)?.parse().ok()?;
    let hour: u8 = text.get(11..13)?.parse().ok()?;
    let minute: u8 = text.get(14..16)?.parse().ok()?;
    let second: u8 = text.get(17..19)?.parse().ok()?;
    let date = Date::from_calendar_date(year, month, day).ok()?;
    let time = Time::from_hms(hour, minute, second).ok()?;
    Some(
        PrimitiveDateTime::new(date, time)
            .assume_utc()
            .unix_timestamp(),
    )
}

fn now_stamp() -> String {
    let now = OffsetDateTime::now_utc();
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second()
    )
}

/// Full `api.json` → Python's slim v3 shape. Provider keys are sorted; model
/// and field order follow the upstream document (`serde_json` preserve_order).
fn slim_payload(full: &Value, keys: &BTreeSet<String>) -> Value {
    let mut providers = Map::new();
    for key in keys {
        let Some(provider) = full.get(key).and_then(Value::as_object) else {
            continue;
        };
        let mut models = Map::new();
        if let Some(source) = provider.get("models").and_then(Value::as_object) {
            for (model_id, model) in source {
                let Some(model) = model.as_object() else {
                    continue;
                };
                models.insert(model_id.clone(), slim_model(model));
            }
        }
        let mut entry = Map::new();
        entry.insert(
            "npm".into(),
            provider.get("npm").cloned().unwrap_or(Value::Null),
        );
        entry.insert("models".into(), Value::Object(models));
        providers.insert(key.clone(), Value::Object(entry));
    }
    json!({
        "version": CACHE_VERSION,
        "source": API_URL,
        "fetched_at": now_stamp(),
        "providers": providers,
    })
}

fn slim_model(model: &Map<String, Value>) -> Value {
    let mut entry = Map::new();
    if let Some(npm) = model
        .get("provider")
        .and_then(Value::as_object)
        .and_then(|provider| provider.get("npm"))
        .filter(|npm| super::truthy(npm))
    {
        entry.insert("npm".into(), npm.clone());
    }
    if let Some(reasoning) = model.get("reasoning").filter(|value| !value.is_null()) {
        entry.insert("reasoning".into(), json!(super::truthy(reasoning)));
    }
    if let Some(modalities) = model
        .get("modalities")
        .and_then(Value::as_object)
        .and_then(|modalities| modalities.get("input"))
        .and_then(Value::as_array)
    {
        entry.insert("modalities_input".into(), Value::Array(modalities.clone()));
    }
    if let Some(limits) = model.get("limit").and_then(Value::as_object) {
        for (source, destination) in [
            ("context", "context"),
            ("input", "max_input"),
            ("output", "max_output"),
        ] {
            if let Some(tokens) = limits.get(source).and_then(positive_token_limit) {
                entry.insert(destination.into(), json!(tokens));
            }
        }
    }
    if let Some(cost) = model.get("cost").and_then(Value::as_object) {
        entry.insert("cost".into(), clean_rates(cost));
    }
    Value::Object(entry)
}

/// Python `config.model_limits.positive_token_limit`: positive integer,
/// bool excluded, numeric strings accepted, integral floats accepted.
fn positive_token_limit(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) => {
            if let Some(tokens) = number.as_i64() {
                return (tokens > 0).then_some(tokens);
            }
            if let Some(tokens) = number.as_u64() {
                return (tokens > 0 && tokens <= i64::MAX as u64).then_some(tokens as i64);
            }
            let tokens = number.as_f64()?;
            (tokens.is_finite()
                && tokens > 0.0
                && tokens.fract() == 0.0
                && tokens <= i64::MAX as f64)
                .then_some(tokens as i64)
        }
        Value::String(text) => text.trim().parse::<i64>().ok().filter(|tokens| *tokens > 0),
        _ => None,
    }
}

/// Python `modules.api_pricing.clean_rates`: finite non-negative numbers only.
fn clean_rates(cost: &Map<String, Value>) -> Value {
    let mut rates = Map::new();
    for key in ["input", "cache_read", "output"] {
        if let Some(number) = cost.get(key).and_then(Value::as_f64)
            && number.is_finite()
            && number >= 0.0
        {
            rates.insert(key.into(), json!(number));
        }
    }
    Value::Object(rates)
}

fn write_cache(path: &Path, value: &Value) -> io::Result<()> {
    let directory = path.parent().unwrap_or(Path::new("."));
    fs::create_dir_all(directory)?;
    let temporary = directory.join(format!(".{CACHE_FILE}.{}.tmp", uuid::Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    let result = (|| {
        let mut writer = io::BufWriter::new(&mut file);
        serde_json::to_writer_pretty(&mut writer, value)?;
        writer.flush()?;
        drop(writer);
        file.sync_all()?;
        replace_file(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn read_providers(path: &Path) -> Option<BTreeMap<String, Value>> {
    let raw: Value = serde_json::from_slice(&fs::read(path).ok()?).ok()?;
    let providers = raw.get("providers")?.as_object()?;
    Some(
        providers
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    )
}
