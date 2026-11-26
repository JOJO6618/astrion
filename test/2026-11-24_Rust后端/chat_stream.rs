use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::mpsc;
use std::thread::{self, JoinHandle};
use std::time::Duration;

use serde_json::{Value, json};

use super::{complete, complete_with_usage};

const DONE: &[u8] = b"data: [DONE]\n\n";
const TEST_TIMEOUT: Duration = Duration::from_secs(5);

struct Request {
    line: String,
    headers: Vec<String>,
    body: Value,
}

impl Request {
    fn read(socket: &mut TcpStream) -> Self {
        let mut reader = BufReader::new(socket);
        let mut line = String::new();
        reader.read_line(&mut line).unwrap();
        let mut headers = Vec::new();
        let mut length = 0;
        loop {
            let mut header = String::new();
            assert_ne!(reader.read_line(&mut header).unwrap(), 0);
            let header = header.trim().to_owned();
            if header.is_empty() {
                break;
            }
            if let Some(value) = header.to_ascii_lowercase().strip_prefix("content-length:") {
                length = value.trim().parse().unwrap();
            }
            headers.push(header);
        }
        let mut body = vec![0; length];
        reader.read_exact(&mut body).unwrap();
        Self {
            line: line.trim().to_owned(),
            headers,
            body: serde_json::from_slice(&body).unwrap(),
        }
    }
}

fn mock(
    status: &'static str,
    media_type: &'static str,
    respond: impl FnOnce(&mut TcpStream) + Send + 'static,
) -> (String, JoinHandle<Request>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!(
        "http://{}/v1/chat/completions",
        listener.local_addr().unwrap()
    );
    let server = thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        socket.set_read_timeout(Some(TEST_TIMEOUT)).unwrap();
        socket.set_write_timeout(Some(TEST_TIMEOUT)).unwrap();
        let request = Request::read(&mut socket);
        write!(
            socket,
            "HTTP/1.1 {status}\r\nContent-Type: {media_type}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        )
        .unwrap();
        respond(&mut socket);
        request
    });
    (endpoint, server)
}

fn chunk(writer: &mut impl Write, bytes: &[u8]) {
    write!(writer, "{:x}\r\n", bytes.len()).unwrap();
    writer.write_all(bytes).unwrap();
    writer.write_all(b"\r\n").unwrap();
}

fn mock_body(
    status: &'static str,
    media_type: &'static str,
    events: &[&[u8]],
    clean_eof: bool,
) -> (String, JoinHandle<Request>) {
    let mut body = Vec::new();
    for event in events {
        chunk(&mut body, event);
    }
    if clean_eof {
        body.extend_from_slice(b"0\r\n\r\n");
    }
    mock(status, media_type, move |socket| {
        socket.write_all(&body).unwrap()
    })
}

fn choice(delta: Value, reason: Option<&str>) -> Vec<u8> {
    let event = json!({"choices": [{"index": 0, "delta": delta, "finish_reason": reason}]});
    format!("data: {event}\n\n").into_bytes()
}

