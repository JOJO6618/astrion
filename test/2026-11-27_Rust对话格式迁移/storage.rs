use super::{pending,run_batch,Job};
use crate::{conversation_db::{Database,FILENAME},runtime::Runtime};
use serde_json::{Value,json};
use std::{fs,path::PathBuf};
use uuid::Uuid;

struct Fixture(PathBuf);
impl Fixture{
    fn new()->Self{let p=std::env::temp_dir().join(format!("astrion-storage-test-{}",Uuid::new_v4()));fs::create_dir(&p).unwrap();Self(p)}
    fn job(&self)->Job{Job{root:self.0.clone(),state:Default::default()}}
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
    run_batch(&job).unwrap();assert_eq!(job.state.lock().unwrap().migrated,1);
    let mut db=Database::open(&dir).unwrap();assert_eq!(db.read(id).unwrap(),Some(raw.clone()));
    drop(rt);let rt=Runtime::open(&f.0).unwrap();rt.bind_workspace("ws",&f.0).unwrap();
    assert!(rt.session_history("ws",id).is_ok());
    assert_eq!(pending(&f.0).unwrap().0.len(),0);
    assert!(db.delete(id).unwrap());
    assert_eq!(rt.session_history("ws",id).unwrap_err().status,404);
    assert_eq!(pending(&f.0).unwrap().0.len(),0);
    assert_eq!(fs::read(&file).unwrap(),original);
}
#[test]
fn damaged_or_conflicting_sources_do_not_replace_other_conversations(){
    let f=Fixture::new();let dir=f.0.join("conversations/ws");fs::create_dir_all(&dir).unwrap();
    let doc=json!({"id":"conv_a","metadata":{},"messages":[]});let bad=dir.join("conv_bad.json");
    fs::write(&bad,"{").unwrap();fs::write(dir.join("conv_a.json"),doc.to_string()).unwrap();
    let job=f.job();run_batch(&job).unwrap();let report=job.state.lock().unwrap().clone();
    assert_eq!((report.migrated,report.failed,report.pending),(1,1,1));
    let mut db=Database::open(&dir).unwrap();assert_eq!(db.read("conv_a").unwrap(),Some(doc.clone()));
    let mut fixed=doc;fixed["id"]=json!("conv_bad");
    db.save(&fixed,&json!({})).unwrap();
    fs::write(&bad,fixed.to_string()).unwrap();
    let retry=f.job();run_batch(&retry).unwrap();
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
