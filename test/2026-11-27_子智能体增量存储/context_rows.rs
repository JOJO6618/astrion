
use super::super::{compression, registry::Registry, worker::Context};
use rusqlite::{Connection,params};
use serde_json::json;
use std::fs;

#[test]
fn child_logs_upgrade_lazily_and_append_without_writing_the_prefix() {
    let root=std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
    let data=root.join("data");
    let workspace=root.join("workspace");
    fs::create_dir_all(&data).unwrap();
    fs::create_dir(&workspace).unwrap();
    let id="sub_long";
    let mut doc=json!({"task_id":id,"future":{"unknown":true},"stats":{"current_context_tokens":10000},
        "messages":[{"role":"system","content":"frozen"},{"role":"user","content":"large".repeat(80000)}]});
    for i in 0..20 { doc["messages"].as_array_mut().unwrap().push(json!({"role":"user","content":format!("m{i}")})); }
    let original=doc.clone();
    let db=Connection::open(data.join("sub_agents.sqlite3")).unwrap();
    db.execute_batch(include_str!("../../backend-rust/src/sub_agents/schema.sql")).unwrap();
    db.execute("INSERT INTO child_task (id,parent_id,record,context) VALUES(?,?,?,?)",params![id,"conv_old","{}",doc.to_string()]).unwrap();
    drop(db);
    let registry=Registry::new(data.clone());
    assert_eq!(registry.context(id).unwrap(),Some(original));
    let mut worker=Context::load(&registry,id).unwrap();
    let mut stale=Context::load(&registry,id).unwrap();
    registry.save_context(&mut worker).unwrap();
    let db=registry.connect(true).unwrap().unwrap();
    db.execute_batch("CREATE TRIGGER old_messages_stay BEFORE UPDATE ON child_message BEGIN SELECT RAISE(ABORT,'rewrote history'); END;
        CREATE TRIGGER old_messages_remain BEFORE DELETE ON child_message BEGIN SELECT RAISE(ABORT,'deleted history'); END;").unwrap();
    worker.document["messages"].as_array_mut().unwrap().push(json!({"role":"assistant","content":"new"}));
    registry.save_context(&mut worker).unwrap();
    assert!(registry.save_context(&mut stale).is_err());
    assert!(db.query_row("SELECT length(context)<200 FROM child_task",[],|r|r.get::<_,bool>(0)).unwrap());
    assert_eq!(registry.context(id).unwrap(),Some(worker.document.clone()));
    db.execute_batch("DROP TRIGGER old_messages_remain;").unwrap();
    worker.threshold=10000;
    assert_eq!(compression::compact(&mut worker,&workspace,&data).unwrap(),Some(1));
    registry.save_context(&mut worker).unwrap();
    let restored=registry.context(id).unwrap().unwrap();
    assert_eq!(restored["messages"].as_array().unwrap().len(),10);
    assert!(restored["messages"][0]["content"].as_str().unwrap().contains("astrion-workspace-memory:start"));
    assert!(restored["messages"][1]["content"].as_str().unwrap().contains("large"));
    assert_eq!(restored["future"],json!({"unknown":true}));
    assert_eq!(restored,worker.document);
    drop(db);
    fs::remove_dir_all(root).unwrap();
}
