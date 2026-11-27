use super::*;

#[test]
fn opt_out_keeps_the_preparing_block_without_intent() {
    let mut stream = ToolCallStream::with_intent(false);
    let first = stream.push(ToolCallDelta {
        index: 0, id: "local", name: "read_file", arguments: r#"{"intent":"读取"#,
    });
    assert!(matches!(first.as_slice(), [ToolStreamEvent::Preparing {
        id, intent: None, intent_complete: false, ..
    }] if id == "local"));
    assert!(stream.push(ToolCallDelta {
        index: 0, id: "local", name: "read_file", arguments: r#"{"intent":"读取文件"}"#,
    }).is_empty());
}

#[test]
fn tool_intent_respects_remote_schema_and_preserves_required_fields() {
    let first_party = serde_json::json!({"function":{"name":"enter_worktree",
        "parameters":{"type":"object","properties":{"target":{"type":"object"}},"required":["target"]}}});
    let mut remote = first_party.clone();
    remote["function"]["name"] = serde_json::json!("mcp__fixture__search");
    let remote_schema = remote.clone();
    super::super::tool_intent::configure_schema(&mut remote, true);
    assert_eq!(remote, remote_schema);

    let mut local = first_party.clone();
    super::super::tool_intent::configure_schema(&mut local, true);
    super::super::tool_intent::configure_schema(&mut local, true);
    assert_eq!(local["function"]["parameters"]["required"], serde_json::json!(["intent","target"]));
    super::super::tool_intent::configure_schema(&mut local, false);
    assert_eq!(local, first_party);
}
