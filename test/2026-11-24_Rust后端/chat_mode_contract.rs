use std::{
    collections::HashSet,
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use serde_json::{Value, json};
use tower::ServiceExt;
use uuid::Uuid;

use crate::{
    catalog::CatalogSource,
    conversation_db::Database,
    runtime::{ModelConfig, Runtime},
    web_assets::personalization::Personalization,
};

use super::{ChatMode, model::ModelResolver, store, tree};

const CONVERSATION: &str = "conv_20240912_104056_001";

struct DataDir(PathBuf);

impl DataDir {
    fn new() -> Self {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-24_Rust后端")
            .join(format!("chat-mode-run-{}", Uuid::new_v4()));
        fs::create_dir_all(path.join("chat/conversations")).unwrap();
        Self(path)
    }

    fn runtime(&self) -> Runtime {
        Runtime::open(&self.0).unwrap()
    }

    fn database(&self) -> Database {
        Database::open(&self.0.join("chat/conversations")).unwrap()
    }

    fn raw(&self, id: &str) -> Value {
        self.database().read(id).unwrap().unwrap()
    }

    fn save(&self, raw: &Value) {
        self.database().save(raw, &store::index_row(raw)).unwrap();
    }

    fn write_fixture(&self) -> Value {
        let raw = json!({
            "id": CONVERSATION,
            "title": "Fixture Chat",
            "created_at": "2024-09-12T10:40:56.001",
            "updated_at": "2024-09-12T10:41:28.300",
            "messages": [
                {"message_id":"msg_user_1","role":"user","content":"hello","timestamp":"2024-09-12T10:40:56.001",
                 "metadata":{"chat":{"parent_id":null,"turn_id":"turn_1"},"visibility":"chat"}},
                {"message_id":"msg_answer_1","role":"assistant","content":"first response","timestamp":"2024-09-12T10:41:00.000",
                 "reasoning_content":"","metadata":{"chat":{"parent_id":"msg_user_1","turn_id":"turn_1"}}},
                {"message_id":"msg_user_2","role":"user","content":"second question","timestamp":"2024-09-12T10:41:20.000",
                 "metadata":{"chat":{"parent_id":"msg_answer_1","turn_id":"turn_2"},"visibility":"chat"}},
                {"message_id":"msg_answer_2","role":"assistant","content":"second response","timestamp":"2024-09-12T10:41:28.300",
                 "reasoning_content":"","metadata":{"chat":{"parent_id":"msg_user_2","turn_id":"turn_2"}}}
            ],
            "metadata": {
                "conversation_kind":"chat",
                "chat": {
                    "version":1,"revision":4,"latest_leaf_id":"msg_answer_2","active_turn_id":null,
                    "turns":[
                        {"id":"turn_1","parent_id":"msg_user_1","tip_id":"msg_answer_1","status":"complete"},
                        {"id":"turn_2","parent_id":"msg_user_2","tip_id":"msg_answer_2","status":"complete"}
                    ],
                    "commands":{},"summaries":[],"handoffs":{},"handoff_count":0
                },
                "project_path":null,"project_relative_path":null,
                "thinking_mode":false,"run_mode":"fast","model_key":"fixture-model",
                "network_enabled":true,"network_revision":0,"pending_network_notices":[],
                "permission_mode":"unrestricted","execution_mode":"sandbox",
                "workspace_memory_enabled":false,"has_images":false,"has_videos":false,
                "total_messages":4,"total_tools":0,"status":"active","quick_entry":false,
                "compression_count":0,"deep_compression_records":[],"frozen_system_prompt":""
            },
            "token_statistics":{}
        });
        self.save(&raw);
        raw
    }
}

impl Drop for DataDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn resolver(model: ModelConfig) -> ModelResolver {
    Arc::new(move |_, _, _| Ok(model.clone()))
}

fn mode_with_resolver(data: &DataDir, resolver: ModelResolver) -> ChatMode {
    let source = CatalogSource::with_paths(&repo_root(), &repo_root().join("config"), &data.0);
    let personalization = Personalization::new(
        source.clone(),
        repo_root().join("agentskills"),
        repo_root().join("agentskills-private"),
    );
    ChatMode::open_with_model(data.runtime(), &data.0, repo_root(), resolver)
        .unwrap()
        .with_catalog(source)
        .with_personalization(personalization)
}

fn mode(data: &DataDir, model: ModelConfig) -> ChatMode {
    mode_with_resolver(data, resolver(model))
}

/// Minimal streaming Chat Completions server. `requests` is how many turns it
/// answers; when `delay` is set, the first chunk arrives before the delay and the
/// rest after it so cancellation can observe partial text.
struct MockModel {
    listener: TcpListener,
    requests: usize,
    delay: Option<Duration>,
}

impl MockModel {
    fn start(requests: usize, delay: Option<Duration>) -> (ModelConfig, Self) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let endpoint = format!(
            "http://{}/v1/chat/completions",
            listener.local_addr().unwrap()
        );
        (
            ModelConfig::fixed(endpoint, None, "chat-fixture".into()),
            Self {
                listener,
                requests,
                delay,
            },
        )
    }

    fn serve(self) -> thread::JoinHandle<()> {
        thread::spawn(move || {
            for _ in 0..self.requests {
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
                        _ => return,
                    }
                };
                socket.set_nonblocking(false).unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(8)))
                    .unwrap();
                let _body = read_request(&mut socket);
                let mut response =
                    "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                        .to_owned();
                socket.write_all(response.as_bytes()).unwrap();
                response.clear();
                write_chunk(
                    &mut socket,
                    b"data: {\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"Hello \"}}]}\n\n",
                ).unwrap();
                if let Some(delay) = self.delay {
                    thread::sleep(delay);
                }
                let tail = write_chunk(
                    &mut socket,
                    b"data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"world\"},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":11,\"completion_tokens\":3}}\n\n",
                ).and_then(|()| write_chunk(&mut socket, b"data: [DONE]\n\n"));
                if let Err(error) = tail {
                    // The cancellation test closes the stream after the first chunk.
                    assert!(self.delay.is_some() && matches!(error.kind(),
                        std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::ConnectionReset), "{error}");
                    continue;
                }
                let _ = socket.write_all(b"0\r\n\r\n");
                let _ = socket.flush();
            }
        })
    }
}

