use super::*;

#[path = "../2026-11-27_RustChat深压缩/contract.rs"]
mod compression;

#[path = "../2026-11-27_RustChat停止保留/contract.rs"]
mod stopped_response;

impl MockModel {
    fn serve_script(self, deltas: Vec<Value>) -> thread::JoinHandle<Vec<Value>> {
        self.serve_streams(deltas.into_iter().map(|mut delta| {
            let has_tools = delta.get("tool_calls").is_some();
            let finish = if has_tools { "tool_calls" } else { "stop" };
            let prose = if has_tools {
                delta.as_object_mut().unwrap().shift_remove("content").map(|content| {
                    let chunk = json!({"choices":[{"index":0,"delta":{"content":content},"finish_reason":null}]});
                    format!("data: {chunk}\n\n")
                }).unwrap_or_default()
            } else {
                String::new()
            };
            let chunk = json!({"choices":[{"index":0,"delta":delta,"finish_reason":finish}]});
            format!("{prose}data: {chunk}\n\ndata: [DONE]\n\n")
        }).collect())
    }

    fn serve_streams(self, streams: Vec<String>) -> thread::JoinHandle<Vec<Value>> {
        assert_eq!(streams.len(), self.requests);
        thread::spawn(move || {
            let mut requests = Vec::new();
            for stream in streams {
                let deadline = Instant::now() + Duration::from_secs(8);
                let mut socket = loop {
                    match self.listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error)
                            if error.kind() == std::io::ErrorKind::WouldBlock
                                && Instant::now() < deadline =>
                        {
                            thread::sleep(Duration::from_millis(5));
                        }
                        Err(error) => panic!("mock Chat request did not arrive: {error}"),
                    }
                };
                socket.set_nonblocking(false).unwrap();
                socket.set_read_timeout(Some(Duration::from_secs(8))).unwrap();
                let request = read_request(&mut socket);
                let split = request.windows(4).position(|window| window == b"\r\n\r\n").unwrap();
                requests.push(serde_json::from_slice(&request[split + 4..]).unwrap());
                socket.write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n",
                ).unwrap();
                write_chunk(&mut socket, stream.as_bytes()).unwrap();
                socket.write_all(b"0\r\n\r\n").unwrap();
            }
            requests
        })
    }
}

