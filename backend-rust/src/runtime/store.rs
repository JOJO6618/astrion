use std::{
    collections::{HashMap, HashSet, VecDeque},
    fs::{self, DirBuilder, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::Arc,
};

#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::sync::oneshot;
use uuid::Uuid;

use crate::{approval::ApprovalStore, goals::GoalRun, legacy::{LegacyDocument, LegacyError}};

use super::{RuntimeError, TaskApprovalSettings, TaskCompletion, now};
use crate::tools::ToolSet;

const EVENT_WINDOW: usize = 20_000;

pub(crate) struct Store {
    pub data_dir: PathBuf,
    runtime_dir: PathBuf,
    _lock: File,
    pub(super) crosspet: Option<crate::crosspet::CrossPetEvents>,
    pub search_sentences: HashMap<PathBuf, (std::time::SystemTime, u64, String)>,
    pub workspaces: HashMap<String, PathBuf>,
    pub(super) workspace_labels: HashMap<String, String>,
    pub workspace_tools: HashMap<String, ToolSet>,
    pub tasks: HashMap<String, Task>,
    pub approvals: ApprovalStore,
    pub(super) review_snapshot: std::cell::RefCell<Option<super::approval::CachedReview>>,
    pub(super) interactions: crate::interactions::InteractionStore,
    pub(super) native_browser: Option<super::DesktopBrowser>,
    pub(super) process_resources: crate::process_runtime::ProcessResources,
    pub(super) mcp_clients: HashMap<(String, String), Arc<crate::mcp::Runtime>>,
    pub(super) draft_categories: HashMap<String, Value>,
    pub(super) cleared_through: HashMap<(String,String),usize>,
    pub(super) compression_requests: HashMap<String,VecDeque<std::time::Instant>>,
    pub(super) title_catalog: Option<crate::catalog::CatalogSource>,
    pub(super) pending_titles: std::collections::HashSet<(String, String)>,
    pub shutting_down: bool,
}

pub(super) struct TaskStart {
    pub id: String,
    pub created_at: f64,
    pub message: String,
    pub model_key: Option<String>,
    pub message_source: Option<String>,
}

#[derive(Serialize)]
struct Event {
    idx: u64,
    #[serde(rename = "type")]
    kind: String,
    data: Value,
    ts: f64,
}

#[derive(Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Status {
    Running,
    Succeeded,
    Failed,
    Stopped,
}

pub(super) struct Task {
    pub start: TaskStart,
    pub(super) version_message_id: Option<String>,
    pub(super) kind: &'static str,
    pub(super) max_iterations: Option<u64>,
    pub(super) auto_compression: bool,
    pub(super) compression_pending: bool,
    pub(super) closing_turn: bool,
    pub(super) compression_stage: Option<&'static str>,
    pub conversation_id: String,
    pub workspace_id: String,
    pub updated_at: f64,
    pub status: Status,
    pub error: Option<String>,
    pub guidance_closed: bool,
    pub guidance_inherited: usize,
    pub guidance_count: usize,
    pub runtime_queue: Value,
    events: VecDeque<Event>,
    next_offset: u64,
    committed_event_offset: u64,
    api_request_pending: bool,
    stream: Option<LiveText>,
    thinking_stream: Option<LiveText>,
    tool_batches: Vec<ring::digest::Digest>,
    monitor_execution_ids: HashMap<String, String>,
    pub cancel_requested: bool,
    pub cancel: Option<oneshot::Sender<()>>,
    pub approval: Option<TaskApprovalSettings>,
    pub(super) pending_host_modes: bool,
    pub execution_root: Option<PathBuf>,
    pub goal_run: Option<GoalRun>,
    pub goal_progress: Value,
    pub(super) crosspet: Option<crate::crosspet::CrossPetEvents>,
    pub completion: Option<Arc<TaskCompletion>>,
}

#[derive(Serialize)]
struct LiveText {
    start_idx: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    response_message_id: Option<String>,
    timestamp: f64,
    content: String,
    streaming: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    summary: Option<Value>,
}

#[derive(Default, Clone, Serialize, Deserialize)]
pub(crate) struct TokenStats {
    pub total_input_tokens: Option<u64>,
    pub total_output_tokens: Option<u64>,
    pub total_cached_input_tokens: Option<u64>,
    pub cache_exempt_input_tokens: Option<u64>,
    pub current_context_tokens: Option<u64>,
}

#[derive(Serialize, Deserialize)]
struct IndexRepair {
    workspace_id: String,
    conversation_id: String,
}

