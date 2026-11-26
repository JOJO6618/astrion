mod advance;
mod text;

pub use advance::{Advance, Progress, ReviewDecision, ReviewRequest};

use super::{NodeKind, Result, Source, Workflow, WorkflowError, WorkflowRegistry, files};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::{
    collections::BTreeMap,
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard},
};

const STATE_FILE: &str = "state.json";
static STATE_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Debug)]
pub struct WorkflowStates {
    workspace_data: PathBuf,
}

#[derive(Debug)]
pub struct Activation {
    pub already: bool,
    pub prompt: String,
    pub snapshot: Value,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(default)]
struct State {
    active: bool,
    workflow_name: String,
    status: Option<String>,
    exit_reason: Option<String>,
    current_node_id: Option<String>,
    stage_rounds: u64,
    round_limit_notified: bool,
    stage_start_msg_index: usize,
    reject_counts: BTreeMap<String, u64>,
    history: Vec<Value>,
    pending_notices: Vec<Value>,
    started_at: Option<f64>,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

impl WorkflowStates {
    /// The workspace data path must come from the trusted workspace registry.
    /// The caller verifies conversation ownership before using any method here.
    pub fn new(workspace_data: impl AsRef<Path>) -> Self {
        Self {
            workspace_data: workspace_data.as_ref().to_path_buf(),
        }
    }

    pub fn status_response(&self, conversation_id: &str) -> Result<Value> {
        Ok(json!({"success": true, "snapshot": self.snapshot(conversation_id)?}))
    }

    pub fn snapshot(&self, conversation_id: &str) -> Result<Value> {
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let state = State::load(&dir)?;
        let definition = if state.is_active() {
            load_definition(&dir)?
        } else {
            None
        };
        Ok(state.snapshot(definition.as_ref()))
    }

    pub fn status_text(&self, conversation_id: &str) -> Result<String> {
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let state = State::load(&dir)?;
        if !state.is_active() {
            return Ok("当前对话没有激活的工作流。".into());
        }
        let definition = load_definition(&dir)?
            .ok_or_else(|| WorkflowError::new(400, "工作流定义快照不存在或无法解析。"))?;
        Ok(text::status(&definition, &state))
    }

    /// A runtime holding the conversation's main-task gate can activate a
    /// workflow and admit its main task. The callback only admits the task. It
    /// runs under the state lock and must not call workflow methods itself.
    pub fn activate(
        &self,
        registry: &WorkflowRegistry,
        conversation_id: &str,
        name: &str,
        message_index: usize,
        admit: impl FnOnce(&Activation) -> Result<()>,
    ) -> Result<Activation> {
        let name = name.trim();
        if name.is_empty() {
            return Err(WorkflowError::new(400, "缺少工作流名称"));
        }
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let previous = State::load(&dir)?;
        if previous.is_active() {
            if previous.workflow_name != name {
                return Err(WorkflowError::new(
                    400,
                    format!(
                        "当前对话已激活工作流「{}」，同一时间只能激活一个工作流。请先调用 deactivate_workflow 退出，再激活新的。",
                        previous.workflow_name,
                    ),
                ));
            }
            let definition = load_definition(&dir)?;
            return Ok(Activation {
                already: true,
                prompt: definition
                    .as_ref()
                    .map(|wf| text::activation(wf, &previous, true))
                    .unwrap_or_default(),
                snapshot: previous.snapshot(definition.as_ref()),
            });
        }
        let (markdown, source) = registry.markdown(name).map_err(|error| {
            if error.status == 404 {
                WorkflowError::new(400, error.message)
            } else {
                error
            }
        })?;
        let definition = Workflow::from_markdown(&markdown, source)?;
        definition.validate()?;
        let entry_node = definition.entry_node().ok_or_else(|| {
            WorkflowError::new(400, "工作流缺少有效的入口节点（开始节点未连接）。")
        })?;
        let state = State {
            active: true,
            workflow_name: definition.name.clone(),
            status: Some("active".into()),
            current_node_id: Some(entry_node.id.clone()),
            stage_start_msg_index: message_index,
            started_at: Some(files::now_epoch()),
            ..State::default()
        };
        let activation = Activation {
            already: false,
            prompt: format!(
                "工作流已激活，请立即开始按流程执行。完成当前步骤后调用 report_workflow_stage(summary) 汇报。\n\n{}",
                text::activation(&definition, &state, false)
            ),
            snapshot: state.snapshot(Some(&definition)),
        };
        fs::create_dir_all(&dir)?;
        let snapshot_path = dir.join(files::MARKDOWN_FILE);
        let state_path = dir.join(STATE_FILE);
        let old_snapshot = files::read(&snapshot_path)?;
        let old_state = files::read(&state_path)?;
        files::write_atomic(&snapshot_path, markdown.as_bytes())?;
        if let Err(error) = state.save(&dir) {
            restore_file(&snapshot_path, old_snapshot.as_deref())?;
            return Err(error);
        }
        if let Err(error) = admit(&activation) {
            restore_file(&state_path, old_state.as_deref())?;
            restore_file(&snapshot_path, old_snapshot.as_deref())?;
            return Err(error);
        }
        Ok(activation)
    }

