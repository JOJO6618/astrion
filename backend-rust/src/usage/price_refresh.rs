use std::{
    collections::BTreeSet,
    fs, io,
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, Instant},
};

use jiff::Timestamp;
use serde_json::{Map, Value, json};
use tokio::sync::Mutex;

use super::{
    PriceSnapshot, PricingSource,
    ledger::{atomic_json, exclusive_lock},
    pricing::{MODELS_DEV, rates},
    refresh::ReferenceSource,
    token_count,
};

const CACHE_VERSION: u64 = 3;
const CACHE_TTL: jiff::SignedDuration = jiff::SignedDuration::from_hours(6);
const FAILURE_BACKOFF: Duration = Duration::from_secs(60);

/// Trusted identity of the selected upstream account and models.dev provider.
/// Match the exact model ID sent on the wire when requesting prices.
#[derive(Clone, Debug, Default)]
pub struct PriceIdentity {
    pub provider_id: Option<String>,
    pub modelsdev_key: Option<String>,
}

/// Shared by a Host runtime so simultaneous model attempts await one update.
#[derive(Clone)]
pub struct PriceRefresh(Arc<Registry>);

struct Registry {
    paths: PricingSource,
    definitions: PathBuf,
    source: ReferenceSource,
    last_failure: Mutex<Option<Instant>>,
}

impl PriceRefresh {
    /// `config_dir` is the trusted resource `config/` containing the bundled
    /// models.dev snapshot and provider catalog, independent of user settings.
    pub fn new(data_dir: impl AsRef<Path>, config_dir: impl AsRef<Path>) -> Self {
        Self::with_source(data_dir.as_ref(), config_dir.as_ref(), MODELS_DEV)
    }

    fn with_source(data_dir: &Path, config_dir: &Path, url: &str) -> Self {
        Self(Arc::new(Registry {
            paths: PricingSource {
                cache_path: data_dir.join("modelsdev_cache.json"),
                bundled_snapshot_path: config_dir.join("modelsdev_snapshot.json"),
            },
            definitions: config_dir.join("providers_catalog.json"),
            source: ReferenceSource::new(url, 32 * 1024 * 1024),
            last_failure: Mutex::new(None),
        }))
    }

    /// Refresh stale pricing before snapshotting it, with the existing cache
    /// or bundled snapshot available immediately if the refresh fails.
    pub async fn capture(
        &self,
        model_key: &str,
        model_id: &str,
        identity: &PriceIdentity,
    ) -> PriceSnapshot {
        if let Some(key) = identity
            .modelsdev_key
            .as_deref()
            .filter(|key| !key.is_empty())
        {
            self.refresh(key).await;
        }
        let paths = self.0.paths.clone();
        let model_key = model_key.to_owned();
        let model_id = model_id.to_owned();
        let identity = identity.clone();
        let fallback = PriceSnapshot {
            model_key: model_key.clone(),
            provider_id: identity.provider_id.clone(),
            model_id: Some(model_id.clone()),
            modelsdev_key: identity.modelsdev_key.clone(),
            ..PriceSnapshot::default()
        };
        if identity.modelsdev_key.is_none() {
            return fallback;
        }
        tokio::task::spawn_blocking(move || {
            paths.capture(
                &model_key,
                identity.provider_id.as_deref(),
                identity.modelsdev_key.as_deref(),
                &model_id,
            )
        })
        .await
        .unwrap_or(fallback)
    }

    async fn refresh(&self, requested: &str) {
        let mut failure = self.0.last_failure.lock().await;
        if failure.is_some_and(|at| at.elapsed() < FAILURE_BACKOFF) {
            return;
        }
        let paths = self.0.clone();
        let fresh = tokio::task::spawn_blocking(move || {
            cache_is_fresh(&paths.paths.cache_path, Timestamp::now())
        })
        .await
        .unwrap_or(false);
        if fresh {
            return;
        }
        // Record the start before the await so an aborted attempt observes the
        // same backoff. A successful refresh clears it.
        *failure = Some(Instant::now());
        match self.fetch_and_store(requested).await {
            Ok(()) => *failure = None,
            Err(error) => eprintln!("usage pricing: refresh failed: {error}"),
        }
    }

