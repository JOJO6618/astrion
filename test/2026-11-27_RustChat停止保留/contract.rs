use super::*;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn chat_stop_preserves_the_visible_response_id_and_only_complete_content() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    for with_prose in [false, true] {
        let data = DataDir::new();
        data.write_fixture();
        let upstream = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let model = ModelConfig::fixed(
            format!("http://{}/v1/chat/completions", upstream.local_addr().unwrap()),
            None,
            "chat-fixture".into(),
        );
        let (release, paused) = tokio::sync::oneshot::channel();
        let upstream = tokio::spawn(async move {
            let (mut socket, _) = upstream.accept().await.unwrap();
            let mut request = [0_u8; 8192];
            let _ = socket.read(&mut request).await.unwrap();
            socket.write_all(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n",
            ).await.unwrap();
            let mut chunks = vec![json!({"reasoning_content":"visible draft thought"})];
            if with_prose {
                chunks.push(json!({"content":"visible draft prose"}));
                chunks.push(json!({"tool_calls":[tool_call(0, "partial_call", "search_chat_memory", "{\"keywords\":[")]}));
            }
            for delta in chunks {
                let frame = format!("data: {}\n\n", json!({"choices":[{"index":0,"delta":delta}]}));
                socket.write_all(format!("{:x}\r\n{frame}\r\n", frame.len()).as_bytes()).await.unwrap();
            }
            paused.await.unwrap();
        });

        let mode = mode(&data, model);
        let request = send(&mode, CONVERSATION, "stopped-stream", "continue", Some("msg_answer_2"));
        let task = request["task_id"].as_str().unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        let response = loop {
            let snapshot = mode.runtime.poll_task("", task, 0).unwrap();
            let events = snapshot["data"]["events"].as_array().unwrap();
            if events.iter().any(|event| event["type"] == if with_prose { "tool_preparing" } else { "thinking_chunk" }) {
                let start = events.iter().find(|event| event["type"] == "thinking_start").unwrap();
                break start["data"]["response_message_id"].as_str().unwrap().to_owned();
            }
            assert!(Instant::now() < deadline, "{snapshot}");
            tokio::time::sleep(Duration::from_millis(10)).await;
        };
        mode.runtime.cancel_task("", task).unwrap();
        let finished = wait_task(&mode, task).await;
        assert_eq!(finished["data"]["status"], "stopped", "{finished}");
        release.send(()).unwrap();
        upstream.await.unwrap();
        let saved = data.raw(CONVERSATION);
        let assistant = latest_assistant(&saved);
        assert_eq!(assistant["message_id"], response);
        assert_eq!(assistant["reasoning_content"], "visible draft thought");
        assert_eq!(assistant["content"], if with_prose { "visible draft prose" } else { "" });
        assert_eq!(assistant["metadata"]["stopped"], true);
        assert!(assistant["tool_calls"].is_null());
        assert!(assistant["metadata"]["responses_reasoning_items"].is_null());
        assert!(!saved["messages"].as_array().unwrap().iter().any(|message| message["tool_call_id"] == "partial_call"));
    }
}
