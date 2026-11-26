use super::*;
use rusqlite::Connection;
use std::{fs, path::PathBuf};
use uuid::Uuid;

const ID: &str = "conv_incremental_fixture";
const NOW: f64 = 1_700_000_000.0;

struct Isolated(PathBuf);
impl Isolated {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("astrion-chat-delta-{}", Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        Self(root)
    }
}
impl Drop for Isolated {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn user() -> Value {
    json!({"message_id":"user_1","role":"user","content":"before","metadata":{
        "future_message_annotation":{"provider_state":[3,2,1]},
    }})
}

fn initial(paths: &ChatPaths) -> Value {
    let mut raw = paths
        .initial_document(ID, NOW, &json!({"operation_id":"initial"}))
        .unwrap();
    raw["future_document_field"] = json!({"revision":"opaque"});
    raw["metadata"]["future_account_setting"] = json!({"objects":[null,{"format":"opaque"}]});
    raw["metadata"]["chat"]["future_branch_annotation"] = json!({"rich":{"content":"opaque"}});
    raw["token_statistics"]["future_metric"] = json!(123456789);
    raw
}

#[test]
fn existing_messages_are_never_upserted_during_appends_or_header_updates() {
    let isolated = Isolated::new();
    let paths = ChatPaths::new(&isolated.0);
    let initial = initial(&paths);
    let first = begin(
        &paths,
        ID,
        "initial",
        Selection::Send {
            parent_id: None,
            message: user(),
        },
        Some(initial),
        None,
        None,
        &Value::Null,
        NOW,
    )
    .unwrap();
    let turn = first["value"]["id"].as_str().unwrap();
    let connection =
        Connection::open(paths.conversations().join(crate::conversation_db::FILENAME)).unwrap();
    connection
        .execute_batch(
            "
        CREATE TABLE audit(seq INTEGER);
        CREATE TRIGGER capture_upsert BEFORE INSERT ON messages
        BEGIN INSERT INTO audit VALUES(NEW.seq); END;
    ",
        )
        .unwrap();
    let append = |id: &str, content: &str| {
        json!({"message_id":id,"role":"assistant","content":content,
        "future_assistant_field":{"kind":"example"}})
    };
    append_batch(
        &paths,
        ID,
        turn,
        vec![
            append("assistant_1", "answer"),
            append("assistant_2", "conclusion"),
        ],
        NOW + 1.0,
        |_| {},
    )
    .unwrap();
    finish(&paths, ID, turn, "complete", NOW + 2.0).unwrap();
    paths.set_title(ID, "new title").unwrap();
    configure(
        &paths,
        ID,
        &json!({"reasoning_effort":"medium"})
            .as_object()
            .unwrap()
            .clone(),
        NOW + 3.0,
    )
    .unwrap();
    edit(&paths, ID, "edit-1", "user_1", "after", None, NOW + 4.0).unwrap();
    let rewritten = connection
        .prepare("SELECT seq FROM audit ORDER BY rowid")
        .unwrap()
        .query_map([], |row| row.get::<_, i64>(0))
        .unwrap()
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    assert_eq!(rewritten, [1, 2, 0]);

    let raw = paths.read_raw(ID).unwrap();
    assert_eq!(raw["title"], "new title");
    assert_eq!(raw["metadata"]["reasoning_effort"], "medium");
    assert_eq!(raw["messages"][0]["content"], "after");
    assert_eq!(
        raw["messages"][0]["metadata"]["future_message_annotation"]["provider_state"],
        json!([3, 2, 1])
    );
    assert_eq!(
        raw["messages"][2]["future_assistant_field"]["kind"],
        "example"
    );
    assert_eq!(raw["future_document_field"]["revision"], "opaque");
    assert_eq!(
        raw["metadata"]["future_account_setting"]["objects"][1]["format"],
        "opaque"
    );
    assert_eq!(
        raw["metadata"]["chat"]["future_branch_annotation"]["rich"]["content"],
        "opaque"
    );
    assert_eq!(raw["token_statistics"]["future_metric"], 123456789);
    assert_eq!(
        raw["metadata"]["chat"]["commands"]["assistant_2"]["result"]["revision"],
        raw["metadata"]["chat"]["commands"]["assistant_1"]["result"]["revision"]
            .as_u64()
            .unwrap()
            + 1
    );
}

#[test]
fn concurrent_header_updates_merge_and_a_branch_uses_only_new_rows() {
    let isolated = Isolated::new();
    let runtime = crate::runtime::Runtime::open(&isolated.0).unwrap();
    fs::create_dir(isolated.0.join("chat")).unwrap();
    runtime
        .with_chat_storage(|root| -> Result<(), ChatError> {
            let paths = ChatPaths::new(root);
            let raw = initial(&paths);
            let first = begin(
                &paths,
                ID,
                "initial",
                Selection::Send {
                    parent_id: None,
                    message: user(),
                },
                Some(raw),
                None,
                None,
                &Value::Null,
                NOW,
            )?;
            let turn = first["value"]["id"].as_str().unwrap();
            append(
                &paths,
                ID,
                turn,
                json!({"message_id":"assistant_1","role":"assistant","content":"first"}),
                NOW + 1.0,
                None,
            )?;
            finish(&paths, ID, turn, "complete", NOW + 2.0)?;
            Ok(())
        })
        .unwrap();
    std::thread::scope(|threads| {
        for index in 0..12 {
            let runtime = &runtime;
            threads.spawn(move || {
                runtime
                    .with_chat_storage(|root| -> Result<(), ChatError> {
                        let paths = ChatPaths::new(root);
                        paths.update_header(ID, |raw, _| {
                            raw["metadata"]["future_concurrent_updates"][index.to_string()] =
                                json!(index);
                            Ok(())
                        })
                    })
                    .unwrap();
            });
        }
    });
    runtime
        .with_chat_storage(|root| -> Result<(), ChatError> {
            let paths = ChatPaths::new(root);
            let (_, before_count) = paths.read_header(ID)?;
            assert_eq!(before_count, 2);
            let current = paths.read_raw(ID)?;
            assert_eq!(
                current["metadata"]["future_concurrent_updates"]
                    .as_object()
                    .unwrap()
                    .len(),
                12
            );
            let edit_begin = begin(
                &paths,
                ID,
                "fork",
                Selection::EditAndSend {
                    message_id: "user_1",
                    content: "fork text",
                },
                None,
                None,
                None,
                &Value::Null,
                NOW + 3.0,
            )?;
            let turn = edit_begin["value"]["id"].as_str().unwrap();
            append(
                &paths,
                ID,
                turn,
                json!({"message_id":"assistant_2","role":"assistant","content":"second"}),
                NOW + 4.0,
                None,
            )?;
            finish(&paths, ID, turn, "complete", NOW + 5.0)?;
            let raw = paths.read_raw(ID)?;
            assert_eq!(
                &raw["messages"].as_array().unwrap()[..2],
                current["messages"].as_array().unwrap()
            );
            let document = LegacyDocument::from_value(raw)?;
            assert_eq!(document.chat_path(None)?[0]["content"], "fork text");
            assert_eq!(
                document.chat_path(Some("assistant_1"))?[0]["content"],
                "before"
            );
            assert_eq!(document.chat_path(None)?[1]["content"], "second");
            Ok(())
        })
        .unwrap();
}
