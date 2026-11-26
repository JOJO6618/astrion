use std::{
    collections::{HashMap, VecDeque},
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

use axum::{
    Router,
    body::{Body, Bytes, to_bytes},
    extract::{Request, State},
    http::{HeaderMap, Method, StatusCode},
    response::Response,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use futures_util::{StreamExt, stream};
use serde_json::{Value, json};
use tokio::{
    net::TcpListener,
    sync::{mpsc, oneshot},
    task::JoinHandle,
    time::timeout,
};
use uuid::Uuid;

use super::{CodexRequestOptions, complete};
use crate::{
    model::{REASONING_CONNECTION, REASONING_ITEMS, complete_with_usage, public_history_message},
    providers::ProviderConfig,
};

const WAIT: Duration = Duration::from_secs(5);
const RESPONSES_PATH: &str = "/backend-api/codex/responses";
const ACCOUNT_A: &str = "openai-codex";
const ACCOUNT_B: &str = "openai-codex-second";
const SUBSCRIPTION_A: &str = "chatgpt-fixture-a";
const SUBSCRIPTION_B: &str = "chatgpt-fixture-b";

struct PostedRequest {
    method: Method,
    path: String,
    headers: HeaderMap,
    body: Bytes,
}

impl PostedRequest {
    fn json(&self) -> Value {
        serde_json::from_slice(&self.body).unwrap()
    }
}

struct Upstream {
    responses: Mutex<VecDeque<Response>>,
    posted: mpsc::UnboundedSender<PostedRequest>,
}

struct Fixture {
    root: PathBuf,
    origin: String,
    posted: mpsc::UnboundedReceiver<PostedRequest>,
    worker: JoinHandle<()>,
}

impl Fixture {
    async fn start(responses: Vec<Response>) -> Self {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-25_RustCodexResponses")
            .join(format!("codex_tmp_{}", Uuid::new_v4().simple()));
        fs::create_dir_all(root.join("codex_accounts")).unwrap();
        let (tx, posted) = mpsc::unbounded_channel();
        let app = Router::new()
            .fallback(handle)
            .with_state(Arc::new(Upstream {
                responses: Mutex::new(responses.into()),
                posted: tx,
            }));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let origin = format!("http://{}", listener.local_addr().unwrap());
        let worker = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        Self {
            root,
            origin,
            posted,
            worker,
        }
    }

    fn config(&self) -> ProviderConfig {
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .read_timeout(WAIT)
            .build()
            .unwrap();
        ProviderConfig::new(&self.root, &self.root, &self.root)
            .with_client(client)
            .without_modelsdev_refresh()
            .with_codex_endpoints(self.url("/models"), self.url("/oauth/token"), "")
            .with_codex_responses_url(self.url(RESPONSES_PATH))
    }

    fn url(&self, path: &str) -> String {
        format!("{}{path}", self.origin)
    }

    fn credential(&self, connection: &str, subscription: &str, rotation: u64) -> String {
        let access = jwt(subscription, rotation);
        let path = if connection == ACCOUNT_A {
            self.root.join("codex_auth.json")
        } else {
            self.root
                .join("codex_accounts")
                .join(format!("{connection}.json"))
        };
        fs::write(
            path,
            json!({"auth_mode":"chatgpt","tokens":{
                "access_token":access, "refresh_token":format!("refresh-{rotation}"),
                "id_token":jwt(subscription, 0), "account_id":subscription
            }})
            .to_string(),
        )
        .unwrap();
        access
    }

    async fn receive(&mut self) -> PostedRequest {
        timeout(WAIT, self.posted.recv()).await.unwrap().unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.worker.abort();
        let _ = fs::remove_dir_all(&self.root);
    }
}

async fn handle(State(upstream): State<Arc<Upstream>>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    upstream
        .posted
        .send(PostedRequest {
            method: parts.method,
            path: parts.uri.path().to_owned(),
            headers: parts.headers,
            body: to_bytes(body, 128 * 1024).await.unwrap(),
        })
        .unwrap();
    upstream
        .responses
        .lock()
        .unwrap()
        .pop_front()
        .expect("unexpected upstream request")
}

fn jwt(subscription: &str, rotation: u64) -> String {
    let payload = json!({
        "sub": format!("synthetic-subject-{subscription}"),
        "exp": 4_102_444_800_u64,
        "rotation": rotation,
        "https://api.openai.com/auth": {"chatgpt_account_id": subscription}
    });
    format!(
        "header.{}.signature",
        URL_SAFE_NO_PAD.encode(payload.to_string())
    )
}

fn response(status: StatusCode, mime: &str, body: impl Into<Body>) -> Response {
    Response::builder()
        .status(status)
        .header("content-type", mime)
        .body(body.into())
        .unwrap()
}

fn sse(body: impl Into<Body>) -> Response {
    response(StatusCode::OK, "text/event-stream", body)
}

fn event(value: Value) -> String {
    format!("data: {value}\r\n\r\n")
}

fn done() -> String {
    event(json!({"type":"response.completed","response":{"output":[]}}))
}

fn new_tokens(access: &str, refresh: &str, id: &str) -> Response {
    response(
        StatusCode::OK,
        "application/json",
        json!({
            "access_token":access, "refresh_token":refresh, "id_token":id
        })
        .to_string(),
    )
}

fn value_options(case: &Value) -> CodexRequestOptions<'_> {
    CodexRequestOptions {
        instructions: case["instructions"].as_str().unwrap(),
        reasoning_effort: case["requested_effort"].as_str(),
        session_id: case["session_id"].as_str().unwrap(),
        fast: case["fast"].as_bool().unwrap(),
        max_output_tokens: case["max_output_tokens"].as_u64(),
        model_info: &case["model_info"],
    }
}

#[tokio::test]
async fn matches_python_oauth_requests_and_streams_before_completion() {
    let (on_text, after_text) = oneshot::channel();
    let delta = event(json!({"type":"response.output_text.delta","delta":"你好"}));
    let split = delta.find('你').unwrap() + 1;
    let head = [
        Bytes::copy_from_slice(&delta.as_bytes()[..split]),
        Bytes::copy_from_slice(&delta.as_bytes()[split..]),
    ];
    let tail = stream::once(async {
        after_text.await.unwrap();
        Ok::<_, std::io::Error>(Bytes::from(done()))
    });
    let streamed = Body::from_stream(stream::iter(head.into_iter().map(Ok)).chain(tail));
    let mut fixture = Fixture::start(vec![sse(streamed), sse(done())]).await;
    let config = fixture.config();
    let cases: Vec<Value> = serde_json::from_str(include_str!("python_requests.json")).unwrap();
    let mut notify = Some(on_text);
    let mut text = String::new();
    for (case, connection) in cases.iter().zip([ACCOUNT_A, ACCOUNT_B]) {
        let options = &case["options"];
        let access = fixture.credential(connection, options["account"].as_str().unwrap(), 1);
        let transport = config.codex_transport(connection);
        let completion = timeout(
            WAIT,
            complete(
                &transport,
                options["model"].as_str().unwrap(),
                options["messages"].as_array().unwrap(),
                options["tools"].as_array().unwrap(),
                &value_options(options),
                |delta| {
                    text.push_str(delta);
                    notify.take().unwrap().send(()).unwrap();
                },
            ),
        )
        .await
        .unwrap()
        .unwrap();
        let request = fixture.receive().await;
        let python = &case["request"];
        assert_eq!(request.method.as_str(), python["method"]);
        assert_eq!(request.path, RESPONSES_PATH);
        assert!(python["url"].as_str().unwrap().ends_with(RESPONSES_PATH));
        assert_eq!(request.json(), python["body"]);
        for (key, expected) in python["headers"].as_object().unwrap() {
            let expected = if key == "Authorization" {
                format!("Bearer {access}")
            } else {
                expected.as_str().unwrap().to_owned()
            };
            assert_eq!(request.headers[key.as_str()], expected, "{key}");
        }
        if connection == ACCOUNT_A {
            assert_eq!(completion.message["content"], "你好");
        }
    }
    assert_eq!(text, "你好");
    assert!(notify.is_none());
}

#[tokio::test]
async fn retries_one_unauthorized_with_identical_body_and_isolates_encrypted_history() {
    let first_stream = [
        event(json!({"type":"response.reasoning_summary_text.done","text":"**checked**"})),
        event(json!({"type":"response.output_item.done","item":{
            "type":"reasoning","id":"server-reasoning-id","summary":[],"encrypted_content":"opaque-a"
        }})),
        event(json!({"type":"response.output_text.delta","delta":"Looking up."})),
        event(json!({"type":"response.output_item.added","item":{
            "type":"function_call","id":"server-function-id","call_id":"call_echo","name":"echo"
        }})),
        event(json!({"type":"response.output_item.done","item":{
            "type":"function_call","id":"server-function-id","call_id":"call_echo",
            "name":"echo","arguments":"{\"text\":\"x\",\"prefix\":null}"
        }})),
        event(json!({"type":"response.completed","response":{"output":[],
            "usage":{"input_tokens":12,"output_tokens":3,"total_tokens":15}}})),
    ].concat();
    let unauthorized = || {
        response(
            StatusCode::UNAUTHORIZED,
            "application/json",
            "{\"error\":\"expired\"}",
        )
    };
    let rotated = jwt(SUBSCRIPTION_A, 2);
    let mut fixture = Fixture::start(vec![
        sse(first_stream),
        unauthorized(),
        new_tokens(&rotated, "refresh-2", &jwt(SUBSCRIPTION_A, 0)),
        sse(done()), // Retry after the first 401.
        sse(done()), // Same connection after rebuilding ProviderConfig.
        sse(done()), // Different subscription.
        sse(done()), // History without a trusted connection fingerprint.
        sse(done()), // Ordinary API-key Responses.
        unauthorized(),
        new_tokens(
            &jwt(SUBSCRIPTION_A, 3),
            "refresh-3",
            &jwt(SUBSCRIPTION_A, 0),
        ),
        unauthorized(),
    ])
    .await;
    let original = fixture.credential(ACCOUNT_A, SUBSCRIPTION_A, 1);
    let other = fixture.credential(ACCOUNT_B, SUBSCRIPTION_B, 1);
    let config = fixture.config();
    let transport = config.codex_transport(ACCOUNT_A);
    let other_transport = config.codex_transport(ACCOUNT_B);
    let legacy_fast = json!({"additional_speed_tiers":["fast"]});
    let opts = CodexRequestOptions {
        instructions: "Prefix and bridge.",
        reasoning_effort: None,
        session_id: "main-session",
        fast: false,
        max_output_tokens: None,
        model_info: &legacy_fast,
    };
    let mut history = vec![json!({"role":"user","content":"start"})];
    let tools = [
        json!({"type":"function","function":{"name":"echo","parameters":{
            "type":"object","required":["text"],"properties":{
                "text":{"type":"string"},"prefix":{"type":"string"}
            }
        }}}),
    ];
    let first = complete(&transport, "same-model", &history, &tools, &opts, |_| {})
        .await
        .unwrap();
    let reasoning = json!({"type":"reasoning","summary":[],"encrypted_content":"opaque-a"});
    assert_eq!(first.message[REASONING_ITEMS], json!([reasoning]));
    assert_eq!(first.message["reasoning_content"], "checked");
    assert_eq!(first.message["tool_calls"][0]["id"], "call_echo");
    assert_eq!(
        first.message["tool_calls"][0]["function"]["arguments"],
        "{\"text\": \"x\"}"
    );
    assert_eq!(
        first.usage.unwrap(),
        json!({"prompt_tokens":12,"completion_tokens":3,"total_tokens":15})
    );
    let public = public_history_message(&first.message);
    assert!(public.get(REASONING_ITEMS).is_none());
    assert!(public.get(REASONING_CONNECTION).is_none());
    assert!(!first.message.to_string().contains(SUBSCRIPTION_A));
    assert!(!first.message.to_string().contains(&original));
    let first_request = fixture.receive().await;
    assert_eq!(
        first_request.json()["reasoning"],
        json!({"effort":"medium","summary":"detailed"})
    );
    assert!(first_request.json().get("service_tier").is_none());
    history.extend([
        serde_json::from_str(&first.message.to_string()).unwrap(),
        json!({"role":"tool","tool_call_id":"call_echo","content":"result"}),
    ]);

    complete(&transport, "same-model", &history, &tools, &opts, |_| {})
        .await
        .unwrap();
    let rejected = fixture.receive().await;
    let token = fixture.receive().await;
    let retried = fixture.receive().await;
    assert_eq!(token.path, "/oauth/token");
    let form: HashMap<String, String> = serde_urlencoded::from_bytes(&token.body).unwrap();
    assert_eq!(form["grant_type"], "refresh_token");
    assert_eq!(form["refresh_token"], "refresh-1");
    assert_eq!(
        rejected.headers["authorization"],
        format!("Bearer {original}")
    );
    assert_eq!(
        retried.headers["authorization"],
        format!("Bearer {rotated}")
    );
    for request in [&first_request, &rejected, &retried] {
        assert_eq!(request.headers["chatgpt-account-id"], SUBSCRIPTION_A);
        assert_eq!(request.headers["session_id"], opts.session_id);
        assert_eq!(request.json()["prompt_cache_key"], opts.session_id);
    }
    assert_eq!(rejected.body, retried.body);
    let retry_body = retried.json();
    let continued = retry_body["input"].as_array().unwrap();
    assert_eq!(continued[2], reasoning);
    assert_eq!(continued[3]["role"], "assistant");
    assert_eq!(continued[4]["arguments"], "{\"text\": \"x\"}");
    assert_eq!(
        continued[5],
        json!({"type":"function_call_output","call_id":"call_echo","output":"result"})
    );

    let new_config = fixture.config();
    complete(
        &new_config.codex_transport(ACCOUNT_A),
        "renamed-model",
        &history,
        &tools,
        &opts,
        |_| {},
    )
    .await
    .unwrap();
    let reopened = fixture.receive().await;
    assert_eq!(
        reopened.headers["authorization"],
        format!("Bearer {rotated}")
    );
    assert_eq!(reopened.json()["input"], retry_body["input"]);

    let opts_b = CodexRequestOptions {
        fast: true,
        model_info: &Value::Null,
        ..opts
    };
    complete(
        &other_transport,
        "same-model",
        &history,
        &tools,
        &opts_b,
        |_| {},
    )
    .await
    .unwrap();
    let switched = fixture.receive().await;
    assert_eq!(switched.headers["authorization"], format!("Bearer {other}"));
    assert_eq!(switched.headers["chatgpt-account-id"], SUBSCRIPTION_B);
    assert!(switched.json().get("service_tier").is_none());
    let without_reasoning: Vec<_> = continued
        .iter()
        .filter(|item| item["type"] != "reasoning")
        .cloned()
        .collect();
    assert_eq!(switched.json()["input"], json!(without_reasoning));

    let mut untagged = history.clone();
    untagged[1]
        .as_object_mut()
        .unwrap()
        .shift_remove(REASONING_CONNECTION);
    complete(&transport, "same-model", &untagged, &tools, &opts, |_| {})
        .await
        .unwrap();
    assert_eq!(
        fixture.receive().await.json()["input"],
        json!(without_reasoning)
    );
    complete_with_usage(
        &fixture.url(RESPONSES_PATH),
        Some("ordinary-api-key"),
        "same-model",
        &history,
        &tools,
        |_| {},
    )
    .await
    .unwrap();
    let bearer = fixture.receive().await;
    assert_eq!(bearer.headers["authorization"], "Bearer ordinary-api-key");
    assert!(bearer.headers.get("chatgpt-account-id").is_none());
    assert!(
        !bearer
            .body
            .windows(b"opaque-a".len())
            .any(|window| window == b"opaque-a")
    );

    let error = complete(&transport, "same-model", &history, &tools, &opts, |_| {
        panic!("unexpected delta")
    })
    .await
    .unwrap_err();
    assert!(error.contains("401"), "{error}");
    for path in [RESPONSES_PATH, "/oauth/token", RESPONSES_PATH] {
        assert_eq!(fixture.receive().await.path, path);
    }
    assert!(fixture.posted.try_recv().is_err());
}