    /// Soft exit. The runtime can drain the notice into the active task or
    /// admit an idle notice task under the main-task gate.
    pub fn deactivate_by_user(&self, conversation_id: &str) -> Result<Value> {
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let mut state = State::require_active(&dir)?;
        let name = &state.workflow_name;
        let message = format!(
            "用户已退出工作流「{name}」。工作流已摘牌，无需继续按流程推进；你可以继续自由工作。若用户之后要求恢复，可重新激活。"
        );
        state.pending_notices.push(json!({
            "type": "deactivated_by_user", "message": message, "created_at": files::now_epoch(),
        }));
        state.terminate("stopped", "user");
        state.save(&dir)?;
        Ok(
            json!({"success": true, "workflow_name":state.workflow_name, "dispatched":false, "snapshot":{"active":false}}),
        )
    }

    pub fn deactivate_by_model(&self, conversation_id: &str, reason: &str) -> Result<Value> {
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let mut state = State::require_active(&dir)?;
        state.terminate("stopped", "model");
        state.save(&dir)?;
        let reason = reason.trim();
        let note = if reason.is_empty() {
            "模型自主退出"
        } else {
            reason
        };
        Ok(json!({"success":true, "message":format!(
            "工作流「{}」已退出（{note}）。工作流状态已摘牌，你可以继续自由工作。", state.workflow_name
        )}))
    }

    /// Drain under the conversation's task gate, then restore on failed admission.
    pub fn poll_notices(&self, conversation_id: &str) -> Result<Vec<Value>> {
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let mut state = State::load(&dir)?;
        let notices = std::mem::take(&mut state.pending_notices);
        if !notices.is_empty() {
            state.save(&dir)?;
        }
        Ok(notices.into_iter().filter(Value::is_object).collect())
    }

    pub fn restore_notices(&self, conversation_id: &str, mut notices: Vec<Value>) -> Result<()> {
        if notices.is_empty() {
            return Ok(());
        }
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let mut state = State::load(&dir)?;
        notices.append(&mut state.pending_notices);
        state.pending_notices = notices;
        fs::create_dir_all(&dir)?;
        state.save(&dir)
    }

    pub fn reset_stage_rounds(&self, conversation_id: &str) -> Result<()> {
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let mut state = State::load(&dir)?;
        if state.is_active() && (state.stage_rounds != 0 || state.round_limit_notified) {
            state.stage_rounds = 0;
            state.round_limit_notified = false;
            state.save(&dir)?;
        }
        Ok(())
    }

    /// Call on each model round. Return the soft notice only on the first
    /// round at or above the configured limit, until the counter is reset.
    pub fn tick_stage_round(&self, conversation_id: &str) -> Result<Option<String>> {
        let _guard = lock()?;
        let dir = self.directory(conversation_id)?;
        let mut state = State::load(&dir)?;
        if !state.is_active() {
            return Ok(None);
        }
        state.stage_rounds = state.stage_rounds.saturating_add(1);
        let definition = load_definition(&dir)?;
        let max_rounds = definition
            .as_ref()
            .map_or(super::definition::DEFAULT_STAGE_ROUNDS, |wf| {
                wf.max_stage_rounds
            });
        let mut notice = None;
        if !state.round_limit_notified && state.stage_rounds >= max_rounds.max(0) as u64 {
            state.round_limit_notified = true;
            let step = definition
                .as_ref()
                .and_then(|wf| state.current_node_id.as_deref().and_then(|id| wf.node(id)))
                .map_or("", |node| &node.name);
            notice = Some(format!(
                "工作流「{}」的当前步骤「{step}」已进行 {} 轮，达到单步轮数上限（{max_rounds}）。请立刻停下当前工作，告知用户已超过 {max_rounds} 轮，并询问是否还要继续。（工作流仍在进行中，等待用户决定；用户回复后可继续推进或退出。）",
                state.workflow_name, state.stage_rounds
            ));
        }
        state.save(&dir)?;
        Ok(notice)
    }