impl Store {
    pub fn open(data_dir: &Path) -> Result<Self, RuntimeError> {
        fs::create_dir_all(data_dir).map_err(RuntimeError::storage)?;
        let data_dir = fs::canonicalize(data_dir).map_err(RuntimeError::storage)?;
        let runtime_dir = data_dir.join("runtime");
        create_private_dir(&runtime_dir)?;
        let lock_path = runtime_dir.join("writer.lock");
        regular_file_exists(&lock_path)?;
        let lock = private_file_options()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(lock_path)
            .map_err(RuntimeError::storage)?;
        fs2::FileExt::try_lock_exclusive(&lock).map_err(|error| {
            if error.kind() == io::ErrorKind::WouldBlock
                || error.raw_os_error() == fs2::lock_contended_error().raw_os_error()
            {
                RuntimeError::new(409, "Another runtime is using this data directory")
            } else {
                RuntimeError::storage(error)
            }
        })?;
        let store = Self {
            data_dir,
            runtime_dir,
            _lock: lock,
            crosspet: None,
            search_sentences: HashMap::new(),
            workspaces: HashMap::new(),
            workspace_labels: HashMap::new(),
            workspace_tools: HashMap::new(),
            tasks: HashMap::new(),
            approvals: ApprovalStore::default(),
            review_snapshot: Default::default(),
            interactions: Default::default(),
            native_browser: None,
            process_resources: Default::default(),
            mcp_clients: HashMap::new(),
            draft_categories: HashMap::new(),
            cleared_through: HashMap::new(),
            compression_requests: Default::default(),
            title_catalog: None,
            pending_titles: Default::default(),
            shutting_down: false,
        };
        // Legacy files remain read-only until an explicit migration.
        Ok(store)
    }

    fn workspace_dir(&self, workspace_id: &str) -> Result<PathBuf, RuntimeError> {
        validate_workspace(workspace_id)?;
        let root = self.data_dir.join("conversations");
        require_directory_if_present(&root)?;
        let dir = root.join(workspace_id);
        require_directory_if_present(&dir)?;
        Ok(dir)
    }

    pub fn conversation_path(&self, workspace_id: &str, id: &str) -> Result<PathBuf, RuntimeError> {
        if !id.strip_prefix("conv_").is_some_and(valid_component) {
            return Err(RuntimeError::bad_request("Invalid conversation ID"));
        }
        Ok(self.workspace_dir(workspace_id)?.join(format!("{id}.json")))
    }

    pub fn conversation_id_exists(&self,id:&str)->Result<bool,RuntimeError>{
        let root=self.data_dir.join("conversations");
        require_directory_if_present(&root)?;
        if !root.exists(){return Ok(false);}
        for entry in fs::read_dir(root).map_err(RuntimeError::storage)?{
            let entry=entry.map_err(RuntimeError::storage)?;
            let name=entry.file_name();
            if let Some(ws)=name.to_str() && validate_workspace(ws).is_ok() && entry.file_type().map_err(RuntimeError::storage)?.is_dir() && self.has_conversation(ws,id)? {return Ok(true);}
        }
        Ok(false)
    }

    pub(super) fn has_conversation(&self,workspace:&str,id:&str)->Result<bool,RuntimeError>{
        let path=self.conversation_path(workspace,id)?;
        let dir=path.parent().unwrap();
        if dir.join(crate::conversation_db::FILENAME).exists(){
            let db=crate::conversation_db::Database::open(dir).map_err(RuntimeError::storage)?;
            if db.contains(id).map_err(RuntimeError::storage)?{return Ok(true);}
            if db.imported(id).map_err(RuntimeError::storage)?{return Ok(false);}
        }
        regular_file_exists(&path)
    }

    pub fn read(&self, workspace_id:&str,id:&str)->Result<LegacyDocument,RuntimeError>{
        let path=self.conversation_path(workspace_id,id)?;
        let dir=path.parent().unwrap();
        if dir.join(crate::conversation_db::FILENAME).exists(){
            let db=crate::conversation_db::Database::open(dir).map_err(RuntimeError::storage)?;
            if let Some(raw)=db.read(id).map_err(RuntimeError::storage)? {
                return LegacyDocument::from_value(raw).map_err(legacy_error);
            }
            if db.imported(id).map_err(RuntimeError::storage)?{return Err(RuntimeError::session_not_found());}
        }
        if regular_file_exists(&path)?{return Err(RuntimeError::new(409,"不兼容的对话记录"));}
        Err(RuntimeError::session_not_found())
    }

    pub(super) fn header(&self, workspace_id: &str, id: &str) -> Result<(Value,usize),RuntimeError> {
        let path = self.conversation_path(workspace_id, id)?;
        let dir = path.parent().unwrap();
        if dir.join(crate::conversation_db::FILENAME).exists() {
            let db = crate::conversation_db::Database::open(dir).map_err(RuntimeError::storage)?;
            if let Some(header) = db.header(id).map_err(RuntimeError::storage)? { return Ok(header); }
            if db.imported(id).map_err(RuntimeError::storage)? { return Err(RuntimeError::session_not_found()); }
        }
        if regular_file_exists(&path)? { return Err(RuntimeError::new(409, crate::conversation_db::MIGRATION_REQUIRED)); }
        Err(RuntimeError::session_not_found())
    }