#[tokio::test]
async fn streams_text_before_completion_and_posts_multimodal_history() {
    let (first_text, seen) = mpsc::channel();
    let content = "\u{feff}: heartbeat\r\nretry: 100\r\n\r\n\
                   id: 1\r\ndata: {\"choices\":[\r\n\
                   data: {\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"你\"}}]}\r\n\r\n";
    let split = content.find('你').unwrap() + 1;
    let (endpoint, server) = mock("200 OK", "text/event-stream", move |socket| {
        chunk(socket, &content.as_bytes()[..split]);
        chunk(socket, &content.as_bytes()[split..content.len() - 1]);
        chunk(socket, &content.as_bytes()[content.len() - 1..]);
        assert_eq!(seen.recv_timeout(TEST_TIMEOUT).unwrap(), "你");
        chunk(socket, &choice(json!({"content":"好"}), Some("stop")));
        chunk(socket, b"data: {\"choices\":[],\"usage\":{}}\r\r");
        chunk(socket, b"data: [DO");
        chunk(socket, b"NE]\r\n\r\n");
        socket.write_all(b"0\r\n\r\n").unwrap();
    });

    let messages = [
        json!({"role":"user","content":"Hello"}),
        json!({"role":"user","content":[
            {"type":"text","text":"Describe these attachments"},
            {"type":"image_url","image_url":{"url":"https://example.invalid/photo.jpg","detail":"low"}},
            {"type":"video_url","video_url":{"url":"data:video/mp4;base64,AA=="}},
            {"type":"input_audio","input_audio":{"data":"AA==","format":"wav"}}
        ]}),
    ];
    let mut text = Vec::new();
    let result = complete(&endpoint, None, "chat-fixture", &messages, &[], |delta| {
        if text.is_empty() {
            first_text.send(delta.to_owned()).unwrap();
        }
        text.push(delta.to_owned());
    })
    .await
    .unwrap();
    assert_eq!(text, ["你", "好"]);
    assert_eq!(result, json!({"role":"assistant","content":"你好"}));
    let request = server.join().unwrap();
    assert_eq!(request.line, "POST /v1/chat/completions HTTP/1.1");
    assert!(
        request
            .headers
            .iter()
            .any(|h| h == "accept: text/event-stream")
    );
    assert!(
        request
            .headers
            .iter()
            .any(|h| h == "content-type: application/json")
    );
    assert!(
        !request
            .headers
            .iter()
            .any(|h| h.starts_with("authorization:"))
    );
    assert_eq!(
        request.body,
        json!({"model":"chat-fixture","messages":messages,"stream":true})
    );
}

#[tokio::test]
async fn interleaves_tool_fragments_and_sends_assistant_message_in_next_turn() {
    let fragments = choice(
        json!({"tool_calls":[
            {"index":1,"id":"call_","type":"function","function":{"name":"look","arguments":"{\"q\":"}},
            {"index":0,"id":"call_0","type":"function","function":{"name":"clock","arguments":"{"}}
        ]}),
        None,
    );
    let tails = choice(
        json!({"tool_calls":[
            {"index":0,"function":{"arguments":"}"}},
            {"index":1,"id":"1","function":{"name":"up","arguments":" \"北京\"}"}}
        ]}),
        None,
    );
    let finish = choice(json!({}), Some("tool_calls"));
    let (endpoint, server) = mock_body(
        "200 OK",
        "text/event-stream",
        &[&fragments, &tails, &finish, DONE],
        true,
    );
    let mut messages = vec![json!({"role":"user","content":"What time is it in Beijing?"})];
    let tools = [
        json!({"type":"function","function":{"name":"clock","parameters":{"type":"object"}}}),
        json!({"type":"function","function":{"name":"lookup","parameters":{"type":"object"}}}),
    ];

    let assistant = complete(
        &endpoint,
        Some("key-fixture"),
        "chat-fixture",
        &messages,
        &tools,
        |_| {
            panic!("tool arguments must not be emitted as text");
        },
    )
    .await
    .unwrap();
    let request = server.join().unwrap();
    assert!(
        request
            .headers
            .iter()
            .any(|h| h == "authorization: Bearer key-fixture")
    );
    assert_eq!(request.body["tools"], json!(tools));
    assert_eq!(request.body["tool_choice"], json!("auto"));
    assert_eq!(
        assistant,
        json!({"role":"assistant","content":null,"tool_calls":[
            {"id":"call_0","type":"function","function":{"name":"clock","arguments":"{}"}},
            {"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{\"q\": \"北京\"}"}}
        ]})
    );

    messages.extend([
        assistant,
        json!({"role":"tool","tool_call_id":"call_0","content":"08:00 UTC"}),
        json!({"role":"tool","tool_call_id":"call_1","content":"UTC+8"}),
    ]);
    let answer = choice(json!({"content":"16:00 in Beijing."}), Some("stop"));
    let (endpoint, server) = mock_body("200 OK", "text/event-stream", &[&answer, DONE], true);
    let result = complete(
        &endpoint,
        Some("key-fixture"),
        "chat-fixture",
        &messages,
        &tools,
        |_| {},
    )
    .await
    .unwrap();
    assert_eq!(
        result,
        json!({"role":"assistant","content":"16:00 in Beijing."})
    );
    assert_eq!(server.join().unwrap().body["messages"], json!(messages));
}

