//! Version 1 conversation history. A summary and its messages commit together.
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde_json::{Map,Value,json};
use std::{collections::{BTreeMap,HashSet}, fs, io, path::Path,time::Duration};
#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt,OpenOptionsExt};
type Inner<T> = Result<T,Box<dyn std::error::Error>>;
pub mod migration;
pub(crate) mod chat_delta;
pub(crate) mod checkpoint_versions;
mod shallow;
mod title;
pub mod reviews;
fn private_connection(dir:&Path,filename:&str)->Inner<Connection>{
        let mut builder=fs::DirBuilder::new();
        #[cfg(unix)] builder.mode(0o700);
        builder.recursive(true).create(dir)?;
        if !fs::symlink_metadata(dir)?.is_dir(){return Err("Invalid conversation directory".into());}
        let path=dir.canonicalize()?.join(filename);
        let mut options=fs::OpenOptions::new();options.write(true).create_new(true);
        #[cfg(unix)] options.mode(0o600);
        match options.open(&path){Ok(_)=>{},Err(e) if e.kind()==io::ErrorKind::AlreadyExists=>{},Err(e)=>return Err(e.into())}
        let flags=rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE|rusqlite::OpenFlags::SQLITE_OPEN_NOFOLLOW;
        let conn=Connection::open_with_flags(&path,flags)?;
        conn.busy_timeout(Duration::from_secs(15))?;
        conn.execute_batch("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")?;
    Ok(conn)
}
pub struct Database { conn:Connection }
type Fields = BTreeMap<(String,String),Value>;
fn error(e:impl std::fmt::Display)->String {e.to_string()}
pub const FILENAME:&str="conversations.sqlite3";
pub const MIGRATION_REQUIRED:&str="不兼容的对话记录";
pub fn migration_required_payload()->Value{
    json!({"success":false,"code":"conversation_migration_required","error":MIGRATION_REQUIRED,"message":MIGRATION_REQUIRED})
}

impl Database {
    pub fn open(dir:&Path)->Result<Self,String> {Self::connect(dir).map_err(error)}
    fn connect(dir:&Path)->Inner<Self>{
        let mut conn=private_connection(dir,FILENAME)?;
        let version:i64=conn.query_row("PRAGMA user_version",[],|r|r.get(0))?;
        if version != 3 {
            let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let current: i64 = tx.query_row("PRAGMA user_version", [], |row| row.get(0))?;
            match current {
                0 => {
                    tx.execute_batch(include_str!("conversation_db/schema.sql"))?;
                    tx.execute_batch(include_str!("conversation_db/media_indexes.sql"))?;
                    tx.execute_batch(include_str!("conversation_db/checkpoint_versions.sql"))?;
                }
                1 => {
                    tx.execute_batch(include_str!("conversation_db/media_indexes.sql"))?;
                    tx.execute_batch(include_str!("conversation_db/checkpoint_versions.sql"))?;
                }
                2 => tx.execute_batch(include_str!("conversation_db/checkpoint_versions.sql"))?,
                3 => (),
                _ => return Err(format!("Unsupported conversation database version {current}").into()),
            }
            tx.commit()?;
        }
        Ok(Self{conn})
    }
    /// This value is comparable only while this same SQLite connection stays open.
    pub(crate) fn data_version(&self)->Result<i64,String>{
        self.conn.query_row("PRAGMA data_version",[],|row|row.get(0)).map_err(error)
    }
    pub fn contains(&self,id:&str)->Result<bool,String>{exists(&self.conn,id).map_err(error)}
    pub fn imported(&self,id:&str)->Result<bool,String>{
        self.conn.query_row("SELECT EXISTS(SELECT 1 FROM legacy_imports WHERE id=?)",[id],|r|r.get(0)).map_err(error)
    }
    pub fn first_user(&self,id:&str)->Result<Option<Value>,String>{
        self.conn.query_row("SELECT body FROM messages WHERE conversation=? AND role='user' ORDER BY seq LIMIT 1",[id],|r|r.get::<_,String>(0)).optional().map_err(error)?
        .map(|s|serde_json::from_str(&s)).transpose().map_err(error)
    }