    /// Update a dialogue setting without reloading or rewriting message bodies.
    pub(super) fn update_agent_metadata<T>(
        &self, workspace_id: &str, id: &str, update: impl FnOnce(&mut Value) -> T,
    ) -> Result<T, RuntimeError> {
        let path = self.conversation_path(workspace_id, id)?;
        let directory = path.parent().ok_or_else(|| RuntimeError::storage("Invalid conversation path"))?;
        let mut db = crate::conversation_db::Database::open(directory).map_err(RuntimeError::storage)?;
        let mut result = None;
        db.append(id, Vec::new(), |mut header, _, _, _, summary| {
            result = Some(update(&mut header["metadata"]));
            Ok((header, summary))
        }).map_err(RuntimeError::storage)?;
        result.ok_or_else(|| RuntimeError::storage("Conversation metadata update was not committed"))
    }

    pub(super) fn has_user_message_id(&self, workspace: &str, conversation: &str, id: &str) -> Result<bool, RuntimeError> {
        let path = self.conversation_path(workspace, conversation)?;
        let directory = path.parent().ok_or_else(|| RuntimeError::storage("Invalid conversation path"))?;
        crate::conversation_db::Database::open(directory).map_err(RuntimeError::storage)?
            .has_user_message_id(conversation, id).map_err(RuntimeError::storage)
    }

    pub fn session_index(&self,workspace_id:&str,id:&str)->Result<Option<Value>,RuntimeError>{
        let path=self.conversation_path(workspace_id,id)?;
        let dir=path.parent().unwrap();
        if dir.join(crate::conversation_db::FILENAME).exists(){
            let db=crate::conversation_db::Database::open(dir).map_err(RuntimeError::storage)?;
            if let Some(row)=db.summary(id).map_err(RuntimeError::storage)?{return Ok(Some(row));}
            if db.imported(id).map_err(RuntimeError::storage)?{return Ok(None);}
        }
        if !regular_file_exists(&path)?{return Ok(None);}
        let index=read_index(&dir.join("index.json")).unwrap_or_else(|_|json!({}));
        Ok(Some(legacy_row(id,&index)))
    }

    pub fn session_rows(&self,workspace_id:&str)->Result<Vec<Value>,RuntimeError>{
        let dir=self.workspace_dir(workspace_id)?;
        if !dir.exists(){return Ok(vec![]);}
        let db=if dir.join(crate::conversation_db::FILENAME).exists(){Some(crate::conversation_db::Database::open(&dir).map_err(RuntimeError::storage)?)}else{None};
        let mut rows=match &db{Some(db)=>db.rows().map_err(RuntimeError::storage)?,None=>vec![]};
        let known=match &db {Some(db)=>db.known_ids().map_err(RuntimeError::storage)?,None=>Default::default()};
        let mut legacy_index=None;
        for entry in fs::read_dir(&dir).map_err(RuntimeError::storage)?{
            let entry=entry.map_err(RuntimeError::storage)?;
            if !entry.file_type().map_err(RuntimeError::storage)?.is_file(){continue;}
            let name=entry.file_name();
            let Some(id)=name.to_str().and_then(|n|n.strip_suffix(".json")).filter(|id|id.strip_prefix("conv_").is_some_and(valid_component)) else{continue;};
            if known.contains(id){continue;}
            let index=legacy_index.get_or_insert_with(||read_index(&dir.join("index.json")).unwrap_or_else(|_|json!({})));
            rows.push(legacy_row(id,index));
        }
        Ok(rows)
    }

    pub fn persist(&self,workspace_id:&str,document:LegacyDocument,create:bool)->Result<(),RuntimeError>{
        let id=document.source_id();
        let path=self.conversation_path(workspace_id,id)?;
        let mut db=crate::conversation_db::Database::open(path.parent().unwrap()).map_err(RuntimeError::storage)?;
        let prior=db.summary(id).map_err(RuntimeError::storage)?;
        if prior.is_none() && regular_file_exists(&path)? && !db.imported(id).map_err(RuntimeError::storage)? {
            return Err(RuntimeError::new(409,"不兼容的对话记录"));
        }
        if create==prior.is_some(){return Err(if create{RuntimeError::new(409,"Conversation already exists")}else{RuntimeError::session_not_found()});}
        db.save(document.raw(),&index_row(document.raw(),prior.as_ref())).map_err(RuntimeError::storage)
    }

    pub(super) fn delete_conversation(&self,workspace:&str,id:&str)->Result<(),RuntimeError>{
        let path=self.conversation_path(workspace,id)?;
        let mut db=crate::conversation_db::Database::open(path.parent().unwrap()).map_err(RuntimeError::storage)?;
        if db.delete(id).map_err(RuntimeError::storage)?{Ok(())}else{Err(RuntimeError::session_not_found())}
    }

