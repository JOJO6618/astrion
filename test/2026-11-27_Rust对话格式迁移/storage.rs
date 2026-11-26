use super::{scan_conversations,run_batch,Job};
use crate::{conversation_db::{Database,FILENAME},runtime::Runtime};
use serde_json::{Value,json};
use std::{fs,path::PathBuf};
use uuid::Uuid;

#[test]
fn shallow_history_keeps_original_content_and_protected_turns() {
    use crate::runtime::shallow::Settings;
    let fixture=Fixture::new();
    let mut db=Database::open(&fixture.0).unwrap();
    let tool=|id:&str,name:&str| json!({"role":"tool","name":name,"tool_call_id":id,"content":"original text","unknown":[17]});
    let doc=json!({"id":"conv_shallow","metadata":{},"token_statistics":{"current_context_tokens":500},
        "messages":[{"role":"user","content":"old round"},tool("a","read_file"),tool("b","submit_plan"),
        {"role":"user","content":"latest round"},tool("c","read_file"),tool("d","edit_file")]});
    db.save(&doc,&json!({})).unwrap();
    let settings=Settings{threshold:100,interval:1,batch:3,recent_tools:1,user_turns:1};
    assert_eq!(db.shallow_compress("conv_shallow",&settings,|_|Ok(()),"test").unwrap(),["a"]);
    let saved=db.read("conv_shallow").unwrap().unwrap();
    assert_eq!(saved["messages"][1]["content"],"original text");
    assert_eq!(saved["messages"][1]["unknown"],json!([17]));
    assert_eq!(saved["messages"][1]["metadata"]["auto_shallow_compacted"],true);
    for i in [0,2,3,4,5]{assert_eq!(saved["messages"][i],doc["messages"][i]);}
    assert!(db.shallow_compress("conv_shallow",&settings,|_|panic!("duplicate compression"),"again").unwrap().is_empty());
}