    /// Bounded excerpt for the opt-in recent-conversations prompt. Runtime
    /// notices and attached media are not a user's first message.
    pub(crate) fn first_prompt_user_excerpt(&self, id: &str) -> Result<Option<String>, String> {
        self.conn.query_row(
            "SELECT substr(trim(json_extract(body,'$.content')),1,100)
             FROM messages WHERE conversation=?1 AND role='user'
             AND json_type(body,'$.content')='text'
             AND trim(json_extract(body,'$.content'))<>''
             AND COALESCE(json_extract(body,'$.metadata.hidden'),0)=0
             AND COALESCE(json_extract(body,'$.metadata.visibility'),'') NOT IN ('hidden','compact')
             AND COALESCE(json_extract(body,'$.metadata.starts_work'),1)<>0
             ORDER BY seq LIMIT 1",
            [id], |row| row.get::<_, String>(0),
        ).optional().map_err(error)
    }
    pub fn field(&self, id: &str, scope: &str, key: &str) -> Result<Option<Value>, String> {
        self.conn.query_row("SELECT value FROM fields WHERE conversation=? AND scope=? AND key=?",
            params![id, scope, key], |row| row.get::<_, String>(0)).optional().map_err(error)?
            .map(|raw| serde_json::from_str(&raw)).transpose().map_err(error)
    }
    pub fn summary(&self,id:&str)->Result<Option<Value>,String>{
        self.conn.query_row("SELECT summary FROM conversations WHERE id=?",[id],|r|r.get::<_,String>(0)).optional().map_err(error)?
        .map(|v|serde_json::from_str(&v)).transpose().map_err(error)
    }
    /// Rows already stored, and legacy sources retained after migration or deletion.
    pub(crate) fn known_ids(&self) -> Result<HashSet<String>,String> {
        let mut query=self.conn.prepare("SELECT id FROM conversations UNION SELECT id FROM legacy_imports").map_err(error)?;
        query.query_map([], |r|r.get(0)).map_err(error)?.collect::<Result<_,_>>().map_err(error)
    }
    pub fn rows(&self)->Result<Vec<Value>,String>{
        let mut st=self.conn.prepare("SELECT id,summary FROM conversations ORDER BY id").map_err(error)?;
        let rows=st.query_map([],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?))).map_err(error)?;
        let mut out=Vec::new();
        for row in rows {let (id,raw)=row.map_err(error)?;let mut summary:Value=serde_json::from_str(&raw).map_err(error)?;summary["id"]=json!(id);out.push(summary);}
        Ok(out)
    }
    pub(crate) fn activity_counts(&self, id: &str) -> Result<(usize,bool,bool),String> {
        self.conn.query_row(
            "SELECT (SELECT count(*) FROM messages WHERE conversation=?1 AND role='tool'),
                    EXISTS(SELECT 1 FROM messages WHERE conversation=?1 AND has_images),
                    EXISTS(SELECT 1 FROM messages WHERE conversation=?1 AND has_videos)",
            [id], |row| Ok((row.get(0)?,row.get(1)?,row.get(2)?)),
        ).map_err(error)
    }

    pub fn header(&self, id: &str) -> Result<Option<(Value,usize)>,String> {
        let tx = self.conn.unchecked_transaction().map_err(error)?;
        let count = tx.query_row("SELECT message_count FROM conversations WHERE id=?", [id], |row| row.get::<_,usize>(0)).optional().map_err(error)?;
        let result = count.map(|n| load_header(&tx,id).map(|header| (header,n))).transpose().map_err(error)?;
        tx.commit().map_err(error)?;
        Ok(result)
    }

    pub(crate) fn has_user_message_id(&self, conversation: &str, id: &str) -> Result<bool, String> {
        let exact = self.conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM messages WHERE conversation=? AND message_id=? AND role='user')",
            params![conversation, id], |row| row.get::<_, bool>(0),
        ).map_err(error)?;
        if exact { return Ok(true); }
        self.conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM messages WHERE conversation=? AND role='user' AND message_id IS NULL AND json_extract(body,'$.metadata.message_id')=?)",
            params![conversation, id], |row| row.get(0),
        ).map_err(error)
    }
    pub fn read(&self,id:&str)->Result<Option<Value>,String>{
        let tx=self.conn.unchecked_transaction().map_err(error)?;
        let doc=read_document(&tx,id).map_err(error)?;
        tx.commit().map_err(error)?;Ok(doc)
    }
    /// Update one header field without touching messages or changing the indexed counts.
    pub(crate) fn set_field(&mut self, id: &str, scope: &str, key: &str, value: &Value) -> Result<(),String> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate).map_err(error)?;
        if !exists(&tx,id).map_err(error)? { return Err("Conversation not found".into()); }
        if scope == "metadata" {
            tx.execute("INSERT INTO fields VALUES(?,'root','metadata','{}') ON CONFLICT(conversation,scope,key) DO UPDATE SET value='{}' WHERE fields.value<>'{}'",[id]).map_err(error)?;
        }
        tx.execute("INSERT INTO fields VALUES(?,?,?,?) ON CONFLICT(conversation,scope,key) DO UPDATE SET value=excluded.value WHERE fields.value<>excluded.value",
            params![id, scope, key, value.to_string()]).map_err(error)?;
        tx.commit().map_err(error)
    }
    pub(crate) fn replace_metadata_fields(
        &mut self, id: &str, before: &Map<String, Value>, after: &Map<String, Value>,
    ) -> Result<(), String> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate).map_err(error)?;
        if !exists(&tx,id).map_err(error)? { return Err("Conversation not found".into()); }
        tx.execute("INSERT INTO fields VALUES(?,'root','metadata','{}') ON CONFLICT(conversation,scope,key) DO UPDATE SET value='{}' WHERE fields.value<>'{}'", [id]).map_err(error)?;
        for key in before.keys().filter(|key| !after.contains_key(*key)) {
            tx.execute("DELETE FROM fields WHERE conversation=? AND scope='metadata' AND key=?", params![id, key]).map_err(error)?;
        }
        for (key, value) in after.iter().filter(|(key, value)| before.get(*key) != Some(*value)) {
            tx.execute("INSERT INTO fields VALUES(?,'metadata',?,?) ON CONFLICT(conversation,scope,key) DO UPDATE SET value=excluded.value",
                params![id, key, value.to_string()]).map_err(error)?;
        }
        tx.commit().map_err(error)
    }
    /// Keep the work bubble and Quick Window's file list in the same SQLite transaction.
    pub(crate) fn record_edit_summary(
        &mut self, id: &str, message_id: &str, summary: &Value, path: &str, op: &str, timestamp: &str,
    ) -> Result<Value, String> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate).map_err(error)?;
        write_edit_summary(&tx, id, message_id, summary).map_err(error)?;

        let mut edited = tx.query_row(
            "SELECT value FROM fields WHERE conversation=? AND scope='metadata' AND key='edited_files'",
            [id], |row| row.get::<_, String>(0),
        ).optional().map_err(error)?
            .and_then(|text| serde_json::from_str::<Vec<Value>>(&text).ok()).unwrap_or_default();
        edited.retain(|entry| entry["path"] != path);
        edited.push(json!({"path":path,"op":op,"ts":timestamp}));
        tx.execute("INSERT INTO fields VALUES(?,'metadata','edited_files',?)
            ON CONFLICT(conversation,scope,key) DO UPDATE SET value=excluded.value",
            params![id, Value::Array(edited.clone()).to_string()],
        ).map_err(error)?;
        tx.commit().map_err(error)?;
        Ok(Value::Array(edited))
    }
    pub(crate) fn finish_edit_summary(&self, id: &str, message_id: &str, summary: &Value) -> Result<(), String> {
        let tx = self.conn.unchecked_transaction().map_err(error)?;
        write_edit_summary(&tx, id, message_id, summary).map_err(error)?;
        tx.commit().map_err(error)
    }
    pub fn delete(&mut self,id:&str)->Result<bool,String>{
        self.conn.execute("DELETE FROM conversations WHERE id=?",[id]).map(|n|n==1).map_err(error)
    }
    pub fn save(&mut self,doc:&Value,summary:&Value)->Result<(),String>{self.write(doc,summary,false).map(|_|()).map_err(error)}
    /// Verify the full semantic round trip and publish the legacy tombstone atomically.
    pub fn import(&mut self,doc:&Value,summary:&Value)->Result<bool,String>{self.write(doc,summary,true).map_err(error)}
    fn write(&mut self,doc:&Value,summary:&Value,importing:bool)->Inner<bool>{
        let id=doc["id"].as_str().filter(|id|!id.is_empty()).ok_or("Missing conversation identity")?;
        if !summary.is_object(){return Err("Invalid conversation summary".into());}
        let messages=doc["messages"].as_array().ok_or("Invalid message list")?;
        let tx=self.conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if importing {
            if tx.query_row("SELECT EXISTS(SELECT 1 FROM legacy_imports WHERE id=?)",[id],|r|r.get::<_,bool>(0))?{return Ok(false);}
            if exists(&tx,id)?{return Err("Conversation ID already exists".into());}
        }
        let calls=messages.iter().map(tool_count).sum::<usize>();
        tx.execute("INSERT INTO conversations VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET summary=excluded.summary,message_count=excluded.message_count,tool_count=excluded.tool_count",
            params![id,summary.to_string(),messages.len(),calls])?;
        sync_header(&tx,id,doc)?;
        for (seq,msg) in messages.iter().enumerate(){write_message(&tx,id,seq,msg)?;}
        tx.execute("DELETE FROM messages WHERE conversation=? AND seq>=?",params![id,messages.len()])?;
        if importing {
            if read_document(&tx,id)?.as_ref()!=Some(doc){return Err("Imported conversation differs from original".into());}
            tx.execute("INSERT INTO legacy_imports VALUES(?)",[id])?;
        }
        tx.commit()?;Ok(true)
    }
    /// The callback sees the original header and only the new batch. No historical message bodies are read.
    pub(crate) fn append<F>(&mut self,id:&str,mut batch:Vec<Value>,apply:F)->Result<(),String>
    where F:FnOnce(Value,Vec<Value>,usize,usize,Value)->Result<(Value,Value),String>{
        let tx=self.conn.transaction_with_behavior(TransactionBehavior::Immediate).map_err(error)?;
        let (seq,calls,summary):(usize,usize,String)=tx.query_row("SELECT message_count,tool_count,summary FROM conversations WHERE id=?",[id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).map_err(error)?;
        for msg in &mut batch {
            if let Some(mid)=msg["message_id"].as_str(){
                let used:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM messages WHERE conversation=? AND message_id=?)",params![id,mid],|r|r.get(0)).map_err(error)?;
                if used {msg.as_object_mut().unwrap().remove("message_id");}
            }
        }
        let header=load_header(&tx,id).map_err(error)?;
        let (doc,row)=apply(header,batch,seq,calls,serde_json::from_str(&summary).map_err(error)?)?;
        let new=doc["messages"].as_array().ok_or("Invalid appended messages")?;
        for (i,msg) in new.iter().enumerate(){write_message(&tx,id,seq+i,msg).map_err(error)?;}
        sync_header(&tx,id,&doc).map_err(error)?;
        tx.execute("UPDATE conversations SET message_count=?,tool_count=?,summary=? WHERE id=?",params![seq+new.len(),calls+new.iter().map(tool_count).sum::<usize>(),row.to_string(),id]).map_err(error)?;
        tx.commit().map_err(error)
    }
}
fn exists(db:&Connection,id:&str)->rusqlite::Result<bool>{
    db.query_row("SELECT EXISTS(SELECT 1 FROM conversations WHERE id=?)",[id],|r|r.get(0))
}
fn tool_count(msg:&Value)->usize{if msg["role"]=="assistant"{msg["tool_calls"].as_array().map_or(0,Vec::len)}else{0}}
fn write_edit_summary(db: &Connection, id: &str, message_id: &str, summary: &Value) -> Inner<()> {
    let (seq, body): (usize, String) = db.query_row(
        "SELECT seq,body FROM messages WHERE conversation=? AND message_id=? AND role='user'",
        params![id, message_id],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    let mut body: Value = serde_json::from_str(&body)?;
    if !body["metadata"].is_object() {
        body["metadata"] = json!({});
    }
    body["metadata"]["edit_summary"] = summary.clone();
    write_message(db, id, seq, &body)
}
fn write_message(db:&Connection,id:&str,seq:usize,msg:&Value)->Inner<()>{
    db.prepare_cached("INSERT INTO messages VALUES(?,?,?,?,?) ON CONFLICT(conversation,seq) DO UPDATE SET body=excluded.body,message_id=excluded.message_id,role=excluded.role WHERE messages.body<>excluded.body")?
        .execute(params![id,seq,msg["message_id"].as_str(),msg["role"].as_str(),msg.to_string()])?;
    Ok(())
}
fn header_fields(doc:&Value)->Inner<Fields>{
    let mut out=Fields::new();
    for (key,value) in doc.as_object().ok_or("Invalid conversation header")? {
        if key=="messages"{continue;}
        if matches!(key.as_str(),"metadata"|"token_statistics") && value.is_object(){
            out.insert(("root".into(),key.clone()),json!({}));
            for (k,v) in value.as_object().unwrap(){out.insert((key.clone(),k.clone()),v.clone());}
        }else{out.insert(("root".into(),key.clone()),value.clone());}
    }
    Ok(out)
}
fn sync_header(db:&Connection,id:&str,doc:&Value)->Inner<()>{
    let fields=header_fields(doc)?;
    let mut q=db.prepare_cached("SELECT scope,key FROM fields WHERE conversation=?")?;
    let previous=q.query_map([id],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?)))?.collect::<rusqlite::Result<HashSet<_>>>()?;
    for (scope,key) in &previous {if !fields.contains_key(&(scope.clone(),key.clone())) {
        db.execute("DELETE FROM fields WHERE conversation=? AND scope=? AND key=?",params![id,scope,key])?;
    }}
    let mut upd=db.prepare_cached("INSERT INTO fields VALUES(?,?,?,?) ON CONFLICT(conversation,scope,key) DO UPDATE SET value=excluded.value WHERE fields.value<>excluded.value")?;
    for ((scope,key),v) in fields {upd.execute(params![id,scope,key,v.to_string()])?;}
    Ok(())
}
fn load_header(db:&Connection,id:&str)->Inner<Value>{
    let mut obj=Value::Object(Map::new());
    let mut stmt=db.prepare_cached("SELECT scope,key,value FROM fields WHERE conversation=? ORDER BY (scope='root') DESC")?;
    let mut rows=stmt.query([id])?;
    while let Some(row)=rows.next()?{let scope:String=row.get(0)?;let key:String=row.get(1)?;let v:String=row.get(2)?;
        let target=if scope=="root"{&mut obj}else{&mut obj[scope]};target[key]=serde_json::from_str(&v)?;
    }
    obj["messages"]=json!([]);Ok(obj)
}
fn read_document(db:&Connection,id:&str)->Inner<Option<Value>>{
    if !exists(db,id)?{return Ok(None);}
    let mut doc=load_header(db,id)?;
    let mut st=db.prepare_cached("SELECT body FROM messages WHERE conversation=? ORDER BY seq")?;
    let mut rows=st.query([id])?;
    let messages=doc["messages"].as_array_mut().unwrap();
    while let Some(row)=rows.next()?{messages.push(serde_json::from_str(&row.get::<_,String>(0)?)?);}
    Ok(Some(doc))
}