    /// During admission the runtime owns the writer gate. Only header fields and the new suffix changed.
    pub(super) fn persist_admission(&self, workspace:&str, document:LegacyDocument, prior_count:usize)->Result<(),RuntimeError>{
        let id = document.source_id().to_owned();
        let path = self.conversation_path(workspace,&id)?;
        let mut db = crate::conversation_db::Database::open(path.parent().unwrap()).map_err(RuntimeError::storage)?;
        let mut raw = document.into_raw();
        let mut rows = raw["messages"].take();
        let suffix = rows.as_array_mut().expect("validated messages").split_off(prior_count);
        drop(rows);
        db.append(&id,suffix,move |_,batch,count,_,prior|{
            if count != prior_count { return Err("Conversation changed during admission".to_owned()); }
            raw["messages"] = Value::Array(batch);
            let summary = index_row(&raw,Some(&prior));
            Ok((raw,summary))
        }).map_err(RuntimeError::storage)
    }

    pub fn append_messages(&self,workspace_id:&str,id:&str,messages:Vec<Value>,ts:f64,usage:Option<&TokenStats>,model_key:Option<&str>)->Result<(),RuntimeError>{
        self.append_with_metadata(workspace_id,id,messages,ts,usage,model_key,|_|())
    }

    pub(super) fn append_with_metadata(&self,workspace_id:&str,id:&str,messages:Vec<Value>,ts:f64,usage:Option<&TokenStats>,model_key:Option<&str>,update:impl FnOnce(&mut Value))->Result<(),RuntimeError>{
        let path=self.conversation_path(workspace_id,id)?;
        let mut db=crate::conversation_db::Database::open(path.parent().unwrap()).map_err(RuntimeError::storage)?;
        db.append(id,messages,|header,batch,count,calls,prior|{
            let title=header.get("title").cloned();
            let doc=LegacyDocument::from_value(header).map_err(|e|e.message)?;
            let mut saved=append_document(doc,batch,ts,usage,model_key).map_err(|e|e.message)?.into_raw();
            if count!=0 {
                if let Some(title)=title {saved["title"]=title;}else{saved.as_object_mut().unwrap().remove("title");}
            }
            if !saved["messages"].as_array().unwrap().is_empty(){
                saved["metadata"]["total_messages"]=json!(count+saved["messages"].as_array().unwrap().len());
                saved["metadata"]["total_tools"]=json!(calls+saved["metadata"]["total_tools"].as_u64().unwrap_or(0) as usize);
            }
            update(&mut saved["metadata"]);
            let row=index_row(&saved,Some(&prior));Ok((saved,row))
        }).map_err(RuntimeError::storage)
    }

    pub fn commit_tool_batch(
        &mut self,
        id: &str,
        sequence: usize,
        messages: Vec<Value>,
    ) -> Result<Vec<Value>, RuntimeError> {
        let task = self
            .tasks
            .get(id)
            .ok_or_else(RuntimeError::task_not_found)?;
        let invalid = || RuntimeError::storage("invalid tool batch checkpoint");
        let index = sequence.checked_sub(1).ok_or_else(invalid)?;
        if let Some(previous) = task.tool_batches.get(index) {
            return if previous.as_ref() == checkpoint_hash(messages).as_ref() {
                Ok(Vec::new())
            } else {
                Err(invalid())
            };
        }
        if index != task.tool_batches.len()
            || task.cancel_requested
            || task.status != Status::Running
        {
            return Err(invalid());
        }
        validate_tool_batch(&messages)?;
        let ts = now();
        let guidance = if task.guidance_count == 0 {
            self.append_messages(&task.workspace_id, &task.conversation_id, messages.clone(), ts, None, None)?;
            super::guidance::Consumed::default()
        } else {
            super::guidance::consume(self, task, &messages, ts)?
        };
        let task = self.tasks.get_mut(id).expect("existing task");
        task.emit_tool_batch(&messages, ts);
        task.tool_batches.push(checkpoint_hash(messages));
        let injected = guidance.publish(task);
        task.checkpoint_events();
        Ok(injected)
    }

    pub fn finish(
        &mut self,
        id: &str,
        result: Result<Vec<Value>, String>,
    ) -> Result<(), RuntimeError> {
        self.finish_with_activity(id, result, json!({}))
    }

