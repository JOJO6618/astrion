use std::{
    fs,
    path::{Path, PathBuf},
};

use axum::{
    Extension, Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use serde_json::{Value, json};
use tokio::sync::oneshot;
use tower::ServiceExt;
use uuid::Uuid;

use super::{
    INPUTS, browser_router, files, host_token_router, integration, mutation_router, mutations,
    open_data_directory,
    transaction::{self, FileState, Scope, Transaction},
};
use crate::{
    legacy::LegacyDocument,
    project_git::runner::Git,
    runtime::{
        Runtime, Status,
        store::{Task, TaskStart},
        verify_workspace_binding,
    },
    workspace_catalog::RegisteredWorkspace,
};

const ID: &str = "conv_version_fixture";
const OTHER: &str = "conv_other_version_fixture";
const WORKSPACE: &str = "version_fixture";

struct Fixture {
    root: PathBuf,
    data: PathBuf,
    work: PathBuf,
    runtime: Runtime,
}

impl Fixture {
    fn new() -> Self {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../test/2026-11-26_Rust版本恢复")
            .join(format!("fixture_{}", Uuid::new_v4().simple()));
        fs::create_dir_all(&root).unwrap();
        let root = root.canonicalize().unwrap();
        let work = root.join("workspace");
        let data = root.join("data");
        fs::create_dir(&work).unwrap();
        let runtime = Runtime::open(&data).unwrap();
        runtime.bind_workspace(WORKSPACE, &work).unwrap();
        Self {
            root,
            data,
            work,
            runtime,
        }
    }

    fn insert(&self, id: &str, versioning: Value, messages: Vec<Value>) {
        let raw = json!({"id":id,"title":"Now","messages":messages,"metadata":{
            "project_path":self.work,"frozen_system_prompt":"Original frozen prompt",
            "model_key":"current/model","versioning":versioning
        }});
        self.runtime
            .store()
            .unwrap()
            .persist(WORKSPACE, LegacyDocument::from_value(raw).unwrap(), true)
            .unwrap();
    }

    fn record_dir(&self) -> PathBuf {
        self.data.join("save").join(ID)
    }

    fn conversation_file(&self) -> PathBuf {
        self.data
            .join("conversations")
            .join(WORKSPACE)
            .join(format!("{ID}.json"))
    }

    fn document(&self, id: &str) -> Value {
        self.runtime
            .store()
            .unwrap()
            .read(WORKSPACE, id)
            .unwrap()
            .into_raw()
    }

    fn router(&self) -> Router {
        browser_router(self.runtime.clone())
            .merge(host_token_router(self.runtime.clone()))
            .merge(mutation_router(self.runtime.clone()))
            .layer(Extension(RegisteredWorkspace {
                id: WORKSPACE.into(),
                path: self.work.clone(),
                label: "Fixture".into(),
            }))
    }

    fn write_rows(&self, rows: &[Value]) {
        fs::create_dir_all(self.record_dir().join("snapshots")).unwrap();
        fs::write(
            self.record_dir().join(INPUTS),
            files::encode_rows(rows).unwrap(),
        )
        .unwrap();
    }

    fn legacy_shallow(&self) {
        self.insert(
            ID,
            json!({"enabled":true,"backup_mode":"shallow","tracking_mode":"conversation_only"}),
            vec![message("before"), message("after")],
        );
        self.write_rows(&[
            json!({"seq":0,"message_index":-1,"tracking_mode":"conversation_only"}),
            json!({"seq":"1","message_index":0,"snapshot_file":"snapshots\\1.json","tracking_mode":"conversation_only"}),
            json!({"seq":2,"message_index":1,"snapshot_file":"snapshots/2.json","tracking_mode":"conversation_only"}),
        ]);
        fs::write(self.record_dir().join("snapshots/1.json"), json!({
            "title":"At checkpoint","shallow_message_id":"before","messages":[message("before")],
            "metadata":{"model_key":"earlier/model","frozen_system_prompt":"Obsolete prompt","project_path":"/foreign"}
        }).to_string()).unwrap();
        fs::write(
            self.record_dir().join("snapshots/2.json"),
            json!({"messages":[message("after")],"shallow_message_id":"after"}).to_string(),
        )
        .unwrap();
        let shallow = self.record_dir().join("shallow_backups");
        fs::create_dir(&shallow).unwrap();
        let keep = "1111111111111111@v1";
        let removed = "2222222222222222@v1";
        fs::write(shallow.join(keep), [0, 255, 10]).unwrap();
        fs::write(shallow.join(removed), b"formerly removed").unwrap();
        fs::write(shallow.join("state.jsonl"), files::encode_rows(&[
            json!({"message_id":"before","tracked_file_backups":{"result.bin":{"backup_file_name":null,"version":1}}}),
            json!({"message_id":"before","tracked_file_backups":{
                "result.bin":{"backup_file_name":keep,"version":1},
                "recreated.txt":{"backup_file_name":null,"version":1},
                "nested/removed.txt":{"backup_file_name":removed,"version":1}
            },"future_field":"retained"}),
            json!({"message_id":"after","tracked_file_backups":{}}),
        ]).unwrap()).unwrap();
        fs::write(self.work.join("result.bin"), b"latest bytes").unwrap();
        fs::write(self.work.join("recreated.txt"), b"created later").unwrap();
        fs::write(self.work.join("untracked.txt"), b"untouched").unwrap();
    }

    fn hold(&self, id: &str, message: &str, created_at: f64) -> String {
        let task_id = Uuid::new_v4().to_string();
        let (sender, _cancel) = oneshot::channel();
        let task = Task::new(
            TaskStart {
                id: task_id.clone(),
                created_at,
                message: message.into(),
                model_key: None,
                message_source: None,
            },
            WORKSPACE,
            id.into(),
            sender,
        );
        self.runtime.store().unwrap().register_task(task);
        task_id
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn message(text: &str) -> Value {
    json!({"role":"user","content":text,"message_id":text})
}

async fn request(
    fixture: &Fixture,
    method: &str,
    id: &str,
    suffix: &str,
    body: Value,
    expected: StatusCode,
) -> Value {
    let path = format!("/api/conversations/{id}/versioning{suffix}");
    let response = fixture
        .router()
        .oneshot(
            Request::builder()
                .uri(path)
                .method(method)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let data: Value = serde_json::from_slice(
        &to_bytes(response.into_body(), 2 * 1024 * 1024)
            .await
            .unwrap(),
    )
    .unwrap();
    assert_eq!(status, expected, "{data}");
    assert_eq!(data["success"], expected.is_success());
    data
}

#[tokio::test]
async fn web_creation_applies_default_flag_and_full_workspace_baseline() {
    let f = Fixture::new();
    let workspace = RegisteredWorkspace {
        id: WORKSPACE.into(),
        path: f.work.clone(),
        label: "Fixture".into(),
    };
    let preferences = f.data.join("personalization.json");
    let create = || {
        f.runtime.create_web_conversation(&workspace, Default::default()).unwrap()
            ["conversation_id"].as_str().unwrap().to_owned()
    };

    fs::write(&preferences, json!({"versioning_enabled_by_default":false,"versioning_backup_mode":"full"}).to_string()).unwrap();
    let off = create();
    assert!(f.document(&off)["metadata"]["versioning"].is_null());
    assert!(!f.data.join("save").join(&off).exists());

    fs::write(&preferences, "{}").unwrap(); // Defaults on, shallow.
    let shallow = create();
    let config = f.document(&shallow)["metadata"]["versioning"].clone();
    assert_eq!(config["enabled"], true);
    assert_eq!(config["backup_mode"], "shallow");
    assert_eq!(config["tracking_mode"], "conversation_only");
    assert!(f.data.join("save").join(&shallow).join("snapshots/0.json").is_file());

    fs::write(&preferences, json!({"versioning_enabled_by_default":true,"versioning_backup_mode":"full"}).to_string()).unwrap();
    fs::write(f.work.join("notes.txt"), b"present before the chat\n").unwrap();
    let full = create();
    let config = f.document(&full)["metadata"]["versioning"].clone();
    assert_eq!(config["enabled"], true);
    assert_eq!(config["backup_mode"], "full");
    assert_eq!(config["tracking_mode"], "workspace_and_conversation");
    assert_eq!(config["last_input_seq"], 0);
    assert!(f.data.join("save").join(&full).join("git/index").is_file());

    // An ordinary command (not a single-file editing tool) changes the initial workspace.
    fs::write(f.work.join("notes.txt"), b"edited outside the tool\n").unwrap();
    fs::write(f.work.join("later.txt"), b"created outside the tool\n").unwrap();
    request(&f, "POST", &full, "/restore", json!({"seq":0}), StatusCode::OK).await;
    assert_eq!(fs::read(f.work.join("notes.txt")).unwrap(), b"present before the chat\n");
    assert!(!f.work.join("later.txt").exists());
    assert_eq!(f.document(&full)["messages"], json!([]));
    // Changing preferences doesn't silently change existing conversations.
    assert_eq!(f.document(&shallow)["metadata"]["versioning"]["backup_mode"], "shallow");
}

#[tokio::test]
async fn web_creation_returns_the_durable_id_when_initial_backup_fails() {
    let f = Fixture::new();
    let workspace = RegisteredWorkspace {
        id: WORKSPACE.into(),
        path: f.work.clone(),
        label: "Fixture".into(),
    };
    fs::write(f.data.join("save"), b"obstruct backup directory").unwrap();
    let result = f.runtime.create_web_conversation(&workspace, Default::default()).unwrap();
    assert_eq!(result["success"], true);
    assert!(result["versioning_error"].as_str().is_some_and(|error| !error.is_empty()));
    let id = result["conversation_id"].as_str().unwrap();
    assert_eq!(f.document(id)["messages"], json!([]));
    assert!(f.document(id)["metadata"]["versioning"].is_null());
}

#[tokio::test]
async fn toggle_and_finished_agent_checkpoint_use_python_layout_and_respect_active_tasks() {
    let f = Fixture::new();
    f.insert(ID, Value::Null, vec![]);
    let options = json!({"enabled":true,"mode":"overwrite"});
    let enabled = request(
        &f,
        "POST",
        "version_fixture",
        "",
        options.clone(),
        StatusCode::OK,
    )
    .await;
    assert_eq!(enabled["data"]["last_input_seq"], 0);
    request(&f, "POST", ID, "", options, StatusCode::OK).await;
    let baseline: Value =
        serde_json::from_slice(&fs::read(f.record_dir().join("snapshots/0.json")).unwrap())
            .unwrap();
    assert!(baseline.get("messages").is_none());
    assert_eq!(baseline["rust_message_revision"]["count"], 0);
    assert_eq!(baseline["run_status"], "initial");
    let created = crate::runtime::now();
    let task_id = f.hold(ID, "edit tracked files", created);
    fs::write(f.work.join("notes.txt"), b"old\n").unwrap();
    {
        let store = f.runtime.store().unwrap();
        store
            .append_messages(
                WORKSPACE,
                ID,
                vec![message("edit tracked files")],
                created,
                None,
                None,
            )
            .unwrap();
        integration::track_edit(&store, &task_id, "notes.txt").unwrap();
    }
    request(
        &f,
        "POST",
        ID,
        "",
        json!({"enabled":false}),
        StatusCode::CONFLICT,
    )
    .await;
    request(
        &f,
        "POST",
        ID,
        "/restore",
        json!({"seq":0}),
        StatusCode::CONFLICT,
    )
    .await;
    fs::write(f.work.join("notes.txt"), b"new\n").unwrap();
    {
        let mut store = f.runtime.store().unwrap();
        store
            .finish(
                &task_id,
                Ok(vec![json!({"role":"assistant","content":"done"})]),
            )
            .unwrap();
        integration::record_finished(&store, &task_id).unwrap();
        integration::record_finished(&store, &task_id).unwrap();
    }
    let listed = request(&f, "GET", ID, "/checkpoints", Value::Null, StatusCode::OK).await;
    let rows = listed["data"]["items"].as_array().unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0]["seq"], 1);
    assert_eq!(rows[0]["message_index"], 0);
    assert_eq!(rows[0]["shallow_message_id"], "edit tracked files");
    assert_eq!(rows[0]["files"][0]["path"], "notes.txt");
    assert_eq!(rows[0]["files"][0]["deletions"], 1);
    assert_eq!(rows[0]["files"][0]["insertions"], 1);
    fs::write(f.work.join("notes.txt"), b"externally edited\n").unwrap();
    request(&f, "POST", ID, "/restore", json!({"seq":1}), StatusCode::OK).await;
    assert_eq!(fs::read(f.work.join("notes.txt")).unwrap(), b"new\n");
    request(&f, "POST", ID, "", json!({"enabled":false}), StatusCode::OK).await;
    let listed = request(&f, "GET", ID, "/checkpoints", Value::Null, StatusCode::OK).await;
    assert_eq!(listed["data"]["items"], json!([]));
    assert!(f.record_dir().join("snapshots/1.json").is_file());
}

#[tokio::test]
async fn automatic_checkpoints_do_not_copy_history_and_forks_keep_revisions_after_source_deletion() {
    let f = Fixture::new();
    let prefix = json!({"role":"assistant","content":"stored fixture transcript ".repeat(16_384),
        "message_id":"retained-prefix","future_message_field":{"value":17}});
    f.insert(ID, Value::Null, vec![prefix.clone()]);
    {
        let store = f.runtime.store().unwrap();
        store.update_agent_metadata(WORKSPACE, ID, |metadata| metadata["future_header_field"] = json!({"saved":true})).unwrap();
    }
    request(&f, "POST", ID, "", json!({"enabled":true}), StatusCode::OK).await;
    for text in ["turn-one", "turn-two"] {
        let created = crate::runtime::now();
        let task = f.hold(ID, text, created);
        let mut store = f.runtime.store().unwrap();
        store.append_messages(WORKSPACE, ID, vec![message(text)], created, None, None).unwrap();
        store.finish(&task, Ok(vec![json!({"role":"assistant","content":format!("reply for {text}")})])).unwrap();
    }
    for sequence in 0..=2 {
        let state = fs::read(f.record_dir().join(format!("snapshots/{sequence}.json"))).unwrap();
        assert!(state.len() < 8_192, "checkpoint {sequence} copied transcript bytes");
        let state: Value = serde_json::from_slice(&state).unwrap();
        assert!(state.get("messages").is_none());
        assert_eq!(state["rust_message_revision"]["count"], 1 + sequence * 2);
    }
    let first = f.document(ID)["messages"].as_array().unwrap()[..3].to_vec();
    let mut rewritten = f.document(ID);
    rewritten["messages"] = json!([{"role":"system","content":"compacted replacement"}]);
    rewritten["metadata"]["future_header_field"] = json!({"saved":false});
    f.runtime.store().unwrap().persist(WORKSPACE, LegacyDocument::from_value(rewritten).unwrap(), false).unwrap();

    let result = request(&f, "POST", ID, "/restore", json!({"seq":1,"mode":"copy"}), StatusCode::OK).await;
    let fork = result["data"]["conversation_id"].as_str().unwrap();
    let copied = f.document(fork);
    assert_eq!(copied["messages"], json!(first));
    assert_eq!(copied["metadata"]["future_header_field"], json!({"saved":true}));

    f.runtime.store().unwrap().delete_conversation(WORKSPACE, ID).unwrap();
    request(&f, "POST", fork, "/restore", json!({"seq":0}), StatusCode::OK).await;
    assert_eq!(f.document(fork)["messages"], json!([prefix]));
}

#[test]
fn interrupted_checkpoint_recovers_files_at_the_metadata_commit_boundary() {
    for committed in [false, true] {
        let f = Fixture::new();
        f.insert(ID, json!({"enabled":true,"last_input_seq":0}), vec![message("persistent transcript")]);
        fs::create_dir_all(f.record_dir()).unwrap();
        let record = open_data_directory(&f.record_dir()).unwrap();
        let records = open_data_directory(&f.data.join("save")).unwrap();
        let before = f.document(ID)["metadata"]["versioning"].clone();
        let after = json!({"enabled":true,"last_input_seq":1});
        let mut tx = Transaction::for_versioning(&record, &records, WORKSPACE, ID, &before, &after).unwrap();
        tx.put(&format!("{ID}/meta.json"), br#"{"new":true}"#.to_vec()).unwrap();
        tx.interrupt_after(1).unwrap();
        {
            let store = f.runtime.store().unwrap();
            if committed { store.update_agent_metadata(WORKSPACE, ID, |metadata| metadata["versioning"] = after.clone()).unwrap(); }
            integration::recover_pending(&store, WORKSPACE, ID).unwrap();
            assert!(transaction::pending(&record).unwrap().is_none());
        }
        assert_eq!(f.record_dir().join("meta.json").is_file(), committed);
        let document = f.document(ID);
        assert_eq!(document["metadata"]["versioning"], if committed { after } else { before });
        assert_eq!(document["messages"], json!([message("persistent transcript")]));
    }
}

#[tokio::test]
async fn shallow_legacy_restores_binary_creates_and_deletes_files_then_prunes() {
    let f = Fixture::new();
    f.legacy_shallow();
    f.insert(OTHER, Value::Null, vec![]);
    let task = f.hold(OTHER, "another active task", crate::runtime::now());
    request(
        &f,
        "POST",
        ID,
        "/restore",
        json!({"seq":1}),
        StatusCode::CONFLICT,
    )
    .await;
    f.runtime
        .store()
        .unwrap()
        .tasks
        .get_mut(&task)
        .unwrap()
        .status = Status::Stopped;
    let restored = request(
        &f,
        "POST",
        ID,
        "/restore",
        json!({"seq":"1"}),
        StatusCode::OK,
    )
    .await;
    assert_eq!(restored["data"]["restored_seq"], 1);
    assert_eq!(fs::read(f.work.join("result.bin")).unwrap(), [0, 255, 10]);
    assert!(!f.work.join("recreated.txt").exists());
    assert_eq!(
        fs::read(f.work.join("nested/removed.txt")).unwrap(),
        b"formerly removed"
    );
    assert_eq!(
        fs::read(f.work.join("untracked.txt")).unwrap(),
        b"untouched"
    );
    assert!(!f.record_dir().join("snapshots/2.json").exists());
    let raw = f.document(ID);
    assert_eq!(raw["title"], "At checkpoint");
    assert_eq!(raw["messages"], json!([message("before")]));
    assert_eq!(raw["metadata"]["versioning"]["last_input_seq"], 1);
    assert_eq!(
        raw["metadata"]["frozen_system_prompt"],
        "Original frozen prompt"
    );
    assert_eq!(raw["metadata"]["project_path"], json!(f.work));
    let state = fs::read_to_string(f.record_dir().join("shallow_backups/state.jsonl")).unwrap();
    assert!(state.contains("future_field"));
    assert!(!state.contains("\"after\""));
    request(
        &f,
        "GET",
        ID,
        "/checkpoints/2",
        Value::Null,
        StatusCode::NOT_FOUND,
    )
    .await;
}

#[tokio::test]
async fn conversation_only_full_can_copy_and_use_legacy_message_index_without_git() {
    let f = Fixture::new();
    f.insert(
        ID,
        json!({"enabled":true,"backup_mode":"full","tracking_mode":"conversation_only"}),
        vec![message("one"), message("two"), message("three")],
    );
    f.write_rows(&[json!({"seq":3,"message_index":0,"tree_hash":"older-tree","tracking_mode":"conversation_only"}),
        json!({"seq":4,"message_index":2,"tracking_mode":"conversation_only"})]);
    fs::write(f.work.join("untouched.txt"), b"latest").unwrap();
    let before = f.document(ID);
    let rows_before = fs::read(f.record_dir().join(INPUTS)).unwrap();
    request(
        &f,
        "POST",
        ID,
        "/restore",
        json!({"seq":3,"tracking_mode":"workspace_and_conversation"}),
        StatusCode::BAD_REQUEST,
    )
    .await;
    let copied = request(
        &f,
        "POST",
        ID,
        "/restore",
        json!({"seq":3,"mode":"copy"}),
        StatusCode::OK,
    )
    .await;
    let id = copied["data"]["conversation_id"].as_str().unwrap();
    assert_ne!(id, ID);
    assert_eq!(f.document(id)["messages"], json!([message("one")]));
    assert_eq!(
        f.document(id)["metadata"]["versioning"]["backup_mode"],
        "full"
    );
    assert_eq!(f.document(ID), before);
    assert_eq!(fs::read(f.record_dir().join(INPUTS)).unwrap(), rows_before);
    assert_eq!(fs::read(f.work.join("untouched.txt")).unwrap(), b"latest");
}

#[tokio::test]
async fn real_persist_error_rolls_back_workspace_and_record_bytes() {
    let f = Fixture::new();
    f.legacy_shallow();
    let before = f.document(ID);
    let inputs = f.record_dir().join(INPUTS);
    let rows_before = fs::read(&inputs).unwrap();
    let sql = rusqlite::Connection::open(f.conversation_file().with_file_name(crate::conversation_db::FILENAME)).unwrap();
    sql.execute_batch("CREATE TRIGGER fail_update BEFORE UPDATE ON conversations BEGIN SELECT RAISE(ABORT, 'test write failure'); END").unwrap();
    request(
        &f,
        "POST",
        ID,
        "/restore",
        json!({"seq":1}),
        StatusCode::SERVICE_UNAVAILABLE,
    )
    .await;
    assert_eq!(f.document(ID), before);
    assert_eq!(fs::read(&inputs).unwrap(), rows_before);
    assert_eq!(
        fs::read(f.work.join("result.bin")).unwrap(),
        b"latest bytes"
    );
    assert_eq!(
        fs::read(f.work.join("recreated.txt")).unwrap(),
        b"created later"
    );
    assert!(!f.work.join("nested").exists());
    assert!(f.record_dir().join("snapshots/2.json").exists());
    let records = open_data_directory(&f.data.join("save")).unwrap();
    assert!(
        transaction::pending_workspace(&records, WORKSPACE)
            .unwrap()
            .is_none()
    );
    sql.execute_batch("DROP TRIGGER fail_update").unwrap();
    request(&f, "POST", ID, "/restore", json!({"seq":1}), StatusCode::OK).await;
}

#[test]
fn interrupted_transaction_releases_other_conversation_only_after_recovery() {
    for committed in [false, true] {
        let f = Fixture::new();
        f.legacy_shallow();
        f.insert(OTHER, Value::Null, vec![]);
        let record = open_data_directory(&f.record_dir()).unwrap();
        let records = open_data_directory(&f.data.join("save")).unwrap();
        let before = f.document(ID);
        let mut raw = before.clone();
        raw["title"] = json!("committed");
        let next = LegacyDocument::from_value(raw).unwrap();
        let mut tx = Transaction::new(
            &record,
            &records,
            WORKSPACE,
            ID,
            Some(&f.work),
            Some(&before),
            &next,
        )
        .unwrap();
        let original_mode =
            transaction::capture(&open_data_directory(&f.work).unwrap(), "result.bin", true)
                .unwrap()
                .unwrap()
                .mode;
        tx.change(
            Scope::Workspace,
            "result.bin",
            Some(FileState::workspace(b"new".to_vec(), original_mode)),
        )
        .unwrap();
        tx.put(
            &format!("{ID}/meta.json"),
            br#"{"committed":true}"#.to_vec(),
        )
        .unwrap();
        tx.interrupt_after(1).unwrap();
        assert_eq!(fs::read(f.work.join("result.bin")).unwrap(), b"new");
        if committed {
            f.runtime
                .store()
                .unwrap()
                .persist(WORKSPACE, next, false)
                .unwrap();
        }
        let store = f.runtime.store().unwrap();
        verify_workspace_binding(&store, WORKSPACE, Some(&f.work)).unwrap();
        integration::recover_pending(&store, WORKSPACE, OTHER).unwrap();
        assert!(transaction::pending(&record).unwrap().is_none());
        assert!(
            transaction::pending_workspace(&records, WORKSPACE)
                .unwrap()
                .is_none()
        );
        assert_eq!(
            fs::read(f.work.join("result.bin")).unwrap(),
            if committed {
                b"new".as_slice()
            } else {
                b"latest bytes".as_slice()
            }
        );
        assert_eq!(f.record_dir().join("meta.json").is_file(), committed);
        assert_eq!(
            store.read(WORKSPACE, ID).unwrap().raw()["title"],
            if committed { "committed" } else { "Now" }
        );
    }
}

#[cfg(unix)]
#[tokio::test]
async fn shallow_rejects_escaped_destinations_and_corrupt_blobs_before_changes() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    f.legacy_shallow();
    let outside = f.root.join("outside");
    fs::create_dir(&outside).unwrap();
    fs::write(outside.join("removed.txt"), b"outside").unwrap();
    symlink(&outside, f.work.join("nested")).unwrap();
    let original = f.document(ID);
    request(
        &f,
        "POST",
        ID,
        "/restore",
        json!({"seq":1}),
        StatusCode::INTERNAL_SERVER_ERROR,
    )
    .await;
    assert_eq!(fs::read(outside.join("removed.txt")).unwrap(), b"outside");
    fs::remove_file(f.work.join("nested")).unwrap();
    fs::remove_file(f.record_dir().join("shallow_backups/1111111111111111@v1")).unwrap();
    request(
        &f,
        "POST",
        ID,
        "/restore",
        json!({"seq":1}),
        StatusCode::INTERNAL_SERVER_ERROR,
    )
    .await;
    assert_eq!(f.document(ID), original);
    assert_eq!(
        fs::read(f.work.join("result.bin")).unwrap(),
        b"latest bytes"
    );
    assert_eq!(
        fs::read(f.work.join("recreated.txt")).unwrap(),
        b"created later"
    );
}

fn git<const N: usize>(root: &Path, hidden: &Path, args: [&str; N]) -> String {
    let scope = [
        "--git-dir",
        hidden.to_str().unwrap(),
        "--work-tree",
        root.to_str().unwrap(),
    ];
    String::from_utf8(Git::new(root).read(scope.into_iter().chain(args)).unwrap())
        .unwrap()
        .trim()
        .to_owned()
}

#[tokio::test]
async fn full_legacy_restores_actual_git_tree_without_touching_project_index() {
    let f = Fixture::new();
    f.insert(
        ID,
        json!({"enabled":true,"backup_mode":"full","tracking_mode":"workspace_and_conversation"}),
        vec![message("before"), message("after")],
    );
    let hidden = f.record_dir().join("git");
    fs::create_dir_all(&hidden).unwrap();
    Git::new(&f.work)
        .read(["init", "--bare", "--template=", hidden.to_str().unwrap()])
        .unwrap();
    Git::new(&f.work).read(["init", "--template="]).unwrap();
    fs::write(f.work.join(".gitignore"), b"local.ignored\n").unwrap();
    fs::write(f.work.join("current.txt"), b"older\n").unwrap();
    fs::write(f.work.join("restore.txt"), b"restore me\n").unwrap();
    git(&f.work, &hidden, ["add", "-A"]);
    let earlier = git(&f.work, &hidden, ["write-tree"]);
    fs::remove_file(f.work.join("restore.txt")).unwrap();
    fs::write(f.work.join("current.txt"), b"later\n").unwrap();
    fs::write(f.work.join("added.txt"), b"remove this later file\n").unwrap();
    git(&f.work, &hidden, ["add", "-A"]);
    let later = git(&f.work, &hidden, ["write-tree"]);
    Git::new(&f.work).read(["add", "current.txt"]).unwrap();
    let project_index = fs::read(f.work.join(".git/index")).unwrap();
    fs::write(f.work.join("local.ignored"), b"ignored").unwrap();
    fs::write(
        f.work.join("untracked.txt"),
        b"old full backup removes untracked",
    )
    .unwrap();
    f.write_rows(&[
        json!({"seq":1,"message_index":0,"workspace_path":f.work,"tree_hash":earlier,"tracking_mode":"workspace_and_conversation"}),
        json!({"seq":2,"message_index":1,"workspace_path":f.work,"tree_hash":later,"tracking_mode":"workspace_and_conversation"}),
    ]);
    request(&f, "POST", ID, "/restore", json!({"seq":1}), StatusCode::OK).await;
    assert_eq!(f.document(ID)["messages"], json!([message("before")]));
    assert_eq!(git(&f.work, &hidden, ["write-tree"]), earlier);
    assert_eq!(fs::read(f.work.join("current.txt")).unwrap(), b"older\n");
    assert_eq!(
        fs::read(f.work.join("restore.txt")).unwrap(),
        b"restore me\n"
    );
    assert_eq!(fs::read(f.work.join("local.ignored")).unwrap(), b"ignored");
    assert_eq!(fs::read(f.work.join(".git/index")).unwrap(), project_index);
    assert!(!f.work.join("added.txt").exists());
    assert!(!f.work.join("untracked.txt").exists());

    let created = crate::runtime::now();
    let task = f.hold(ID, "full checkpoint", created);
    fs::write(f.work.join("current.txt"), b"after restore\n").unwrap();
    let mut store = f.runtime.store().unwrap();
    store
        .append_messages(
            WORKSPACE,
            ID,
            vec![message("full checkpoint")],
            created,
            None,
            None,
        )
        .unwrap();
    store.finish(&task, Ok(vec![])).unwrap();
    integration::record_finished(&store, &task).unwrap();
    let raw = store.read(WORKSPACE, ID).unwrap().into_raw();
    assert_eq!(raw["metadata"]["versioning"]["last_input_seq"], 2);
    assert_ne!(raw["metadata"]["versioning"]["last_commit"], earlier);
}