#[tokio::test]
async fn returns_reported_usage_and_leaves_unreported_usage_absent() {
    let answer = choice(json!({"content":"done"}), Some("stop"));
    let initial_usage = b"data: {\"usage\":{\"prompt_tokens\":5}}\n\n";
    let usage = json!({"prompt_tokens":5,"completion_tokens":3,"total_tokens":8,"completion_tokens_details":{"reasoning_tokens":1}});
    let final_usage = format!("data: {}\n\n", json!({"choices":[],"usage":usage}));
    let no_usage = b"data: {\"choices\":[],\"usage\":null}\n\n";

    for (events, expected) in [
        (vec![answer.as_slice(), no_usage, DONE], None),
        (
            vec![
                initial_usage,
                &answer,
                final_usage.as_bytes(),
                no_usage,
                DONE,
            ],
            Some(usage),
        ),
    ] {
        let (endpoint, server) = mock_body("200 OK", "text/event-stream", &events, true);
        let result = complete_with_usage(&endpoint, None, "chat-fixture", &[], &[], |_| {})
            .await
            .unwrap();
        assert_eq!(result.message, json!({"role":"assistant","content":"done"}));
        assert_eq!(result.usage, expected);
        server.join().unwrap();
    }
}

#[tokio::test]
async fn accepts_either_completion_marker_on_clean_streams() {
    let text = choice(json!({"content":"complete"}), None);
    let finish = choice(json!({}), Some("stop"));
    for events in [
        vec![text.as_slice(), &finish],
        vec![text.as_slice(), &finish[..finish.len() - 2]],
        vec![text.as_slice(), DONE],
        vec![text.as_slice(), b"data: [DONE]\n"],
        vec![text.as_slice(), b"data: [DONE]"],
    ] {
        let (endpoint, server) = mock_body("200 OK", "text/event-stream", &events, true);
        let result = complete(&endpoint, None, "chat-fixture", &[], &[], |_| {})
            .await
            .unwrap();
        assert_eq!(result, json!({"role":"assistant","content":"complete"}));
        server.join().unwrap();
    }
}

#[tokio::test]
async fn accepts_complete_tool_calls_with_stop_or_done_only() {
    let delta = json!({"content":"I'll check.","tool_calls":[
        {"index":0,"id":"call_0","function":{"name":"clock","arguments":"{}"}}
    ]});
    let stop = choice(delta.clone(), Some("stop"));
    let no_reason = choice(delta, None);
    for events in [vec![stop.as_slice()], vec![no_reason.as_slice(), DONE]] {
        let (endpoint, server) = mock_body("200 OK", "text/event-stream", &events, true);
        let result = complete(&endpoint, None, "chat-fixture", &[], &[], |_| {})
            .await
            .unwrap();
        assert_eq!(
            result,
            json!({"role":"assistant","content":"I'll check.","tool_calls":[
                {"id":"call_0","type":"function","function":{"name":"clock","arguments":"{}"}}
            ]})
        );
        server.join().unwrap();
    }
}

#[tokio::test]
async fn rejects_incomplete_streams_after_delivering_partial_text() {
    let text = choice(json!({"content":"partial"}), None);
    let finish = choice(json!({}), Some("stop"));
    for (events, clean_eof, expected) in [
        (
            vec![text.as_slice()],
            true,
            "before [DONE] or a successful finish reason",
        ),
        (
            vec![text.as_slice(), &finish, b"data: [DON"],
            true,
            "invalid Chat Completions SSE chunk",
        ),
        (vec![text.as_slice(), &finish], false, "stream read failed"),
        (
            vec![text.as_slice(), &finish, &finish, DONE],
            true,
            "after its finish reason",
        ),
    ] {
        let (endpoint, server) = mock_body("200 OK", "text/event-stream", &events, clean_eof);
        let mut content = String::new();
        let error = complete(&endpoint, None, "chat-fixture", &[], &[], |delta| {
            content.push_str(delta)
        })
        .await
        .unwrap_err();
        assert!(error.contains(expected), "{error}");
        assert_eq!(content, "partial");
        server.join().unwrap();
    }
}

