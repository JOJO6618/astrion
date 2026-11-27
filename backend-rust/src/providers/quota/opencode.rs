use std::{sync::Arc, time::Instant};

use axum::{
    extract::{Query, State},
    response::Response,
};
use ring::digest::{SHA256, digest};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{CachedOpenCode, QuotaState, USAGE_CACHE_TTL, USAGE_TIMEOUT, success_response};
use crate::providers::Providers;

#[derive(Deserialize)]
pub(super) struct OpenCodeQuery {
    provider_id: Option<String>,
    refresh: Option<String>,
}

fn error(provider_id: &str, error: &str) -> Response {
    success_response(json!({"success": false, "error": error, "provider_id": provider_id}))
}

fn is_go_url(record: &Value) -> bool {
    record["base_url"]
        .as_str()
        .and_then(|base| reqwest::Url::parse(base.trim()).ok())
        .is_some_and(|url| {
            matches!(url.scheme(), "http" | "https")
                && url.host_str() == Some("opencode.ai")
                && url.path().contains("/zen/go")
        })
}

fn enabled(record: &Value) -> bool {
    record["enabled"].as_bool().unwrap_or(true)
}

fn key(record: &Value) -> &str {
    record["api_key"].as_str().unwrap_or_default().trim()
}

fn windows(payload: &Value) -> Vec<Value> {
    ["rolling", "weekly", "monthly"]
        .into_iter()
        .filter_map(|id| {
            let window = &payload["usage"][id];
            let percent = window["percent"]
                .as_f64()?
                .round_ties_even()
                .clamp(0.0, 100.0) as u64;
            let reset = window["resetsAt"]
                .as_str()
                .filter(|value| !value.is_empty())
                .or_else(|| window["resets_at"].as_str())
                .unwrap_or_default();
            Some(json!({"key": id, "percent": percent, "reset_at": reset,
            "status": window["status"].as_str().unwrap_or_default()}))
        })
        .collect()
}

pub(super) async fn opencode_usage(
    State(state): State<Arc<QuotaState>>,
    Query(query): Query<OpenCodeQuery>,
) -> Response {
    let provider_id = query.provider_id.as_deref().unwrap_or_default().trim();
    let config = state.config.clone();
    let snapshot = tokio::task::spawn_blocking(move || {
        let providers = Providers::new(config);
        let entries = providers.entries();
        providers.store.snapshot(&providers.names(&entries))
    })
    .await;
    let Ok(snapshot) = snapshot else {
        return error(provider_id, "provider_not_found");
    };
    let selected = if provider_id.is_empty() {
        snapshot
            .records()
            .iter()
            .find(|(_, record)| enabled(record) && is_go_url(record) && !key(record).is_empty())
            .map(|(id, record)| (id.as_str(), record))
    } else {
        snapshot
            .record(provider_id)
            .map(|record| (provider_id, record))
    };
    let Some((connection, record)) = selected.filter(|(_, record)| record.is_object()) else {
        return error(provider_id, "provider_not_found");
    };
    if !enabled(record) {
        return error(provider_id, "provider_disabled");
    }
    if !is_go_url(record) {
        return error(provider_id, "not_opencode_provider");
    }
    let credential = key(record);
    if credential.is_empty() {
        return error(provider_id, "credential_missing");
    }
    let label = record["name"]
        .as_str()
        .filter(|name| !name.is_empty())
        .or_else(|| record["base_name"].as_str().filter(|name| !name.is_empty()))
        .unwrap_or("OpenCode Go");
    let fingerprint: [u8; 32] = digest(&SHA256, credential.as_bytes())
        .as_ref()
        .try_into()
        .unwrap();
    let cache_key = (provider_id.to_owned(), connection.to_owned());
    if query.refresh.as_deref() != Some("1")
        && let Some(cached) = state.opencode_cache().entries.get(&cache_key)
        && cached.fingerprint == fingerprint
        && cached.fetched_at.elapsed() < USAGE_CACHE_TTL
    {
        let mut payload = cached.payload.clone();
        payload["label"] = json!(label);
        return success_response(payload);
    }
    let client = match state.config.client.clone().map(Ok).unwrap_or_else(|| {
        crate::net::apply(
            reqwest::Client::builder()
                .connect_timeout(std::time::Duration::from_secs(5))
                .redirect(reqwest::redirect::Policy::none()),
            crate::net::resolve(None).as_ref(),
        )
        .build()
    }) {
        Ok(client) => client,
        Err(_) => return error(provider_id, "network_error"),
    };
    let response = match client
        .get(&state.endpoints.opencode_usage_url)
        .bearer_auth(credential)
        .header("accept", "application/json")
        .timeout(USAGE_TIMEOUT)
        .send()
        .await
    {
        Ok(response) => response,
        Err(failure) => {
            return error(
                provider_id,
                if failure.is_timeout() {
                    "timeout"
                } else {
                    "network_error"
                },
            );
        }
    };
    match response.status().as_u16() {
        200 => {}
        401 | 403 => return error(provider_id, "invalid_credential"),
        404 => return error(provider_id, "not_subscribed"),
        status => return error(provider_id, &format!("http_{status}")),
    }
    let Ok(payload) = response.json::<Value>().await else {
        return error(provider_id, "invalid_response");
    };
    let windows = windows(&payload);
    if windows.is_empty() {
        return error(provider_id, "invalid_response");
    }
    let result = json!({"success": true, "source": "opencode", "provider_id": provider_id,
        "label": label, "windows": windows, "fetched_at": jiff::Timestamp::now().as_second()});
    state.opencode_cache().entries.insert(
        cache_key,
        CachedOpenCode {
            payload: result.clone(),
            fingerprint,
            fetched_at: Instant::now(),
        },
    );
    success_response(result)
}
