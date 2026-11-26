use std::{collections::VecDeque, sync::Mutex, time::Duration};

use axum::{
    Router,
    body::{Body, Bytes, to_bytes},
    extract::{Request, State},
    http::{HeaderMap, Method, StatusCode, Uri},
    response::Response,
};
use futures_util::{StreamExt, stream};
use serde_json::{Value, json};
use tokio::{
    net::TcpListener,
    sync::{mpsc, oneshot},
    task::JoinHandle,
    time::timeout,
};

use super::{REASONING_CONNECTION, REASONING_ITEMS, complete_with_usage, public_history_message};

const TEST_TIMEOUT: Duration = Duration::from_secs(5);
const RESPONSES_PATH: &str = "/v1/responses/?tenant=one";
const KEY: &str = "synthetic-model-credential";

struct PostedRequest {
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Value,
}

struct Fixture {
    responses: Mutex<VecDeque<Response>>,
    requests: mpsc::UnboundedSender<PostedRequest>,
}

struct Server {
    origin: String,
    requests: mpsc::UnboundedReceiver<PostedRequest>,
    worker: JoinHandle<()>,
}

impl Server {
    async fn start(responses: Vec<Response>) -> Self {
        let (sender, requests) = mpsc::unbounded_channel();
        let state = std::sync::Arc::new(Fixture {
            responses: Mutex::new(responses.into()),
            requests: sender,
        });
        let router = Router::new().fallback(handle_request).with_state(state);
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let origin = format!("http://{}", listener.local_addr().unwrap());
        let worker = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        Self {
            origin,
            requests,
            worker,
        }
    }

    fn endpoint(&self, path: &str) -> String {
        format!("{}{path}", self.origin)
    }

    async fn received(&mut self) -> PostedRequest {
        timeout(TEST_TIMEOUT, self.requests.recv())
            .await
            .unwrap()
            .unwrap()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.worker.abort();
    }
}

async fn handle_request(
    State(state): State<std::sync::Arc<Fixture>>,
    request: Request,
) -> Response {
    let (parts, body) = request.into_parts();
    let body = to_bytes(body, 128 * 1024).await.unwrap();
    state
        .requests
        .send(PostedRequest {
            method: parts.method,
            uri: parts.uri,
            headers: parts.headers,
            body: serde_json::from_slice(&body).unwrap(),
        })
        .unwrap();
    state
        .responses
        .lock()
        .unwrap()
        .pop_front()
        .expect("unexpected provider request")
}

fn response(status: StatusCode, media_type: &str, body: impl Into<Body>) -> Response {
    Response::builder()
        .status(status)
        .header("content-type", media_type)
        .body(body.into())
        .unwrap()
}

fn sse(body: impl Into<Body>) -> Response {
    response(StatusCode::OK, "text/event-stream", body)
}

fn event(value: Value) -> String {
    format!("data: {value}\r\n\r\n")
}

fn completed(output: Vec<Value>) -> Value {
    json!({"type":"response.completed","response":{"output":output}})
}

fn reasoning_item(id: &str) -> Value {
    json!({"type":"reasoning","id":id,"summary":[],"encrypted_content":"test-opaque-state"})
}

fn reasoning_events() -> String {
    [
        event(json!({"type":"response.reasoning_summary_text.done","text":"**检查**"})),
        event(json!({"type":"response.output_item.done","item":reasoning_item("rs_live")})),
    ]
    .concat()
}

