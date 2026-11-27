//! Conversation-owned Host processes. Runtime admission supplies all authority.

pub(crate) mod policy;
#[cfg(target_os = "linux")]
pub(crate) mod linux;
#[cfg(windows)]
pub(crate) mod windows;
#[cfg(any(windows, test))]
mod windows_plan;
mod process;
mod tools;

use std::{
    collections::{BTreeMap, HashMap, VecDeque},
    io,
    path::{Component, Path, PathBuf},
    sync::Arc,
    time::Duration,
};

use serde_json::{Value, json};
use uuid::Uuid;
use crate::approval::ExecutionMode;

pub(crate) use policy::{Network, SandboxAccess, terminal_allowed};
use policy::{HostPolicy, denied};
use process::{Process, Status, now};
pub(crate) use tools::{Invocation, child_schemas, schemas, tools_category};

const TERMINALS: usize = 3;
const BACKGROUND_WAIT: Duration = Duration::from_secs(5);
const MONITOR_LIMIT: usize = 120;

#[derive(Clone, Hash, PartialEq, Eq)]
pub(crate) struct Owner {
    pub(crate) user: String,
    pub(crate) workspace_id: String,
    pub(crate) workspace: PathBuf,
    pub(crate) conversation: String,
    pub(crate) actor: Option<String>,
}

#[derive(PartialEq, Eq)]
pub(crate) struct Scope {
    pub(crate) owner: Owner,
    pub(crate) execution_root: PathBuf,
    pub(crate) access: SandboxAccess,
    pub(crate) mode: ExecutionMode,
    pub(crate) approval_required: bool,
}

#[derive(Default)]
pub struct ProcessResources {
    policy: Option<HostPolicy>,
    closing: bool,
    conversations: HashMap<Owner, Resources>,
    monitor: VecDeque<MonitorEntry>,
}

#[derive(Default)]
struct Resources {
    terminals: BTreeMap<String, Terminal>,
    terminal_scope: Option<PathBuf>,
    active: Option<String>,
    background: Vec<Background>,
    foreground: Vec<Arc<Process>>,
}

struct Terminal {
    process: Arc<Process>,
    cwd: PathBuf,
    access: SandboxAccess,
    mode: ExecutionMode,
}

struct Background {
    id: String,
    command: String,
    timeout: Duration,
    process: Arc<Process>,
    consumed: bool,
    suppress_notice: bool,
}

struct MonitorEntry {
    owner: Owner,
    id: String,
    before: Option<Value>,
    after: Option<Value>,
}

#[derive(Clone, Copy)]
pub enum MonitorStage { Before, After }

impl MonitorStage {
    pub(crate) fn text(self) -> &'static str {
        match self { Self::Before => "before", Self::After => "after" }
    }
}

impl ProcessResources {
    pub(crate) fn configure(&mut self, deploy: &Path, data: &Path) -> io::Result<()> {
        if self.closing || self.policy.is_some() {
            return Err(denied("Host process configuration is already fixed"));
        }
        self.policy = Some(HostPolicy::new(deploy, data)?);
        Ok(())
    }

    pub(crate) fn available(&self) -> bool {
        !self.closing && self.policy.as_ref().is_some_and(HostPolicy::supports_processes)
    }

    pub(crate) fn deployment(&self) -> Option<&Path> {
        self.policy.as_ref().map(HostPolicy::deploy)
    }

    pub(crate) fn available_mode(&self, mode: ExecutionMode) -> bool {
        match mode {
            ExecutionMode::Sandbox => self.available(),
            ExecutionMode::Direct => !self.closing && self.policy.as_ref().is_some_and(HostPolicy::supports_direct),
        }
    }

    pub(crate) fn start(&mut self, scope: Scope, call: Invocation) -> io::Result<Operation> {
        self.start_scoped(scope, call, None)
    }

    pub(crate) fn start_child(&mut self, scope: Scope, call: Invocation, fixed_write_root: Option<&Path>) -> io::Result<Operation> {
        if scope.owner.actor.is_none() || matches!(call, Invocation::Command { background: true, .. }
            | Invocation::Command { full_access: true, .. } | Invocation::Wait { .. } | Invocation::Sleep { .. })
        {
            return Err(denied("The requested process capability is not available for this child"));
        }
        self.start_scoped(scope, call, fixed_write_root)
    }

