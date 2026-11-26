use super::{
    Bytes, Failure, Providers, Response, State, StatusCode, account_lock, json_response,
    object_body, read_object, write_private_json_blocking,
};
use axum::response::IntoResponse;
use serde_json::{Value, json};
use std::sync::Arc;

fn text(value: &Value) -> Option<String> {
    if !crate::providers::truthy(value) {
        return None;
    }
    let raw = crate::responses::python_string(value);
    let trimmed = raw.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_owned())
}

pub(super) fn effective_proxy(settings: &Value) -> Option<String> {
    ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"]
        .iter()
        .filter_map(|key| std::env::var(key).ok())
        .find_map(|raw| text(&Value::String(raw)))
        .or_else(|| text(&settings["proxy"]))
}

pub(super) fn version(settings: &Value) -> String {
    text(&settings["client_version"]).unwrap_or_else(|| super::DEFAULT_CLIENT_VERSION.to_owned())
}

pub(super) async fn get_settings(State(app): State<Arc<Providers>>) -> Response {
    let path = app.config.data_dir.join("codex_models.json");
    match tokio::task::spawn_blocking(move || read_object(&path)).await {
        Ok(settings) => json_response(
            StatusCode::OK,
            json!({
                "success": true, "proxy": effective_proxy(&settings), "client_version": version(&settings)
            }),
        ),
        Err(error) => Failure::internal(error.to_string()).into_response(),
    }
}

pub(super) async fn save_settings(State(app): State<Arc<Providers>>, body: Bytes) -> Response {
    let patch = object_body(&body);
    let data = &app.config.data_dir;
    let path = data.join("codex_models.json");
    // The legacy account refresh updates the same document. Hold its lock through replacement.
    let guard = account_lock(data, "openai-codex").lock_owned().await;
    let saved = tokio::task::spawn_blocking(move || -> std::io::Result<Value> {
        let _guard = guard;
        let mut settings = read_object(&path);
        if !settings.is_object() {
            settings = json!({});
        }
        let fields = settings.as_object_mut().expect("object was normalized");
        let mut requested = false;
        for key in ["proxy", "client_version"] {
            if let Some(value) = patch.get(key) {
                requested = true;
                if let Some(value) = text(value) {
                    fields.insert(key.into(), Value::String(value));
                } else {
                    fields.remove(key);
                }
            }
        }
        if requested {
            write_private_json_blocking(&path, &settings)?;
        }
        Ok(json!({"success": true, "proxy": effective_proxy(&settings)}))
    })
    .await;
    match saved {
        Ok(Ok(payload)) => json_response(StatusCode::OK, payload),
        Ok(Err(error)) => Failure::internal(error.to_string()).into_response(),
        Err(error) => Failure::internal(error.to_string()).into_response(),
    }
}
