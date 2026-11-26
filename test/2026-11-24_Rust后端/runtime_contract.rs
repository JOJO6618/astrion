use std::{
    fs,
    io::{BufRead, BufReader, ErrorKind, Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    process::Command,
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

use serde_json::{Value, json};
use uuid::Uuid;

use super::{ModelConfig, Runtime, Task, TaskStart, now};

const WORKSPACE: &str = "default";
const TIMEOUT: Duration = Duration::from_secs(8);
const LOCK_PROBE: &str = "ASTRION_RUNTIME_UNIT_LOCK_PROBE";
const LEGACY_ID: &str = "conv_20240912_104056_001";

struct DataDir(PathBuf);

impl DataDir {
    fn new() -> Self {
        Self(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../test/2026-11-24_Rust后端")
                .join(format!("runtime-run-{}", Uuid::new_v4())),
        )
    }

    fn runtime(&self) -> Runtime {
        let project = self.0.join("project");
        fs::create_dir_all(&project).unwrap();
        let runtime = Runtime::open(self.0.join("data")).unwrap();
        runtime.bind_workspace(WORKSPACE, project).unwrap();
        runtime
    }

    fn conversation(&self, id: &str) -> PathBuf {
        self.0
            .join("data/conversations")
            .join(WORKSPACE)
            .join(format!("{id}.json"))
    }

    fn index(&self) -> PathBuf {
        self.conversation("index")
    }

    fn disk(&self, id: &str) -> Value {
        self.db().read(id).unwrap().unwrap()
    }

    fn db(&self)->crate::conversation_db::Database{
        crate::conversation_db::Database::open(self.index().parent().unwrap()).unwrap()
    }

    fn fail_writes(&self,enabled:bool){
        let connection=rusqlite::Connection::open(self.index().with_file_name(crate::conversation_db::FILENAME)).unwrap();
        connection.execute_batch(if enabled {"CREATE TRIGGER block_save BEFORE UPDATE ON conversations BEGIN SELECT RAISE(ABORT,'storage unavailable'); END;"}else{"DROP TRIGGER block_save;"}).unwrap();
    }

    fn fixture(&self) -> Value {
        let raw = json!({
            "id":LEGACY_ID,"title":"Python title","created_at":"2024-09-12T10:40:56.001",
            "updated_at":"2024-09-12T10:41:28.300","messages":[
                {"role":"user","content":"original user","message_id":"msg_old_user","metadata":{"legacy_extra":[1,2]}},
                {"role":"assistant","content":"original response","message_id":"msg_old_assistant"}
            ],
            "metadata":{"project_path":"/old-project","run_mode":"fast","model_key":"old-key",
                "frozen_system_prompt":"The original frozen prompt","extra_field":{"mode":"custom"},
                "total_messages":2,"total_tools":0},
            "token_statistics":{"total_input_tokens":10,"vendor_field":{"keep":true}},
            "todo_list":[{"text":"preserve"}],"opaque_future_field":{"keep":true}
        });
        fs::create_dir_all(self.index().parent().unwrap()).unwrap();
        fs::write(
            self.conversation(LEGACY_ID),
            serde_json::to_vec(&raw).unwrap(),
        )
        .unwrap();
        fs::write(
            self.index(),
            serde_json::to_vec(&json!({
                LEGACY_ID:{"title":"Python title","model_key":"old-key","extra_index_field":42},
                "conv_other":{"title":"Unrelated row","extra_index_field":"other"}
            }))
            .unwrap(),
        )
        .unwrap();
        self.db().import(&raw,&json!({"title":"Python title","model_key":"old-key","extra_index_field":42})).unwrap();
        raw
    }
}

impl Drop for DataDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn model() -> (ModelConfig, TcpListener) {
    let server = TcpListener::bind("127.0.0.1:0").unwrap();
    server.set_nonblocking(true).unwrap();
    let endpoint = format!(
        "http://{}/v1/chat/completions",
        server.local_addr().unwrap()
    );
    (
        ModelConfig::fixed(endpoint, None, "runtime-fixture".into()),
        server,
    )
}

fn request(server: &TcpListener) -> (TcpStream, Value) {
    let deadline = Instant::now() + TIMEOUT;
    let mut socket = loop {
        match server.accept() {
            Ok((socket, _)) => break socket,
            Err(error) if error.kind() == ErrorKind::WouldBlock && Instant::now() < deadline => {
                thread::sleep(Duration::from_millis(5));
            }
            Err(error) => panic!("mock did not receive a model request: {error}"),
        }
    };
    socket.set_nonblocking(false).unwrap();
    socket.set_read_timeout(Some(TIMEOUT)).unwrap();
    socket.set_write_timeout(Some(TIMEOUT)).unwrap();
    let mut reader = BufReader::new(&mut socket);
    let mut length = None;
    let mut first_line = String::new();
    reader.read_line(&mut first_line).unwrap();
    assert_eq!(first_line.trim(), "POST /v1/chat/completions HTTP/1.1");
    loop {
        let mut line = String::new();
        assert_ne!(reader.read_line(&mut line).unwrap(), 0);
        if line.trim().is_empty() {
            break;
        }
        if let Some(value) = line.to_ascii_lowercase().strip_prefix("content-length:") {
            length = Some(value.trim().parse().unwrap());
        }
    }
    let mut body = vec![0; length.unwrap()];
    reader.read_exact(&mut body).unwrap();
    (socket, serde_json::from_slice(&body).unwrap())
}

fn stream_head(socket: &mut TcpStream) {
    socket
        .write_all(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n",
        )
        .unwrap();
}

fn delta(socket: &mut TcpStream, delta: Value, reason: Option<&str>) {
    let data = json!({"choices":[{"index":0,"delta":delta,"finish_reason":reason}]});
    write!(socket, "data: {data}\n\n").unwrap();
}

fn done(socket: &mut TcpStream) {
    socket.write_all(b"data: [DONE]\n\n").unwrap();
}

fn usage(socket: &mut TcpStream, input: u64, output: u64) {
    let data = json!({"choices":[],"usage":{"prompt_tokens":input,"completion_tokens":output}});
    write!(socket, "data: {data}\n\n").unwrap();
}

fn tool(index: u64, name: &str, arguments: &str) -> Value {
    json!({"index":index,"id":format!("call_{index}"),"type":"function",
        "function":{"name":name,"arguments":arguments}})
}

fn echo_round(socket: &mut TcpStream, multi: bool) {
    stream_head(socket);
    let mut calls = vec![tool(0, "astrion_echo", r#"{"text":"first"}"#)];
    if multi {
        calls.push(tool(1, "astrion_echo", r#"{"text":"second"}"#));
    }
    delta(socket, json!({"tool_calls":calls}), Some("tool_calls"));
    usage(socket, 5, 2);
    done(socket);
}

fn accept(runtime: &Runtime, payload: Value, model: ModelConfig) -> (String, String) {
    let accepted = runtime.create_task(WORKSPACE, &payload, model).unwrap();
    assert_eq!(accepted["data"]["status"], "running");
    (
        accepted["data"]["conversation_id"].as_str().unwrap().into(),
        accepted["data"]["task_id"].as_str().unwrap().into(),
    )
}

async fn poll_until(runtime: &Runtime, id: &str, ready: impl Fn(&Value) -> bool) -> Value {
    tokio::time::timeout(TIMEOUT, async {
        loop {
            let poll = runtime.poll_task(WORKSPACE, id, 0).unwrap();
            if ready(&poll["data"]) {
                break poll["data"].clone();
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("task poll reached expected state")
}

async fn terminal(runtime: &Runtime, id: &str) -> Value {
    poll_until(runtime, id, |poll| poll["status"] != "running").await
}

fn roles(messages: &Value) -> Vec<&str> {
    messages
        .as_array()
        .unwrap()
        .iter()
        .map(|message| message["role"].as_str().unwrap())
        .collect()
}

#[tokio::test]
async fn imported_python_document_keeps_metadata_and_updates_sql_summary() {
    let dir = DataDir::new();
    let old = dir.fixture();
    let runtime = dir.runtime();
    let (model, server) = model();
    assert_eq!(
        runtime
            .session_model_key(WORKSPACE, LEGACY_ID)
            .unwrap()
            .as_deref(),
        Some("old-key")
    );
    let rejected = runtime.create_task(
        WORKSPACE,
        &json!({"message":"wrong model","model_key":"other"}),
        model.clone(),
    );
    assert_eq!(rejected.unwrap_err().status, 400);
    let mock = thread::spawn(move || {
        let (mut socket, body) = request(&server);
        assert_eq!(
            body["messages"][0],
            json!({"role":"system","content":"The original frozen prompt"})
        );
        assert_eq!(
            body["messages"][1],
            json!({"role":"user","content":"original user"})
        );
        assert_eq!(
            body["messages"][3],
            json!({"role":"user","content":"continue"})
        );
        echo_round(&mut socket, true);
        drop(socket);
        let (mut socket, body) = request(&server);
        assert_eq!(
            body["messages"][5],
            json!({"role":"tool","tool_call_id":"call_0","content":"first"})
        );
        assert_eq!(
            body["messages"][6],
            json!({"role":"tool","tool_call_id":"call_1","content":"second"})
        );
        stream_head(&mut socket);
        delta(&mut socket, json!({"content":"finished"}), Some("stop"));
        usage(&mut socket, 7, 4);
        done(&mut socket);
    });
    let (id, task) = accept(
        &runtime,
        json!({"conversation_id":LEGACY_ID,"message":"continue"}),
        model,
    );
    assert_eq!(id, LEGACY_ID);
    let poll = terminal(&runtime, &task).await;
    assert_eq!(poll["status"], "succeeded", "{poll}");
    mock.join().unwrap();
    let events = poll["events"].as_array().unwrap();
    assert_eq!(
        events
            .iter()
            .map(|event| event["type"].as_str().unwrap())
            .collect::<Vec<_>>(),
        [
            "api_request_start",
            "token_update",
            "tool_start",
            "tool_start",
            "update_action",
            "update_action",
            "api_request_start",
            "text_start",
            "text_chunk",
            "text_end",
            "token_update",
            "task_complete"
        ]
    );
    for (i, event) in events.iter().enumerate() {
        assert_eq!(event["idx"], i);
        assert_eq!(event["data"]["conversation_id"], LEGACY_ID);
    }
    assert_eq!(events[6]["data"]["attempt"], 1);
    assert_eq!(events[6]["data"]["max_attempts"], 1);
    let drained = runtime
        .poll_task(WORKSPACE, &task, events.len() as u64)
        .unwrap();
    assert_eq!(drained["data"]["events"], json!([]));
    assert_eq!(drained["data"]["next_offset"], events.len());
    let raw = dir.disk(LEGACY_ID);
    assert_eq!(
        raw["messages"].as_array().unwrap()[..2],
        old["messages"].as_array().unwrap()[..]
    );
    assert_eq!(
        roles(&raw["messages"]),
        [
            "user",
            "assistant",
            "user",
            "assistant",
            "tool",
            "tool",
            "assistant"
        ]
    );
    for key in [
        "id",
        "title",
        "created_at",
        "todo_list",
        "opaque_future_field",
    ] {
        assert_eq!(raw[key], old[key]);
    }
    for key in ["project_path", "frozen_system_prompt", "extra_field"] {
        assert_eq!(raw["metadata"][key], old["metadata"][key]);
    }
    assert_eq!(raw["token_statistics"]["total_input_tokens"], 22);
    assert_eq!(raw["token_statistics"]["total_output_tokens"], 6);
    assert_eq!(
        raw["token_statistics"]["vendor_field"],
        old["token_statistics"]["vendor_field"]
    );
    assert_eq!(raw["metadata"]["total_tools"], 2);
    let index: Value = serde_json::from_slice(&fs::read(dir.index()).unwrap()).unwrap();
    assert_eq!(index[LEGACY_ID]["extra_index_field"], 42);
    let summary=dir.db().summary(LEGACY_ID).unwrap().unwrap();
    assert_eq!(summary["total_messages"],7);
    assert_eq!(summary["extra_index_field"],42);
    assert_eq!(index["conv_other"]["extra_index_field"], "other");
    assert_eq!(
        runtime
            .session_history("other", LEGACY_ID)
            .unwrap_err()
            .status,
        404
    );
    assert_eq!(
        runtime.list_sessions(WORKSPACE, 10, 0, None).unwrap()["total"],
        1
    );
    drop(runtime);
    let reopened = dir.runtime();
    assert_eq!(
        reopened
            .poll_task(WORKSPACE, &task, events.len() as u64)
            .unwrap_err()
            .status,
        404
    );
    assert_eq!(
        reopened.session_history(WORKSPACE, LEGACY_ID).unwrap()["conversation"],
        raw
    );
}

#[tokio::test]
async fn next_model_failure_preserves_the_durable_complete_tool_batch() {
    let dir = DataDir::new();
    let runtime = dir.runtime();
    let (model, server) = model();
    let mock = thread::spawn(move || {
        let (mut socket, _) = request(&server);
        echo_round(&mut socket, true);
        drop(socket);
        let (mut socket, body) = request(&server);
        assert_eq!(body["messages"][4]["tool_call_id"], "call_1");
        stream_head(&mut socket);
        delta(&mut socket, json!({"content":"uncommitted followup"}), None);
    });
    let (id, task) = accept(&runtime, json!({"message":"start"}), model);
    assert_eq!(terminal(&runtime, &task).await["status"], "failed");
    mock.join().unwrap();
    let document = dir.disk(&id);
    assert!(id.starts_with("conv_"));
    assert_eq!(
        document["metadata"]["project_path"],
        json!(fs::canonicalize(dir.0.join("project")).unwrap())
    );
    assert_eq!(
        roles(&document["messages"]),
        ["user", "assistant", "tool", "tool"]
    );
    assert_eq!(document["messages"][3]["tool_call_id"], "call_1");
}

#[tokio::test]
async fn unknown_tool_in_a_batch_prevents_all_results_from_reaching_disk() {
    let dir = DataDir::new();
    let runtime = dir.runtime();
    let (model, server) = model();
    let mock = thread::spawn(move || {
        let (mut socket, _) = request(&server);
        stream_head(&mut socket);
        delta(
            &mut socket,
            json!({"tool_calls":[
                tool(0,"astrion_echo", r#"{"text":"must not checkpoint"}"#), tool(1,"unknown_tool","{}")
            ]}),
            Some("tool_calls"),
        );
        done(&mut socket);
    });
    let (id, task) = accept(&runtime, json!({"message":"start"}), model);
    let failed = terminal(&runtime, &task).await;
    mock.join().unwrap();
    assert_eq!(failed["status"], "failed");
    assert_eq!(failed["error"], "Unknown tool: unknown_tool");
    assert_eq!(roles(&dir.disk(&id)["messages"]), ["user"]);
    assert_eq!(failed["events"].as_array().unwrap().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancel_drops_pending_upstream_after_checkpoint_and_releases_session_gate() {
    let dir = DataDir::new();
    let runtime = dir.runtime();
    let (model, server) = model();
    let (server_ready, ready) = mpsc::channel();
    let mock = thread::spawn(move || {
        let (mut socket, _) = request(&server);
        echo_round(&mut socket, false);
        drop(socket);
        let (mut socket, _) = request(&server);
        server_ready.send(()).unwrap();
        assert_eq!(socket.read(&mut [0; 1]).unwrap(), 0);
        drop(socket);
        let (mut socket, body) = request(&server);
        assert_eq!(body["messages"][3]["tool_call_id"], "call_0");
        stream_head(&mut socket);
        delta(&mut socket, json!({"content":"recovered"}), Some("stop"));
        done(&mut socket);
    });
    let (id, task) = accept(&runtime, json!({"message":"start"}), model.clone());
    tokio::task::spawn_blocking(move || ready.recv_timeout(TIMEOUT).unwrap())
        .await
        .unwrap();
    assert_eq!(
        roles(&dir.disk(&id)["messages"]),
        ["user", "assistant", "tool"]
    );
    let next = json!({"conversation_id":id,"message":"retry"});
    assert_eq!(
        runtime
            .create_task(WORKSPACE, &next, model.clone())
            .unwrap_err()
            .status,
        409
    );
    assert_eq!(runtime.cancel_task("other", &task).unwrap_err().status, 404);
    runtime.cancel_task(WORKSPACE, &task).unwrap();
    runtime.cancel_task(WORKSPACE, &task).unwrap();
    let stopped = terminal(&runtime, &task).await;
    assert_eq!(stopped["status"], "stopped");
    assert_eq!(
        stopped["events"].as_array().unwrap().last().unwrap()["type"],
        "task_stopped"
    );
    let (_, retry) = accept(&runtime, next, model);
    assert_eq!(terminal(&runtime, &retry).await["status"], "succeeded");
    mock.join().unwrap();
}

#[test]
fn restart_recovers_index_and_preserves_checkpoint_without_resuming_task() {
    let dir = DataDir::new();
    let runtime = dir.runtime();
    let (model, server) = model();
    let mock = thread::spawn(move || {
        let (mut socket, _) = request(&server);
        echo_round(&mut socket, false);
        drop(socket);
        let (mut socket, _) = request(&server);
        stream_head(&mut socket);
        delta(&mut socket, json!({"content":"volatile fragment"}), None);
        assert_eq!(socket.read(&mut [0; 1]).unwrap(), 0);
    });
    let tokio = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let (id, task, offset) = tokio.block_on(async {
        let (id, task) = accept(&runtime, json!({"message":"first"}), model);
        let poll = poll_until(&runtime, &task, |poll| {
            poll["events"]
                .as_array()
                .unwrap()
                .iter()
                .any(|event| event["type"] == "text_chunk")
        })
        .await;
        assert_eq!(poll["status"], "running");
        (id, task, poll["next_offset"].as_u64().unwrap())
    });
    drop(tokio);
    drop(runtime);
    mock.join().unwrap();
    let checkpoint = dir.disk(&id);
    assert_eq!(
        roles(&checkpoint["messages"]),
        ["user", "assistant", "tool"]
    );
    assert!(!checkpoint.to_string().contains("volatile fragment"));
    // Model a process exit after the authoritative file was replaced but before index replace.
    let marker = dir.0.join("data/runtime/index-repair.json");
    fs::write(
        &marker,
        json!({"workspace_id":WORKSPACE,"conversation_id":id}).to_string(),
    )
    .unwrap();
    fs::write(
        dir.index(),
        json!({"conv_unrelated":{"custom":42}}).to_string(),
    )
    .unwrap();
    let reopened = dir.runtime();
    assert!(marker.exists()); // historical JSON metadata remains intact
    assert_eq!(dir.db().summary(&id).unwrap().unwrap()["total_messages"],3);
    let index: Value = serde_json::from_slice(&fs::read(dir.index()).unwrap()).unwrap();
    assert!(index.get(&id).is_none());
    assert_eq!(index["conv_unrelated"]["custom"], 42);
    assert_eq!(
        reopened
            .poll_task(WORKSPACE, &task, offset)
            .unwrap_err()
            .status,
        404
    );
    assert_eq!(dir.disk(&id), checkpoint);
}

fn manual_task(runtime: &Runtime, id: &str) -> String {
    let task_id = Uuid::new_v4().to_string();
    let (cancel, _) = tokio::sync::oneshot::channel();
    let start = TaskStart {
        id: task_id.clone(),
        created_at: now(),
        message: "question".into(),
        model_key: None,
        message_source: None,
    };
    runtime.store().unwrap().tasks.insert(
        task_id.clone(),
        Task::new(start, WORKSPACE, id.into(), cancel),
    );
    task_id
}

#[test]
fn checkpoint_retry_is_idempotent_and_event_window_stays_in_memory() {
    let dir = DataDir::new();
    let runtime = dir.runtime();
    let session = runtime.create_session(WORKSPACE, &json!({})).unwrap();
    let id = session["conversation_id"].as_str().unwrap();
    let task = manual_task(&runtime, id);
    let mut batch = vec![
        json!({"role":"assistant","tool_calls":[tool(0,"astrion_echo", r#"{"text":"echo"}"#)]}),
        json!({"role":"tool","tool_call_id":"call_0","content":"echo"}),
    ];
    assert!(
        runtime
            .commit_tool_batch(&task, 1, batch[..1].to_vec())
            .is_err()
    );
    runtime.commit_tool_batch(&task, 1, batch.clone()).unwrap();
    let committed = dir.disk(id);
    runtime.commit_tool_batch(&task, 1, batch.clone()).unwrap();
    batch[1]["content"] = json!("conflicting retry");
    assert!(runtime.commit_tool_batch(&task, 1, batch).is_err());
    assert_eq!(
        runtime.poll_task(WORKSPACE, &task, 0).unwrap()["data"]["next_offset"],
        1
    );
    for index in 0..20_003 {
        runtime
            .emit(&task, "text_chunk", json!({"content":index}))
            .unwrap();
    }
    let poll = runtime.poll_task(WORKSPACE, &task, 0).unwrap();
    assert_eq!(poll["data"]["window_start"], 4);
    assert_eq!(poll["data"]["next_offset"], 20_004);
    assert_eq!(poll["data"]["events"].as_array().unwrap().len(), 20_000);
    let tail = runtime.poll_task(WORKSPACE, &task, 20_003).unwrap();
    assert_eq!(tail["data"]["events"][0]["idx"], 20_003);
    assert_eq!(dir.disk(id), committed);
}

#[tokio::test]
async fn a_failed_sql_transaction_preserves_the_document_and_releases_failed_task() {
    let dir = DataDir::new();
    let old = dir.fixture();
    let runtime = dir.runtime();
    let (model, server) = model();
    dir.fail_writes(true);
    let payload = json!({"conversation_id":LEGACY_ID,"message":"try"});
    assert_eq!(
        runtime
            .create_task(WORKSPACE, &payload, model.clone())
            .unwrap_err()
            .status,
        503
    );
    assert_eq!(dir.disk(LEGACY_ID), old);
    dir.fail_writes(false);

    let (server_ready, ready) = mpsc::channel();
    let (release, release_mock) = mpsc::channel();
    let mock = thread::spawn(move || {
        let (mut socket, _) = request(&server);
        server_ready.send(()).unwrap();
        release_mock.recv_timeout(TIMEOUT).unwrap();
        echo_round(&mut socket, false);
    });
    let (_, task) = accept(&runtime, payload, model);
    tokio::task::spawn_blocking(move || ready.recv_timeout(TIMEOUT).unwrap())
        .await
        .unwrap();
    let accepted = dir.disk(LEGACY_ID);
    dir.fail_writes(true);
    release.send(()).unwrap();
    let failed = terminal(&runtime, &task).await;
    mock.join().unwrap();
    assert_eq!(failed["status"], "failed");
    assert!(
        failed["events"]
            .as_array()
            .unwrap()
            .iter()
            .all(|event| event["type"] != "update_action")
    );
    assert_eq!(dir.disk(LEGACY_ID), accepted);
    dir.fail_writes(false);
}

#[test]
fn runtime_lock_is_held_across_processes_and_released_on_close() {
    if let Some(data_dir) = std::env::var_os(LOCK_PROBE) {
        assert_eq!(Runtime::open(data_dir).err().unwrap().status, 409);
        return;
    }
    let dir = DataDir::new();
    let runtime = dir.runtime();
    let child = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "runtime::tests::runtime_lock_is_held_across_processes_and_released_on_close",
        ])
        .env(LOCK_PROBE, dir.0.join("data"))
        .output()
        .unwrap();
    assert!(
        child.status.success(),
        "{}",
        String::from_utf8_lossy(&child.stdout)
    );
    assert!(String::from_utf8_lossy(&child.stdout).contains("1 passed"));
    drop(runtime);
    assert!(Runtime::open(dir.0.join("data")).is_ok());
}

#[cfg(unix)]
#[test]
fn creates_private_directory_and_conversation_under_a_public_parent() {
    use std::os::unix::fs::PermissionsExt;
    let dir = DataDir::new();
    fs::create_dir(&dir.0).unwrap();
    fs::set_permissions(&dir.0, fs::Permissions::from_mode(0o755)).unwrap();
    let runtime = dir.runtime();
    let session = runtime.create_session(WORKSPACE, &json!({})).unwrap();
    let id = session["conversation_id"].as_str().unwrap();
    let store_dir = dir.0.join("data/runtime");
    let mode = |path| fs::metadata(path).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode(store_dir.clone()), 0o700);
    assert_eq!(mode(store_dir.join("writer.lock")), 0o600);
    assert!(dir.db().contains(id).unwrap());
    assert_eq!(mode(dir.index().with_file_name(crate::conversation_db::FILENAME)), 0o600);
}
