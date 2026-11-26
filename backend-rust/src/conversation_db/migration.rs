//! Explicit legacy import shared by the two Host browser surfaces.
use std::{fs,io,path::{Path,PathBuf},sync::{Arc,Mutex}};
use axum::{Json,Router,extract::State,http::StatusCode,routing::get};
use serde::Serialize;
use serde_json::{Value,json};
use super::{Database,FILENAME};
use crate::{legacy::{LegacyDocument,LegacyKind},runtime::store::index_row};

#[derive(Clone,Serialize,Default)]
struct Report{pending:usize,migrated:usize,failed:usize,errors:Vec<String>,running:bool}
#[derive(Clone)]
struct Job{root:PathBuf,state:Arc<Mutex<Report>>}
#[derive(Clone)]
struct Source{dir:PathBuf,id:String,chat:bool}
type Reply=(StatusCode,Json<Value>);
fn reply(report:&Report)->Reply{(StatusCode::OK,Json(json!(report)))}
fn failure(e:impl std::fmt::Display)->Reply{(StatusCode::INTERNAL_SERVER_ERROR,Json(json!({"error":e.to_string()})))}

pub fn router(root:PathBuf)->Router{
    Router::new().route("/api/conversation-storage/migration",get(status).post(start))
        .with_state(Job{root,state:Default::default()})
}
async fn status(State(job):State<Job>)->Reply{
    tokio::task::spawn_blocking(move||{
        let snapshot=job.state.lock().unwrap().clone();
        if snapshot.running{return reply(&snapshot);}
        match pending(&job.root){
            Ok((sources,children))=>{
                let mut current=job.state.lock().unwrap().clone();
                if !current.running{current.pending=sources.len()+children;}
                reply(&current)
            }
            Err(e)=>failure(e),
        }
    }).await.unwrap_or_else(failure)
}
async fn start(State(job):State<Job>)->Reply{
    let mut current=job.state.lock().unwrap();
    if current.running{return reply(&current);}
    current.running=true;current.failed=0;current.migrated=0;current.errors.clear();
    let runner=job.clone();
    match std::thread::Builder::new().name("conversation-migration".into()).spawn(move||run(runner)){
        Ok(_)=>reply(&current),Err(e)=>{current.running=false;failure(e)}
    }
}
fn run(job:Job){
    let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(||run_batch(&job)));
    let mut st=job.state.lock().unwrap_or_else(|p|p.into_inner());
    match result{Ok(Ok(()))=>{},Ok(Err(e))=>record_failure(&mut st,e),Err(_)=>record_failure(&mut st,"Conversation migration interrupted".into())}
    st.running=false;
}
#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust对话格式迁移/storage.rs"]
mod tests;

fn record_failure(st:&mut Report,message:String){st.failed+=1;st.errors.push(message);}
fn run_batch(job:&Job)->Result<(),String>{
    let (sources,children)=pending(&job.root)?;
    job.state.lock().unwrap().pending=sources.len()+children;
    for source in sources{
        let result=convert(&source);let mut state=job.state.lock().unwrap();
        match result{
            Ok(inserted)=>{state.migrated+=usize::from(inserted);state.pending=state.pending.saturating_sub(1);}
            Err(e)=>record_failure(&mut state,format!("{}: {e}",source.id))
        }
    }
    if children!=0{
        let n=crate::sub_agents::migrate_legacy(&job.root)?;
        let mut st=job.state.lock().unwrap();st.migrated+=n;st.pending=st.pending.saturating_sub(n);
    }
    Ok(())
}
fn directories(path:&Path)->Result<Vec<fs::DirEntry>,String>{
    match fs::symlink_metadata(path){
        Ok(m) if m.is_dir()=>fs::read_dir(path).and_then(|entries|entries.collect()).map_err(super::error),
        Ok(_)=>Err("Expected a conversation directory".into()),
        Err(e) if e.kind()==io::ErrorKind::NotFound=>Ok(Vec::new()),
        Err(e)=>Err(e.to_string())
    }
}
fn pending(root:&Path)->Result<(Vec<Source>,usize),String>{
    let mut sources=vec![];
    for ws in directories(&root.join("conversations"))?{
        if ws.file_type().map_err(super::error)?.is_dir(){scan(&ws.path(),false,&mut sources)?;}
    }
    let chat=root.join("chat");
    match fs::symlink_metadata(&chat){
        Ok(m) if m.is_dir()=>scan(&chat.join("conversations"),true,&mut sources)?,
        Ok(_)=>return Err("Invalid Chat storage directory".into()),
        Err(e) if e.kind()==io::ErrorKind::NotFound=>{},
        Err(e)=>return Err(e.to_string()),
    }
    sources.sort_by(|a,b|(&a.dir,&a.id).cmp(&(&b.dir,&b.id)));
    Ok((sources,crate::sub_agents::legacy_pending(root)?))
}
fn scan(dir:&Path,chat:bool,out:&mut Vec<Source>)->Result<(),String>{
    let entries=directories(dir)?;
    let db=if dir.join(FILENAME).exists(){Some(Database::open(dir)?)}else{None};
    for entry in entries{
        if !entry.file_type().map_err(super::error)?.is_file(){continue;}
        let name=entry.file_name();let Some(id)=name.to_str().and_then(|n|n.strip_suffix(".json"))else{continue};
        if !id.starts_with("conv_") || !id.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'_'||b==b'-'){continue;}
        if let Some(db)=&db && db.imported(id)? {continue;}
        out.push(Source{dir:dir.to_owned(),id:id.into(),chat});
    }
    Ok(())
}
fn convert(src:&Source)->Result<bool,String>{
    let path=src.dir.join(format!("{}.json",src.id));
    let doc=LegacyDocument::read(&path).map_err(|e|e.message)?;
    if doc.source_id()!=src.id || (doc.kind()==LegacyKind::Chat)!=src.chat {return Err("Legacy record identity or type mismatch".into());}
    let prior=crate::runtime::store::read_index(&src.dir.join("index.json")).unwrap_or_default();
    let mut summary=if src.chat {
        doc.chat_path(None).map_err(|e|e.message)?;
        crate::chat_mode::migration_summary(doc.raw())
    } else {index_row(doc.raw(),prior.get(&src.id))};
    if let Some(old)=prior[&src.id].as_object(){for (k,v) in old{if summary.get(k).is_none(){summary[k]=v.clone();}}}
    Database::open(&src.dir)?.import(doc.raw(),&summary)
}