    fn directory(&self, conversation_id: &str) -> Result<PathBuf> {
        let conversation_id = conversation_id.trim();
        if conversation_id.is_empty() {
            return Err(WorkflowError::new(400, "缺少 conversation_id"));
        }
        if !conversation_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return Err(WorkflowError::new(400, "非法 conversation_id"));
        }
        let workspace = files::resolve_root(&self.workspace_data)?;
        let root = workspace.join("workflow_states");
        files::directory(&root)?;
        let dir = root.join(conversation_id);
        files::directory(&dir)?;
        Ok(dir)
    }
}

impl State {
    fn load(dir: &Path) -> Result<Self> {
        let Some(contents) = files::read(&dir.join(STATE_FILE))? else {
            return Ok(Self::default());
        };
        let Ok(Value::Object(mut value)) = serde_json::from_str(&contents) else {
            return Ok(Self::default());
        };
        // Old state files can omit any field. Normalize invalid collection
        // shapes without dropping history, notices, or unknown top-level keys.
        for key in ["history", "pending_notices"] {
            if value.get(key).is_some_and(|value| !value.is_array()) {
                value.shift_remove(key);
            }
        }
        if let Some(counts) = value.get_mut("reject_counts") {
            if let Some(counts) = counts.as_object_mut() {
                counts.retain(|_, count| count.as_u64().is_some());
            } else {
                value.shift_remove("reject_counts");
            }
        }
        for key in ["stage_rounds", "stage_start_msg_index"] {
            if let Some(field) = value.get_mut(key) {
                *field = json!(
                    field
                        .as_u64()
                        .or_else(|| field.as_str()?.parse().ok())
                        .unwrap_or(0)
                );
            }
        }
        Ok(serde_json::from_value(Value::Object(value)).unwrap_or_default())
    }

    fn require_active(dir: &Path) -> Result<Self> {
        let state = Self::load(dir)?;
        if state.is_active() {
            Ok(state)
        } else {
            Err(WorkflowError::new(400, "当前对话没有激活的工作流。"))
        }
    }

    fn is_active(&self) -> bool {
        self.active && self.status.as_deref() == Some("active")
    }

    fn terminate(&mut self, status: &str, reason: &str) {
        self.active = false;
        self.status = Some(status.into());
        self.exit_reason = Some(reason.into());
    }

    fn save(&self, dir: &Path) -> Result<()> {
        let path = dir.join(STATE_FILE);
        files::file(&path)?;
        let contents = serde_json::to_vec_pretty(self)
            .map_err(|error| WorkflowError::new(500, format!("工作流状态保存失败：{error}")))?;
        files::write_atomic(&path, &contents)?;
        Ok(())
    }

    fn move_to(&mut self, node_id: &str, message_index: usize) {
        self.current_node_id = Some(node_id.into());
        self.stage_rounds = 0;
        self.round_limit_notified = false;
        self.stage_start_msg_index = message_index;
    }

    fn snapshot(&self, definition: Option<&Workflow>) -> Value {
        if !self.is_active() {
            return json!({"active": false});
        }
        let history: Vec<_> = self.history.iter().filter(|item| item["kind"] == "stage")
            .map(|item| json!({"name":item["name"].as_str().unwrap_or_default(), "rounds":item["rounds"]}))
            .collect();
        let current = definition.and_then(|wf| wf.node(self.current_node_id.as_deref()?));
        let next = current.and_then(|node| match &node.kind {
            NodeKind::Stage { next, .. } => definition?
                .node(next.as_deref()?)
                .map(|next| next.name.as_str()),
            _ => None,
        });
        json!({
            "active": true, "name": self.workflow_name, "status": self.status,
            "history": history, "current": current.map(|node| json!({"name":node.name, "rounds":self.stage_rounds})),
            "next": next, "reviewing": false, "footnote": null,
        })
    }
}

fn lock() -> Result<MutexGuard<'static, ()>> {
    STATE_LOCK
        .lock()
        .map_err(|_| WorkflowError::new(500, "无法锁定工作流状态"))
}

fn load_definition(dir: &Path) -> Result<Option<Workflow>> {
    Ok(files::read(&dir.join(files::MARKDOWN_FILE))?
        .and_then(|markdown| Workflow::from_markdown(&markdown, Source::Snapshot).ok()))
}

fn restore_file(path: &Path, old: Option<&str>) -> Result<()> {
    if let Some(contents) = old {
        files::write_atomic(path, contents.as_bytes())?;
    } else if let Err(error) = fs::remove_file(path)
        && error.kind() != ErrorKind::NotFound
    {
        return Err(error.into());
    }
    Ok(())
}