    pub(super) fn finish_with_activity(
        &mut self, id: &str, result: Result<Vec<Value>, String>, activity: Value,
    ) -> Result<(), RuntimeError> {
        let task = self
            .tasks
            .get(id)
            .ok_or_else(RuntimeError::task_not_found)?;
        if task.status != Status::Running {
            return Ok(());
        }
        let ts = now();
        let (status, error, kind, mut data, messages) = if task.cancel_requested {
            (
                Status::Stopped,
                None,
                "task_stopped",
                json!({"message":"Task stopped"}),
                Vec::new(),
            )
        } else {
            match result {
                Ok(messages) => (Status::Succeeded, None, "task_complete", json!({}), messages),
                Err(error) => {
                    let data = json!({"message":error,"retry":false});
                    (Status::Failed, Some(error), "error", data, Vec::new())
                }
            }
        };
        if let (Some(data), Value::Object(activity)) = (data.as_object_mut(), activity) {
            data.extend(activity);
        }
        let queue = super::guidance::finish(self, task, status != Status::Succeeded, messages, ts);
        if self.review_snapshot.get_mut().as_ref().is_some_and(|cached|cached.task_id==id) {
            self.review_snapshot.get_mut().take();
        }
        super::interactions::expire_task(self, id);
        if let Err(error) = super::host_modes::apply_pending_to_task(self, id, false) {
            self.tasks.get_mut(id).expect("existing task").emit("runtime_mode_error", json!({"message":error.message}));
        }
        let task = self.tasks.get_mut(id).expect("existing task");
        task.guidance_closed = true;
        task.guidance_count = 0;
        let result = match queue {
            Ok(queue) => {
                task.runtime_queue = queue;
                task.terminal(status, error, kind, data, ts);
                Ok(())
            }
            Err(storage) => {
                // The previous document remains recoverable through the idle snapshot or admission.
                let data = json!({"message":storage.message,"retry":false});
                task.terminal(Status::Failed, Some(storage.message.clone()), "error", data, ts);
                Err(storage)
            }
        };
        if result.is_ok() && let Err(error) = super::versioning::record_finished(self, id) {
            self.tasks.get_mut(id).expect("finished task").emit("versioning_error", json!({"message":error.message}));
        }
        self.approvals.expire_task(id);
        result
    }
}

pub(super) fn append_document(
    mut document: LegacyDocument,
    messages: Vec<Value>,
    ts: f64,
    usage: Option<&TokenStats>,
    model_key: Option<&str>,
) -> Result<LegacyDocument, RuntimeError> {
    let loaded_tools = super::tool_loading::checkpoint(document.raw(), &messages);
    let knowledge_reads = super::knowledge_tools::checkpoint(&messages).cloned();
    let acknowledged_worktree = super::worktree::complete_notice(document.raw(), &messages);
    let was_empty = document.raw()["messages"]
        .as_array()
        .is_some_and(Vec::is_empty);
    let title = if was_empty && document.raw()["metadata"]["title_locked"] != true {
        messages
            .first()
            .filter(|message| message["role"] == "user")
            .and_then(|message| message["content"].as_str())
            .map(|text| {
                text.lines()
                    .next()
                    .unwrap_or_default()
                    .chars()
                    .take(80)
                    .collect::<String>()
            })
    } else {
        None
    };
    let usage = usage.map(|stats| {
        let mut value = json!(stats);
        value
            .as_object_mut()
            .expect("token statistics object")
            .retain(|_, value| !value.is_null());
        value
    });
    document
        .append_agent_messages(messages, ts, usage.as_ref())
        .map_err(legacy_error)?;
    if title.is_none() && model_key.is_none() && loaded_tools.is_none() && knowledge_reads.is_none() && !acknowledged_worktree {
        return Ok(document);
    }
    let mut raw = document.into_raw();
    if acknowledged_worktree && let Some(meta) = raw["metadata"].as_object_mut() {
        meta.shift_remove("git_worktree_notice");
    }
    if let Some(reads) = knowledge_reads {
        super::knowledge_tools::persist(&mut raw, reads);
    }
    if let Some(state) = loaded_tools {
        raw["metadata"]["tool_loading"] = state;
    }
    if let Some(title) = title {
        raw["title"] = json!(title);
    }
    if let Some(model_key) = model_key {
        raw["metadata"]["model_key"] = json!(model_key);
    }
    LegacyDocument::from_value(raw).map_err(legacy_error)
}

pub(super) fn legacy_error(error: LegacyError) -> RuntimeError {
    RuntimeError::new(error.status, error.message)
}

/// Retain one digest per committed batch. Sorting the private copy preserves JSON object equality on retries.
fn checkpoint_hash(mut messages:Vec<Value>) -> ring::digest::Digest {
    struct Sink(ring::digest::Context);
    impl std::io::Write for Sink {
        fn write(&mut self, bytes:&[u8]) -> std::io::Result<usize> { self.0.update(bytes); Ok(bytes.len()) }
        fn flush(&mut self)->std::io::Result<()> {Ok(())}
    }
    for message in &mut messages {message.sort_all_objects();}
    let mut sink=Sink(ring::digest::Context::new(&ring::digest::SHA256));
    serde_json::to_writer(&mut sink,&messages).expect("JSON values serialize into an infallible digest");
    sink.0.finish()
}