fn tool_call(index: usize, id: &str, name: &str, arguments: &str) -> Value {
    json!({"index":index,"id":id,"type":"function","function":{"name":name,"arguments":arguments}})
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancellation_preserves_completed_tool_and_pairs_only_unfinished_tools() {
    let data = DataDir::new();
    data.write_fixture();
    let search = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", search.local_addr().unwrap());
    fs::write(data.0.join("service_config.json"), json!({
        "search_provider":"searxng","searxng_base_url":endpoint
    }).to_string()).unwrap();
    let (model, server) = MockModel::start(2, None);
    let server = server.serve_script(vec![
        json!({"tool_calls":[
            tool_call(0, "fixture_done", "update_memory", r#"{"operation":"append","content":"first write"}"#),
            tool_call(1, "fixture_waiting", "web_search", r#"{"query":"synthetic search"}"#),
            tool_call(2, "fixture_unstarted", "update_memory", r#"{"operation":"append","content":"must not happen"}"#),
        ]}),
        json!({"content":"resumed"}),
    ]);
    let mode = mode(&data, model);
    let task = send(&mode, CONVERSATION, "cancel-batch", "batch question", Some("msg_answer_2"));
    let task_id = task["task_id"].as_str().unwrap();
    let (socket, _) = tokio::time::timeout(Duration::from_secs(5), search.accept()).await.unwrap().unwrap();
    mode.runtime.cancel_task("", task_id).unwrap();
    let completed = wait_task(&mode, task_id).await;
    assert_eq!(completed["data"]["status"], "stopped", "{completed}");
    drop(socket);
    let raw = data.raw(CONVERSATION);
    let results: Vec<_> = raw["messages"].as_array().unwrap().iter()
        .filter(|message| message["role"] == "tool").collect();
    let content = |message: &Value| serde_json::from_str::<Value>(message["content"].as_str().unwrap()).unwrap();
    assert_eq!(results.iter().map(|item| item["tool_call_id"].as_str().unwrap()).collect::<Vec<_>>(),
        ["fixture_done", "fixture_waiting", "fixture_unstarted"]);
    assert_eq!(content(results[0])["success"], true);
    assert_eq!(content(results[1])["status"], "cancelled");
    assert_eq!(content(results[2])["status"], "cancelled");
    let parent = raw["metadata"]["chat"]["latest_leaf_id"].as_str();
    let next = send(&mode, CONVERSATION, "resume-batch", "continue", parent);
    let next = wait_task(&mode, next["task_id"].as_str().unwrap()).await;
    assert_eq!(next["data"]["status"], "succeeded", "{next}");
    let requests = server.join().unwrap();
    let replay: Vec<_> = requests[1]["messages"].as_array().unwrap().iter()
        .filter(|message| message["role"] == "tool").collect();
    assert_eq!(replay.len(), 3);
    assert_eq!(content(replay[0])["success"], true);
    assert_eq!(content(replay[2])["status"], "cancelled");
    let memory: Value = serde_json::from_slice(
        &fs::read(data.0.join("chat/memory/memories.json")).unwrap()
    ).unwrap();
    assert_eq!(memory["global"], json!(["first write"]));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn rejected_tool_and_round_limit_leave_a_resumable_branch() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, server) = MockModel::start(10, None);
    let mut script = vec![json!({"content":"before invalid call","tool_calls":[
        tool_call(0, "unavailable_tool", "run_command", "{}")
    ]})];
    script.extend((0..8).map(|index| json!({"tool_calls":[
        tool_call(0, &format!("round_{index}"), "search_chat_memory", r#"{"keywords":["fixture"]}"#)
    ]})));
    script.push(json!({"content":"finished from intact branch"}));
    let server = server.serve_script(script);
    let mode = mode(&data, model);

    let task = send(&mode, CONVERSATION, "invalid-call", "invalid", Some("msg_answer_2"));
    let finished = wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "failed", "{finished}");
    assert!(finished["data"]["error"].as_str().unwrap().contains("Tool unavailable in Chat"));
    let invalid = data.raw(CONVERSATION);
    assert_eq!(latest_assistant(&invalid)["content"], "before invalid call");
    assert!(latest_assistant(&invalid)["tool_calls"].is_null());

    let parent = invalid["metadata"]["chat"]["latest_leaf_id"].as_str();
    let limited = send(&mode, CONVERSATION, "round-limit", "limit", parent);
    let finished = wait_task(&mode, limited["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "failed", "{finished}");
    assert!(finished["data"]["error"].as_str().unwrap().contains("round limit"));
    let limited = data.raw(CONVERSATION);
    let final_result = limited["messages"].as_array().unwrap().last().unwrap();
    assert_eq!(final_result["tool_call_id"], "round_7");
    let cancelled: Value = serde_json::from_str(final_result["content"].as_str().unwrap()).unwrap();
    assert_eq!(cancelled["status"], "cancelled");
    let parent = limited["metadata"]["chat"]["latest_leaf_id"].as_str();
    let next = send(&mode, CONVERSATION, "after-limit", "next", parent);
    let finished = wait_task(&mode, next["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");
    let captured = server.join().unwrap();
    let messages = captured.last().unwrap()["messages"].as_array().unwrap();
    assert_eq!(messages.iter().filter(|message| message["role"] == "tool").count(), 8);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn model_can_read_early_rust_stopped_turn_with_a_missing_tool_result() {
    let data = DataDir::new();
    let mut fixture = data.write_fixture();
    fixture["messages"][3]["tool_calls"] = json!([
        tool_call(0, "old_incomplete", "search_chat_memory", r#"{"keywords":["fixture"]}"#)
    ]);
    fixture["metadata"]["chat"]["turns"][1]["status"] = json!("stopped");
    data.save(&fixture);
    let (model, server) = MockModel::start(1, None);
    let server = server.serve_script(vec![json!({"content":"resumed"})]);
    let mode = mode(&data, model);
    let task = send(&mode, CONVERSATION, "resume-old", "continue", Some("msg_answer_2"));
    let finished = wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");
    let captured = server.join().unwrap();
    assert!(captured[0]["messages"].as_array().unwrap().iter()
        .any(|message| message["tool_call_id"] == "old_incomplete"));
    assert!(!data.raw(CONVERSATION)["messages"].as_array().unwrap().iter()
        .any(|message| message["tool_call_id"] == "old_incomplete"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn chat_network_notice_reaches_the_next_model_request_in_the_same_turn() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let data = DataDir::new();
    data.write_fixture();
    let search = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", search.local_addr().unwrap());
    fs::write(data.0.join("service_config.json"), json!({
        "search_provider":"searxng","searxng_base_url":endpoint
    }).to_string()).unwrap();
    let (model, server) = MockModel::start(3, None);
    let server = server.serve_script(vec![
        json!({"tool_calls":[tool_call(0, "search_first", "web_search", r#"{"query":"first"}"#)]}),
        json!({"tool_calls":[tool_call(0, "search_denied", "web_search", r#"{"query":"denied"}"#)]}),
        json!({"content":"done"}),
    ]);
    let mode = mode(&data, model);
    let task = send(&mode, CONVERSATION, "network-in-turn", "fixture", Some("msg_answer_2"));
    let (mut socket, _) = tokio::time::timeout(Duration::from_secs(5), search.accept()).await.unwrap().unwrap();
    let mut request = [0u8; 4096];
    let _ = tokio::time::timeout(Duration::from_secs(5), socket.read(&mut request)).await.unwrap().unwrap();
    let response = mode.clone().router().oneshot(Request::builder()
        .method("POST")
        .uri(format!("/api/chat/conversations/{CONVERSATION}/network"))
        .header("content-type", "application/json")
        .body(Body::from(r#"{"operation_id":"offline-in-turn","enabled":false}"#))
        .unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 14\r\nConnection: close\r\n\r\n{\"results\":[]}").await.unwrap();
    drop(socket);
    // If the guard regresses, the listener rejects the second call quickly
    // instead of hanging on an unanswered local fixture.
    drop(search);
    let finished = wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");
    let requests = server.join().unwrap();
    let second = requests[1]["messages"].as_array().unwrap();
    assert!(second[0]["content"].as_str().unwrap().contains("联网：关闭"));
    assert_eq!(second[second.len()-2]["tool_call_id"], "search_first");
    assert!(second.last().unwrap()["content"].as_str().unwrap().contains("[Chat 联网设置更新] 联网已关闭"));
    let third = requests[2]["messages"].as_array().unwrap();
    let denied: Value = serde_json::from_str(third.last().unwrap()["content"].as_str().unwrap()).unwrap();
    assert_eq!(denied["code"], "chat_network_disabled");
    let saved = data.raw(CONVERSATION);
    assert!(saved["messages"].as_array().unwrap().iter()
        .any(|message| message["metadata"]["hidden"] == true && message["metadata"]["network_revision"] == 1));
    assert_eq!(saved["metadata"]["pending_network_notices"], json!([]));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn chat_replays_provider_reasoning_on_the_followup_tool_round() {
    let data = DataDir::new();
    data.write_fixture();
    let (_, server) = MockModel::start(2, None);
    let endpoint = format!("http://{}/v1/responses", server.listener.local_addr().unwrap());
    let model = ModelConfig::fixed(endpoint, None, "chat-fixture".into());
    let frame = |event: Value| format!("data: {event}\n\n");
    let initial = [
        json!({"type":"response.created"}),
        json!({"type":"response.output_item.done","item":{
            "type":"reasoning","id":"rs_chat_round","summary":[],"encrypted_content":"fixture-encrypted-context"
        }}),
        json!({"type":"response.output_item.added","item":{
            "type":"function_call","id":"fc_chat_round","call_id":"call_chat_round","name":"search_chat_memory"
        }}),
        json!({"type":"response.function_call_arguments.delta","item_id":"fc_chat_round","delta":"{\"keywords\":[\"fixture\"]}"}),
        json!({"type":"response.output_item.done","item":{
            "type":"function_call","id":"fc_chat_round","call_id":"call_chat_round","name":"search_chat_memory"
        }}),
        json!({"type":"response.completed","response":{"output":[]}}),
    ].into_iter().map(frame).collect();
    let resumed = [
        json!({"type":"response.output_text.delta","delta":"fixture complete"}),
        json!({"type":"response.completed","response":{"output":[]}}),
    ].into_iter().map(frame).collect();
    let server = server.serve_streams(vec![initial, resumed]);
    let mode = mode(&data, model);
    let task = send(&mode, CONVERSATION, "follow-up-opaque", "fixture", Some("msg_answer_2"));
    let finished = wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");
    let requests = server.join().unwrap();
    let input = requests[1]["input"].as_array().unwrap();
    assert!(input.iter().any(|value| value["type"] == "reasoning"
        && value["encrypted_content"] == "fixture-encrypted-context"), "{input:?}");
    assert!(input.iter().any(|value| value["type"] == "function_call_output"
        && value["call_id"] == "call_chat_round"), "{input:?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn normal_chat_wire_prompt_uses_shared_preferences_and_private_chat_instructions() {
    let data = DataDir::new();
    data.write_fixture();
    let profile = json!({
        "enabled":true,"self_identify":"fixture assistant","user_name":"fixture visitor",
        "profession":"fixture occupation","tone":"fixture tone","considerations":"fixture extra context",
        "communication_style":"human_like","conversation_continuity":"high",
        "chat_instructions":"fixture chat-only instructions",
        "tavily_api_key":"fixture-not-a-credential"
    });
    let (model, server) = MockModel::start(2, None);
    let server = server.serve_script(vec![json!({"content":"yes"}), json!({"content":"also yes"})]);
    let mode = mode(&data, model);
    let write = |preferences: &Value| fs::write(data.0.join("personalization.json"), preferences.to_string()).unwrap();
    write(&profile);
    let task = send(&mode, CONVERSATION, "with-shared-profile", "fixture", Some("msg_answer_2"));
    let finished = wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");

    let mut disabled = profile;
    disabled["enabled"] = json!(false);
    write(&disabled);
    let saved = data.raw(CONVERSATION);
    let parent = saved["metadata"]["chat"]["latest_leaf_id"].as_str();
    let task = send(&mode, CONVERSATION, "disabled-shared-profile", "fixture", parent);
    let finished = wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");

    let requests = server.join().unwrap();
    let system = |index: usize| requests[index]["messages"][0]["content"].as_str().unwrap();
    for setting in ["fixture assistant", "fixture visitor", "fixture occupation", "fixture tone",
        "fixture extra context", "【对话连续性：高】", "【交流风格要求】"] {
        assert!(system(0).contains(setting), "Missing shared preference {setting}");
        assert!(!system(1).contains(setting), "Leaked disabled shared preference {setting}");
    }
    for index in [0, 1] {
        assert!(system(index).contains("fixture chat-only instructions"));
        assert!(!system(index).contains("fixture-not-a-credential"));
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn poll_and_bootstrap_share_reconnectable_chat_action_state() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, _server) = MockModel::start(0, None);
    let chat = mode(&data, model);
    let (id, _cancel) = chat.runtime.register_chat_task(crate::runtime::ChatTaskStart {
        conversation_id: CONVERSATION.into(),
        message: "fixture chat snapshot".into(),
        model_key: None,
        message_source: None,
        created_at: crate::chat_mode::tasks::now(),
    }).unwrap();
    let app = chat.clone().router();
    let result = |path: String| {
        let app = app.clone();
        async move {
            let response = app.oneshot(Request::builder().uri(path).body(Body::empty()).unwrap()).await.unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            let bytes = axum::body::to_bytes(response.into_body(), usize::MAX).await.unwrap();
            serde_json::from_slice::<Value>(&bytes).unwrap()["data"].clone()
        }
    };
    let emit = |kind: &str, body: Value| chat.runtime.emit_chat_event(&id, kind, body).unwrap();
    let bootstrap = format!("/api/chat/conversations/{CONVERSATION}");
    let poll = format!("/api/chat/tasks/{id}");
    emit("api_request_start", json!({"response_message_id":"msg_round_1"}));
    let waiting = result(bootstrap.clone()).await;
    let item = &waiting["display"]["messages"][0];
    assert_eq!(item["role"], "assistant");
    assert_eq!(item["id"], "msg_round_1");
    assert_eq!(item["awaitingFirstContent"], true);
    let cursor = waiting["display"]["next_event_idx"].as_u64().unwrap();

    emit("thinking_start", json!({"response_message_id":"msg_round_1"}));
    emit("thinking_chunk", json!({"content":"checking details"}));
    emit("thinking_end", json!({"full_content":"checking details"}));
    emit("text_start", json!({"response_message_id":"msg_round_1"}));
    emit("text_chunk", json!({"content":"here is how"}));
    emit("tool_preparing", json!({"id":"call_live","name":"update_memory","intent":"keep","intent_complete":false}));
    emit("tool_intent", json!({"id":"call_live","name":"update_memory","intent":"keep note","intent_complete":true}));
    let observed = result(format!("{poll}?from={cursor}")).await;
    let actions = observed["view"]["display"]["messages"][0]["actions"].as_array().unwrap();
    assert_eq!(actions.iter().map(|action| action["type"].as_str().unwrap()).collect::<Vec<_>>(),
        ["thinking", "text", "tool"]);
    assert_eq!(actions[1]["content"], "here is how");
    assert_eq!(actions[2]["tool"]["status"], "preparing");
    assert_eq!(actions[2]["tool"]["intent_full"], "keep note");
    let action_id = actions[2]["id"].clone();
    let cursor = observed["next_offset"].as_u64().unwrap();
    assert_eq!(observed["view"]["display"]["next_event_idx"], cursor);

    emit("tool_start", json!({"id":"call_live","preparing_id":"call_live","name":"update_memory","arguments":{"intent":"keep note","content":"offline"}}));
    let running = result(bootstrap.clone()).await;
    let tool = &running["display"]["messages"][0]["actions"][2];
    assert_eq!(tool["id"], action_id);
    assert_eq!(tool["tool"]["status"], "running");
    assert_eq!(tool["tool"]["arguments"]["content"], "offline");
    emit("update_action", json!({"id":"call_live","preparing_id":"call_live","status":"completed","result":{"success":true}}));
    let finished = result(format!("{poll}?from={cursor}")).await;
    let tool = &finished["view"]["display"]["messages"][0]["actions"][2];
    assert_eq!(tool["id"], action_id);
    assert_eq!(tool["tool"]["status"], "completed");
    assert_eq!(tool["tool"]["result"]["success"], true);

    emit("stream_reset", json!({}));
    let reset = result(bootstrap.clone()).await;
    assert_eq!(reset["display"]["messages"], json!([]));
    emit("api_request_start", json!({"response_message_id":"msg_round_2"}));
    let retry = result(bootstrap).await;
    assert_eq!(retry["display"]["messages"][0]["id"], "msg_round_2");
    assert_eq!(retry["display"]["messages"][0]["awaitingFirstContent"], true);
    assert_eq!(retry["display"]["messages"][0]["actions"], json!([]));
}
