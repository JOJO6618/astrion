//! Fixed Agent context overhead measured from the first real request on a prefix.

use serde_json::{Map, Value, json};

pub(crate) fn invalidate_if_model_changed(metadata: &mut Map<String, Value>, model: &str) {
    let changed = ["model_key", "context_baseline_model_key"].into_iter().any(|key| {
        metadata.get(key).and_then(Value::as_str).is_some_and(|previous| previous != model)
    });
    if changed {
        for key in ["context_baseline_tokens", "context_baseline_model_key", "context_baseline_at"] {
            metadata.insert(key.into(), Value::Null);
        }
        metadata.insert("context_baseline_pending".into(), json!(true));
    }
}

pub(super) fn record(metadata: &mut Value, usage: &Value, model: &str, now: f64) {
    if metadata["context_baseline_pending"] != true || metadata["model_key"] != model {
        return;
    }
    let Some(tokens) = crate::usage::normalize_usage(usage)
        .map(|tokens| tokens.current_context_tokens).filter(|tokens| *tokens > 0)
    else { return };
    metadata["context_baseline_tokens"] = json!(tokens);
    metadata["context_baseline_model_key"] = json!(model);
    metadata["context_baseline_at"] = json!(super::iso_time(now));
    metadata["context_baseline_pending"] = json!(false);
}
