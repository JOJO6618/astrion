//! Python-compatible request cost ledger and its usage reports.
//!
//! Call `router` inside the authenticated Web router. Its paths must come from
//! the trusted Host workspace catalog and Chat scope. Model callers capture a
//! `PriceSnapshot` before dispatch and commit under a stable request ID.

mod exchange_rates;
mod ledger;
mod money;
mod price_refresh;
mod pricing;
mod refresh;
mod report;
mod sources;

pub use exchange_rates::ExchangeRateRefresh;
pub use ledger::{Actor, Ledger, RequestTiming, SpeedSample, SubAgent};
pub use price_refresh::{PriceIdentity, PriceRefresh};
pub(crate) use pricing::normalize_usage;
pub use pricing::{CostEntry, PriceSnapshot, PricingSource, Rates, Status, Tokens, calculate_cost};
pub use report::ReportQuery;
pub use sources::{UsageConfig, WorkspaceRef};
pub(crate) use sources::{quota_timestamp, weekly_gpt_values};

use std::{io, path::PathBuf, sync::Arc};

use axum::{
    Json, Router,
    extract::{Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde_json::{Value, json};

/// Routes for a scope that the server has already resolved. Apply the Web
/// authentication middleware to the returned router.
pub fn router(config: UsageConfig) -> Router {
    let exchange = config
        .exchange_rate_dir
        .as_ref()
        .map(ExchangeRateRefresh::new);
    routes(Arc::new(move || Ok(config.clone())), exchange)
}

/// Mount inside `WebAuth::protect_api`. Each report reads the trusted Host
/// workspace catalog so changes to the authorized workspace list take effect.
pub fn host_router(data_dir: impl Into<PathBuf>, workspace_catalog: impl Into<PathBuf>) -> Router {
    let (data_dir, workspace_catalog) = (data_dir.into(), workspace_catalog.into());
    let exchange = ExchangeRateRefresh::new(&data_dir);
    routes(
        Arc::new(move || UsageConfig::from_host_catalog(&data_dir, &workspace_catalog)),
        Some(exchange),
    )
}

type ResolveConfig = Arc<dyn Fn() -> io::Result<UsageConfig> + Send + Sync>;

#[derive(Clone)]
struct ReportState {
    resolve: ResolveConfig,
    exchange: Option<ExchangeRateRefresh>,
}

/// Host quota query exposed by both Python application entry points.
pub fn quota_router() -> Router {
    Router::new().route(
        "/api/usage",
        get(|| async { Json(json!({"success": true, "applicable": false, "data": null})) }),
    )
}

fn routes(resolve: ResolveConfig, exchange: Option<ExchangeRateRefresh>) -> Router {
    Router::new()
        .route("/api/usage-stats", get(usage_stats))
        .with_state(ReportState { resolve, exchange })
        .merge(quota_router())
}

async fn usage_stats(
    State(ReportState { resolve, exchange }): State<ReportState>,
    Query(query): Query<ReportQuery>,
) -> impl IntoResponse {
    match tokio::task::spawn_blocking(move || {
        Ok::<_, io::Error>(resolve()?.report(&query, jiff::Timestamp::now()))
    })
    .await
    {
        Ok(Ok(Ok(data))) => {
            if let Some(exchange) = exchange {
                exchange.refresh_if_stale(&data["exchange_rate"]);
            }
            (StatusCode::OK, Json(json!({"success": true, "data": data})))
        }
        Ok(Ok(Err(error))) => (StatusCode::BAD_REQUEST, failure(error)),
        _ => (
            StatusCode::INTERNAL_SERVER_ERROR,
            failure("usage stats unavailable"),
        ),
    }
}

fn failure(error: &str) -> Json<Value> {
    Json(json!({"success": false, "error": error}))
}

fn token_count(value: &Value) -> Option<u64> {
    match value {
        Value::Number(number) => number.as_u64().or_else(|| {
            number
                .as_f64()
                .filter(|n| *n >= 0.0 && *n < u64::MAX as f64)
                .map(|n| n as u64)
        }),
        Value::String(text) => text
            .trim()
            .strip_prefix('+')
            .unwrap_or(text.trim())
            .parse()
            .ok(),
        _ => None,
    }
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/usage_test_support.rs"]
pub(crate) mod test_support;

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/usage_contract.rs"]
mod tests;
