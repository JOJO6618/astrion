use std::{
    fs, io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use jiff::{Timestamp, civil::Date, tz::TimeZone};
use serde_json::{Map, Value, json};

use super::{
    ledger::{atomic_json, exclusive_lock},
    refresh::ReferenceSource,
};

const FRANKFURTER: &str = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY";
const RETRY_BACKOFF: Duration = Duration::from_secs(3600);
const CACHE_FILENAME: &str = "exchange_rates.json";

/// Refresh the USD/CNY display rate in the background, retaining the last
/// valid reference rate on any fetch or filesystem error.
#[derive(Clone)]
pub struct ExchangeRateRefresh(Arc<RefreshState>);

struct RefreshState {
    directory: PathBuf,
    source: ReferenceSource,
    last_attempt: Mutex<Option<Instant>>,
}

impl ExchangeRateRefresh {
    pub fn new(directory: impl AsRef<Path>) -> Self {
        Self::with_source(directory.as_ref(), FRANKFURTER)
    }

    fn with_source(directory: &Path, url: &str) -> Self {
        Self(Arc::new(RefreshState {
            directory: directory.to_owned(),
            source: ReferenceSource::new(url, 64 * 1024),
            last_attempt: Mutex::new(None),
        }))
    }

    pub fn cached(&self) -> Value {
        cached_rate(Some(&self.0.directory), Timestamp::now())
    }

    /// Schedules one detached attempt for a stale report. Call within Tokio.
    pub fn refresh_if_stale(&self, cached: &Value) {
        if cached["stale"] != true {
            return;
        }
        let Ok(mut previous) = self.0.last_attempt.lock() else {
            return;
        };
        if previous.is_some_and(|at| at.elapsed() < RETRY_BACKOFF) {
            return;
        }
        *previous = Some(Instant::now());
        let service = self.clone();
        tokio::spawn(async move {
            if let Err(error) = service.fetch_and_store().await {
                eprintln!("usage exchange rate: refresh failed: {error}");
            }
        });
    }

    async fn fetch_and_store(&self) -> io::Result<()> {
        let response = self.0.source.fetch().await?;
        let now = Timestamp::now();
        let payload = reference_rate(&response, now, &self.0.source.url)?;
        let dir = self.0.directory.clone();
        tokio::task::spawn_blocking(move || {
            fs::create_dir_all(&dir)?;
            let _lock = exclusive_lock(&dir.join(".exchange_rates.lock"))?;
            if cached_rate(Some(&dir), now)["stale"] == true {
                atomic_json(&dir.join(CACHE_FILENAME), &payload)?;
            }
            Ok(())
        })
        .await
        .map_err(io::Error::other)?
    }
}

fn reference_rate(response: &Value, now: Timestamp, source: &str) -> io::Result<Value> {
    let invalid = || io::Error::new(io::ErrorKind::InvalidData, "invalid USD/CNY reference rate");
    if response["base"] != "USD" {
        return Err(invalid());
    }
    let rate = response["rates"]["CNY"]
        .as_f64()
        .filter(|rate| rate.is_finite() && *rate > 0.0)
        .ok_or_else(invalid)?;
    let date = response["date"]
        .as_str()
        .and_then(|date| date.parse::<Date>().ok())
        .ok_or_else(invalid)?;
    Ok(
        json!({"usd_cny": rate, "rate_date": date.to_string(), "fetched_at": now.to_string(), "source": source}),
    )
}

pub(super) fn cached_rate(directory: Option<&Path>, now: Timestamp) -> Value {
    let cached = directory
        .and_then(|root| fs::read(root.join(CACHE_FILENAME)).ok())
        .and_then(|bytes| serde_json::from_slice::<Map<String, Value>>(&bytes).ok())
        .filter(|cache| {
            cache
                .get("usd_cny")
                .and_then(|rate| rate.as_f64().or_else(|| rate.as_str()?.parse::<f64>().ok()))
                .is_some_and(|rate| rate.is_finite() && rate > 0.0)
        });
    let mut data = cached.unwrap_or_default();
    let today = now.to_zoned(TimeZone::UTC).date().to_string();
    let stale = !data
        .get("fetched_at")
        .and_then(Value::as_str)
        .is_some_and(|at| at.starts_with(&today));
    data.insert("stale".into(), json!(stale));
    Value::Object(data)
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/usage_exchange_rates.rs"]
mod tests;