fn validate_tool_batch(messages: &[Value]) -> Result<(), RuntimeError> {
    let invalid = || RuntimeError::storage("invalid tool batch checkpoint");
    let (assistant, results) = messages.split_first().ok_or_else(invalid)?;
    let calls = assistant["tool_calls"].as_array().ok_or_else(invalid)?;
    if assistant["role"] != "assistant" || calls.is_empty() || calls.len() != results.len() {
        return Err(invalid());
    }
    let mut ids = HashSet::new();
    for (call, result) in calls.iter().zip(results) {
        let id = call["id"]
            .as_str()
            .filter(|id| !id.is_empty())
            .ok_or_else(invalid)?;
        if !ids.insert(id)
            || result["role"] != "tool"
            || result["tool_call_id"] != id
            || !result["content"].is_string()
        {
            return Err(invalid());
        }
    }
    Ok(())
}

impl Task {
    pub fn new(
        start: TaskStart,
        workspace_id: &str,
        conversation_id: String,
        cancel: oneshot::Sender<()>,
    ) -> Self {
        Self {
            updated_at: start.created_at,
            version_message_id: None,
            kind: "chat",
            max_iterations: None,
            auto_compression: false,
            compression_pending: false,
            closing_turn: false,
            compression_stage: None,
            start,
            workspace_id: workspace_id.into(),
            conversation_id,
            status: Status::Running,
            error: None,
            guidance_closed: false,
            guidance_inherited: 0,
            guidance_count: 0,
            runtime_queue: json!({"paused":false,"messages":[]}),
            events: VecDeque::new(),
            next_offset: 0,
            committed_event_offset: 0,
            api_request_pending: false,
            stream: None,
            thinking_stream: None,
            tool_batches: Vec::new(),
            monitor_execution_ids: HashMap::new(),
            cancel_requested: false,
            cancel: Some(cancel),
            approval: None,
            pending_host_modes: false,
            execution_root: None,
            goal_run: None,
            goal_progress: Value::Null,
            crosspet: None,
            completion: None,
        }
    }

    fn emit_tool_batch(&mut self, messages: &[Value], ts: f64) {
        let calls = messages[0]["tool_calls"]
            .as_array()
            .expect("validated tool batch");
        for (call, result) in calls.iter().zip(&messages[1..]) {
            let id = &call["id"];
            let function = &call["function"];
            let result = result["content"].as_str().unwrap_or_default();
            let result = if function["name"] == "astrion_echo" {
                json!(result)
            } else {
                serde_json::from_str::<Value>(result).unwrap_or_else(|_| json!(result))
            };
            let status = if result["success"] == false {
                "failed"
            } else {
                "completed"
            };
            let mut update = json!({"id":id,"preparing_id":id,"status":status,"result":result});
            if let Some(execution_id) = id.as_str().and_then(|id| self.monitor_execution_ids.remove(id)) {
                update["execution_id"] = json!(execution_id);
            }
            self.emit_at(
                "update_action",
                update,
                ts,
            );
        }
    }

    pub fn checkpoint_events(&mut self) {
        self.committed_event_offset = self.next_offset;
        self.stream = None;
        self.thinking_stream = None;
    }

    pub fn terminal(&mut self, status: Status, error: Option<String>, kind: &str, data: Value, ts: f64) {
        self.emit_at(kind, data, ts);
        self.status = status;
        self.crosspet_finished();
        self.error = error;
        self.cancel = None;
        self.approval = None;
        self.stream = None;
        self.thinking_stream = None;
    }

    pub fn emit(&mut self, kind: &str, data: Value) {
        self.emit_at(kind, data, now());
    }

