use std::{
    fs,
    path::{Path, PathBuf},
};

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use serde_json::{Value, json};
use tower::ServiceExt;
use uuid::Uuid;

use super::{Batch, Job, Report, router, run_batch};
use crate::conversation_db::{Database, FILENAME};

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let path =
            std::env::temp_dir().join(format!("astrion-migration-isolation-{}", Uuid::new_v4()));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn job(&self) -> Job {
        Job {
            root: self.0.clone(),
            logs: self.0.join("logs"),
            state: Default::default(),
        }
    }

    fn run(&self) -> Report {
        let job = self.job();
        run_batch(&job);
        let outcome = job.state.lock().unwrap().clone();
        outcome
    }

    fn write(&self, path: &str, value: &[u8]) -> PathBuf {
        let path = self.0.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, value).unwrap();
        path
    }

    fn json(&self, path: &str, value: &Value) {
        self.write(path, value.to_string().as_bytes());
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn conversation(id: &str, chat: bool) -> Value {
    let mut document = json!({"id":id,"title":"Fixture","messages":[],"metadata":{
        "frozen_system_prompt":"Fixture system prompt"
    }});
    if chat {
        document["metadata"]["conversation_kind"] = json!("chat");
        document["metadata"]["chat"] = json!({"version":1,"revision":0,"latest_leaf_id":null,
            "active_turn_id":null,"turns":[],"commands":{},"summaries":[]});
    }
    document
}

fn review() -> Value {
    json!({"messages":[{"role":"user","content":"Fixture transcript"}],"trace":[],
        "created_at":1700000000000_u64,"final_result":{"decision":"approved"}})
}

fn task(id: &str, parent: &str) -> Value {
    json!({"task_id":id,"conversation_id":parent,"name":id,"status":"idle"})
}

fn sql_count(data: &Path, file: &str, query: &str) -> usize {
    let db = rusqlite::Connection::open(data.join(file)).unwrap();
    db.query_row(query, [], |row| row.get(0)).unwrap()
}

#[tokio::test]
async fn status_reports_broken_sources_and_healthy_sources_migrate_and_retry() {
    let fixture = Fixture::new();
    let old_agent = conversation("conv_agent", false);
    let old_chat = conversation("conv_chat", true);
    fixture.json("conversations/working/conv_agent.json", &old_agent);
    fixture.json("chat/conversations/conv_chat.json", &old_chat);
    fixture.json(
        "conversations/damaged/conv_blocked.json",
        &conversation("conv_blocked", false),
    );
    let database = fixture.write(
        "conversations/damaged/conversations.sqlite3",
        b"broken SQLite",
    );
    let subagents = fixture.write("sub_agent_runtime.json", b"{malformed");
    let approval = fixture.write("logs/approval_agent", b"file where a directory should be");
    fixture.json(
        "logs/goal_review_agent/goal_review_1700000000000.json",
        &review(),
    );

    let application = router(fixture.0.clone(), fixture.0.join("logs"));
    let response = application
        .oneshot(
            Request::builder()
                .uri("/api/conversation-storage/migration")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 32 * 1024).await.unwrap()).unwrap();
    assert_eq!(
        (body["pending"].as_u64(), body["failed"].as_u64()),
        (Some(6), Some(3)),
        "{body}"
    );
    assert!(
        !fixture
            .0
            .join("conversations/working")
            .join(FILENAME)
            .exists()
    );
    assert!(!fixture.0.join("chat/conversations").join(FILENAME).exists());
    assert!(!fixture.0.join("review_history.sqlite3").exists());

    let report = fixture.run();
    assert_eq!(
        (report.migrated, report.failed, report.pending),
        (3, 3, 3),
        "{:?}",
        report.errors
    );
    assert!(
        report
            .errors
            .iter()
            .any(|line| line.contains("Agent 工作区 damaged"))
    );
    assert!(
        report
            .errors
            .iter()
            .any(|line| line.contains("approval_agent"))
    );
    assert!(
        report
            .errors
            .iter()
            .any(|line| line.contains("子智能体注册表"))
    );
    assert_eq!(
        Database::open(&fixture.0.join("conversations/working"))
            .unwrap()
            .read("conv_agent")
            .unwrap(),
        Some(old_agent)
    );
    assert_eq!(
        Database::open(&fixture.0.join("chat/conversations"))
            .unwrap()
            .read("conv_chat")
            .unwrap(),
        Some(old_chat)
    );
    assert_eq!(
        sql_count(
            &fixture.0,
            "review_history.sqlite3",
            "SELECT count(*) FROM reviews"
        ),
        1
    );
    assert_eq!(fs::read(&database).unwrap(), b"broken SQLite");
    assert_eq!(fs::read(&subagents).unwrap(), b"{malformed");

    fs::remove_file(database).unwrap();
    fs::remove_file(approval).unwrap();
    fixture.json("logs/approval_agent/approval_1700000000001.json", &review());
    fixture.json(
        "sub_agent_runtime.json",
        &json!({"version":1,"tasks":{
            "sub_retry":task("sub_retry","conv_agent")
        }}),
    );
    let retry = fixture.run();
    assert_eq!(
        (retry.migrated, retry.failed, retry.pending),
        (3, 0, 0),
        "{:?}",
        retry.errors
    );
    assert_eq!(
        sql_count(
            &fixture.0,
            "review_history.sqlite3",
            "SELECT count(*) FROM reviews"
        ),
        2
    );
    assert_eq!(
        sql_count(
            &fixture.0,
            "sub_agents.sqlite3",
            "SELECT count(*) FROM child_task"
        ),
        1
    );
    assert_eq!(
        Batch::discover(&fixture.0, &fixture.0.join("logs")).pending(),
        0
    );
}

