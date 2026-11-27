use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};

use axum::{Json, Router, extract::State, response::IntoResponse, routing::post};
use serde_json::{Value, json};
use tokio::{net::TcpListener, sync::mpsc, time::timeout};
use uuid::Uuid;

use super::{LegacyChatTurn, LegacyDocument, LegacyKind};
use crate::runtime::{ModelConfig, Runtime};

const WORKSPACE: &str = "legacy-fixture-workspace";
const FROZEN: &str = "旧版首次请求的完整提示。\n\n保留旧环境说明、原有换行和字符。\n";
const SOURCE_ID: &str = "conv_20261123_091511_014";
const CHAT_ID: &str = "conv_20261123_092214_015";
const PREVIOUS_ARGS: &str = r#"{"path":"old-file.txt"}"#;
const CIPHERTEXT: &str = "opaque-python-provider-fixture";
const NEXT_PROMPT: &str = "请继续原有会话。";
const NEXT_ANSWER: &str = "已经在原会话中继续。";

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let parent = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../test/2026-11-24_Rust后端");
        let root = parent.join(format!("legacy-run-{}", Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        Self(root.canonicalize().unwrap())
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn agent() -> Value {
    json!({
        "id":SOURCE_ID,
        "title":"旧 Agent 标题",
        "created_at":"2026-11-23T09:15:11.014000",
        "updated_at":"2026-11-23T09:16:52+08:00",
        "messages":[
            {"role":"user","message_id":"old_user_id","timestamp":"2026-11-23T09:15:11.017000","content":"查看这份结果。","metadata":{"future_field":{"opaque":42}}},
            {"role":"assistant","message_id":"old_call_id","content":"","reasoning_content":"模型的原始思考",
                "metadata":{"model_key":"old-provider/model","responses_reasoning_items":[{"type":"reasoning","encrypted_content":CIPHERTEXT}]},
                "tool_calls":[{"id":"call_old","type":"function","function":{"name":"read_file","arguments":PREVIOUS_ARGS,"future_function_field":true},"future_call_field":["x"]}]},
            {"role":"tool","message_id":"old_result_id","content":"历史结果","tool_call_id":"call_old","name":"read_file"},
            {"role":"assistant","message_id":"old_answer_id","content":"已经查看。","reasoning_content":""}
        ],
        "todo_list":{"items":[{"title":"保留旧待办","completed":true}]},
        "metadata":{
            "project_path":"/never-open/python-workspace",
            "project_relative_path":"fixtures/original",
            "model_key":"old-provider/model",
            "run_mode":"fast",
            "thinking_mode":false,
            "status":"active",
            "total_messages":4,
            "total_tools":1,
            "title_locked":true,
            "frozen_system_prompt":FROZEN,
            "frozen_custom_system_prompt":"旧字段也必须整份保留",
            "custom":{"private_key_shape":[1, "文字", {"next": null}]}
        },
        "unknown_root":[123,"unknown"],
        "token_statistics":{"total_input_tokens":4,"total_output_tokens":2,"total_tokens":6,"unknown_field":true}
    })
}

#[test]
fn reads_chat_branches_without_modifying_the_original_document() {
    let dir = Scratch::new();
    let bytes = br#"{
        "id":"conv_chat_original",
        "messages":[
            {"message_id":"msg_root","role":"user","content":"root","metadata":{"chat":{"parent_id":null,"turn_id":null}}},
            {"message_id":"msg_old","role":"assistant","content":"old branch","metadata":{"chat":{"parent_id":"msg_root","turn_id":"turn_old"}},"videos":["C:\\media\\old.mp4"]},
            {"message_id":"msg_new","role":"assistant","content":"latest branch","metadata":{"chat":{"parent_id":"msg_root","turn_id":"turn_new"},"media_refs":[{"source_path":"/original/asset.png","path":"relative/output.png","media_id":"sha256:fixture"}]}}
        ],
        "metadata":{"conversation_kind":"chat","chat":{"version":1,"latest_leaf_id":"msg_new","active_turn_id":null,"revision":8,"turns":[{"id":"turn_old","parent_id":"msg_root","tip_id":"msg_old","status":"complete"},{"id":"turn_new","parent_id":"msg_root","tip_id":"msg_new","status":"complete"}],"commands":{},"future_chat":{"number":1844674407370955161601}},"frozen_system_prompt":"frozen exact prompt"},
        "unknown_top":{"tell_future":true}
    }"#;
    let source = dir.0.join("conv_chat_original.json");
    fs::write(&source, bytes).unwrap();
    let mut document = LegacyDocument::read(&source).unwrap();
    let inspection = document.inspect();
    assert_eq!(inspection.kind, LegacyKind::Chat);
    assert_eq!(inspection.source_id, "conv_chat_original");
    assert!(!inspection.runnable);
    assert_eq!(
        document.raw()["metadata"]["frozen_system_prompt"],
        "frozen exact prompt"
    );
    assert_eq!(
        document.raw()["metadata"]["chat"]["future_chat"]["number"].to_string(),
        "1844674407370955161601"
    );

    let current = document.chat_path(None).unwrap();
    let old = document.chat_path(Some("msg_old")).unwrap();
    assert_eq!(current[0]["message_id"], "msg_root");
    assert_eq!(current[1]["message_id"], "msg_new");
    assert_eq!(old[1]["videos"][0], r"C:\media\old.mp4");
    assert_eq!(
        current[1]["metadata"]["media_refs"][0]["source_path"],
        "/original/asset.png"
    );
    assert_eq!(
        current[1]["metadata"]["media_refs"][0]["path"],
        "relative/output.png"
    );
    assert!(document.chat_path(Some("unknown")).is_err());
    assert_eq!(document.agent_history().unwrap_err().status, 422);
    assert_eq!(
        document
            .append_agent_messages(vec![], 0.0, None)
            .unwrap_err()
            .status,
        422
    );
    assert_eq!(fs::read(&source).unwrap(), bytes);

    let mut malformed = document.into_raw();
    malformed["messages"][1]["metadata"]["chat"]["parent_id"] = json!("msg_new");
    let malformed = LegacyDocument::from_value(malformed).unwrap();
    assert!(malformed.chat_path(None).is_err());
    assert_eq!(malformed.raw()["messages"].as_array().unwrap().len(), 3);
}

#[test]
fn reports_which_agent_documents_can_run_without_rewriting_their_history() {
    assert_eq!(LegacyDocument::parse(b"[]").unwrap_err().status, 400);
    let check = |raw, reason: &str| {
        let document = LegacyDocument::from_value(raw).unwrap();
        let inspection = document.inspect();
        assert_eq!(inspection.kind, LegacyKind::Agent);
        assert!(!inspection.runnable, "{inspection:?}");
        assert!(
            inspection.reasons.iter().any(|text| text.contains(reason)),
            "{inspection:?}"
        );
        assert_eq!(document.agent_history().unwrap_err().status, 422);
    };
    let mut raw = agent();
    raw["metadata"]["frozen_system_prompt"] = Value::Null;
    check(raw, "complete frozen system prompt");
    let mut raw = agent();
    raw["messages"][0]["metadata"]["media_refs"] = json!([{"path":"/not-opened/image.png"}]);
    let document = LegacyDocument::from_value(raw.clone()).unwrap();
    assert!(document.inspect().runnable);
    assert_eq!(document.agent_history().unwrap()[1]["content"], raw["messages"][0]["content"]);
    let mut raw = agent();
    raw["messages"][0]["metadata"]["deep_compacted"] = json!(true);
    check(raw, "Compressed history");
    let mut raw = agent();
    raw["metadata"]["pending_work_mode"] = json!("execute");
    check(raw, "pending Python runtime state");
    let mut raw = agent();
    raw["messages"].as_array_mut().unwrap().remove(2);
    check(raw, "no matching result");
    let mut raw = agent();
    raw["messages"][1]["tool_calls"][0]["function"]["arguments"] = json!("[]");
    check(raw, "must be a JSON object");

    let document = LegacyDocument::from_value(agent()).unwrap();
    assert!(document.inspect().runnable);
    assert!(document.created_at().is_some());
    assert!(document.updated_at().is_some());
    let history = document.agent_history().unwrap();
    assert_eq!(
        history[0],
        json!({"role":"system","content":FROZEN,"_frozen_system_prompt":true})
    );
    assert_eq!(
        history[1],
        json!({"role":"user","content":"查看这份结果。"})
    );
    assert!(
        !serde_json::to_string(&history)
            .unwrap()
            .contains(CIPHERTEXT)
    );
    assert_eq!(
        document.public_messages()[1]["reasoning_content"],
        "模型的原始思考"
    );
}

#[test]
fn appends_python_shaped_messages_and_merges_stats_without_touching_prior_fields() {
    let raw = agent();
    let mut document = LegacyDocument::from_value(raw.clone()).unwrap();
    document.append_agent_messages(vec![], 10.0, None).unwrap();
    assert_eq!(document.raw(), &raw);
    document
        .append_agent_messages(vec![], 10.0, Some(&json!({"total_input_tokens":13})))
        .unwrap();
    assert_eq!(document.raw()["messages"], raw["messages"]);
    assert_eq!(document.raw()["updated_at"], raw["updated_at"]);
    assert_eq!(document.raw()["token_statistics"]["total_tokens"], 15);
    assert_eq!(document.raw()["token_statistics"]["unknown_field"], true);

    let connection = "a".repeat(64);
    let reasoning = json!([{"type":"reasoning","encrypted_content":"new-rust-opaque"}]);
    document
        .append_agent_messages(
            vec![json!({
                "role":"assistant","message_id":"msg_existing_rust_id","content":"done",
                "_responses_connection":connection,"responses_reasoning_items":reasoning,
                "metadata":{"future_message_field":7}
            })],
            11.0,
            None,
        )
        .unwrap();
    let stored = &document.raw()["messages"][4];
    assert_eq!(stored["message_id"], "msg_existing_rust_id");
    assert!(stored.get("responses_reasoning_items").is_none());
    assert_eq!(stored["metadata"]["responses_reasoning_items"], reasoning);
    assert_eq!(stored["metadata"]["future_message_field"], 7);
    assert_eq!(
        document.agent_history().unwrap()[5]["_responses_connection"],
        connection
    );
    let public = document.public_messages();
    assert!(public[4]["metadata"].get("_responses_connection").is_none());
    assert!(
        public[4]["metadata"]
            .get("responses_reasoning_items")
            .is_none()
    );
    assert_eq!(public[4]["metadata"]["future_message_field"], 7);
}

#[tokio::test]
async fn production_python_conversation_migrates_once_and_rust_resumes_in_sqlite() {
    let dir = Scratch::new();
    let project = dir.0.join("workspace");
    fs::create_dir(&project).unwrap();
    fs::write(
        dir.0.join("host_workspaces.json"),
        serde_json::to_vec(&json!({
            "workspaces":[{"workspace_id":WORKSPACE,"path":project}]
        }))
        .unwrap(),
    )
    .unwrap();
    python_fixture(&dir.0, &project, SOURCE_ID, "seed", &agent());
    let source = dir
        .0
        .join("conversations")
        .join(WORKSPACE)
        .join(format!("{SOURCE_ID}.json"));
    let index = source.with_file_name("index.json");
    let source_bytes = fs::read(&source).unwrap();
    let index_bytes = fs::read(&index).unwrap();
    let original = LegacyDocument::parse(&source_bytes).unwrap().into_raw();

    let runtime = Runtime::open(&dir.0).unwrap();
    runtime.bind_workspace(WORKSPACE, &project).unwrap();
    assert_eq!(runtime.session_history(WORKSPACE, SOURCE_ID).unwrap_err().status, 409);
    let db_dir = source.parent().unwrap();
    let rows: Value = serde_json::from_slice(&index_bytes).unwrap();
    crate::conversation_db::Database::open(db_dir).unwrap().import(&original,&rows[SOURCE_ID]).unwrap();
    let public = runtime.session_history(WORKSPACE, SOURCE_ID).unwrap();
    assert_eq!(public["conversation"]["id"], SOURCE_ID);
    assert_eq!(
        public["conversation"]["messages"][1]["message_id"],
        "old_call_id"
    );
    assert!(!public.to_string().contains(CIPHERTEXT));
    assert_eq!(
        runtime.list_sessions(WORKSPACE, 20, 0, None).unwrap()["conversations"][0]["id"],
        SOURCE_ID
    );
    assert_eq!(fs::read(&source).unwrap(), source_bytes);
    assert_eq!(fs::read(&index).unwrap(), index_bytes);

    let (sender, mut request) = mpsc::unbounded_channel();
    let router = Router::new()
        .route("/v1/responses", post(mock_model))
        .with_state(sender);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/v1/responses", listener.local_addr().unwrap());
    let model = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let accepted = runtime
        .create_task(
            WORKSPACE,
            &json!({"conversation_id":SOURCE_ID,"message":NEXT_PROMPT}),
            ModelConfig::fixed(endpoint, None, "legacy-offline-model".into()),
        )
        .unwrap();
    assert_eq!(accepted["data"]["conversation_id"], SOURCE_ID);
    let task_id = accepted["data"]["task_id"].as_str().unwrap();
    let body = timeout(Duration::from_secs(5), request.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(body["instructions"], FROZEN);
    assert_eq!(
        body["input"],
        json!([
            {"type":"message","role":"user","content":[{"type":"input_text","text":"查看这份结果。"}]},
            {"type":"function_call","call_id":"call_old","name":"read_file","arguments":PREVIOUS_ARGS},
            {"type":"function_call_output","call_id":"call_old","output":"历史结果"},
            {"type":"message","role":"assistant","content":[{"type":"output_text","text":"已经查看。"}]},
            {"type":"message","role":"user","content":[{"type":"input_text","text":NEXT_PROMPT}]}
        ])
    );
    assert!(!body.to_string().contains(CIPHERTEXT));
    let poll = timeout(Duration::from_secs(5), async {
        loop {
            let poll = runtime.poll_task(WORKSPACE, task_id, 0).unwrap();
            if poll["data"]["status"] != "running" {
                break poll;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(poll["data"]["status"], "succeeded", "{poll}");
    assert!(
        poll["data"]["events"]
            .as_array()
            .unwrap()
            .iter()
            .all(|event| event["type"] != "tool_start")
    );
    assert!(request.try_recv().is_err());
    model.abort();
    drop(runtime);

    assert_eq!(fs::read(&source).unwrap(), source_bytes);
    let runtime = Runtime::open(&dir.0).unwrap();
    let reloaded = runtime.session_history(WORKSPACE, SOURCE_ID).unwrap();
    assert_eq!(
        reloaded["conversation"]["messages"][5]["content"],
        NEXT_ANSWER
    );
    assert_eq!(fs::read(&index).unwrap(), index_bytes);
    let db = crate::conversation_db::Database::open(db_dir).unwrap();
    assert_eq!(db.read(SOURCE_ID).unwrap().unwrap()["messages"][0], original["messages"][0]);
    assert_eq!(
        reloaded["conversation"]["metadata"]["frozen_system_prompt"],
        FROZEN
    );
    assert_eq!(fs::read(&source).unwrap(), source_bytes);
}

#[test]
fn production_python_preserves_every_chat_branch_across_rust_writes() {
    let dir = Scratch::new();
    let initial = json!({
        "id":CHAT_ID, "title":"preserve title", "created_at":"2026-11-23T09:22:14.015000",
        "updated_at":"2026-11-23T09:22:14.015000", "messages":[], "todo_list":null,
        "metadata":{"conversation_kind":"chat", "title_locked":true, "frozen_system_prompt":FROZEN,
            "project_path":null, "thinking_mode":false, "total_messages":0, "total_tools":0,
            "chat":{"version":1,"revision":0,"latest_leaf_id":null,"active_turn_id":null,
                "turns":[],"commands":{},"summaries":[],"future":{"keep":[null, 9]}}},
        "unknown_root":{"name":"unchanged"}
    });
    python_fixture(&dir.0, &dir.0, CHAT_ID, "chat_seed", &initial);
    let source = dir
        .0
        .join("chat/conversations")
        .join(format!("{CHAT_ID}.json"));
    let bytes = fs::read(&source).unwrap();
    let mut document = LegacyDocument::read(&source).unwrap();
    let original = document.raw().clone();
    let revision = document.chat_revision().unwrap();
    assert_eq!(document.source_id(), CHAT_ID);
    assert_eq!(revision, 7);
    assert_eq!(
        document.chat_path(None).unwrap()[1]["message_id"],
        "msg_right"
    );
    assert_eq!(
        document.chat_path(None).unwrap()[1]["media_refs"][0]["source_path"],
        r"C:\media\old.png"
    );
    assert_eq!(document.chat_model_history(None).unwrap_err().status, 422);
    assert_eq!(
        document.chat_model_history(Some("msg_left")).unwrap(),
        json!([
            {"role":"system","content":FROZEN,"_frozen_system_prompt":true},
            {"role":"user","content":"original question"},
            {"role":"assistant","content":"first answer"}
        ])
        .as_array()
        .unwrap()
        .clone()
    );
    assert!(
        !json!(document.public_chat_path(Some("msg_left")).unwrap())
            .to_string()
            .contains("old-chat-opaque")
    );
    assert_eq!(
        document
            .check_chat_revision(Some(revision - 1))
            .unwrap_err()
            .status,
        409
    );
    assert_eq!(fs::read(&source).unwrap(), bytes);

    let regenerated = document
        .begin_chat_turn(
            LegacyChatTurn::Regenerate {
                turn_id: original["metadata"]["chat"]["turns"][0]["id"]
                    .as_str()
                    .unwrap(),
            },
            20.0,
        )
        .unwrap();
    assert_eq!(document.chat_turn_history(&regenerated).unwrap().len(), 2);
    let reserved = document.raw().clone();
    assert_eq!(
        document
            .begin_chat_turn(
                LegacyChatTurn::EditAndSend {
                    message_id: "msg_root",
                    content: "busy"
                },
                20.0
            )
            .unwrap_err()
            .status,
        409
    );
    assert_eq!(
        document
            .append_chat_messages(
                &regenerated,
                vec![
                    json!({"role":"assistant","content":"valid prefix"}),
                    json!({"role":"assistant","message_id":"msg_right","content":"duplicate"})
                ],
                20.0,
                None
            )
            .unwrap_err()
            .status,
        409
    );
    assert_eq!(document.raw(), &reserved);
    document
        .append_chat_messages(
            &regenerated,
            vec![json!({
                "role":"assistant","content":"Rust regenerated","message_id":"msg_rust_regenerated"
            })],
            21.0,
            None,
        )
        .unwrap();
    document
        .finish_chat_turn(&regenerated, "complete", 22.0)
        .unwrap();
    document
        .finish_chat_turn(&regenerated, "complete", 23.0)
        .unwrap();
    assert_eq!(document.chat_revision().unwrap(), revision + 3);

    let fork = document
        .begin_chat_turn(
            LegacyChatTurn::EditAndSend {
                message_id: "msg_root",
                content: "forked in Rust",
            },
            24.0,
        )
        .unwrap();
    let fork_id = document.chat_path(None).unwrap()[0]["message_id"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(
        document.chat_path(None).unwrap()[0]["metadata"]["python_extension"],
        original["messages"][0]["metadata"]["python_extension"]
    );
    assert_eq!(
        document.chat_siblings("msg_root").unwrap(),
        ["msg_root", &fork_id]
    );
    document
        .append_chat_messages(
            &fork,
            vec![json!({
                "role":"assistant","content":"initial Rust reply","message_id":"msg_rust_reply"
            })],
            25.0,
            None,
        )
        .unwrap();
    document.finish_chat_turn(&fork, "complete", 26.0).unwrap();
    document
        .edit_chat_message("msg_rust_reply", "edited Rust reply", 27.0)
        .unwrap();
    assert_eq!(document.chat_revision().unwrap(), revision + 8);
    assert_eq!(document.raw()["title"], initial["title"]);
    let new_bytes = serde_json::to_vec(document.raw()).unwrap();
    fs::write(&source, &new_bytes).unwrap();
    drop(document);
    assert_eq!(
        LegacyDocument::read(&source)
            .unwrap()
            .chat_path(None)
            .unwrap()[1]["content"],
        "edited Rust reply"
    );
    assert_eq!(fs::read(&source).unwrap(), new_bytes);
    python_fixture(
        &dir.0,
        &dir.0,
        CHAT_ID,
        "chat_check_and_append",
        &json!({
            "original":original, "revision":revision + 8,
            "paths":{"msg_left":["msg_root","msg_left"], "msg_right":["msg_root","msg_right"],
                "msg_rust_regenerated":["msg_root","msg_rust_regenerated"],
                "msg_rust_reply":[fork_id,"msg_rust_reply"]}
        }),
    );
    let reopened = LegacyDocument::read(&source).unwrap();
    assert_eq!(reopened.source_id(), CHAT_ID);
    assert_eq!(reopened.chat_revision().unwrap(), revision + 11);
    let path = reopened.chat_path(None).unwrap();
    assert_eq!(path[0]["message_id"], fork_id);
    assert_eq!(path[2]["message_id"], "msg_python_again");
    assert_eq!(reopened.raw()["metadata"]["frozen_system_prompt"], FROZEN);
    assert_eq!(fs::read_dir(source.parent().unwrap()).unwrap().count(), 2);
}

fn python_fixture(root: &Path, project: &Path, conversation_id: &str, action: &str, input: &Value) {
    let script = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../test/2026-11-24_Rust后端/legacy_python_fixture.py");
    let interpreter =
        std::env::var_os("ASTRION_LEGACY_TEST_PYTHON").unwrap_or_else(|| "python3".into());
    let mut command = Command::new(interpreter);
    command
        .env_clear()
        .env("PATH", std::env::var_os("PATH").unwrap_or_default())
        .env("ASTRION_IGNORE_DOTENV", "1")
        .env("ASTRION_DATA_ROOT", root.join("python-config"))
        .env("DATA_DIR", root)
        .env("LOGS_DIR", root.join("python-logs"))
        .env("DEPLOY_CONFIG_DIR", root.join("python-deploy"))
        .env("HOST_WORKSPACES_FILE", root.join("host_workspaces.json"))
        .args(["-B", "-X", "utf8"])
        .arg(script.with_file_name("with_legacy_source.py"))
        .arg(script)
        .arg(action)
        .arg(root)
        .arg(project)
        .arg(conversation_id)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for key in ["HOME", "USERPROFILE", "SystemRoot", "TMPDIR", "ASTRION_LEGACY_TEST_SOURCE"] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    let mut child = command
        .spawn()
        .expect("Python 3 for legacy compatibility test");
    child
        .stdin
        .take()
        .unwrap()
        .write_all(&serde_json::to_vec(input).unwrap())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "Python fixture {action} failed:\n{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

async fn mock_model(
    State(sender): State<mpsc::UnboundedSender<Value>>,
    Json(request): Json<Value>,
) -> impl IntoResponse {
    sender.send(request).unwrap();
    let output = json!({"type":"response.output_text.delta","delta":NEXT_ANSWER});
    let completed = json!({"type":"response.completed","response":{
        "output":[],"usage":{"input_tokens":9,"output_tokens":5,"total_tokens":14}
    }});
    (
        [("content-type", "text/event-stream")],
        format!("data: {output}\n\ndata: {completed}\n\n"),
    )
}

#[test]
fn media_uses_bound_roots_and_preserves_path_only_messages() {
    let scratch = Scratch::new();
    let project = scratch.0.join("project");
    let tree = scratch.0.join("worktree");
    let data = scratch.0.join("private-data");
    let imported = project.join(".astrion/conversation_assets");
    fs::create_dir_all(&imported).unwrap();
    fs::create_dir(&tree).unwrap();
    fs::create_dir(&data).unwrap();
    fs::write(project.join("image.png"), b"wrong").unwrap();
    fs::write(tree.join("image.png"), b"A").unwrap();
    fs::write(imported.join("archive.png"), b"B").unwrap();
    let mut raw = agent();
    raw["metadata"]["conversation_transfer"]["paths"] = json!({
        "old-image": ".astrion/conversation_assets/archive.png",
        "bad-image": ".astrion/conversation_assets/../image.png"
    });
    raw["messages"][0]["metadata"]["images"] = json!(["image.png", "old-image", "bad-image", "missing.png"]);
    let project_history = |raw: &Value| {
        LegacyDocument::from_value(raw.clone()).unwrap()
            .agent_history_with_media(&data, &project, &tree).unwrap()
    };
    let history = project_history(&raw);
    let parts = history[1]["content"].as_array().unwrap();
    assert_eq!(parts.len(), 3);
    assert_eq!(parts[1]["image_url"]["url"], "data:image/png;base64,QQ==");
    assert_eq!(parts[2]["image_url"]["url"], "data:image/png;base64,Qg==");
    raw["messages"][0]["metadata"]["media_paths_only"] = json!(true);
    assert_eq!(project_history(&raw)[1]["content"], raw["messages"][0]["content"]);
}