    fn start_scoped(&mut self, scope: Scope, call: Invocation, fixed_write_root: Option<&Path>) -> io::Result<Operation> {
        if !self.available_mode(scope.mode) { return Err(denied("The selected Host execution environment is unavailable")); }
        let policy = self.policy.as_ref().expect("available process policy");
        if call.requires_terminal_panel() && !policy.terminal_allowed()? {
            return Err(denied("Realtime terminal has been disabled by the administrator"));
        }
        if scope.approval_required && (matches!(call, Invocation::Command { .. } | Invocation::Open { .. } | Invocation::Close { .. } | Invocation::Reset { .. } | Invocation::List)
            || scope.mode == ExecutionMode::Direct && matches!(call, Invocation::Input { .. }))
        {
            return Err(denied("Required process approval is unavailable"));
        }
        let owner = scope.owner;
        let records = self.conversations.entry(owner.clone()).or_default();
        records.foreground.retain(|process| process.state().status == Status::Running);
        // The shell cannot change its security profile. Close sessions whose
        // captured mode or network policy no longer equals current authority.
        if call.requires_terminal_panel() {
            let moved = records.terminal_scope.as_ref().is_some_and(|root| root != &scope.execution_root);
            records.terminals.retain(|_, terminal| {
                if !moved && terminal.access == scope.access && terminal.mode == scope.mode { return true; }
                terminal.process.cancel();
                terminal.process.wait_blocking();
                false
            });
            records.terminal_scope = Some(scope.execution_root.clone());
            records.reselect();
        }
        let operation = match call {
            Invocation::Command { command, timeout, background, full_access } => {
                if full_access && scope.mode != ExecutionMode::Direct {
                    return Err(denied("A full-access command requires its own execution grant"));
                }
                policy.validate_command(&command)?;
                let plan = policy.command(&owner.workspace, &scope.execution_root, scope.access, scope.mode, Some(&command), fixed_write_root)?;
                let process = Process::spawn(plan, Some(timeout), false)?;
                let row = Background { id: format!("cmd_{}", Uuid::new_v4().simple()), command, timeout,
                    process: process.clone(), consumed: false, suppress_notice: false };
                let guard = CancelOnDrop(Some(process));
                if background {
                    records.background.push(row.clone());
                } else {
                    records.foreground.push(row.process.clone());
                }
                Operation::Command { owner, row, background, guard }
            }
            Invocation::Open { name, cwd } => {
                if records.terminals.contains_key(&name) { return Err(denied("Terminal session already exists")); }
                if records.terminals.len() >= TERMINALS { return Err(denied("Maximum terminal session count reached")); }
                let cwd = terminal_cwd(&owner.workspace, &scope.execution_root, &cwd)?;
                let terminal = Terminal::start(policy, &owner.workspace, cwd, scope.access, scope.mode, fixed_write_root)?;
                let output = terminal_result("open", &name, &terminal);
                records.terminals.insert(name.clone(), terminal);
                records.active = Some(name);
                Operation::Ready(output)
            }
            Invocation::Close { name } => {
                let terminal = records.terminals.remove(&name).ok_or_else(terminal_missing)?;
                terminal.process.cancel();
                terminal.process.wait_blocking();
                records.reselect();
                Operation::Ready(json!({"success":true,"action":"close","session":name,"new_active":records.active}))
            }
            Invocation::Reset { name } => {
                let name = records.terminal_name(name.as_deref())?.to_owned();
                let cwd = records.terminals[&name].cwd.clone();
                let previous = records.terminals.remove(&name).expect("existing session");
                previous.process.cancel();
                previous.process.wait_blocking();
                records.reselect();
                let terminal = Terminal::start(policy, &owner.workspace, cwd, scope.access, scope.mode, fixed_write_root)?;
                let output = terminal_result("reset", &name, &terminal);
                records.terminals.insert(name.clone(), terminal);
                records.active = Some(name);
                Operation::Ready(output)
            }
            Invocation::List => {
                let mut result = records.terminal_list();
                result["action"] = json!("list");
                Operation::Ready(result)
            }
            Invocation::Input { name, command, wait } => {
                policy.validate_command(&command)?;
                let terminal = &records.terminals[records.terminal_name(Some(&name))?];
                let process = terminal.process.clone();
                let cursor = process.input(&command)?;
                Operation::Input { name, process, cursor, wait }
            }
            Invocation::Snapshot { name, lines, max_chars } => {
                let key = records.terminal_name(name.as_deref())?;
                Operation::Ready(records.terminals[key].snapshot(key, lines, max_chars))
            }
            Invocation::Wait { id } => Operation::Wait { owner, row: records.background(&id)?.clone() },
            Invocation::Sleep { wait } => Operation::Sleep(wait),
        };
        Ok(operation)
    }