    fn emit_at(&mut self, kind: &str, mut data: Value, ts: f64) {
        if kind == "tool_start"
            && let (Some(call), Some(execution)) = (data["id"].as_str(), data["execution_id"].as_str())
        {
            self.monitor_execution_ids.insert(call.to_owned(), execution.to_owned());
        }
        self.crosspet_event(kind, &data);
        if matches!(kind, "goal_progress" | "goal_completed" | "goal_stopped") {
            self.goal_progress = data.clone();
        }
        match kind {
            "api_request_start" => {
                self.api_request_pending = true;
                self.thinking_stream = None;
                self.stream = None;
            }
            "thinking_start" | "text_start" => {
                self.api_request_pending = false;
                let stream = if kind == "thinking_start" { &mut self.thinking_stream } else { &mut self.stream };
                *stream = Some(LiveText {
                    start_idx:self.next_offset, timestamp:ts, content:String::new(), streaming:true,
                    response_message_id:data["response_message_id"].as_str().map(str::to_owned),
                    summary: None,
                });
            }
            "thinking_chunk" | "text_chunk" => {
                self.api_request_pending = false;
                let stream = if kind == "thinking_chunk" { &mut self.thinking_stream } else { &mut self.stream };
                if let Some(text) = stream {
                    text.content.push_str(data["content"].as_str().unwrap_or_default());
                }
            }
            "thinking_end" | "text_end" => {
                let stream = if kind == "thinking_end" { &mut self.thinking_stream } else { &mut self.stream };
                if let Some(text) = stream {
                    if let Some(content) = data["full_content"].as_str() { text.content = content.into(); }
                    text.streaming = false;
                }
            }
            "thinking_summary" => {
                if let Some(thinking) = &mut self.thinking_stream
                    && thinking.response_message_id.as_deref() == data["response_message_id"].as_str()
                {
                    thinking.summary = Some(data.clone());
                }
            }
            "stream_reset" => { self.stream = None; self.thinking_stream = None; }
            "tool_start" | "error" | "task_complete" | "task_stopped" => self.api_request_pending = false,
            _ => {}
        }
        data["task_id"] = json!(self.start.id);
        data["task_type"] = json!(self.kind);
        data["conversation_id"] = json!(self.conversation_id);
        data["workspace_id"] = json!(self.workspace_id);
        if self.events.len() == EVENT_WINDOW {
            self.events.pop_front();
        }
        self.events.push_back(Event {
            idx: self.next_offset,
            kind: kind.into(),
            data,
            ts,
        });
        self.next_offset += 1;
        self.updated_at = ts;
    }

    pub fn poll(&self, from: u64) -> Value {
        if self.status != Status::Running
            && let Some(completion) = &self.completion
        {
            completion.wait();
        }
        json!({"success":true,"data":self.activity(from)})
    }

    pub fn activity(&self, from: u64) -> Value {
        let window_start = self
            .events
            .front()
            .map_or(self.next_offset, |event| event.idx);
        let skip = from.clamp(window_start, self.next_offset) - window_start;
        let events: Vec<_> = self.events.iter().skip(skip as usize).collect();
        json!({
            "task_id":self.start.id,"workspace_id":self.workspace_id,"status":self.status,
            "created_at":self.start.created_at,"updated_at":self.updated_at,"message":self.start.message,
            "conversation_id":self.conversation_id,"error":self.error,"message_source":self.start.message_source,
            "task_type":self.kind,"goal_mode":self.goal_run.is_some(),"goal_progress":self.goal_progress,"events":events,
            "next_offset":self.next_offset,"window_start":window_start,
            "runtime_queue_paused":self.runtime_queue["paused"],"runtime_queued_messages":self.runtime_queue["messages"],
            "committed_event_offset":self.committed_event_offset,"api_request_pending":self.api_request_pending,
            "cancel_requested":self.cancel_requested,"stream":self.stream,"thinking_stream":self.thinking_stream
        })
    }
}

impl TokenStats {
    pub fn from_document(value: &Value) -> Self {
        Self {
            total_input_tokens: value["total_input_tokens"].as_u64(),
            total_output_tokens: value["total_output_tokens"].as_u64(),
            total_cached_input_tokens: value["total_cached_input_tokens"].as_u64(),
            cache_exempt_input_tokens: value["cache_exempt_input_tokens"].as_u64(),
            current_context_tokens: value["current_context_tokens"].as_u64(),
        }
    }

    pub fn record(&mut self, usage: &Value) -> bool {
        let prompt = usage["prompt_tokens"].as_u64();
        let completion = usage["completion_tokens"].as_u64();
        let cached = usage["prompt_tokens_details"]["cached_tokens"]
            .as_u64()
            .or_else(|| usage["cached_input_tokens"].as_u64());
        let context = usage["current_context_tokens"].as_u64().or(prompt);
        let exempt = usage["cache_exempt_input_tokens"].as_u64();
        add_usage(&mut self.total_input_tokens, prompt);
        add_usage(&mut self.total_output_tokens, completion);
        add_usage(&mut self.total_cached_input_tokens, cached);
        add_usage(&mut self.cache_exempt_input_tokens, exempt);
        self.current_context_tokens = context.or(self.current_context_tokens);
        prompt.is_some()
            || completion.is_some()
            || cached.is_some()
            || context.is_some()
            || exempt.is_some()
    }

    pub fn event_data(&self) -> Value {
        json!({"cumulative_input_tokens":self.total_input_tokens,"cumulative_output_tokens":self.total_output_tokens,
            "cumulative_cached_input_tokens":self.total_cached_input_tokens,"cache_exempt_input_tokens":self.cache_exempt_input_tokens,
            "current_context_tokens":self.current_context_tokens})
    }
}

fn add_usage(total: &mut Option<u64>, amount: Option<u64>) {
    if let Some(value) = amount {
        *total = Some(total.unwrap_or_default().saturating_add(value));
    }
}

fn legacy_row(id:&str,index:&Value)->Value {
    let mut row=index.get(id).filter(|v|v.is_object()).cloned().unwrap_or_else(||json!({"title":id}));
    row["id"]=json!(id);row["incompatible"]=json!(true);row
}