#[tokio::test]
async fn responses_tool_result_and_encrypted_context_round_trip_over_http() {
    let tools = [json!({
        "type":"function", "function":{
            "name":"echo", "description":"Return the supplied text",
            "parameters":{"type":"object","required":["text"],"properties":{
                "text":{"type":"string"},"prefix":{"type":"string"}
            }}
        }
    })];
    let first_stream = [
        event(json!({"type":"response.created"})),
        reasoning_events(),
        event(json!({"type":"response.output_text.delta","delta":"先核对。"})),
        event(json!({"type":"response.output_item.added","item":{
            "type":"function_call","id":"fc_server_item","call_id":"call_echo","name":"echo"
        }})),
        event(json!({"type":"response.function_call_arguments.delta","item_id":"fc_server_item","delta":"{\"text\":\"你好\","})),
        event(json!({"type":"response.function_call_arguments.delta","item_id":"fc_server_item","delta":"\"prefix\":null}"})),
        event(json!({"type":"response.output_item.done","item":{
            "type":"function_call","id":"fc_server_item","call_id":"call_echo","name":"echo"
        }})),
        event(json!({"type":"response.completed","response":{
            "output":[reasoning_item("rs_repeated")],"usage":{
                "input_tokens":13,"output_tokens":8,"total_tokens":21,
                "input_tokens_details":{"cached_tokens":3},"output_tokens_details":{"reasoning_tokens":2}
            }
        }})),
    ].concat();

    let (saw_text, release_tail) = oneshot::channel();
    let prelude = "\u{feff}: keep-alive\r\n\r\nevent: response.output_text.delta\r\ndata: {\"type\":\"response.output_text.delta\",\r\ndata: \"delta\":\"结\"}\r\n\r\n";
    let split = prelude.find('结').unwrap() + 1;
    let prelude = [
        Bytes::copy_from_slice(&prelude.as_bytes()[..split]),
        Bytes::copy_from_slice(&prelude.as_bytes()[split..]),
    ];
    let tail = stream::once(async {
        release_tail.await.unwrap();
        tokio::time::sleep(Duration::from_millis(25)).await;
        let text = event(json!({"type":"response.output_text.delta","delta":"果"}));
        // A complete final data frame may end at EOF.
        Ok::<_, std::io::Error>(Bytes::from(format!("{text}data: {}", completed(vec![]))))
    });
    let second_stream = stream::iter(prelude.into_iter().map(Ok)).chain(tail);
    let mut server = Server::start(vec![
        sse(first_stream),
        sse(Body::from_stream(second_stream)),
    ])
    .await;
    let endpoint = server.endpoint(RESPONSES_PATH);
    let mut history = vec![
        json!({"role":"system","content":"保持上下文。","_frozen_system_prompt":true}),
        json!({"role":"user","content":[{"type":"text","text":"请原样返回你好。"}]}),
    ];

    let mut text = String::new();
    let first = timeout(
        TEST_TIMEOUT,
        complete_with_usage(
            &endpoint,
            Some(KEY),
            "gpt-fixture",
            &history,
            &tools,
            |delta| text.push_str(delta),
        ),
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(text, "先核对。");
    assert_eq!(first.message["content"], "先核对。");
    assert_eq!(first.message["reasoning_content"], "检查");
    assert_eq!(
        first.usage.unwrap(),
        json!({
            "prompt_tokens":13,"completion_tokens":8,"total_tokens":21,
            "prompt_tokens_details":{"cached_tokens":3},"completion_tokens_details":{"reasoning_tokens":2}
        })
    );
    let reasoning =
        json!({"type":"reasoning","summary":[],"encrypted_content":"test-opaque-state"});
    assert_eq!(first.message[REASONING_ITEMS], json!([reasoning]));
    let call = &first.message["tool_calls"][0];
    assert_eq!(call["id"], "call_echo");
    assert_eq!(call["function"]["name"], "echo");
    let arguments = call["function"]["arguments"].as_str().unwrap().to_owned();
    assert_eq!(
        serde_json::from_str::<Value>(&arguments).unwrap(),
        json!({"text":"你好"})
    );
    let persisted = first.message.to_string();
    assert!(!persisted.contains(KEY));
    let public = public_history_message(&first.message);
    assert_eq!(public["reasoning_content"], "检查");
    assert!(public.get(REASONING_ITEMS).is_none());
    assert!(public.get(REASONING_CONNECTION).is_none());
    history.extend([
        serde_json::from_str(&persisted).unwrap(),
        json!({"role":"tool","tool_call_id":"call_echo","content":"你好"}),
    ]);

    let mut saw_text = Some(saw_text);
    let mut text = String::new();
    let second_endpoint = format!("{endpoint}#ignored-fragment");
    let second = timeout(
        TEST_TIMEOUT,
        complete_with_usage(
            &second_endpoint,
            Some(KEY),
            "gpt-fixture-next",
            &history,
            &tools,
            |delta| {
                text.push_str(delta);
                if let Some(sender) = saw_text.take() {
                    sender.send(()).unwrap();
                }
            },
        ),
    )
    .await
    .unwrap()
    .unwrap();
    assert_eq!(text, "结果");
    assert_eq!(second.message, json!({"role":"assistant","content":"结果"}));
    assert_eq!(second.usage, None);
    assert!(second.responses_duration_ms.is_some_and(|duration| duration >= 20));

    let first_request = server.received().await;
    let second_request = server.received().await;
    for request in [&first_request, &second_request] {
        assert_eq!(request.method, Method::POST);
        assert_eq!(request.uri.to_string(), RESPONSES_PATH);
        assert_eq!(request.headers["authorization"], format!("Bearer {KEY}"));
        assert_eq!(request.headers["accept"], "text/event-stream");
        assert_eq!(request.headers["content-type"], "application/json");
        assert_eq!(request.body["store"], false);
        assert_eq!(request.body["stream"], true);
        assert_eq!(
            request.body["include"],
            json!(["reasoning.encrypted_content"])
        );
        assert_eq!(request.body["reasoning"], json!({"summary":"detailed"}));
        assert_eq!(request.body["tool_choice"], "auto");
        assert_eq!(request.body["tools"][0]["name"], "echo");
        assert_eq!(
            request.body["tools"][0]["parameters"]["properties"]["prefix"]["type"],
            json!(["string", "null"])
        );
        for field in ["instructions", "messages", "previous_response_id"] {
            assert!(
                request.body.get(field).is_none(),
                "unexpected field {field}"
            );
        }
    }
    let developer = json!({"type":"message","role":"developer","content":[{"type":"input_text","text":"保持上下文。"}]});
    let user = json!({"type":"message","role":"user","content":[{"type":"input_text","text":"请原样返回你好。"}]});
    assert_eq!(first_request.body["model"], "gpt-fixture");
    assert_eq!(first_request.body["input"], json!([developer, user]));
    assert_eq!(second_request.body["model"], "gpt-fixture-next");
    assert_eq!(
        second_request.body["input"],
        json!([
            developer, user, reasoning,
            {"type":"message","role":"assistant","content":[{"type":"output_text","text":"先核对。"}]},
            {"type":"function_call","call_id":"call_echo","name":"echo","arguments":arguments},
            {"type":"function_call_output","call_id":"call_echo","output":"你好"}
        ])
    );
}

#[tokio::test]
async fn responses_replay_checks_endpoint_credentials_and_provenance_before_protocol_switch() {
    let first_stream = format!("{}{}", reasoning_events(), event(completed(vec![])));
    let chat_stream = format!(
        "{}data: [DONE]\n\n",
        event(json!({"choices":[{
            "index":0,"delta":{"content":"chat answer"},"finish_reason":"stop"
        }]}))
    );
    let mut server = Server::start(vec![
        sse(first_stream),
        sse(event(completed(vec![]))),
        sse(event(completed(vec![]))),
        sse(event(completed(vec![]))),
        sse(chat_stream),
    ])
    .await;
    let endpoint = server.endpoint(RESPONSES_PATH);
    let mut history = vec![json!({"role":"user","content":"start"})];
    let first = complete_with_usage(&endpoint, Some(KEY), "fixture", &history, &[], |_| {})
        .await
        .unwrap();
    history.extend([first.message, json!({"role":"user","content":"continue"})]);
    assert!(history[1].get(REASONING_CONNECTION).is_some());
    assert!(history[1].get(REASONING_ITEMS).is_some());
    server.received().await;

    let other_endpoint = server.endpoint("/v1/responses/?tenant=two");
    let mut untagged = history.clone();
    untagged[1]
        .as_object_mut()
        .unwrap()
        .shift_remove(REASONING_CONNECTION);
    for (url, key, messages) in [
        (endpoint.as_str(), Some("rotated-key"), history.as_slice()),
        (other_endpoint.as_str(), Some(KEY), history.as_slice()),
        (endpoint.as_str(), Some(KEY), untagged.as_slice()),
    ] {
        complete_with_usage(url, key, "fixture", messages, &[], |_| {})
            .await
            .unwrap();
        let request = server.received().await;
        assert_eq!(
            request.body["input"],
            json!([
                {"type":"message","role":"user","content":[{"type":"input_text","text":"start"}]},
                {"type":"message","role":"user","content":[{"type":"input_text","text":"continue"}]}
            ])
        );
        assert_eq!(request.body["instructions"], "");
    }

    let chat = server.endpoint("/v1/chat/completions?name=/responses");
    let mut text = String::new();
    let result = complete_with_usage(&chat, None, "fixture", &history, &[], |delta| {
        text.push_str(delta)
    })
    .await
    .unwrap();
    assert_eq!(text, "chat answer");
    assert_eq!(
        result.message,
        json!({"role":"assistant","content":"chat answer"})
    );
    let request = server.received().await;
    assert_eq!(request.uri.path(), "/v1/chat/completions");
    assert!(request.headers.get("authorization").is_none());
    assert_eq!(
        request.body,
        json!({"model":"fixture","stream":true,"messages":[
            history[0], {"role":"assistant","content":""}, history[2]
        ]})
    );
}

#[tokio::test]
async fn responses_reports_http_and_unexpected_json_errors() {
    let mut server = Server::start(vec![
        response(
            StatusCode::TOO_MANY_REQUESTS,
            "application/json",
            r#"{"error":{"message":"model quota exceeded"}}"#,
        ),
        response(
            StatusCode::OK,
            "application/problem+json",
            r#"{"message":"proxy returned JSON"}"#,
        ),
        Response::builder()
            .status(StatusCode::TEMPORARY_REDIRECT)
            .header("location", "/v1/other/responses")
            .body(Body::empty())
            .unwrap(),
    ])
    .await;
    let endpoint = server.endpoint(RESPONSES_PATH);
    for expected in [
        "HTTP 429 Too Many Requests: model quota exceeded",
        "expected an SSE stream (HTTP 200 OK): proxy returned JSON",
        "HTTP 307 Temporary Redirect",
    ] {
        let error = timeout(
            TEST_TIMEOUT,
            complete_with_usage(&endpoint, None, "fixture", &[], &[], |_| {
                panic!("HTTP error emitted model text")
            }),
        )
        .await
        .unwrap()
        .unwrap_err();
        assert!(error.contains(expected), "{error:?}");
        assert_eq!(server.received().await.uri.path(), "/v1/responses/");
    }
    assert!(matches!(
        server.requests.try_recv(),
        Err(mpsc::error::TryRecvError::Empty)
    ));
}

#[tokio::test]
async fn responses_reports_stream_errors_and_never_accepts_partial_assistant_messages() {
    let text_event = event(json!({"type":"response.output_text.delta","delta":"partial"}));
    let failed = event(json!({"type":"response.failed","response":{"error":{
        "code":"provider_failure","message":"model stopped unexpectedly"
    }}}));
    let mut server = Server::start(vec![
        sse(format!("{text_event}{failed}")),
        sse(format!("{text_event}event: error\ndata: {{\"error\":{{\"message\":\"gateway SSE error\"}}}}\n\n")),
        sse(format!("{text_event}data: [DONE]\r\n\r\n")),
    ]).await;
    let endpoint = server.endpoint(RESPONSES_PATH);
    for expected in [
        "(provider_failure): model stopped unexpectedly",
        "gateway SSE error",
        "stream_incomplete",
    ] {
        let mut text = String::new();
        let error = timeout(
            TEST_TIMEOUT,
            complete_with_usage(&endpoint, None, "fixture", &[], &[], |delta| {
                text.push_str(delta)
            }),
        )
        .await
        .unwrap()
        .unwrap_err();
        assert_eq!(text, "partial");
        assert!(error.contains(expected), "{error:?}");
        server.received().await;
    }
}
