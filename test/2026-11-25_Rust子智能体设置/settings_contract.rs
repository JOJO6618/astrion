use super::{Settings, capacity, requested_workspace, settings_patch};
use axum::http::{HeaderMap, HeaderValue};
use serde_json::{Value, json};

#[test]
fn settings_keep_legacy_read_coercion_separate_from_strict_put() {
    let legacy = json!({
        "sub_agent_compress_threshold_tokens": " +275_000 ",
        "sub_agent_max_instances": "41",
    });
    let settings = Settings::from(&legacy);
    assert_eq!(settings.sub_agent_compress_threshold_tokens, 275_000);
    assert_eq!(settings.sub_agent_max_instances, 20);
    assert!(settings_patch(&legacy).is_err());

    let patch = serde_json::from_str::<Value>(
        r#"{
        "sub_agent_compress_threshold_tokens": 100000000000000000000000000000000,
        "sub_agent_max_instances": 9007199254740991
    }"#,
    )
    .unwrap();
    let saved = settings_patch(&patch).unwrap();
    assert_eq!(saved["sub_agent_compress_threshold_tokens"], 10_000_000);
    assert_eq!(saved["sub_agent_max_instances"], 9_007_199_254_740_991_i64);
    for raw in ["false", "1.0", "1e0", "9007199254740992", "\"1\""] {
        assert_eq!(capacity(&serde_json::from_str(raw).unwrap()), None);
    }
}

#[test]
fn first_nonempty_workspace_input_is_selected_before_trimming() {
    let mut headers = HeaderMap::new();
    let body = json!({"workspace_id": " body "});
    assert_eq!(
        requested_workspace(None, &headers, &body).as_deref(),
        Some("body")
    );
    headers.insert("x-astrion-workspace-id", HeaderValue::from_static("header"));
    assert_eq!(
        requested_workspace(Some("workspace_id="), &headers, &body).as_deref(),
        Some("header")
    );
    assert_eq!(
        requested_workspace(
            Some("workspace_id=query&workspace_id=ignored"),
            &headers,
            &body
        )
        .as_deref(),
        Some("query")
    );
    assert_eq!(
        requested_workspace(Some("workspace_id=%20"), &headers, &body),
        None
    );
}
