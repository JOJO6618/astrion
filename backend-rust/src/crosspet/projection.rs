use std::{
    cmp::Reverse,
    collections::{BTreeMap, BTreeSet},
    fmt::Write,
};

use ring::digest::{SHA256, digest};
use serde_json::{Value, json};
use uuid::Uuid;

use super::{
    Action, CrossPetRuntime, Foreground, GptQuota, SessionIdentity, TaskEvent, TaskObservation,
    TaskRef, TerminalStatus, WaitReason,
};

const SESSION_LIMIT: usize = 256;
const RETAIN_TERMINAL_SECONDS: f64 = 60.0;
const MAX_LABEL_CHARS: usize = 160;

#[derive(Default)]
pub(super) struct Projection {
    sessions: BTreeMap<String, Session>,
    focused: Option<Focus>,
}

impl Projection {
    pub(super) fn apply(&mut self, input: TaskEvent, at: f64) {
        let id = session_key(&input.task.session);
        // Compaction can replace the conversation ID while the same task runs.
        if let Some(old) = self.sessions.iter().find_map(|(key, session)| {
            (*key != id && session.task.task_id == input.task.task_id).then(|| key.clone())
        }) && self
            .sessions
            .get(&id)
            .is_none_or(|session| session.task.task_id == input.task.task_id)
        {
            if let Some(mut session) = self.sessions.remove(&old) {
                session.task.session = input.task.session.clone();
                self.sessions.insert(id.clone(), session);
            }
            if let Some(focus) = &mut self.focused
                && focus.session == old
            {
                focus.session.clone_from(&id);
            }
        }
        let session = self
            .sessions
            .entry(id)
            .or_insert_with(|| Session::new(input.task.clone(), at));
        if session.task.task_id != input.task.task_id {
            if !matches!(input.action, Action::Started) {
                return; // A stale event cannot change the successor's session.
            }
            *session = Session::new(input.task, at);
        }
        if !input.model_key.is_empty() && session.model_key != input.model_key {
            session.model_key = input.model_key;
            session.model = None;
        }
        session.apply(input.action, at);
    }

    pub(super) fn inspect(&mut self, runtime: &dyn CrossPetRuntime, at: f64) {
        if let Ok(next) = runtime.foreground() {
            self.focused = next.map(|Foreground { session, model_key }| {
                let identity = session_key(&session);
                let model = model(runtime, &model_key);
                Focus {
                    session: identity,
                    model,
                }
            });
        }
        let tasks: Vec<TaskRef> = self
            .sessions
            .values()
            .map(|session| session.task.clone())
            .collect();
        let observations = if tasks.is_empty() {
            BTreeMap::new()
        } else {
            runtime.observe(&tasks)
        };
        for session in self.sessions.values_mut() {
            if let Some(observation) = observations.get(&session.task) {
                session.observe(*observation, at);
            }
        }
        self.sessions.retain(|_, session| {
            session.active() || at - session.event_at <= RETAIN_TERMINAL_SECONDS
        });
    }

    pub(super) fn resolve_models(&mut self, runtime: &dyn CrossPetRuntime) {
        for session in self.sessions.values_mut() {
            if session.model.is_none() {
                session.model = Some(model(runtime, &session.model_key));
            }
        }
    }

    pub(super) fn busy(&self) -> bool {
        self.sessions.values().any(Session::active)
    }

    pub(super) fn snapshot(
        &self,
        instance: Uuid,
        sequence: u64,
        at: f64,
        quota: Option<&GptQuota>,
    ) -> Value {
        let focused_key = self
            .focused
            .as_ref()
            .map_or("", |focus| focus.session.as_str());
        let focused_model = self
            .focused
            .as_ref()
            .map_or("", |focus| focus.model.as_str());
        let foreground_model = self
            .sessions
            .get(focused_key)
            .filter(|session| session.active() || focused_model.is_empty())
            .map_or(focused_model, Session::model);
        let foreground = json!({"session": focused_key, "model": foreground_model,
            "character": character_for_model(foreground_model), "ts": at});
        let mut ranked: Vec<_> = self.sessions.iter().collect();
        ranked.sort_by(|(left_id, left), (right_id, right)| {
            Reverse((
                *left_id == focused_key,
                left.active(),
                !left.waits.is_empty(),
            ))
            .cmp(&Reverse((
                *right_id == focused_key,
                right.active(),
                !right.waits.is_empty(),
            )))
            .then_with(|| right.event_at.total_cmp(&left.event_at))
        });
        let sessions: serde_json::Map<String, Value> = ranked
            .into_iter()
            .take(SESSION_LIMIT)
            .map(|(key, session)| (key.clone(), session.payload(at)))
            .collect();
        let quota = quota
            .cloned()
            .map(normalize_quota)
            .map_or_else(|| json!({}), |quota| json!({"gpt": quota}));
        json!({"version": 1, "source": "astrion", "instance": instance.simple().to_string(),
            "sequence": sequence, "ts": at, "foreground": foreground, "sessions": sessions, "quota": quota})
    }
}