pub(crate) fn index_row(document: &Value, prior: Option<&Value>) -> Value {
    let mut row = prior
        .filter(|value| value.is_object())
        .cloned()
        .unwrap_or_else(|| json!({}));
    for key in ["title", "created_at", "updated_at"] {
        row[key] = document[key].clone();
    }
    let meta = &document["metadata"];
    for key in [
        "project_path",
        "project_relative_path",
        "model_key",
        "custom_prompt_name",
        "personalization_name",
    ] {
        row[key] = meta[key].clone();
    }
    for (key, default) in [
        ("thinking_mode", json!(false)),
        (
            "run_mode",
            json!(if meta["thinking_mode"] == true {
                "thinking"
            } else {
                "fast"
            }),
        ),
        ("has_images", json!(false)),
        ("has_videos", json!(false)),
        ("total_messages", json!(0)),
        ("total_tools", json!(0)),
        ("status", json!("active")),
        ("quick_entry", json!(false)),
    ] {
        row[key] = meta
            .get(key)
            .filter(|value| !value.is_null())
            .cloned()
            .unwrap_or(default);
    }
    row
}

pub(crate) fn read_index(path: &Path) -> Result<Value, RuntimeError> {
    let index = read_json(path)?.unwrap_or_else(|| json!({}));
    if !index.is_object() {
        return Err(RuntimeError::storage("conversation index is not an object"));
    }
    Ok(index)
}

pub(super) fn read_json(path: &Path) -> Result<Option<Value>, RuntimeError> {
    if !regular_file_exists(path)? {
        return Ok(None);
    }
    let file = File::open(path).map_err(RuntimeError::storage)?;
    serde_json::from_reader(io::BufReader::new(file))
        .map(Some)
        .map_err(RuntimeError::storage)
}

pub(super) fn write_atomically(path: &Path, document: &impl Serialize) -> Result<(), RuntimeError> {
    let directory = path.parent().expect("persistent file must have a parent");
    let temp = directory.join(format!(".astrion-{}.tmp", Uuid::new_v4()));
    let result = (|| -> io::Result<()> {
        let mut file = private_file_options()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        {
            let mut writer = io::BufWriter::new(&mut file);
            serde_json::to_writer(&mut writer, document)?;
            writer.write_all(b"\n")?;
            writer.flush()?;
        }
        file.sync_all()?;
        drop(file);
        replace_file(&temp, path)?;
        #[cfg(unix)]
        File::open(directory)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result.map_err(RuntimeError::storage)
}

fn replace_file(from: &Path, to: &Path) -> io::Result<()> {
    #[cfg(windows)]
    for delay in [10, 20, 40, 80] {
        match fs::rename(from, to) {
            Err(error) if matches!(error.raw_os_error(), Some(5 | 32 | 33)) => {
                std::thread::sleep(std::time::Duration::from_millis(delay));
            }
            result => return result,
        }
    }
    fs::rename(from, to)
}

pub(super) fn valid_component(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

pub(super) fn validate_workspace(id: &str) -> Result<(), RuntimeError> {
    if !id.is_empty() && id.len() <= 64 && !matches!(id, "." | "..")
        && id.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
    {
        Ok(())
    } else {
        Err(RuntimeError::bad_request("Invalid workspace ID"))
    }
}

pub(super) fn require_directory_if_present(path: &Path) -> Result<(), RuntimeError> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.is_dir() => Ok(()),
        Ok(_) => Err(RuntimeError::storage(
            "expected a regular conversation directory",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(RuntimeError::storage(error)),
    }
}

fn regular_file_exists(path: &Path) -> Result<bool, RuntimeError> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.is_file() => Ok(true),
        Ok(_) => Err(RuntimeError::storage("expected a regular runtime file")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(RuntimeError::storage(error)),
    }
}

pub(super) fn create_private_dir(path: &Path) -> Result<(), RuntimeError> {
    let builder = &mut DirBuilder::new();
    #[cfg(unix)]
    builder.mode(0o700);
    if let Err(error) = builder.create(path)
        && error.kind() != io::ErrorKind::AlreadyExists
    {
        return Err(RuntimeError::storage(error));
    }
    let meta = fs::symlink_metadata(path).map_err(RuntimeError::storage)?;
    if !meta.is_dir() {
        return Err(RuntimeError::storage(
            "expected a regular runtime directory",
        ));
    }
    #[cfg(unix)]
    if meta.permissions().mode() & 0o077 != 0 {
        return Err(RuntimeError::new(
            400,
            "The runtime directory must only be accessible to its owner",
        ));
    }
    Ok(())
}

fn private_file_options() -> OpenOptions {
    let options = OpenOptions::new();
    #[cfg(unix)]
    let options = {
        let mut options = options;
        options.mode(0o600);
        options
    };
    options
}