fn read_request(socket: &mut TcpStream) -> Vec<u8> {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 4096];
    let mut header_end = None;
    let mut content_length = 0usize;
    loop {
        let read = socket.read(&mut chunk).unwrap();
        if read == 0 {
            break;
        }
        buffer.extend_from_slice(&chunk[..read]);
        if header_end.is_none()
            && let Some(position) = buffer.windows(4).position(|window| window == b"\r\n\r\n")
        {
            header_end = Some(position + 4);
            let headers = String::from_utf8_lossy(&buffer[..position]);
            for line in headers.lines() {
                if let Some(value) = line
                    .split_once(':')
                    .filter(|(name, _)| name.eq_ignore_ascii_case("content-length"))
                    .map(|(_, value)| value.trim())
                {
                    content_length = value.parse().unwrap_or(0);
                }
            }
        }
        if let Some(end) = header_end
            && buffer.len() >= end + content_length
        {
            break;
        }
    }
    buffer
}

fn write_chunk(socket: &mut TcpStream, data: &[u8]) -> std::io::Result<()> {
    socket.write_all(format!("{:x}\r\n", data.len()).as_bytes())?;
    socket.write_all(data)?;
    socket.write_all(b"\r\n")?;
    socket.flush()
}

async fn wait_task(mode: &ChatMode, task_id: &str) -> Value {
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        let task = mode.runtime.poll_task("", task_id, 0).unwrap();
        let status = task["data"]["status"].as_str().unwrap_or_default();
        if matches!(status, "succeeded" | "failed" | "stopped") {
            return task;
        }
        assert!(Instant::now() < deadline, "Chat task timed out: {task}");
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

fn send(mode: &ChatMode, conversation: &str, operation: &str, message: &str, parent: Option<&str>) -> Value {
    let mut payload = json!({
        "operation_id": operation,
        "conversation_id": conversation,
        "message": message,
        "thinking_mode": false,
    });
    if let Some(parent) = parent {
        payload["parent_id"] = json!(parent);
    }
    mode.create_task(&payload).unwrap()
}

fn latest_turn(raw: &Value) -> String {
    raw["metadata"]["chat"]["turns"]
        .as_array()
        .unwrap()
        .last()
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned()
}

fn latest_assistant(raw: &Value) -> Value {
    let leaf = raw["metadata"]["chat"]["latest_leaf_id"]
        .as_str()
        .unwrap();
    raw["messages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|message| message["message_id"] == leaf)
        .unwrap()
        .clone()
}

#[test]
fn sqlite_document_preserves_chat_tree_and_supports_edit_and_search() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, _server) = MockModel::start(0, None);
    let mode = mode(&data, model);

    let rows = mode.storage(|paths| paths.list_rows()).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["id"], CONVERSATION);
    assert_eq!(rows[0]["title"], "Fixture Chat");

    let public = mode
        .storage(|paths| paths.read_document(CONVERSATION).map(|doc| doc.public_chat_path(None).unwrap()))
        .unwrap();
    let ids: Vec<_> = public
        .iter()
        .map(|message| message["message_id"].as_str().unwrap())
        .collect();
    assert_eq!(ids, ["msg_user_1", "msg_answer_1", "msg_user_2", "msg_answer_2"]);

    let hits = mode
        .storage(|paths| paths.search("second response", 10))
        .unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0]["matches"].as_array().unwrap().len(), 1);

    let first = mode
        .storage(|paths| {
            tree::edit(
                paths,
                CONVERSATION,
                "edit-1",
                "msg_user_2",
                "edited question",
                None,
                1_700_000_000.0,
            )
        })
        .unwrap();
    assert_eq!(first["value"]["message_id"], "msg_user_2");
    assert!(first["revision"].as_u64().is_some());
    let replay = mode
        .storage(|paths| {
            tree::edit(
                paths,
                CONVERSATION,
                "edit-1",
                "msg_user_2",
                "edited question",
                None,
                1_700_000_000.0,
            )
        })
        .unwrap();
    assert_eq!(first, replay);
    let conflict = mode
        .storage(|paths| {
            tree::edit(
                paths,
                CONVERSATION,
                "edit-1",
                "msg_user_2",
                "different text",
                None,
                1_700_000_000.0,
            )
        })
        .unwrap_err();
    assert_eq!(conflict.status, 409);

    let raw = data.raw(CONVERSATION);
    assert_eq!(raw["messages"][2]["content"], "edited question");
    assert_eq!(raw["messages"][2]["message_id"], "msg_user_2");
    assert_eq!(
        raw["metadata"]["chat"]["commands"]["edit-1"]["fingerprint"]
            .as_str()
            .unwrap()
            .len(),
        64
    );

    let stale = mode
        .storage(|paths| {
            tree::edit(
                paths,
                CONVERSATION,
                "edit-2",
                "msg_user_2",
                "stale",
                Some(0),
                1_700_000_000.0,
            )
        })
        .unwrap_err();
    assert_eq!(stale.status, 409);

    // Unknown document fields survive a Chat write with the legacy index left
    // intact for offline migration.
    let mut raw = data.raw(CONVERSATION);
    raw["future_field"] = json!({"keep": true});
    data.save(&raw);
    let index_path = data.0.join("chat/conversations/index.json");
    let index = json!({"unrelated":{"keep":true}});
    fs::write(&index_path, serde_json::to_vec(&index).unwrap()).unwrap();
    mode.storage(|paths| {
        tree::edit(
            paths,
            CONVERSATION,
            "edit-3",
            "msg_answer_2",
            "edited answer",
            None,
            1_700_000_001.0,
        )
    })
    .unwrap();
    let archived_index: Value = serde_json::from_slice(&fs::read(&index_path).unwrap()).unwrap();
    assert_eq!(archived_index, index);
    assert_eq!(data.raw(CONVERSATION)["future_field"]["keep"], true);
}

