use super::*;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn chat_reuses_and_extends_its_branch_summary_before_regular_generation() {
    let data = DataDir::new();
    let mut fixture = data.write_fixture();
    let initial = "first request retained on disk";
    let older_answer = "oversized response ".repeat(500);
    fixture["messages"][0]["content"] = json!(initial);
    fixture["messages"][1]["content"] = json!(older_answer);
    fixture["metadata"]["chat"]["summaries"] = json!([
        {"id":"elsewhere","valid":true,"message_ids":["different_branch","msg_answer_1"],"content":"must not use"},
    ]);
    fixture["metadata"]["compress_limit_tokens"] = json!(6_000);
    data.save(&fixture);
    let (model, server) = MockModel::start(3, None);
    let server = server.serve_script(vec![
        json!({"content":"compact continuity facts"}),
        json!({"content":"short follow-up"}),
        json!({"content":"continued from archive"}),
    ]);
    let mode = mode(&data, model);
    let request = send(&mode, CONVERSATION, "compact-before-send", "user three", Some("msg_answer_2"));
    let finished = wait_task(&mode, request["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");
    let events = finished["data"]["events"].as_array().unwrap();
    let compression_states: Vec<_> = events.iter().filter(|event| event["type"] == "compression_state")
        .map(|event| event["data"]["in_progress"].as_bool().unwrap()).collect();
    assert_eq!(compression_states, [true, false]);
    let saved = data.raw(CONVERSATION);
    let latest = &saved["metadata"]["chat"]["summaries"][1];
    assert_eq!(latest["message_ids"], json!(["msg_user_1", "msg_answer_1"]));
    assert_eq!(latest["content"], "compact continuity facts");
    assert_eq!(saved["messages"][0]["content"], initial);
    assert_eq!(saved["messages"][1]["content"], older_answer);

    let parent = saved["metadata"]["chat"]["latest_leaf_id"].as_str();
    let request = send(&mode, CONVERSATION, "use-archive", "user four", parent);
    let finished = wait_task(&mode, request["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");
    let captured = server.join().unwrap();
    assert!(captured[0]["tools"].is_null());
    assert_eq!(captured[0]["messages"][1]["content"], initial);
    assert_eq!(captured[0]["messages"][2]["content"], older_answer);
    for request in &captured[1..] {
        let messages = request["messages"].as_array().unwrap();
        assert!(messages[0]["content"].as_str().unwrap().contains("当前模型"));
        assert!(messages[1]["content"].as_str().unwrap().contains("compact continuity facts"));
        assert!(!messages.iter().any(|message| message["content"] == initial || message["content"] == older_answer));
        assert!(!messages.iter().any(|message| message["content"] == "must not use"));
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn edit_invalidates_an_archived_summary_without_destroying_history() {
    let data = DataDir::new();
    let mut fixture = data.write_fixture();
    fixture["metadata"]["chat"]["summaries"] = json!([
        {"id":"archive","valid":true,"message_ids":["msg_user_1","msg_answer_1"],"content":"old summary"},
    ]);
    data.save(&fixture);
    let (model, server) = MockModel::start(1, None);
    let server = server.serve_script(vec![json!({"content":"fresh from edited history"})]);
    let mode = mode(&data, model);
    mode.storage(|paths| tree::edit(paths, CONVERSATION, "edit-for-summary", "msg_user_1",
        "edited ancestor", None, crate::chat_mode::tasks::now())).unwrap();
    let request = send(&mode, CONVERSATION, "continue-edited", "more", Some("msg_answer_2"));
    let finished = wait_task(&mode, request["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");
    let captured = server.join().unwrap();
    let messages = captured[0]["messages"].as_array().unwrap();
    assert!(messages.iter().any(|message| message["content"] == "edited ancestor"));
    assert!(!messages.iter().any(|message| message["content"].as_str().unwrap_or_default().contains("old summary")));
    assert_eq!(data.raw(CONVERSATION)["metadata"]["chat"]["summaries"][0]["valid"], false);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancellation_during_chat_compression_keeps_the_original_branch() {
    let data = DataDir::new();
    let mut fixture = data.write_fixture();
    fixture["messages"][1]["content"] = json!("oversized old response ".repeat(500));
    // Exercise an old conversation without a saved override.
    fs::write(data.0.join("personalization.json"), r#"{"chat_deep_compress_trigger_tokens":6000}"#).unwrap();
    data.save(&fixture);
    let (model, server) = MockModel::start(1, Some(Duration::from_millis(800)));
    let server = server.serve();
    let mode = mode(&data, model);
    let request = send(&mode, CONVERSATION, "cancel-summary", "continue", Some("msg_answer_2"));
    let task = request["task_id"].as_str().unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let state = mode.runtime.poll_task("", task, 0).unwrap();
        if state["data"]["events"].as_array().unwrap().iter()
            .any(|event| event["type"] == "compression_state" && event["data"]["in_progress"] == true) {
            break;
        }
        assert!(Instant::now() < deadline, "{state}");
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    mode.runtime.cancel_task("", task).unwrap();
    let finished = wait_task(&mode, task).await;
    assert_eq!(finished["data"]["status"], "stopped", "{finished}");
    server.join().unwrap();
    let saved = data.raw(CONVERSATION);
    assert!(saved["metadata"]["chat"]["summaries"].as_array().unwrap().is_empty());
    assert_eq!(&saved["messages"].as_array().unwrap()[..4], fixture["messages"].as_array().unwrap());
    assert_eq!(saved["metadata"]["chat"]["active_turn_id"], Value::Null);
}
