use std::{
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

use axum::{
    body::Bytes,
    extract::{Query, State},
    response::Response,
};
use jiff::Timestamp;
use serde_json::{Value, json};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use tokio::time::Instant;

use super::{
    AccountQuery, QuotaState, WEEKLY_TIMEOUT, requested_account, resolve_account, success_response,
    wham_request,
};
use crate::{
    providers::{codex, codex_oauth, object_body},
    usage::{quota_timestamp, weekly_gpt_values},
};

const FILENAME: &str = "weekly_quota.json";
const WEEK_SECONDS: f64 = 604_800.0;
const WINDOW_TOLERANCE: f64 = 0.15;
const SAMPLE_GAP: i64 = 300;
const SAMPLE_INTERVAL: Duration = Duration::from_secs(3600);
const HISTORY_LIMIT: usize = 26;
const DECREASE_EPSILON: f64 = 0.01;

pub(super) fn start(state: &Arc<QuotaState>, periodic: bool) {
    let Ok(runtime) = tokio::runtime::Handle::try_current() else {
        return;
    };
    let wake = state.weekly_refresh.clone();
    let state = Arc::downgrade(state);
    runtime.spawn(async move {
        let mut timer =
            tokio::time::interval_at(Instant::now() + Duration::from_secs(20), SAMPLE_INTERVAL);
        timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                _ = timer.tick(), if periodic => {},
                _ = wake.notified() => {},
            }
            let Some(state) = state.upgrade() else { break };
            sample(&state).await;
        }
    });
}

pub(super) async fn weekly_value(
    State(state): State<Arc<QuotaState>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Response {
    let data_dir = state.config.data_dir.clone();
    let account = match resolve_account(
        data_dir.clone(),
        requested_account(&query, &object_body(&body)),
    )
    .await
    {
        Ok(account) => account,
        Err(response) => return response,
    };
    let id = account.id;
    let reply = tokio::task::spawn_blocking(move || snapshot(&data_dir, &id))
        .await
        .unwrap_or_else(|_| json!({"success": false, "error": "weekly_state_unavailable"}));
    if query.refresh.as_deref() == Some("1") {
        state.weekly_refresh.notify_one();
    }
    success_response(reply)
}

fn snapshot(data: &Path, account_id: &str) -> Value {
    let state = read_state(data);
    let account = &state["accounts"][account_id];
    let current = account["current"].as_object();
    let mut history: Vec<_> = account["history"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|entry| entry.is_object())
        .cloned()
        .collect();
    history
        .sort_by_key(|entry| std::cmp::Reverse(entry["period_end"].as_i64().unwrap_or_default()));
    json!({"success": true, "account_id": account_id, "current": current, "history": history,
        "last_attempt_at": state["last_attempt_at"], "last_error": state["last_error"].as_str().unwrap_or_default()})
}

fn read_state(data: &Path) -> Value {
    let current = codex_oauth::read_object(&data.join(FILENAME));
    if current["accounts"].is_object() {
        current
    } else {
        json!({"version": 1, "accounts": {}, "last_attempt_at": null, "last_error": ""})
    }
}

async fn sample(state: &QuotaState) {
    let data = state.config.data_dir.clone();
    let loaded = tokio::task::spawn_blocking(move || {
        let state = read_state(&data);
        let accounts = codex::accounts(&data)
            .into_iter()
            .filter(|account| account.connected)
            .map(|account| account.id)
            .collect::<Vec<_>>();
        (state, accounts)
    })
    .await;
    let Ok((mut stored, accounts)) = loaded else {
        return;
    };
    let recent = stored["last_attempt_at"]
        .as_str()
        .and_then(quota_timestamp)
        .is_some_and(|last| {
            Timestamp::now()
                .as_second()
                .saturating_sub(last.as_second())
                < SAMPLE_GAP
        });
    if recent
        && stored["accounts"]
            .as_object()
            .is_some_and(|accounts| !accounts.is_empty())
    {
        return;
    }
    stored["last_attempt_at"] = json!(now_iso());
    let mut failures = Vec::new();
    for account in accounts {
        match sample_account(state, &account).await {
            Ok(mut sample) => update_account(&mut stored["accounts"][&account], &mut sample),
            Err(error) => failures.push(format!("{account}: {error}")),
        }
    }
    stored["last_error"] = json!(failures.join("; ").chars().take(500).collect::<String>());
    let path = state.config.data_dir.join(FILENAME);
    let _ = tokio::task::spawn_blocking(move || {
        codex_oauth::write_private_json_blocking(&path, &stored)
    })
    .await;
}

struct WeeklyWindow {
    seconds: i64,
    reset: i64,
    used: f64,
}

fn weekly_window(payload: &Value) -> Option<WeeklyWindow> {
    ["primary_window", "secondary_window"]
        .into_iter()
        .filter_map(|id| {
            let window = &payload["rate_limit"][id];
            let used = window["used_percent"].as_f64()?;
            let seconds = window["limit_window_seconds"].as_f64()?;
            let reset = window["reset_at"].as_f64()?;
            let gap = (seconds - WEEK_SECONDS).abs();
            if !(0.0..=100.0).contains(&used)
                || !(1.0..=253_402_300_799.0).contains(&reset)
                || gap > WEEK_SECONDS * WINDOW_TOLERANCE
            {
                return None;
            }
            Some((
                gap,
                WeeklyWindow {
                    seconds: seconds.round_ties_even() as i64,
                    reset: reset as i64,
                    used,
                },
            ))
        })
        .min_by(|(left, _), (right, _)| left.total_cmp(right))
        .map(|(_, window)| window)
}

async fn sample_account(state: &QuotaState, account: &str) -> Result<Value, String> {
    let (response, _) = wham_request(
        state,
        account,
        "GET",
        &state.endpoints.usage_url,
        None,
        WEEKLY_TIMEOUT,
        false,
    )
    .await
    .map_err(|(_, error)| error)?;
    let window = weekly_window(&response).ok_or("weekly_window_unavailable")?;
    let start = window.reset - window.seconds;
    let now = Timestamp::now();
    let end = now.min(Timestamp::new(window.reset, 0).map_err(|error| error.to_string())?);
    let data: PathBuf = state.config.data_dir.clone();
    let (value, max) =
        tokio::task::spawn_blocking(move || weekly_gpt_values(&data, start, end, window.used))
            .await
            .map_err(|error| error.to_string())?;
    Ok(
        json!({"period_start": start, "period_end": window.reset, "resets_at": window.reset,
        "window_seconds": window.seconds, "used_percent": (window.used * 100.0).round_ties_even() / 100.0,
        "api_value_usd": value, "max_value_usd": max, "sampled_at": now_iso(),
        "stale": false}),
    )
}

fn update_account(account: &mut Value, sample: &mut Value) {
    if !account.is_object() {
        *account = json!({"current": null, "history": []});
    }
    if let Some(previous) = account["current"].as_object()
        && (previous["resets_at"] != sample["resets_at"]
            || sample["used_percent"].as_f64().unwrap_or_default()
                < previous["used_percent"].as_f64().unwrap_or_default() - DECREASE_EPSILON)
    {
        let mut archived = previous.clone();
        archived.insert("archived_at".into(), json!(now_iso()));
        if !account["history"].is_array() {
            account["history"] = json!([]);
        }
        let history = account["history"].as_array_mut().unwrap();
        history.insert(0, Value::Object(archived));
        history.truncate(HISTORY_LIMIT);
    }
    account["current"] = sample.take();
}

fn now_iso() -> String {
    OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_default()
        .replace('Z', "+00:00")
}
