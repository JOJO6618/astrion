//! ChatGPT/Codex quota handlers. Account selection and token refresh are
//! shared with model refresh; only the wham endpoint differs per route.

use axum::{
    body::Bytes,
    extract::{Query, State},
    http::StatusCode,
    response::Response,
};
use serde_json::{Map, Value, json};

use super::{
    AccountQuery, QuotaState, error_response, merge_object, requested_account, resolve_account,
    success_response, wham_request,
};
use crate::providers::codex;

pub(super) async fn usage(
    State(state): State<std::sync::Arc<QuotaState>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Response {
    let payload = crate::providers::object_body(&body);
    let requested = requested_account(&query, &payload);
    let account = match resolve_account(state.config.data_dir.clone(), requested).await {
        Ok(account) => account,
        Err(response) => return response,
    };
    let force = query.refresh.as_deref() == Some("1");
    let auth_path = codex::credential_path_for(&state.config.data_dir, &account.id);
    let slot = state.usage_slot(&account.id);
    let mut cache = slot.lock().await;
    let Some(before) = super::credential_signature(&auth_path) else {
        return error_response(StatusCode::BAD_GATEWAY, "credential_unavailable");
    };

    if !force
        && let Some(cached) = cache.as_ref()
        && cached.signature == before
        && cached.fetched_at.elapsed() < super::USAGE_CACHE_TTL
    {
        return success_response(usage_payload(&account, cached.payload.clone()));
    }

    let result = wham_request(
        &state,
        &account.id,
        "GET",
        &state.endpoints.usage_url,
        None,
        super::USAGE_TIMEOUT,
        true,
    )
    .await;
    let (payload, after) = match result {
        Ok(result) => result,
        Err((status, error)) => return error_response(status, &error),
    };
    if !payload.get("rate_limit").is_some_and(Value::is_object) {
        return error_response(StatusCode::BAD_GATEWAY, "invalid_subscription_usage");
    }
    if let Some(signature) = after {
        *cache = Some(super::CachedUsage {
            payload: payload.clone(),
            signature,
            fetched_at: std::time::Instant::now(),
        });
    }
    success_response(usage_payload(&account, payload))
}

fn usage_payload(account: &codex::Account, usage: Value) -> Value {
    json!({
        "success": true,
        "account_id": account.id,
        "account_hash": account.hash,
        "usage": usage,
    })
}

pub(super) async fn reset_credits(
    State(state): State<std::sync::Arc<QuotaState>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Response {
    let payload = crate::providers::object_body(&body);
    let requested = requested_account(&query, &payload);
    let account = match resolve_account(state.config.data_dir.clone(), requested).await {
        Ok(account) => account,
        Err(response) => return response,
    };
    match wham_request(
        &state,
        &account.id,
        "GET",
        &state.endpoints.reset_credits_url,
        None,
        super::USAGE_TIMEOUT,
        true,
    )
    .await
    {
        Ok((upstream, _)) => match upstream {
            Value::Object(fields) => success_response(Value::Object(merge_object(
                base_payload(&account.id),
                Value::Object(fields),
            ))),
            _ => error_response(StatusCode::BAD_GATEWAY, "invalid_response"),
        },
        Err((status, error)) => error_response(status, &error),
    }
}

pub(super) async fn consume_reset_credits(
    State(state): State<std::sync::Arc<QuotaState>>,
    Query(query): Query<AccountQuery>,
    body: Bytes,
) -> Response {
    let payload = crate::providers::object_body(&body);
    let requested = requested_account(&query, &payload);
    let account = match resolve_account(state.config.data_dir.clone(), requested).await {
        Ok(account) => account,
        Err(response) => return response,
    };
    let Some(credit_id) = payload
        .get("credit_id")
        .and_then(scalar_string)
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
    else {
        return crate::providers::json_response(
            StatusCode::BAD_REQUEST,
            json!({"success": false, "error": "missing credit_id"}),
        );
    };
    let request_body = json!({
        "credit_id": credit_id,
        "redeem_request_id": uuid::Uuid::new_v4().to_string(),
    });
    match wham_request(
        &state,
        &account.id,
        "POST",
        &state.endpoints.reset_credits_consume_url,
        Some(request_body),
        super::USAGE_TIMEOUT,
        false,
    )
    .await
    {
        Ok((upstream, _)) => {
            state.invalidate_usage(&account.id).await;
            match upstream {
                Value::Object(fields) => success_response(Value::Object(merge_object(
                    base_payload(&account.id),
                    Value::Object(fields),
                ))),
                _ => error_response(StatusCode::BAD_GATEWAY, "invalid_response"),
            }
        }
        Err((status, error)) => error_response(status, &error),
    }
}

fn base_payload(account_id: &str) -> Map<String, Value> {
    let mut base = Map::new();
    base.insert("success".into(), json!(true));
    base.insert("account_id".into(), json!(account_id));
    base
}

fn scalar_string(value: &Value) -> Option<String> {
    match value {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) if value.as_f64().is_some_and(|value| value != 0.0) => {
            Some(value.to_string())
        }
        Value::Bool(true) => Some("True".into()),
        _ => None,
    }
}