    pub(crate) fn terminal_list(&self, owner: Option<&Owner>) -> Value {
        owner.and_then(|owner| self.conversations.get(owner)).map(Resources::terminal_list)
            .unwrap_or_else(|| json!({"sessions":[],"active":null,"total":0}))
    }

    pub(crate) fn terminal_output(&self, owner: &Owner, name: &str, lines: usize) -> io::Result<Value> {
        let terminal = self.conversations.get(owner).and_then(|resources| resources.terminals.get(name))
            .ok_or_else(terminal_missing)?;
        Ok(terminal.snapshot(name, Some(lines), 100_000))
    }

    pub(crate) fn background_list(&self, owner: Option<&Owner>, limit: usize) -> Value {
        let data: Vec<_> = owner.and_then(|owner| self.conversations.get(owner).map(|records| (owner, records)))
            .into_iter().flat_map(|(owner, records)| records.background.iter().rev().take(limit)
                .map(|row| row.project(owner, false))).collect();
        json!({"success":true,"data":data})
    }

    /// Child and main resources share the same workspace/conversation identity.
    /// Only a running background process or a live shell still in the departing
    /// tree counts: terminal notices do not keep a worktree occupied.
    pub(crate) fn worktree_busy(
        &self, workspace_id: &str, workspace: &Path, conversation: &str, root: &Path,
    ) -> (bool, Option<String>) {
        let mut background = false;
        let mut terminal = None;
        for (owner, resources) in &self.conversations {
            if owner.workspace_id != workspace_id || owner.workspace != workspace || owner.conversation != conversation {
                continue;
            }
            background |= resources.background.iter().any(|row| row.process.state().status == Status::Running);
            if terminal.is_none() {
                terminal = resources.terminals.iter()
                    .find(|(_, shell)| shell.cwd.starts_with(root) && shell.process.state().status == Status::Running)
                    .map(|(name, _)| name.clone());
            }
        }
        (background, terminal)
    }

    pub(crate) fn has_pending(&self, owner: &Owner) -> bool {
        self.conversations.get(owner).is_some_and(|records| records.background.iter().any(|row| {
            row.process.state().status == Status::Running || !(row.consumed || row.suppress_notice)
        }))
    }

    pub(crate) fn has_notices(&self, owner: &Owner) -> bool {
        self.conversations.get(owner).is_some_and(|records| {
            records.background.iter().any(|row| !row.consumed && !row.suppress_notice && row.process.state().status != Status::Running)
        })
    }

    pub(crate) fn background_detail(&self, owner: &Owner, id: &str) -> io::Result<Value> {
        Ok(json!({"success":true,"data":self.background(owner, id)?.project(owner, true)}))
    }

    pub(crate) fn cancel_background(&mut self, owner: &Owner, id: &str) -> io::Result<PendingCancellation> {
        let row = self.conversations.get_mut(owner).and_then(|records| {
            records.background.iter_mut().find(|row| row.id == id)
        }).ok_or_else(background_missing)?;
        // The initiating user sees the cancellation result in the REST response.
        row.suppress_notice = true;
        row.process.cancel();
        Ok(PendingCancellation(row.clone()))
    }

    fn background(&self, owner: &Owner, id: &str) -> io::Result<&Background> {
        self.conversations.get(owner).ok_or_else(background_missing)?.background(id)
    }

    pub(crate) fn consume(&mut self, owner: &Owner, id: &str) {
        if let Some(row) = self.conversations.get_mut(owner).and_then(|records| records.background.iter_mut().find(|row| row.id == id))
            && row.process.state().status != Status::Running
        {
            row.consumed = true;
        }
    }

    pub(crate) fn take_notices(&mut self, owner: &Owner) -> Vec<Value> {
        let Some(records) = self.conversations.get_mut(owner) else { return Vec::new() };
        records.background.iter_mut().filter_map(|row| {
            if row.consumed || row.suppress_notice || row.process.state().status == Status::Running { return None; }
            row.consumed = true;
            Some(row.result(true))
        }).collect()
    }