struct Fixture(PathBuf);
impl Fixture{
    fn new()->Self{let p=std::env::temp_dir().join(format!("astrion-storage-test-{}",Uuid::new_v4()));fs::create_dir(&p).unwrap();Self(p)}
    fn job(&self)->Job{Job{root:self.0.clone(),logs:self.0.join("logs"),state:Default::default()}}
}
impl Drop for Fixture{fn drop(&mut self){let _=fs::remove_dir_all(&self.0);}}
#[test]
fn legacy_requires_explicit_migration_and_survives_restart(){
    let f=Fixture::new();let dir=f.0.join("conversations/ws");fs::create_dir_all(&dir).unwrap();
    let id="conv_old";let raw=json!({"id":id,"title":"旧记录","messages":[{"role":"user","content":"one","odd":null}],"metadata":{"frozen_system_prompt":"old"},"future":[null,0]});
    let file=dir.join(format!("{id}.json"));let original=serde_json::to_vec_pretty(&raw).unwrap();fs::write(&file,&original).unwrap();
    let rt=Runtime::open(&f.0).unwrap();rt.bind_workspace("ws",&f.0).unwrap();
    assert_eq!(rt.session_history("ws",id).unwrap_err().status,409);
    assert_eq!(rt.web_conversation_list("ws",10,0,true).unwrap().to_string().contains("\"incompatible\":true"),true);
    assert!(!dir.join(FILENAME).exists());
    let job=f.job();let _new=rt.create_session("ws",&json!({})).unwrap();
    run_batch(&job);assert_eq!(job.state.lock().unwrap().migrated,1);
    let mut db=Database::open(&dir).unwrap();assert_eq!(db.read(id).unwrap(),Some(raw.clone()));
    drop(rt);let rt=Runtime::open(&f.0).unwrap();rt.bind_workspace("ws",&f.0).unwrap();
    assert!(rt.session_history("ws",id).is_ok());
    assert_eq!(scan_conversations(&f.0).sources.len(),0);
    assert!(db.delete(id).unwrap());
    assert_eq!(rt.session_history("ws",id).unwrap_err().status,404);
    assert_eq!(scan_conversations(&f.0).sources.len(),0);
    assert_eq!(fs::read(&file).unwrap(),original);
}
#[test]
fn reviewer_logs_migrate_once_without_guessing_their_conversation(){
    let fixture=Fixture::new();
    let job=fixture.job();
    let payload=json!({"messages":[{"role":"user","content":"fixture"}],"trace":[],"created_at":1700000000000_u64,"final_result":{"decision":"approved"},"unknown":{"future":[1,2]}});
    for (folder,name) in [("approval_agent","approval"),("goal_review_agent","goal_review"),("workflow_review_agent","workflow_review")] {
        let dir=job.logs.join(folder);fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join(format!("{name}_1700000000000.json")),payload.to_string()).unwrap();
    }
    run_batch(&job);
    assert_eq!(job.state.lock().unwrap().migrated,3);
    run_batch(&fixture.job());
    let db=rusqlite::Connection::open(fixture.0.join("review_history.sqlite3")).unwrap();
    assert_eq!(db.query_row("SELECT count(*) FROM reviews WHERE conversation='' AND outcome='approved'",[],|r|r.get::<_,u64>(0)).unwrap(),3);
    let events=db.prepare("SELECT body FROM review_events").unwrap().query_map([],|r|r.get::<_,String>(0)).unwrap().collect::<Result<Vec<_>,_>>().unwrap();
    assert_eq!(events.len(),3);
    for value in events {assert_eq!(serde_json::from_str::<Value>(&value).unwrap()["transcript"],payload);}
    assert_eq!(fs::read(job.logs.join("approval_agent/approval_1700000000000.json")).unwrap(),payload.to_string().as_bytes());
}
#[test]
fn damaged_or_conflicting_sources_do_not_replace_other_conversations(){
    let f=Fixture::new();let dir=f.0.join("conversations/ws");fs::create_dir_all(&dir).unwrap();
    let doc=json!({"id":"conv_a","metadata":{},"messages":[]});let bad=dir.join("conv_bad.json");
    fs::write(&bad,"{").unwrap();fs::write(dir.join("conv_a.json"),doc.to_string()).unwrap();
    let job=f.job();run_batch(&job);let report=job.state.lock().unwrap().clone();
    assert_eq!((report.migrated,report.failed,report.pending),(1,1,1));
    let mut db=Database::open(&dir).unwrap();assert_eq!(db.read("conv_a").unwrap(),Some(doc.clone()));
    let mut fixed=doc;fixed["id"]=json!("conv_bad");
    db.save(&fixed,&json!({})).unwrap();
    fs::write(&bad,fixed.to_string()).unwrap();
    let retry=f.job();run_batch(&retry);
    assert_eq!(retry.state.lock().unwrap().failed,1);
    assert!(!db.imported("conv_bad").unwrap());
}
#[test]
fn tool_batch_commits_new_rows_and_leaves_original_message_bytes_intact(){
    let f=Fixture::new();let dir=f.0.join("conversations/ws");
    let original=json!({"id":"conv_1","title":"locked","metadata":{"title_locked":true},"messages":[{"role":"user","message_id":"old","content":"first"}]});
    let mut db=Database::open(&dir).unwrap();db.import(&original,&json!({})).unwrap();
    let rt=Runtime::open(&f.0).unwrap();rt.bind_workspace("ws",&f.0).unwrap();
    let trigger=rusqlite::Connection::open(dir.join(FILENAME)).unwrap();
    trigger.execute_batch("CREATE TRIGGER immutable_history BEFORE UPDATE ON messages WHEN OLD.seq=0 BEGIN SELECT RAISE(ABORT,'historical update'); END;").unwrap();

    drop(rt);
    let store=crate::runtime::store::Store::open(&f.0).unwrap();
    store.append_messages("ws","conv_1",vec![json!({"role":"assistant","tool_calls":[{"id":"one"}]}),json!({"role":"tool","tool_call_id":"one","content":"ok"})],1700000000.0,None,None).unwrap();
    let doc=db.read("conv_1").unwrap().unwrap();assert_eq!(doc["messages"][0],original["messages"][0]);assert_eq!(doc["metadata"]["total_messages"],3);
    assert_eq!(doc["metadata"]["total_tools"],1);assert_eq!(doc["title"],"locked");
    assert_ne!(doc["messages"][2]["message_id"],Value::Null);assert_eq!(db.rows().unwrap()[0]["total_messages"],3);
}