#[tokio::test]
async fn reports_http_upstream_and_protocol_errors() {
    let length = choice(json!({}), Some("length"));
    let filtered = choice(json!({}), Some("content_filter"));
    for (status, media_type, data, expected) in [
        (
            "429 Too Many Requests",
            "application/json",
            b"{\"error\":{\"message\":\"rate limited\"}}".as_slice(),
            "HTTP 429 Too Many Requests: rate limited",
        ),
        (
            "307 Temporary Redirect\r\nLocation: http://127.0.0.1:1/redirect",
            "text/plain",
            b"provider moved",
            "HTTP 307 Temporary Redirect: provider moved",
        ),
        (
            "200 OK",
            "application/problem+json",
            b"{\"message\":\"credentials expired\"}",
            "credentials expired",
        ),
        (
            "200 OK",
            "text/event-stream",
            b"data: {\"error\":{\"message\":\"overloaded\"}}\n\n",
            "stream error: overloaded",
        ),
        (
            "200 OK",
            "text/event-stream",
            b"event: error\ndata: model unavailable\n\n",
            "stream error: model unavailable",
        ),
        (
            "200 OK",
            "text/event-stream",
            b"data: {\"type\":\"error\",\"message\":\"service unavailable\"}\n\n",
            "stream error: service unavailable",
        ),
        (
            "200 OK",
            "text/event-stream",
            DONE,
            "no assistant output or finish reason",
        ),
        (
            "200 OK",
            "text/event-stream",
            b"data: {\n\n",
            "invalid Chat Completions SSE chunk",
        ),
        (
            "200 OK",
            "text/event-stream",
            b"data: []\n\n",
            "expected a JSON object",
        ),
        (
            "200 OK",
            "text/event-stream",
            b"data: \xff\n\n",
            "invalid UTF-8",
        ),
        (
            "200 OK",
            "text/event-stream",
            &length,
            "finish_reason \"length\"",
        ),
        (
            "200 OK",
            "text/event-stream",
            &filtered,
            "finish_reason \"content_filter\"",
        ),
    ] {
        let (endpoint, server) = mock_body(status, media_type, &[data], true);
        let error = complete(&endpoint, None, "chat-fixture", &[], &[], |_| {})
            .await
            .unwrap_err();
        assert!(error.contains(expected), "{error}");
        server.join().unwrap();
    }
}

#[tokio::test]
async fn rejects_invalid_tool_calls_at_completion() {
    let valid = json!({"index":0,"id":"call_0","function":{"name":"clock","arguments":"{}"}});
    for (calls, reason, expected) in [
        (json!([]), "tool_calls", "sent no tool calls"),
        (
            json!([{"index":0,"function":{"name":"clock","arguments":"{}"}}]),
            "tool_calls",
            "missing an id or function name",
        ),
        (
            json!([{"index":0,"id":"call_0","function":{"arguments":"{}"}}]),
            "tool_calls",
            "missing an id or function name",
        ),
        (
            json!([{"index":0,"id":"call_0","function":{"name":"clock","arguments":"{"}}]),
            "tool_calls",
            "invalid JSON arguments",
        ),
        (
            json!([{"index":0,"id":"call_0","function":{"name":"clock","arguments":"[]"}}]),
            "tool_calls",
            "arguments must be a JSON object",
        ),
        (
            json!([{"index":1,"id":"call_1","function":{"name":"clock","arguments":"{}"}}]),
            "tool_calls",
            "skipped tool call index 0",
        ),
        (
            json!([valid, {"index":1,"id":"call_0","function":{"name":"clock","arguments":"{}"}}]),
            "tool_calls",
            "duplicate id",
        ),
    ] {
        let content = choice(json!({"tool_calls":calls}), Some(reason));
        let (endpoint, server) = mock_body("200 OK", "text/event-stream", &[&content, DONE], true);
        let error = complete(&endpoint, None, "chat-fixture", &[], &[], |_| {})
            .await
            .unwrap_err();
        assert!(error.contains(expected), "{error}");
        server.join().unwrap();
    }
}