#[test]
fn malformed_child_and_unreadable_parent_do_not_hide_a_committed_child() {
    let fixture = Fixture::new();
    let mut old = json!({"version":1,"tasks":{
        "sub_first":task("sub_first","conv_healthy_parent"),
        "sub_failed":task("sub_failed","conv_failed_parent"),
        "sub_waiting":task("sub_waiting","conv_failed_parent"),
        "sub_malformed":"not an object"
    }});
    fixture.json("sub_agent_runtime.json", &old);
    fixture.write("sub_agent_tasks/sub_failed/conversation.json", b"{broken");

    let report = fixture.run();
    assert_eq!(
        (report.migrated, report.failed, report.pending),
        (1, 3, 3),
        "{:?}",
        report.errors
    );
    assert!(
        report
            .errors
            .iter()
            .any(|line| line.contains("sub_malformed"))
    );
    assert!(
        report
            .errors
            .iter()
            .any(|line| line.contains("conv_failed_parent"))
    );
    assert_eq!(
        sql_count(
            &fixture.0,
            "sub_agents.sqlite3",
            "SELECT count(*) FROM child_task WHERE parent_id='conv_healthy_parent'"
        ),
        1
    );
    assert_eq!(
        sql_count(
            &fixture.0,
            "sub_agents.sqlite3",
            "SELECT count(*) FROM child_task WHERE parent_id='conv_failed_parent'"
        ),
        0
    );

    old["tasks"]["sub_malformed"] = task("sub_malformed", "conv_repaired_parent");
    fixture.json("sub_agent_runtime.json", &old);
    fixture.json(
        "sub_agent_tasks/sub_failed/conversation.json",
        &json!({"task_id":"sub_failed","messages":[
            {"role":"system","content":"Fixture system prompt"}
        ]}),
    );
    let report = fixture.run();
    assert_eq!(
        (report.migrated, report.failed, report.pending),
        (3, 0, 0),
        "{:?}",
        report.errors
    );
    assert_eq!(
        sql_count(
            &fixture.0,
            "sub_agents.sqlite3",
            "SELECT count(*) FROM child_task"
        ),
        4
    );
}

#[test]
fn damaged_chat_and_review_database_do_not_block_agent_or_child() {
    let fixture = Fixture::new();
    fixture.json(
        "conversations/working/conv_agent.json",
        &conversation("conv_agent", false),
    );
    fixture.json(
        "chat/conversations/conv_chat.json",
        &conversation("conv_chat", true),
    );
    fixture.write(
        "chat/conversations/conversations.sqlite3",
        b"bad chat SQLite",
    );
    fixture.json("logs/approval_agent/approval_1700000000000.json", &review());
    fixture.write("review_history.sqlite3", b"bad review SQLite");
    fixture.json(
        "sub_agent_runtime.json",
        &json!({"version":1,"tasks":{
            "sub_healthy":task("sub_healthy","conv_agent")
        }}),
    );

    let report = fixture.run();
    assert_eq!(
        (report.migrated, report.failed, report.pending),
        (2, 2, 2),
        "{:?}",
        report.errors
    );
    assert!(report.errors.iter().any(|line| line.starts_with("Chat:")));
    assert!(
        report
            .errors
            .iter()
            .any(|line| line.starts_with("审核记录数据库:"))
    );
    assert_eq!(
        sql_count(
            &fixture.0,
            "sub_agents.sqlite3",
            "SELECT count(*) FROM child_task"
        ),
        1
    );
    assert_eq!(
        fs::read(fixture.0.join("review_history.sqlite3")).unwrap(),
        b"bad review SQLite"
    );
}