#[tokio::test]
async fn unindexed_chat_source_is_listed_and_returns_migration_conflict() {
    let data = DataDir::new();
    let mut old = data.write_fixture();
    old["id"] = json!("conv_old");
    let path = data.0.join("chat/conversations/conv_old.json");
    let source = serde_json::to_vec(&old).unwrap();
    fs::write(&path, &source).unwrap();
    let (model, _server) = MockModel::start(0, None);
    let app = mode(&data, model).router();

    let response = app.clone().oneshot(
        Request::builder().uri("/api/chat/conversations").body(Body::empty()).unwrap(),
    ).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    let rows = body["data"].as_array().unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows.iter().find(|row| row["id"] == "conv_old").unwrap()["incompatible"], true);

    let response = app.clone().oneshot(
        Request::builder().uri("/api/chat/conversations/conv_old").body(Body::empty()).unwrap(),
    ).await.unwrap();
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = body_json(response).await;
    assert_eq!(body["code"], "conversation_migration_required");
    assert_eq!(body["error"], "不兼容的对话记录");
    assert_eq!(fs::read(path).unwrap(), source);
    assert!(!data.database().contains("conv_old").unwrap());

    let mut database = data.database();
    assert!(database.import(&old, &store::index_row(&old)).unwrap());
    assert!(database.delete("conv_old").unwrap());
    let response = app.clone().oneshot(
        Request::builder().uri("/api/chat/conversations/conv_old").body(Body::empty()).unwrap(),
    ).await.unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let response = app.oneshot(
        Request::builder().uri("/api/chat/conversations").body(Body::empty()).unwrap(),
    ).await.unwrap();
    assert_eq!(body_json(response).await["data"].as_array().unwrap().len(), 1);
}

#[test]
fn sqlite_tree_protects_media_and_deleted_import_does_not_retain_it() {
    let data = DataDir::new();
    let mut old = data.write_fixture();
    old["id"] = json!("conv_media");
    let hash = "1".repeat(64);
    let media_id = format!("sha256:{hash}");
    old["messages"][0]["metadata"]["media_refs"] = json!([{"media_id":media_id}]);
    let directory = data.0.join("chat/conversations");
    fs::write(directory.join("conv_media.json"), old.to_string()).unwrap();
    let media = directory.join("media_store");
    let relative = format!("blobs/11/{hash}.png");
    let blob = media.join(&relative);
    fs::create_dir_all(blob.parent().unwrap()).unwrap();
    fs::write(&blob, b"media fixture").unwrap();
    let index = json!({"media":{&media_id:{"blob_rel_path":relative,"updated_at":"2000-01-01T00:00:00Z"}}});
    fs::write(media.join("index.json"), index.to_string()).unwrap();
    let (model, _server) = MockModel::start(0, None);
    let mode = mode(&data, model);
    let collect = || mode.storage(|paths| {
        super::media_gc::collect_after_write(paths);
        Ok(())
    }).unwrap();
    collect();
    assert!(blob.exists());

    let mut database = data.database();
    assert!(database.import(&old, &store::index_row(&old)).unwrap());
    let source = directory.join("conv_media.json");
    fs::remove_file(&source).unwrap();
    collect();
    assert!(blob.exists());

    fs::write(source, old.to_string()).unwrap();
    assert!(database.delete("conv_media").unwrap());
    collect();
    assert!(!blob.exists());
}