    pub(crate) fn restore_notices(&mut self, owner: &Owner, ids: &[String]) {
        if let Some(records) = self.conversations.get_mut(owner) {
            for row in &mut records.background {
                if ids.contains(&row.id) { row.consumed = false; }
            }
        }
    }

    pub(crate) fn stop_background(&mut self, owner: &Owner) -> usize {
        let Some(records) = self.conversations.get_mut(owner) else { return 0 };
        let running: Vec<_> = records.background.iter().filter(|row| row.process.state().status == Status::Running).collect();
        for row in &running { row.process.cancel(); }
        for row in &running { row.process.wait_blocking(); }
        let stopped = running.len();
        for row in &mut records.background {
            if row.process.state().status != Status::Running { row.consumed = true; }
        }
        stopped
    }

    pub(crate) fn close_conversation(&mut self, workspace: &str, conversation: Option<&str>) {
        self.conversations.retain(|owner, resources| {
            if owner.workspace_id != workspace || conversation.is_some_and(|id| owner.conversation != id) { return true; }
            resources.cancel_all();
            resources.wait_all();
            false
        });
        self.monitor.retain(|entry| entry.owner.workspace_id != workspace || conversation.is_some_and(|id| entry.owner.conversation != id));
    }

    pub(crate) fn close_child(&mut self, workspace: &str, conversation: &str, actor: &str) {
        self.conversations.retain(|owner, resources| {
            if owner.workspace_id != workspace || owner.conversation != conversation || owner.actor.as_deref() != Some(actor) {
                return true;
            }
            resources.cancel_all();
            resources.wait_all();
            false
        });
    }

    pub(crate) fn close_terminals(&mut self, workspace: Option<&str>, conversation: Option<&str>) {
        for (owner, resources) in &mut self.conversations {
            if workspace.is_some_and(|workspace| workspace != owner.workspace_id)
                || conversation.is_some_and(|conversation| conversation != owner.conversation)
            {
                continue;
            }
            let terminals = std::mem::take(&mut resources.terminals);
            for terminal in terminals.values() { terminal.process.cancel(); }
            for terminal in terminals.values() { terminal.process.wait_blocking(); }
            resources.active = None;
        }
    }

    pub(crate) fn shutdown(&mut self) {
        self.closing = true;
        for resources in self.conversations.values() { resources.cancel_all(); }
        for resources in self.conversations.values() { resources.wait_all(); }
        self.conversations.clear();
        self.monitor.clear();
    }

    pub(crate) fn record_monitor(&mut self, owner: Owner, id: &str, stage: MonitorStage, snapshot: Value) {
        if self.closing || !snapshot["content"].as_str().is_some_and(|content| !content.is_empty()) { return; }
        let mut entry = self.monitor.iter().position(|row| row.owner == owner && row.id == id)
            .and_then(|index| self.monitor.remove(index))
            .unwrap_or_else(|| MonitorEntry { owner, id: id.into(), before: None, after: None });
        match stage { MonitorStage::Before => entry.before = Some(snapshot), MonitorStage::After => entry.after = Some(snapshot) }
        if self.monitor.len() == MONITOR_LIMIT { self.monitor.pop_front(); }
        self.monitor.push_back(entry);
    }

    pub(crate) fn monitor(&self, user: &str, id: &str, stage: MonitorStage) -> Option<(&Owner, Value)> {
        let entry = self.monitor.iter().find(|entry| entry.owner.user == user && entry.id == id)?;
        let snapshot = match stage { MonitorStage::Before => &entry.before, MonitorStage::After => &entry.after };
        Some((&entry.owner, snapshot.clone()?))
    }
}

impl Drop for ProcessResources {
    fn drop(&mut self) { self.shutdown(); }
}

impl Resources {
    fn terminal_name<'a>(&'a self, requested: Option<&'a str>) -> io::Result<&'a str> {
        requested.or(self.active.as_deref()).filter(|name| self.terminals.contains_key(*name))
            .ok_or_else(terminal_missing)
    }

    fn reselect(&mut self) {
        if self.active.as_ref().is_none_or(|active| !self.terminals.contains_key(active)) {
            self.active = self.terminals.keys().next().cloned();
        }
    }

