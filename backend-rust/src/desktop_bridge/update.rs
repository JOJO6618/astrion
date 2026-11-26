use super::{Bridge, BridgeError, Desktop, truthy};
use axum::{
    Json,
    extract::{RawQuery, State},
    http::Method,
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};
use std::{
    cmp::Ordering,
    sync::Arc,
    time::{Duration, Instant, SystemTime},
};
use tokio::sync::Mutex;

const MANIFEST_BASE: &str = "https://astrion.cyjai.com/downloads";
const CACHE_TTL: Duration = Duration::from_secs(30);
pub(super) type Cache = Mutex<Option<(Instant, Value)>>;

pub(super) async fn check(
    State(bridge): State<Arc<Bridge>>,
    RawQuery(query): RawQuery,
) -> Response {
    let result = check_native(&bridge, force_refresh(query.as_deref().unwrap_or_default())).await;
    match result {
        Ok(payload) => Json(payload).into_response(),
        Err(error) => error.response(&bridge),
    }
}

async fn check_native(bridge: &Bridge, force: bool) -> Result<Value, BridgeError> {
    let desktop = bridge.desktop()?;
    let (channel, file) = match (std::env::consts::OS, std::env::consts::ARCH) {
        ("macos", "aarch64") => ("darwin-aarch64", "latest-mac.json"),
        ("macos", _) => ("darwin-x86_64", "latest-mac.json"),
        ("windows", _) => ("windows-x86_64", "latest-windows-x86_64.json"),
        _ => return Err(BridgeError::UnsupportedPlatform),
    };
    check_channel(
        bridge,
        desktop,
        force,
        channel,
        &format!("{MANIFEST_BASE}/{file}"),
    )
    .await
}

fn force_refresh(query: &str) -> bool {
    serde_urlencoded::from_str::<Vec<(String, String)>>(query)
        .unwrap_or_default()
        .into_iter()
        .find(|(key, _)| key == "force")
        .is_some_and(|(_, value)| value == "1")
}

async fn check_channel(
    bridge: &Bridge,
    desktop: &Desktop,
    force: bool,
    channel: &str,
    url: &str,
) -> Result<Value, BridgeError> {
    let started = Instant::now();
    if !force
        && let Some((at, cached)) = bridge.update_cache.lock().await.as_ref()
        && at.elapsed() < CACHE_TTL
    {
        return Ok(cached.clone());
    }
    let checked_at = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let policy = if desktop.policy_protocol {
        let route = if force {
            "/update/policy?force=1"
        } else {
            "/update/policy"
        };
        bridge
            .request(Method::GET, route, 8, None)
            .await
            .ok()
            .and_then(|body| {
                body.get("policy")
                    .filter(|value| value.is_object())
                    .cloned()
            })
            .unwrap_or_else(|| json!({}))
    } else {
        json!({})
    };
    let manual = policy["delivery"] == "manual";
    let manifest = match fetch_manifest(bridge, desktop, url).await {
        Ok(manifest) => manifest,
        Err(_) if truthy(&policy["required"]) || manual => json!({}),
        Err(error) => return Err(error),
    };
    let latest = manifest
        .get("version")
        .filter(|value| truthy(value))
        .unwrap_or(&policy["latest_version"]);
    let latest = text(latest);
    let latest = latest.trim();
    let available = !latest.is_empty() && newer_version(latest, &desktop.version)
        || policy["required"] == true
        || manual;
    let payload = json!({
        "success": true,
        "data": {
            "current_version": desktop.version,
            "current_build_id": desktop.build_id,
            "latest_version": if latest.is_empty() { &desktop.version } else { latest },
            "update_available": available,
            "policy": policy,
            "notes": text(&manifest["notes"]),
            "pub_date": text(&manifest["pub_date"]),
            "channel": channel,
            "bridge_available": desktop.port.is_some(),
            "checked_at": checked_at,
        },
    });
    *bridge.update_cache.lock().await = Some((started, payload.clone()));
    Ok(payload)
}

async fn fetch_manifest(
    bridge: &Bridge,
    desktop: &Desktop,
    url: &str,
) -> Result<Value, BridgeError> {
    let error = |error: reqwest::Error| BridgeError::CheckFailed(error.to_string());
    let response = bridge
        .manifest
        .get(url)
        .timeout(Duration::from_secs(8))
        .query(&[
            ("current_version", &desktop.version),
            ("build_id", &desktop.build_id),
            ("platform", &desktop.platform),
            ("arch", &desktop.arch),
        ])
        .send()
        .await
        .map_err(error)?;
    if !response.status().is_success() {
        return Err(BridgeError::CheckFailed(format!(
            "HTTP {} for {url}",
            response.status()
        )));
    }
    let value: Value = response.json().await.map_err(error)?;
    if !value.is_object() {
        return Err(BridgeError::CheckFailed(
            "Expected an update manifest object".into(),
        ));
    }
    Ok(value)
}

fn text(value: &Value) -> String {
    match value {
        _ if !truthy(value) => String::new(),
        Value::String(value) => value.clone(),
        Value::Bool(_) => "True".into(),
        value => value.to_string(),
    }
}

fn version_parts(text: &str) -> impl Iterator<Item = String> + '_ {
    text.trim().split('.').map(|part| {
        let digits: String = part.chars().filter(char::is_ascii_digit).collect();
        digits.trim_start_matches('0').to_owned()
    })
}

fn newer_version(latest: &str, current: &str) -> bool {
    let mut current = version_parts(current);
    for latest in version_parts(latest) {
        let Some(current) = current.next() else {
            return true;
        };
        match latest
            .len()
            .cmp(&current.len())
            .then_with(|| latest.cmp(&current))
        {
            Ordering::Less => return false,
            Ordering::Greater => return true,
            Ordering::Equal => (),
        }
    }
    false
}

#[cfg(test)]
#[path = "../../../test/2026-11-25_Rust桌面壳桥接/update_tests.rs"]
mod tests;