struct Focus {
    session: String,
    model: String,
}

struct Session {
    task: TaskRef,
    model_key: String,
    model: Option<String>,
    pose: &'static str,
    event: &'static str,
    event_at: f64,
    revision: u64,
    tool: String,
    tool_calls: BTreeSet<String>,
    waits: BTreeSet<WaitReason>,
    previous_tool: Option<(&'static str, f64)>,
    after: Option<(&'static str, f64)>,
    terminal: Option<TerminalStatus>,
    background: Option<bool>,
    response_error: bool,
}

impl Session {
    fn new(task: TaskRef, at: f64) -> Self {
        Self {
            task,
            model_key: String::new(),
            model: None,
            pose: "thinking",
            event: "UserPromptSubmit",
            event_at: at,
            revision: 1,
            tool: String::new(),
            tool_calls: BTreeSet::new(),
            waits: BTreeSet::new(),
            previous_tool: None,
            after: None,
            terminal: None,
            background: None,
            response_error: false,
        }
    }

    fn model(&self) -> &str {
        self.model.as_deref().unwrap_or_default()
    }

    fn active(&self) -> bool {
        self.terminal.is_none() || self.background != Some(false)
    }

    fn apply(&mut self, action: Action, at: f64) {
        if self.terminal.is_some() {
            return;
        }
        match action {
            Action::Started | Action::Thinking if self.waits.is_empty() => {
                self.change("thinking", "BeforeModel", at);
                self.after = self.previous_tool.filter(|(_, stamp)| at - stamp < 3.0);
            }
            Action::ToolStarted { call_id, name } => {
                if self.tool_calls.insert(call_id) {
                    self.tool = name.chars().take(MAX_LABEL_CHARS).collect();
                    let pose = tool_pose(&self.tool);
                    self.previous_tool = Some((pose, at));
                    self.change(pose, "PreToolUse", at);
                }
            }
            Action::ToolFinished { failed: true } | Action::Error { retry: false } => {
                self.change("oops", "PostToolUseFailure", at);
            }
            Action::ToolFinished { failed: false } if self.waits.is_empty() => {
                self.change("thinking", "PostToolUse", at);
                self.after = self.previous_tool;
            }
            Action::Waiting(reason) => {
                self.waits.insert(reason);
                self.change("asking", "PermissionRequest", at);
            }
            Action::Resolved(reason) if self.waits.remove(&reason) => {
                self.change("thinking", "BeforeModel", at);
            }
            Action::Compression { active: true } => self.change("compact", "PreCompact", at),
            Action::Compression { active: false } => self.change("thinking", "PostCompact", at),
            Action::Response { error } => self.response_error |= error,
            Action::Finished(status) => self.finish(status, at),
            _ => {}
        }
    }

    fn observe(&mut self, observation: TaskObservation, at: f64) {
        let previous = self.background;
        if let Some(background) = observation.background {
            self.background = Some(background);
        }
        if let Some(terminal) = observation.terminal.or(self.terminal)
            && (self.terminal != Some(terminal) || self.background != previous)
        {
            self.finish(terminal, at);
        }
    }

    fn finish(&mut self, status: TerminalStatus, at: f64) {
        self.terminal = Some(status);
        self.waits.clear();
        if self.background != Some(false) {
            self.change("delegating", "SubagentStart", at);
            return;
        }
        let pose = match status {
            TerminalStatus::Succeeded if !self.response_error && self.tool_calls.len() >= 8 => {
                "proud"
            }
            TerminalStatus::Succeeded if !self.response_error => "happy",
            TerminalStatus::Stopped => "surprised",
            _ => "oops",
        };
        self.change(pose, "Stop", at);
    }

    fn change(&mut self, pose: &'static str, event: &'static str, at: f64) {
        self.pose = if self.waits.is_empty() {
            pose
        } else {
            "asking"
        };
        self.event = event;
        self.event_at = at;
        self.revision += 1;
        self.after = None;
    }

    fn payload(&self, at: f64) -> Value {
        let model = self.model();
        let mut value = json!({"character": character_for_model(model), "model": model,
            "pose": self.pose, "event": self.event, "tool": self.tool,
            "ts": if self.active() { at } else { self.event_at }, "event_ts": self.event_at,
            "revision": self.revision, "active": self.active(), "tools": self.tool_calls.len()});
        if let Some((pose, stamp)) = self.after {
            value["after"] = json!(pose);
            value["afterTs"] = json!(stamp);
        }
        value
    }
}

fn model(runtime: &dyn CrossPetRuntime, key: &str) -> String {
    if key.is_empty() {
        return String::new();
    }
    runtime
        .model_id(key)
        .filter(|id| !id.is_empty())
        .as_deref()
        .unwrap_or(key)
        .chars()
        .take(MAX_LABEL_CHARS)
        .collect()
}

fn normalize_quota(mut quota: GptQuota) -> GptQuota {
    if !quota.connected {
        quota.epoch = None;
        quota.at = None;
        quota.rate_limits = None;
        return quota;
    }
    quota.at = quota.at.filter(|at| at.is_finite() && *at > 0.0);
    if let Some(limits) = &mut quota.rate_limits {
        limits.primary = limits.primary.filter(super::QuotaWindow::valid);
        limits.secondary = limits.secondary.filter(super::QuotaWindow::valid);
        if limits.primary.is_none() && limits.secondary.is_none() {
            quota.rate_limits = None;
        }
    }
    quota
}

pub(super) fn session_key(identity: &SessionIdentity) -> String {
    let conversation = identity
        .conversation_id
        .strip_prefix("conv_")
        .unwrap_or(&identity.conversation_id);
    let encode = |text: &str| {
        let mut encoded = String::new();
        for c in serde_json::to_string(text)
            .expect("serialize session identity")
            .encode_utf16()
        {
            if (0x20..=0x7e).contains(&c) {
                encoded.push(c as u8 as char);
            } else {
                write!(encoded, "\\u{c:04x}").expect("format session identity");
            }
        }
        encoded
    };
    // The separators and ASCII escapes match Python json.dumps' session key.
    let source = format!(
        "[\"host\", {}, {}]",
        encode(&identity.workspace_id),
        encode(conversation)
    );
    let mut key = String::with_capacity(32);
    for byte in &digest(&SHA256, source.as_bytes()).as_ref()[..16] {
        write!(key, "{byte:02x}").expect("format session hash");
    }
    key
}

fn character_for_model(model: &str) -> &'static str {
    let name = model.to_ascii_lowercase();
    for (character, terms) in [
        ("glm", &["glm", "zhipu", "z.ai"][..]),
        ("kimi", &["kimi", "moonshot"]),
        ("deepseek", &["deepseek"]),
        ("claude", &["claude", "anthropic"]),
        ("gemini", &["gemini"]),
        ("gpt", &["gpt", "codex", "openai/", "o1", "o3", "o4"]),
    ] {
        if terms.iter().any(|term| name.contains(term)) {
            return character;
        }
    }
    "current"
}

fn tool_pose(tool: &str) -> &'static str {
    let tool = tool.to_ascii_lowercase();
    match tool.as_str() {
        "ask_user" | "submit_plan" => "asking",
        "web_search" | "extract_webpage" | "save_webpage" => "searching",
        "create_sub_agent"
        | "ask_sub_agent"
        | "send_message_to_sub_agent"
        | "ask_other_agent"
        | "ask_master" => "delegating",
        _ if tool.starts_with("mcp__playwright__") => "searching",
        _ if ["image_gen", "generate_image", "video_gen"]
            .iter()
            .any(|word| tool.contains(word)) =>
        {
            "drawing"
        }
        _ if ["todo_", "get_sub_agent", "terminate_sub_agent"]
            .iter()
            .any(|prefix| tool.starts_with(prefix)) =>
        {
            "delegating"
        }
        _ if ["read", "view", "recall", "search", "conversation_"]
            .iter()
            .any(|prefix| tool.starts_with(prefix)) =>
        {
            "reading"
        }
        _ if [
            "write",
            "edit",
            "update_memory",
            "update_project_memory",
            "create_skill",
        ]
        .iter()
        .any(|prefix| tool.starts_with(prefix)) =>
        {
            "writing"
        }
        _ => "running",
    }
}