    fn terminal_list(&self) -> Value {
        let sessions: Vec<_> = self.terminals.iter().map(|(name, terminal)| {
            let state = terminal.process.state();
            json!({
                "session_name":name,"is_active":self.active.as_ref()==Some(name),"working_dir":terminal.cwd,
                "shell":terminal.shell(),"execution_mode":terminal.mode_name(),"is_running":state.status==Status::Running,
                "is_interactive":state.interactive(),"last_command":state.last_command,"command_count":state.command_count,
                "buffer_size":state.output.len(),"last_activity":iso_time(state.updated_at),"start_time":iso_time(state.created_at),
                "uptime_seconds":now()-state.created_at,"seconds_since_last_output":state.last_output_at.map(|at| now()-at),
                "echo_loop_detected":false,
            })
        }).collect();
        json!({"success":true,"sessions":sessions,"active":self.active,"total":sessions.len(),"max_allowed":TERMINALS})
    }

    fn background(&self, id: &str) -> io::Result<&Background> {
        self.background.iter().find(|row| row.id == id).ok_or_else(background_missing)
    }

    fn processes(&self) -> impl Iterator<Item = &Process> {
        self.foreground.iter().map(Arc::as_ref)
            .chain(self.background.iter().map(|row| row.process.as_ref()))
            .chain(self.terminals.values().map(|terminal| terminal.process.as_ref()))
    }

    fn cancel_all(&self) { for process in self.processes() { process.cancel(); } }
    fn wait_all(&self) { for process in self.processes() { process.wait_blocking(); } }
}

impl Terminal {
    fn start(policy: &HostPolicy, workspace: &Path, cwd: PathBuf, access: SandboxAccess, mode: ExecutionMode,
        fixed_write_root: Option<&Path>) -> io::Result<Self> {
        let command = policy.command(workspace, &cwd, access, mode, None, fixed_write_root)?;
        let cmd = cfg!(windows) && mode == ExecutionMode::Direct;
        Ok(Self { process: Process::spawn(command, None, cmd)?, cwd, access, mode })
    }

    fn shell(&self) -> &'static str {
        if cfg!(windows) && self.mode == ExecutionMode::Direct { "cmd.exe" } else { "/bin/bash" }
    }

    fn mode_name(&self) -> &'static str {
        match self.mode { ExecutionMode::Sandbox => "sandbox", ExecutionMode::Direct => "direct" }
    }

    fn snapshot(&self, name: &str, lines: Option<usize>, max_chars: usize) -> Value {
        let state = self.process.state();
        let buffer = state.output.text(0);
        let first = lines.and_then(|lines| buffer.trim_end_matches('\n').rmatch_indices('\n').nth(lines.saturating_sub(1)))
            .map_or(0, |(index, _)| index + 1);
        let output = &buffer[first..];
        let char_start = output.char_indices().rev().nth(max_chars).map_or(0, |(index, ch)| index + ch.len_utf8());
        let output = &output[char_start..];
        json!({
            "success":true,"session":name,"output":output,"is_interactive":state.interactive(),
            "last_command":state.last_command,"last_event_time":state.last_output_at,
            "seconds_since_last_output":state.last_output_at.map(|at| now()-at),"lines_returned":output.lines().count(),
            "truncated":state.output.truncated() || first > 0 || char_start > 0,"echo_loop_detected":false,
        })
    }
}

fn terminal_cwd(workspace: &Path, root: &Path, requested: &Path) -> io::Result<PathBuf> {
    if requested.components().any(|part| !matches!(part, Component::Normal(_) | Component::CurDir)) {
        return Err(denied("Terminal working directory must stay in the workspace"));
    }
    let path = root.join(requested);
    let cwd = path.canonicalize()?;
    if !cwd.is_dir() || !cwd.starts_with(workspace) || !cwd.starts_with(root) {
        return Err(denied("Terminal working directory must stay in the execution root"));
    }
    Ok(cwd)
}

fn terminal_result(action: &str, name: &str, terminal: &Terminal) -> Value {
    json!({"success":true,"action":action,"session":name,"working_dir":terminal.cwd,
        "shell":terminal.shell(),"execution_mode":terminal.mode_name()})
}

fn terminal_missing() -> io::Error { io::Error::new(io::ErrorKind::NotFound, "Terminal session not found") }
fn background_missing() -> io::Error { io::Error::new(io::ErrorKind::NotFound, "Background command not found") }

impl Clone for Background {
    fn clone(&self) -> Self {
        Self { id: self.id.clone(), command: self.command.clone(), timeout: self.timeout, process: self.process.clone(),
            consumed: self.consumed, suppress_notice: self.suppress_notice }
    }
}

