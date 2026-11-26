use std::{
    collections::HashMap,
    io::{self, BufReader, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock, Weak},
    time::{SystemTime, UNIX_EPOCH},
};

use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
use cap_std::{ambient_authority, fs::{Dir, OpenOptions}};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use uuid::Uuid;

use super::GoalError;

const DIRECTORY: &str = "goal_states";
const STATE_VERSION: u32 = 2;

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Running,
    Reviewing,
    Retrying,
    Paused,
    Blocked,
    Done,
    #[default]
    Stopped,
}

impl Status {
    pub fn active(self) -> bool {
        matches!(self, Self::Running | Self::Reviewing | Self::Retrying)
    }

    pub fn resumable(self) -> bool {
        matches!(self, Self::Paused | Self::Blocked)
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewMode {
    #[default]
    Readonly,
    Active,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub(super) struct ReviewEntry {
    pub main_output: String,
    pub review_reply: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub(super) struct GoalState {
    pub version: u32,
    pub revision: u64,
    pub run_id: Option<String>,
    pub process_id: Option<String>,
    pub active: bool,
    pub status: Status,
    pub goal: String,
    pub review_mode: ReviewMode,
    pub max_turns: Option<u64>,
    pub max_tokens: Option<u64>,
    pub started_at: Option<f64>,
    pub updated_at: Option<f64>,
    pub turn_count: u64,
    pub tokens_used: u64,
    pub tool_calls_used: u64,
    pub review_history: Vec<ReviewEntry>,
    pub final_summary: Option<String>,
    pub stopped_reason: Option<String>,
    pub last_message: String,
    pub retry_count: u64,
    pub next_retry_at: Option<f64>,
    pub task_id: Option<String>,
    pub no_progress_count: u64,
    pub last_review_signature: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub turn_baseline: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token_limit_baseline: Option<u64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub usage_request_ids: Vec<String>,
    #[serde(flatten)]
    pub(super) extras: Map<String, Value>,
}

impl Default for GoalState {
    fn default() -> Self {
        Self {
            version: STATE_VERSION, revision: 0, run_id: None, process_id: None,
            active: false, status: Status::Stopped, goal: String::new(), review_mode: ReviewMode::Readonly,
            max_turns: None, max_tokens: None, started_at: None, updated_at: None,
            turn_count: 0, tokens_used: 0, tool_calls_used: 0, review_history: Vec::new(),
            final_summary: None, stopped_reason: None, last_message: String::new(), retry_count: 0,
            next_retry_at: None, task_id: None, no_progress_count: 0, last_review_signature: String::new(),
            turn_baseline: None, token_limit_baseline: None, usage_request_ids: Vec::new(), extras: Map::new(),
        }
    }
}

impl GoalState {
    pub fn unreadable() -> Self {
        Self {
            status: Status::Blocked,
            stopped_reason: Some("state_unreadable".into()),
            last_message: "目标状态文件无法读取，请检查文件后重新开始。".into(),
            ..Self::default()
        }
    }

    pub fn progress(&self, conversation_id: &str) -> Value {
        if self.goal.is_empty() && self.status != Status::Blocked {
            return json!({});
        }
        let mut value = json!(self);
        let fields = value.as_object_mut().expect("goal state is an object");
        fields.shift_remove("usage_request_ids");
        fields.shift_remove("review_history");
        let started = self.started_at.unwrap_or_else(now);
        fields.insert("conversation_id".into(), json!(conversation_id));
        fields.insert("duration_seconds".into(), json!((now() - started).max(0.0)));
        fields.insert("tool_calls".into(), json!(self.tool_calls_used));
        value
    }

    pub fn stop(&mut self, status: Status, reason: &str, message: String) {
        self.status = status;
        self.stopped_reason = Some(reason.into());
        self.last_message = message;
        self.next_retry_at = None;
    }

    pub fn limit_reached(&self) -> Option<&'static str> {
        if self.max_turns.is_some_and(|limit| self.turn_count.saturating_sub(self.turn_baseline.unwrap_or_default()) >= limit) {
            Some("max_turns")
        } else if self.max_tokens.is_some_and(|limit| self.tokens_used.saturating_sub(self.token_limit_baseline.unwrap_or_default()) >= limit) {
            Some("max_tokens")
        } else {
            None
        }
    }
}

#[derive(Clone)]
pub(super) struct GoalStore {
    data_dir: Arc<PathBuf>,
}

type StateLocks = Mutex<HashMap<PathBuf, Weak<Mutex<()>>>>;
static LOCKS: OnceLock<StateLocks> = OnceLock::new();
static PROCESS_ID: OnceLock<String> = OnceLock::new();

impl GoalStore {
    pub fn new(path: impl AsRef<Path>) -> Self {
        let path = path.as_ref();
        // Runtime supplies its canonical, server-owned Host data root.
        let path = path.canonicalize().or_else(|_| std::path::absolute(path)).unwrap_or_else(|_| path.to_owned());
        Self { data_dir: Arc::new(path) }
    }

    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    pub fn personalization(&self) -> Value {
        std::fs::read(self.data_dir.join("personalization.json"))
            .ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default()
    }

    pub fn transaction<T, E: From<GoalError>>(
        &self,
        conversation_id: &str,
        operation: impl FnOnce(&mut Transaction<'_>) -> Result<T, E>,
    ) -> Result<T, E> {
        let name = filename(conversation_id)?;
        let lock = path_lock(self.data_dir.join(DIRECTORY).join(&name))?;
        let _guard = lock.lock().map_err(GoalError::storage)?;
        let state = self.read(&name)?;
        let mut txn = Transaction { store: self, name, state };
        if txn.state.status.active() && txn.state.process_id.as_deref() != Some(process_id()) {
            let mut next = txn.state.clone();
            next.run_id.get_or_insert_with(new_id);
            next.process_id = Some(process_id().into());
            next.task_id = None;
            next.stop(Status::Paused, "process_restarted", "应用已重启，点击继续以恢复目标。".into());
            txn.replace(next)?;
        }
        operation(&mut txn)
    }

    fn directory(&self, create: bool) -> Result<Option<Dir>, GoalError> {
        let data = Dir::open_ambient_dir(&*self.data_dir, ambient_authority()).map_err(GoalError::storage)?;
        match data.open_dir_nofollow(DIRECTORY) {
            Ok(dir) => Ok(Some(dir)),
            Err(error) if error.kind() == io::ErrorKind::NotFound && create => {
                match data.create_dir(DIRECTORY) {
                    Ok(()) => {}
                    Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
                    Err(error) => return Err(GoalError::storage(error)),
                }
                data.open_dir_nofollow(DIRECTORY).map(Some).map_err(GoalError::storage)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(GoalError::storage(error)),
        }
    }

    fn read(&self, name: &str) -> Result<GoalState, GoalError> {
        let Some(dir) = self.directory(false)? else { return Ok(GoalState::default()) };
        let result = (|| -> io::Result<GoalState> {
            let metadata = dir.symlink_metadata(name)?;
            if !metadata.is_file() || metadata.is_symlink() || metadata.nlink() != 1 {
                return Err(io::Error::other("goal state is not a private regular file"));
            }
            let mut options = OpenOptions::new();
            options.read(true).follow(FollowSymlinks::No);
            #[cfg(unix)]
            {
                use cap_std::fs::OpenOptionsExt;
                options.custom_flags(libc::O_NONBLOCK);
            }
            let file = dir.open_with(name, &options)?;
            let opened = file.metadata()?;
            if !opened.is_file() || opened.nlink() != 1 || opened.dev() != metadata.dev() || opened.ino() != metadata.ino() {
                return Err(io::Error::other("goal state changed during read"));
            }
            let mut raw: Value = serde_json::from_reader(BufReader::new(file))?;
            let object = raw.as_object_mut().ok_or_else(|| io::Error::other("invalid goal state"))?;
            if object.get("review_history").is_some_and(|history| !history.is_array()) {
                object.insert("review_history".into(), json!([]));
            }
            if object.get("usage_request_ids").is_some_and(Value::is_null) {
                object.shift_remove("usage_request_ids");
            }
            let mut state: GoalState = serde_json::from_value(raw)?;
            state.active = state.status.active();
            Ok(state)
        })();
        match result {
            Ok(state) => Ok(state),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(GoalState::default()),
            Err(_) => Ok(GoalState::unreadable()),
        }
    }

    fn write(&self, name: &str, state: &GoalState) -> Result<(), GoalError> {
        let dir = self.directory(true)?.expect("created goal directory");
        let temp = format!(".{name}.{}.tmp", new_id());
        let mut options = OpenOptions::new();
        options.write(true).create_new(true).follow(FollowSymlinks::No);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = dir.open_with(&temp, &options).map_err(GoalError::storage)?;
        let result = (|| -> io::Result<()> {
            serde_json::to_writer_pretty(&mut file, state)?;
            file.flush()?;
            file.sync_all()?;
            drop(file);
            dir.rename(&temp, &dir, name)
        })();
        if result.is_err() {
            let _ = dir.remove_file(&temp);
        }
        result.map_err(GoalError::storage)
    }
}

pub(super) struct Transaction<'a> {
    store: &'a GoalStore,
    name: String,
    pub state: GoalState,
}

impl Transaction<'_> {
    pub fn replace(&mut self, mut next: GoalState) -> Result<(), GoalError> {
        next.revision = self.state.revision.checked_add(1).ok_or_else(|| GoalError::storage("goal revision overflow"))?;
        next.updated_at = Some(now());
        next.active = next.status.active();
        next.version = STATE_VERSION;
        self.store.write(&self.name, &next)?;
        self.state = next;
        Ok(())
    }
}

fn path_lock(path: PathBuf) -> Result<Arc<Mutex<()>>, GoalError> {
    let mut locks = LOCKS.get_or_init(Mutex::default).lock().map_err(GoalError::storage)?;
    if let Some(lock) = locks.get(&path).and_then(Weak::upgrade) {
        return Ok(lock);
    }
    locks.retain(|_, lock| lock.strong_count() > 0);
    let lock = Arc::new(Mutex::new(()));
    locks.insert(path, Arc::downgrade(&lock));
    Ok(lock)
}

fn filename(id: &str) -> Result<String, GoalError> {
    if id.is_empty() || !id.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-')) {
        return Err(GoalError::bad_request("Invalid conversation_id"));
    }
    Ok(format!("{id}.json"))
}

pub(super) fn process_id() -> &'static str {
    PROCESS_ID.get_or_init(new_id)
}

pub(super) fn new_id() -> String {
    Uuid::new_v4().simple().to_string()
}

pub(super) fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()
}

pub(super) fn bounded_tail(input: &str, limit: usize) -> String {
    let start = input.char_indices().rev().nth(limit).map_or(0, |(index, ch)| index + ch.len_utf8());
    input[start..].to_owned()
}