    async fn fetch_and_store(&self, requested: &str) -> io::Result<()> {
        let upstream = self.0.source.fetch().await?;
        let paths = self.0.clone();
        let requested = requested.to_owned();
        tokio::task::spawn_blocking(move || {
            let cache = &paths.paths.cache_path;
            let root = cache
                .parent()
                .ok_or_else(|| io::Error::other("invalid pricing cache path"))?;
            fs::create_dir_all(root)?;
            let _lock = exclusive_lock(&root.join(".modelsdev_cache.lock"))?;
            let now = Timestamp::now();
            if cache_is_fresh(cache, now) {
                return Ok(());
            }
            let keys = configured_provider_keys(&paths.definitions, &requested);
            let payload = slim_registry(&upstream, &keys, now, &paths.source.url)?;
            atomic_json(cache, &payload)
        })
        .await
        .map_err(io::Error::other)?
    }
}

fn cache_is_fresh(path: &Path, now: Timestamp) -> bool {
    let Some(cache) = fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
    else {
        return false;
    };
    if cache["version"].as_u64() != Some(CACHE_VERSION) || !cache["providers"].is_object() {
        return false;
    }
    cache["fetched_at"]
        .as_str()
        .and_then(|time| time.parse::<Timestamp>().ok())
        .is_some_and(|time| time <= now && now.duration_since(time) < CACHE_TTL)
}

fn configured_provider_keys(path: &Path, requested: &str) -> BTreeSet<String> {
    let mut keys = BTreeSet::from([requested.to_owned()]);
    let definitions = fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    if let Some(entries) = definitions
        .as_ref()
        .and_then(|data| data["providers"].as_array())
    {
        keys.extend(
            entries
                .iter()
                .filter_map(|entry| entry["modelsdev_key"].as_str())
                .filter(|key| !key.is_empty())
                .map(str::to_owned),
        );
    }
    keys
}

fn slim_registry(
    full: &Value,
    keys: &BTreeSet<String>,
    now: Timestamp,
    source: &str,
) -> io::Result<Value> {
    let mut providers = Map::new();
    for key in keys {
        let upstream = &full[key];
        let Some(models) = upstream["models"].as_object() else {
            continue;
        };
        let slim: Map<_, _> = models
            .iter()
            .filter(|(_, model)| model.is_object())
            .map(|(id, model)| (id.clone(), slim_model(model)))
            .collect();
        providers.insert(key.clone(), json!({"npm": upstream["npm"], "models": slim}));
    }
    if providers.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "no configured providers in price registry",
        ));
    }
    let fetched_at = now.strftime("%Y-%m-%dT%H:%M:%SZ").to_string();
    Ok(
        json!({"version": CACHE_VERSION, "source": source, "fetched_at": fetched_at, "providers": providers}),
    )
}

fn slim_model(model: &Value) -> Value {
    let mut entry = Map::new();
    for (key, value) in [
        ("npm", &model["provider"]["npm"]),
        ("reasoning", &model["reasoning"]),
        ("modalities_input", &model["modalities"]["input"]),
    ] {
        if !value.is_null() {
            entry.insert(key.into(), value.clone());
        }
    }
    for (upstream, field) in [
        ("context", "context"),
        ("input", "max_input"),
        ("output", "max_output"),
    ] {
        let value = &model["limit"][upstream];
        if let Some(n) = token_count(value).filter(|n| (1..=i64::MAX as u64).contains(n))
            && value.as_f64().is_none_or(|raw| raw == n as f64)
        {
            entry.insert(field.into(), json!(n));
        }
    }
    if model["cost"].is_object() {
        entry.insert("cost".into(), json!(rates(&model["cost"])));
    }
    Value::Object(entry)
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/usage_price_refresh.rs"]
mod tests;