impl Background {
    fn project(&self, owner: &Owner, detail: bool) -> Value {
        let state = self.process.state();
        let mut data = json!({
            "command_id":self.id,"status":state.status.text(),"command":self.command,"conversation_id":owner.conversation,
            "created_at":state.created_at,"updated_at":state.updated_at,"finished_at":state.finished_at,
            "timeout":self.timeout.as_secs_f64(),"return_code":state.return_code,"run_in_background":true,
            "notice_pending":state.status!=Status::Running && !self.consumed && !self.suppress_notice,
        });
        if detail {
            data["message"] = json!(state.message);
            data["output"] = json!(state.output.text(0));
        }
        data
    }

    fn result(&self, background: bool) -> Value {
        let state = self.process.state();
        let running = state.status == Status::Running;
        let mut result = json!({
            "success":running || state.status==Status::Completed,"status":if running { "running_background" } else { state.status.text() },
            "command":self.command,"output":state.output.text(0),"return_code":state.return_code,
            "timeout":self.timeout.as_secs_f64(),"truncated":state.output.truncated(),
            "elapsed_ms":((state.finished_at.unwrap_or_else(now)-state.created_at)*1000.0).max(0.0) as u64,
        });
        if let Some(message) = &state.message { result["message"] = json!(message); }
        if background {
            result["command_id"] = json!(self.id);
            result["run_in_background"] = json!(true);
            result["background_task_created"] = json!(running);
        }
        result
    }
}

pub(crate) struct PendingCancellation(Background);

impl PendingCancellation {
    pub(crate) async fn finish(self) -> io::Result<Value> {
        self.0.process.wait().await;
        let state = self.0.process.state();
        if state.status == Status::Failed && state.message.as_deref().is_some_and(|value| value.starts_with("Process supervision failed")) {
            return Err(io::Error::other("Could not verify background process termination"));
        }
        Ok(json!({"success":true,"data":{"command_id":self.0.id,"status":state.status.text()}}))
    }
}

pub(crate) enum Operation {
    Ready(Value),
    Command { owner: Owner, row: Background, background: bool, guard: CancelOnDrop },
    Input { name: String, process: Arc<Process>, cursor: u64, wait: Duration },
    Wait { owner: Owner, row: Background },
    Sleep(Duration),
}

pub(crate) struct CancelOnDrop(Option<Arc<Process>>);

impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        if let Some(process) = &self.0 {
            process.cancel();
            process.wait_blocking();
        }
    }
}

impl Operation {
    pub(crate) async fn finish(self) -> (Value, Option<(Owner, String)>) {
        match self {
            Self::Ready(result) => (result, None),
            Self::Command { owner, row, background, mut guard } => {
                if background {
                    let _ = tokio::time::timeout(BACKGROUND_WAIT, row.process.wait()).await;
                } else { row.process.wait().await; }
                let result = row.result(background);
                guard.0.take();
                let claim = (background && result["background_task_created"] == false).then_some((owner, row.id));
                (result, claim)
            }
            Self::Input { name, process, cursor, wait } => {
                let timed_out = tokio::time::timeout(wait, process.wait_input()).await.is_err();
                let state = process.state();
                (json!({"success":true,"session":name,"command":state.last_command,"output":state.output.text(cursor),
                    "status":if timed_out { "timeout" } else { "completed" },"output_wait":wait.as_secs_f64(),
                    "timeout":wait.as_secs_f64(),"is_interactive":state.interactive()}), None)
            }
            Self::Wait { owner, row } => {
                row.process.wait().await;
                let result = row.result(true);
                (json!({"success":result["success"],"mode":"wait_runcommand_id","command_id":row.id,"result":result}), Some((owner, row.id)))
            }
            Self::Sleep(wait) => {
                tokio::time::sleep(wait).await;
                (json!({"success":true,"message":format!("Waited {} seconds", wait.as_secs_f64())}), None)
            }
        }
    }
}

fn iso_time(timestamp: f64) -> Option<String> {
    use time::{OffsetDateTime, format_description::well_known::Rfc3339};
    OffsetDateTime::from_unix_timestamp_nanos((timestamp * 1_000_000_000.0) as i128).ok()?.format(&Rfc3339).ok()
}

#[cfg(all(test, unix))]
#[path = "../../../test/2026-11-27_Rust工作树活动退出/worktree_process_contract.rs"]
mod worktree_process_contract;