#[test]
fn network_and_settings_use_conversation_metadata() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, _server) = MockModel::start(0, None);
    let mode = mode(&data, model);

    let result = mode
        .storage(|paths| tree::set_network(paths, CONVERSATION, "network-1", false, 1_700_000_000.0))
        .unwrap();
    assert_eq!(result["value"]["enabled"], false);
    assert_eq!(result["value"]["revision"], 1);
    assert_eq!(result["revision"], 5);
    let raw = data.raw(CONVERSATION);
    assert_eq!(raw["metadata"]["network_enabled"], false);
    assert_eq!(
        raw["metadata"]["pending_network_notices"].as_array().unwrap().len(),
        1
    );

    let settings = json!({"thinking_mode": true, "reasoning_effort": "high"})
        .as_object()
        .unwrap()
        .clone();
    mode.storage(|paths| tree::configure(paths, CONVERSATION, &settings, 1_700_000_001.0))
        .unwrap();
    let raw = data.raw(CONVERSATION);
    assert_eq!(raw["metadata"]["thinking_mode"], true);
    assert_eq!(raw["metadata"]["run_mode"], "thinking");
    assert_eq!(raw["metadata"]["reasoning_effort"], "high");

    let invalid = json!({"permission_mode":"unrestricted"})
        .as_object()
        .unwrap()
        .clone();
    assert!(!invalid.is_empty());
    let error = mode
        .storage(|paths| tree::configure(paths, CONVERSATION, &invalid, 1_700_000_002.0));
    // tree::configure itself is schema-free; the route validates. Ensure the
    // route helper rejects unknown settings separately.
    assert!(error.is_ok());
    let route_error = super::routes::validate_settings(&invalid).unwrap_err();
    assert_eq!(route_error.status, 400);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn task_admission_writes_branch_events_and_finishes_turn() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, server) = MockModel::start(2, None);
    let server = server.serve();
    let mode = mode(&data, model);

    let task = send(&mode, CONVERSATION, "send-1", "third question", Some("msg_answer_2"));
    assert_eq!(task["status"], "running");
    assert_eq!(task["conversation_id"], CONVERSATION);
    assert_eq!(task["conversation_kind"], "chat");
    let task_id = task["task_id"].as_str().unwrap().to_owned();

    // The task is visible to navigation and therefore to the Host drain loop.
    let runs = mode.runtime.navigation_runs().unwrap();
    assert!(runs.iter().any(|run| run.workspace_id.is_empty() && run.running));

    let finished = wait_task(&mode, &task_id).await;
    assert_eq!(finished["data"]["status"], "succeeded", "{finished}");

    let raw = data.raw(CONVERSATION);
    assert_eq!(raw["metadata"]["chat"]["active_turn_id"], Value::Null);
    let leaf = raw["metadata"]["chat"]["latest_leaf_id"].as_str().unwrap();
    let leaf_message = raw["messages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|message| message["message_id"] == leaf)
        .unwrap();
    assert_eq!(leaf_message["role"], "assistant");
    assert_eq!(leaf_message["content"], "Hello world");
    assert_eq!(raw["messages"].as_array().unwrap().len(), 6);
    let turn = latest_turn(&raw);
    assert!(
        raw["metadata"]["chat"]["commands"]
            .get("send-1")
            .is_some()
    );
    assert!(
        raw["metadata"]["chat"]["turns"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item["id"] == turn && item["status"] == "complete")
    );
    assert_eq!(
        raw["metadata"]["chat"]["commands"]["send-1"]["result"]["value"]["id"],
        turn
    );

    // Replaying the accepted operation after the task retired is a conflict, not
    // a second branch.
    let duplicate = mode
        .create_task(&json!({
            "operation_id":"send-1","conversation_id":CONVERSATION,
            "message":"third question","parent_id":leaf,
        }))
        .unwrap_err();
    assert_eq!(duplicate.status, 409);

    // A regenerate creates an assistant sibling and keeps the old answer.
    let regenerate = mode
        .create_task(&json!({
            "operation_id":"regen-1","conversation_id":CONVERSATION,
            "regenerate_turn_id":turn,"message":"",
        }))
        .unwrap();
    wait_task(&mode, regenerate["task_id"].as_str().unwrap()).await;
    server.join().unwrap();
    let raw = data.raw(CONVERSATION);
    assert_eq!(raw["messages"].as_array().unwrap().len(), 7);
    let latest = latest_assistant(&raw);
    assert_eq!(latest["role"], "assistant");
    assert_eq!(latest["content"], "Hello world");
    let parent = latest["metadata"]["chat"]["parent_id"].as_str().unwrap();
    assert!(
        raw["messages"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|message| message["metadata"]["chat"]["parent_id"] == parent)
            .count()
            >= 2
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancel_finishes_the_turn_and_releases_the_gate() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, server) = MockModel::start(1, Some(Duration::from_millis(800)));
    let server = server.serve();
    let mode = mode(&data, model);

    let task = send(&mode, CONVERSATION, "cancel-1", "slow question", Some("msg_answer_2"));
    let task_id = task["task_id"].as_str().unwrap().to_owned();
    // Wait until the stream has started so the cancel path exercises partial text.
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let current = mode.runtime.poll_task("", &task_id, 0).unwrap();
        let has_text = current["data"]["events"]
            .as_array()
            .is_some_and(|events| events.iter().any(|event| event["type"] == "text_chunk"));
        if has_text {
            break;
        }
        assert!(Instant::now() < deadline);
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    mode.runtime.cancel_task("", &task_id).unwrap();
    let finished = wait_task(&mode, &task_id).await;
    assert_eq!(finished["data"]["status"], "stopped", "{finished}");
    server.join().unwrap();

    let raw = data.raw(CONVERSATION);
    assert_eq!(raw["metadata"]["chat"]["active_turn_id"], Value::Null);
    let turn = latest_turn(&raw);
    assert!(
        raw["metadata"]["chat"]["turns"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item["id"] == turn && item["status"] == "stopped")
    );
    assert!(
        !mode
            .runtime
            .navigation_runs()
            .unwrap()
            .iter()
            .any(|run| run.running)
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn stale_active_turn_is_recovered_before_next_send() {
    let data = DataDir::new();
    let mut fixture = data.write_fixture();
    // Simulate a worker that retired mid-generation: the durable turn is still
    // running and holds a completed tool call without a result.
    fixture["messages"].as_array_mut().unwrap().push(json!({
        "message_id":"msg_stale_assistant","role":"assistant","content":"",
        "tool_calls":[{"id":"call_stale","type":"function","function":{"name":"web_search","arguments":"{\"query\":\"x\"}"}}],
        "metadata":{"chat":{"parent_id":"msg_user_2","turn_id":"turn_stale"}}
    }));
    fixture["metadata"]["chat"]["turns"].as_array_mut().unwrap().push(json!({
        "id":"turn_stale","parent_id":"msg_user_2","tip_id":"msg_stale_assistant","status":"running"
    }));
    fixture["metadata"]["chat"]["active_turn_id"] = json!("turn_stale");
    fixture["metadata"]["chat"]["latest_leaf_id"] = json!("msg_stale_assistant");
    data.save(&fixture);

    let (model, server) = MockModel::start(1, None);
    let server = server.serve();
    let mode = mode(&data, model);
    let task = send(&mode, CONVERSATION, "after-recovery", "new question", Some("msg_stale_assistant"));
    wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    server.join().unwrap();

    let raw = data.raw(CONVERSATION);
    let recovered = raw["messages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|message| message["tool_call_id"] == "call_stale")
        .unwrap();
    assert_eq!(recovered["role"], "tool");
    let content: Value = serde_json::from_str(recovered["content"].as_str().unwrap()).unwrap();
    assert_eq!(content["status"], "cancelled");
    assert!(
        raw["metadata"]["chat"]["turns"]
            .as_array()
            .unwrap()
            .iter()
            .any(|turn| turn["id"] == "turn_stale" && turn["status"] == "failed")
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn http_routes_read_edit_and_run_tasks() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, server) = MockModel::start(1, None);
    let server = server.serve();
    let mode = mode(&data, model);
    let app = mode.clone().router();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/chat/conversations")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["data"].as_array().unwrap().len(), 1);

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/chat/conversations/{CONVERSATION}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = body_json(response).await;
    assert_eq!(body["data"]["document"]["id"], CONVERSATION);
    assert_eq!(
        body["data"]["document"]["messages"].as_array().unwrap().len(),
        4
    );

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri(format!("/api/chat/conversations/{CONVERSATION}/title"))
                .header("content-type", "application/json")
                .body(Body::from(json!({"title":"Renamed"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(data.raw(CONVERSATION)["title"], "Renamed");

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/api/chat/conversations/{CONVERSATION}/network"))
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({"operation_id":"network-route","enabled":false}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/chat/tasks")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "operation_id":"route-send",
                        "conversation_id":CONVERSATION,
                        "message":"route question",
                        "parent_id":"msg_answer_2",
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    let task_id = body["data"]["task_id"].as_str().unwrap().to_owned();

    let deadline = Instant::now() + Duration::from_secs(8);
    let mut final_body = Value::Null;
    loop {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/chat/tasks/{task_id}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = body_json(response).await;
        if body["data"]["status"] == "succeeded" {
            final_body = body;
            break;
        }
        assert!(Instant::now() < deadline, "{body}");
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    server.join().unwrap();
    assert_eq!(final_body["data"]["conversation_kind"], "chat");
    assert!(final_body["data"]["view"]["document"]["messages"].is_array());
    assert!(final_body["data"]["events"].as_array().unwrap().iter().any(|event| event["type"] == "text_chunk"));

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/api/chat/conversations/{CONVERSATION}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(!data.database().contains(CONVERSATION).unwrap());
}

async fn body_json(response: axum::response::Response) -> Value {
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn account_settings_and_memory_match_python_documents() {
    let data = DataDir::new();
    data.write_fixture();
    fs::write(
        data.0.join("personalization.json"),
        serde_json::to_vec(&json!({
            "chat_default_model": "fixture-model",
            "chat_default_thinking_mode": false,
            "chat_default_reasoning_effort": "high",
            "chat_network_enabled": false,
            "chat_instructions": "seed instructions",
            "theme": "dark",
            "legacy_extension": {"keep": true},
            "tavily_api_key": "legacy-secret",
        }))
        .unwrap(),
    )
    .unwrap();
    let (model, _server) = MockModel::start(0, None);
    let mode = mode(&data, model);
    let app = mode.clone().router();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/chat/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["data"]["settings"]["chat_default_model"], "fixture-model");
    assert_eq!(body["data"]["settings"]["chat_default_thinking_mode"], false);
    assert_eq!(body["data"]["settings"]["chat_default_reasoning_effort"], "high");
    assert_eq!(body["data"]["settings"]["chat_network_enabled"], false);
    assert_eq!(body["data"]["settings"]["chat_deep_compress_trigger_tokens"], 250_000);
    assert_eq!(body["data"]["username"], "host");
    assert_eq!(body["data"]["theme"], "dark");
    assert!(body["data"]["models"].is_array());
    assert!(body["data"]["personalization"].get("tavily_api_key").is_none());
    assert!(body["data"]["personalization"].get("legacy_extension").is_none());

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/api/chat/settings")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "chat_network_enabled": true,
                        "chat_instructions": "updated",
                        "chat_deep_compress_trigger_tokens": 300000
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["data"]["settings"]["chat_network_enabled"], true);
    assert_eq!(body["data"]["settings"]["chat_instructions"], "updated");
    assert_eq!(body["data"]["settings"]["chat_deep_compress_trigger_tokens"], 300_000);
    let raw: Value = serde_json::from_slice(&fs::read(data.0.join("personalization.json")).unwrap()).unwrap();
    assert_eq!(raw["legacy_extension"]["keep"], true);
    assert_eq!(raw["tavily_api_key"], "legacy-secret");
    assert_eq!(raw["chat_network_enabled"], true);

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/api/chat/settings")
                .header("content-type", "application/json")
                .body(Body::from(json!({"unknown": true}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    let memory = data.0.join("chat/memory/memories.json");
    let get_memory = || {
        app.clone().oneshot(
            Request::builder()
                .uri("/api/chat/memory")
                .body(Body::empty())
                .unwrap(),
        )
    };
    let response = get_memory().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["data"]["global"], json!([]));
    assert_eq!(body["data"]["topics"], json!({}));

    for payload in [
        json!({"operation":"append","content":"first entry"}),
        json!({"operation":"replace","index":1,"content":"replaced entry"}),
        json!({"kind":"topic","name":"topic_a","description":"when to use it","content":"topic body"}),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/chat/memory")
                    .header("content-type", "application/json")
                    .body(Body::from(payload.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK, "payload {payload}");
    }
    let snapshot = body_json(get_memory().await.unwrap()).await;
    assert_eq!(snapshot["data"]["global"], json!(["replaced entry"]));
    assert_eq!(snapshot["data"]["topics"]["topic_a"]["description"], "when to use it");
    assert!(snapshot["data"]["topics"]["topic_a"]["updated_at"].as_str().is_some());
    let raw_memory: Value = serde_json::from_slice(&fs::read(&memory).unwrap()).unwrap();
    assert_eq!(raw_memory["version"], 1);
    assert_eq!(raw_memory["topics"]["topic_a"]["content"], "topic body");

    let prompt = mode.system_prompt(&data.raw(CONVERSATION)).unwrap();
    assert!(prompt.contains("## Chat 全局记忆\n1. replaced entry"));
    assert!(prompt.contains("## Chat 专题记忆索引\ntopic_a：when to use it"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn composer_drafts_use_revision_and_writer_watermarks() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, _server) = MockModel::start(0, None);
    let app = mode(&data, model).router();
    let sha256 = "a".repeat(64);
    let media_id = format!("sha256:{sha256}");
    let blob = data.0.join(format!("chat/conversations/media_store/blobs/aa/{sha256}.png"));
    fs::create_dir_all(blob.parent().unwrap()).unwrap();
    fs::write(&blob, b"x").unwrap();
    let mut media_index = json!({"version":1,"media":{},"message_map":{}});
    media_index["media"][&media_id] = json!({
        "media_id":media_id,"sha256":sha256,"kind":"image","mime_type":"image/png",
        "size":1,"blob_rel_path":format!("blobs/aa/{sha256}.png")
    });
    fs::write(
        data.0.join("chat/conversations/media_store/index.json"),
        serde_json::to_vec(&media_index).unwrap(),
    )
    .unwrap();

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/chat/drafts/new")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(body_json(response).await["data"], json!({}));

    // A document written by Python's save_draft remains readable as-is.
    let python_draft = data.0.join("chat/drafts").join(format!("{CONVERSATION}.json"));
    fs::create_dir_all(python_draft.parent().unwrap()).unwrap();
    fs::write(
        &python_draft,
        serde_json::to_vec(&json!({
            "text":"python draft",
            "media":[],
            "writer_sequences":{"window-a":3},
            "revision":7
        }))
        .unwrap(),
    )
    .unwrap();
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/chat/drafts/{CONVERSATION}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = body_json(response).await;
    assert_eq!(body["data"]["text"], "python draft");
    assert_eq!(body["data"]["revision"], 7);
    assert_eq!(body["data"]["writer_sequences"]["window-a"], 3);

    let save = |body: Value| {
        app.clone().oneshot(
            Request::builder()
                .method("PUT")
                .uri("/api/chat/drafts/new")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
    };
    let response = save(json!({
        "text":"first draft",
        "media":[{"media_id":media_id.clone()}],
        "client_id":"window-a",
        "sequence":1,
        "base_revision":0
    }))
    .await
    .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["data"]["revision"], 1);
    assert_eq!(body["data"]["text"], "first draft");
    assert_eq!(body["data"]["writer_sequences"]["window-a"], 1);
    assert_eq!(body["data"]["media"], json!([{
        "media_id":media_id, "kind":"image", "mime_type":"image/png", "size":1, "sha256":sha256
    }]));
    let path = data.0.join("chat/drafts/new.json");
    assert!(path.exists());

    // An unregistered/bad reference is rejected before touching the saved draft.
    let response = save(json!({
        "text":"bad media",
        "media":[{"media_id":format!("sha256:{}", "f".repeat(64))}],
        "client_id":"window-b",
        "sequence":1,
        "base_revision":1
    }))
    .await
    .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let raw: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    assert_eq!(raw["text"], "first draft");
    assert_eq!(raw["revision"], 1);

    let response = save(json!({
        "text":"second draft",
        "media":[],
        "client_id":"window-b",
        "sequence":1,
        "base_revision":1
    }))
    .await
    .unwrap();
    assert_eq!(body_json(response).await["data"]["revision"], 2);

    // A stale writer keeps the newest text instead of clearing another window.
    let response = save(json!({
        "text":"stale draft",
        "client_id":"window-a",
        "sequence":1,
        "base_revision":0
    }))
    .await
    .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["data"]["text"], "second draft");
    assert_eq!(body["data"]["revision"], 2);

    let response = save(json!({
        "text":"conflicting draft",
        "client_id":"window-a",
        "sequence":2,
        "base_revision":1
    }))
    .await
    .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let raw: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    assert_eq!(raw["text"], "second draft");
    assert_eq!(raw["revision"], 2);

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/chat/drafts/{CONVERSATION}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(body_json(response).await["data"]["text"], "python draft");
    let response = app
        .oneshot(
            Request::builder()
                .uri("/api/chat/drafts/conv_20240101_000000_999")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn duplicate_copies_the_tree_once_and_requires_an_idle_source() {
    let data = DataDir::new();
    data.write_fixture();
    // Old branch plus legacy handoff records: copy must keep history decisions
    // while removing every live owner/retry identity.
    let mut source = data.raw(CONVERSATION);
    source["messages"].as_array_mut().unwrap().push(json!({
        "message_id":"msg_answer_1b","role":"assistant",
        "content":"alternate response <suggest_agent reason=\"continue\"/>",
        "timestamp":"2024-09-12T10:41:10.000","metadata":{"chat":{"parent_id":"msg_user_1","turn_id":"turn_3"}}
    }));
    source["metadata"]["chat"]["turns"].as_array_mut().unwrap().push(json!({
        "id":"turn_3","parent_id":"msg_user_1","tip_id":"msg_answer_1b","status":"complete"
    }));
    source["metadata"]["chat"]["handoffs"] = json!({
        "msg_answer_1":{"status":"pending"},
        "msg_answer_1b":{"status":"preparing","presentation_id":"presentation-old",
            "operation_id":"operation-old","task_id":"task-old","agent_task_id":"agent-old"}
    });
    source["metadata"]["chat"]["handoff_count"] = json!(2);
    data.save(&source);
    let (model, _server) = MockModel::start(0, None);
    let app = mode(&data, model).router();
    let duplicate = |operation: Option<&str>| {
        let body = match operation {
            Some(operation) => json!({"operation_id": operation}),
            None => json!({}),
        };
        app.clone().oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/api/chat/conversations/{CONVERSATION}/duplicate"))
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
    };
    let response = duplicate(None).await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    let response = duplicate(Some("copy-1")).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    let copy_id = body["data"]["conversation_id"].as_str().unwrap().to_owned();
    assert_ne!(copy_id, CONVERSATION);
    let copy = data.raw(&copy_id);
    assert_eq!(copy["title"], "Fixture Chat (copy)");
    assert_eq!(copy["messages"], data.raw(CONVERSATION)["messages"]);
    let chat = &copy["metadata"]["chat"];
    assert_eq!(chat["latest_leaf_id"], "msg_answer_2");
    assert_eq!(chat["revision"], 4);
    assert_eq!(chat["copied_from"], CONVERSATION);
    assert!(chat["creation_operation_id"].as_str().unwrap().starts_with("copy_"));
    assert_eq!(chat["creation_operation_id"].as_str().unwrap().len(), 69);
    assert_eq!(chat["commands"], json!({}));
    let handoffs = chat["handoffs"].as_object().unwrap();
    assert_eq!(handoffs.len(), 2);
    assert_eq!(chat["handoff_count"], 2);
    for state in handoffs.values() {
        assert!(state["id"].as_str().unwrap().starts_with("handoff_"));
        assert!(state["source_handoff_id"].as_str().is_some());
        assert!(state.get("presentation_id").is_none());
        assert!(state.get("operation_id").is_none());
        assert!(state.get("task_id").is_none());
        assert!(state.get("agent_task_id").is_none());
        assert_eq!(state["countdown_enabled"], false);
    }
    let old = handoffs
        .values()
        .find(|state| state["message_id"] == "msg_answer_1")
        .unwrap();
    assert_eq!(old["status"], "superseded");
    let preparing = handoffs
        .values()
        .find(|state| state["message_id"] == "msg_answer_1b")
        .unwrap();
    assert_eq!(preparing["status"], "pending");
    assert_eq!(preparing["error"], "");
    assert_eq!(copy["metadata"]["model_key"], "fixture-model");
    assert_eq!(data.raw(CONVERSATION)["title"], "Fixture Chat");

    let response = duplicate(Some("copy-1")).await.unwrap();
    assert_eq!(body_json(response).await["data"]["conversation_id"], copy_id);
    assert_eq!(data.database().rows().unwrap().len(), 2);

    // A retry of the same operation repairs a copy created before population.
    let mut partial = data.raw(&copy_id);
    partial["messages"] = json!([]);
    partial["metadata"]["chat"].as_object_mut().unwrap().remove("copied_from");
    partial["metadata"]["chat"]["latest_leaf_id"] = Value::Null;
    data.save(&partial);
    let response = duplicate(Some("copy-1")).await.unwrap();
    assert_eq!(body_json(response).await["data"]["conversation_id"], copy_id);
    assert_eq!(data.raw(&copy_id)["messages"], data.raw(CONVERSATION)["messages"]);

    let mut source = data.raw(CONVERSATION);
    source["metadata"]["chat"]["active_turn_id"] = json!("turn_2");
    data.save(&source);
    let response = duplicate(Some("copy-2")).await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn new_chat_uses_account_defaults_and_rejects_corrupt_preferences() {
    let data = DataDir::new();
    fs::write(
        data.0.join("personalization.json"),
        serde_json::to_vec(&json!({
            "chat_default_model": "fixture-model",
            "chat_default_thinking_mode": false,
            "chat_default_reasoning_effort": "high",
            "chat_network_enabled": false,
        }))
        .unwrap(),
    )
    .unwrap();
    let (model, server) = MockModel::start(1, None);
    let server = server.serve();
    let mode = mode(&data, model);
    let task = mode
        .create_task(&json!({"operation_id":"defaults-1","message":"hello"}))
        .unwrap();
    let task_id = task["task_id"].as_str().unwrap().to_owned();
    wait_task(&mode, &task_id).await;
    server.join().unwrap();
    let conversation = task["conversation_id"].as_str().unwrap();
    let raw = data.raw(conversation);
    assert_eq!(raw["metadata"]["model_key"], "fixture-model");
    assert_eq!(raw["metadata"]["thinking_mode"], false);
    assert_eq!(raw["metadata"]["reasoning_effort"], "high");
    assert_eq!(raw["metadata"]["network_enabled"], false);

    let broken = br#"{"chat_network_enabled": tru"#.to_vec();
    fs::write(data.0.join("personalization.json"), &broken).unwrap();
    let response = mode
        .clone()
        .router()
        .oneshot(
            Request::builder()
                .uri("/api/chat/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(fs::read(data.0.join("personalization.json")).unwrap(), broken);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn oauth_session_preferences_use_conversation_effort_and_inherit_metadata() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, server) = MockModel::start(2, None);
    let server = server.serve();
    let observed = Arc::new(Mutex::new(Vec::new()));
    let seen = observed.clone();
    let mode = mode_with_resolver(
        &data,
        Arc::new(move |key, thinking, effort| {
            seen.lock().unwrap().push((
                key.map(str::to_owned),
                thinking,
                effort.map(str::to_owned),
            ));
            Ok(model.clone())
        }),
    );

    let task = mode
        .create_task(&json!({
            "operation_id":"oauth-settings-1",
            "conversation_id":CONVERSATION,
            "parent_id":"msg_answer_2",
            "message":"use effort",
            "thinking_mode":false,
            "settings":{"reasoning_effort":"xhigh","chatgpt_fast_mode":true},
        }))
        .unwrap();
    wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    assert_eq!(
        observed.lock().unwrap().as_slice(),
        [(
            Some("fixture-model".to_owned()),
            false,
            Some("xhigh".to_owned())
        )]
    );
    let raw = data.raw(CONVERSATION);
    assert_eq!(raw["metadata"]["reasoning_effort"], "xhigh");
    assert_eq!(raw["metadata"]["chatgpt_fast_mode"], true);

    let task = mode
        .create_task(&json!({
            "operation_id":"oauth-settings-2",
            "conversation_id":CONVERSATION,
            "parent_id":latest_assistant(&raw)["message_id"],
            "message":"inherit effort",
            "thinking_mode":false,
        }))
        .unwrap();
    wait_task(&mode, task["task_id"].as_str().unwrap()).await;
    server.join().unwrap();
    assert_eq!(
        observed.lock().unwrap().as_slice(),
        [
            (
                Some("fixture-model".to_owned()),
                false,
                Some("xhigh".to_owned())
            ),
            (
                Some("fixture-model".to_owned()),
                false,
                Some("xhigh".to_owned())
            )
        ]
    );
}

#[test]
fn unknown_agent_tool_and_foreign_conversation_are_rejected() {
    let data = DataDir::new();
    data.write_fixture();
    let (model, _server) = MockModel::start(0, None);
    let mode = mode(&data, model);

    let mut used = HashSet::new();
    let error = super::tasks::validated_calls(
        &json!({"tool_calls":[{"id":"call_1","function":{"name":"run_command","arguments":"{}"}}]}),
        &mut used,
    )
    .unwrap_err();
    assert!(error.contains("Tool unavailable in Chat"));

    // A Chat document never reads or writes Agent conversations.
    std::fs::create_dir_all(data.0.join("conversations/default")).unwrap();
    std::fs::write(
        data.0.join("conversations/default/conv_agent.json"),
        b"{\"id\":\"conv_agent\"}",
    )
    .unwrap();
    let rows = mode.storage(|paths| paths.list_rows()).unwrap();
    assert_eq!(rows.len(), 1);
}

#[test]
fn chat_scope_requires_an_absolute_data_root() {
    let scope = super::chat_scope("/srv/astrion/data").unwrap();
    assert_eq!(scope.root, PathBuf::from("/srv/astrion/data/chat"));
    assert_eq!(scope.preferences_dir, PathBuf::from("/srv/astrion/data"));
    assert_eq!(
        super::chat_scope("relative/path").unwrap_err().status,
        400
    );
}
