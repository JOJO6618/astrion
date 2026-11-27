use std::{collections::HashMap, io, ops::{Deref, DerefMut}, path::{Path, PathBuf}, sync::{Arc, Mutex, MutexGuard}, time::{Duration, SystemTime, UNIX_EPOCH}};

use serde_json::{Map, Value, json};
use jiff::{Timestamp, tz::TimeZone};
use tokio::{runtime::Handle, sync::{oneshot, watch}, time::Instant};

use crate::{catalog::CatalogSource, runtime::ModelConfig, workspace_catalog::RegisteredWorkspace};

use super::{Record, Records, SubAgents, normalize_conversation, owns_path, running, string,
    protocol::{self, NoticeType, message_id, notice},
    registry::mutation_guard, scope::{Access, ChildScope, ParentAccess, identity, working_directory},
    worker::{self, Context},
};

#[derive(Default)]
pub(super) struct Execution {
    state: Mutex<Live>,
    pub(super) catalog: Option<CatalogSource>,
    pub(super) fixed_model: Option<ModelConfig>,
}

#[derive(Default)]
pub(super) struct Live {
    pub(super) workers: HashMap<String, watch::Sender<Stop>>,
    models: HashMap<String, ModelConfig>,
    pub(super) questions: HashMap<String, Question>,
    pub(super) mcp_clients: HashMap<String, Arc<crate::mcp::Runtime>>,
    pub(super) shutting_down: bool,
}

pub(super) struct LiveGuard<'a> {
    state: MutexGuard<'a, Live>,
    _migration: MutexGuard<'static, ()>,
}

impl Deref for LiveGuard<'_> {
    type Target = Live;
    fn deref(&self) -> &Self::Target { &self.state }
}

impl DerefMut for LiveGuard<'_> {
    fn deref_mut(&mut self) -> &mut Self::Target { &mut self.state }
}

pub(super) struct Question {
    pub(super) child: String,
    pub(super) target: Option<String>,
    pub(super) generation: String,
    pub(super) answer: oneshot::Sender<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Stop {
    Continue,
    Master,
    User,
    Terminate,
    Shutdown,
}

impl Stop {
    pub(super) fn cancelled(self) -> bool { self != Self::Continue }
    pub(super) fn tool_result(self) -> &'static str {
        match self {
            Self::Master => "被主智能体取消",
            Self::User => "被用户取消",
            Self::Terminate => "工作进程在此工具结果回填前中断；执行结果未知，请核实实际状态。",
            Self::Shutdown => "系统关闭，工具结果未知，请核实实际状态。",
            Self::Continue => unreachable!(),
        }
    }
}

#[derive(Clone)]
pub(crate) struct Parent {
    pub workspace_id: String,
    pub root: PathBuf,
    pub conversation_id: String,
}

pub(crate) struct Create<'a> {
    pub model: &'a ModelConfig,
    pub access: ParentAccess,
    pub owner: &'a str,
    pub execution_root: Option<&'a Path>,
    pub reasoning_effort: Option<&'a str>,
    pub args: &'a Value,
}

pub(super) fn storage(error: impl std::fmt::Display) -> String { format!("子智能体存储不可用：{error}") }

const SYSTEM_TEMPLATE: &str = include_str!("../../../prompts/sub_agent/system.txt");

fn system_base(root: &Path, access: Access, created_at: f64) -> String {
    let direct = matches!(access, Access::Full);
    let (environment, shell) = match (std::env::consts::OS, direct) {
        ("windows", true) => ("Windows 宿主机直接执行", "Windows cmd（dir、type、findstr）"),
        ("windows", false) => ("Windows 上的 WSL2 Linux 沙箱", "Linux bash；工作区内命令使用相对路径"),
        (_, true) => ("宿主机直接执行", "bash"),
        (_, false) => ("宿主机操作系统沙箱", "bash"),
    };
    let section = format!("## 执行环境\n\n- 运行环境：{environment}\n- 命令解释器：{shell}");
    let system = format!("{} ({})", std::env::consts::OS, std::env::consts::ARCH);
    let time = Timestamp::from_second(created_at.floor() as i64)
        .unwrap_or(Timestamp::UNIX_EPOCH).to_zoned(TimeZone::system())
        .strftime("%Y-%m-%d %H:%M:%S %:z").to_string();
    SYSTEM_TEMPLATE.replace("{execution_env_section}", &section)
        .replace("{system_info}", &system)
        .replace("{current_time}", &time)
        .replace("{workspace_path}", &root.display().to_string())
}

fn repair_legacy_template(context: &mut Context, root: &Path, record: &Value) {
    let prompt = &mut context.document["messages"][0]["content"];
    // Only replace the exact unrendered Rust template, not imported Python
    // prompts or dynamically saved memory, directory and later worktree notices.
    let Some(suffix) = prompt.as_str().and_then(|text| text.strip_prefix(SYSTEM_TEMPLATE)) else { return; };
    let (Ok(access), Some(created_at)) = (Access::parse(&record["access_level"]), record["created_at"].as_f64())
        else { return; };
    *prompt = json!(system_base(root, access, created_at) + suffix);
    context.rewrite_history = true;
}

#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust子智能体环境/child_prompt_contract.rs"]
mod prompt_tests;

impl SubAgents {
    pub fn with_catalog(mut self, catalog: CatalogSource, fixed_model: Option<ModelConfig>) -> Self {
        self.execution = std::sync::Arc::new(Execution { catalog: Some(catalog), fixed_model, ..Execution::default() });
        self
    }

    pub(super) fn lock(&self) -> Result<LiveGuard<'_>, String> {
        let migration = mutation_guard().map_err(storage)?;
        let state = self.execution.state.lock().map_err(|_| "子智能体监督器不可用")?;
        Ok(LiveGuard { state, _migration: migration })
    }

    pub(crate) fn create(&self, parent: &Parent, request: Create<'_>) -> Result<Value, String> {
        let Create { model, access, owner, execution_root, reasoning_effort, args } = request;
        let name = protocol::name(&args["name"])?;
        let summary = protocol::text(args, "summary")?;
        let task = protocol::text(args, "task")?;
        let intelligence = protocol::text(args, "intelligence")?;
        let deadline = protocol::timeout(args)?;
        let fixed_access = Access::parse(&args["access_level"])?;
        fixed_access.check_parent(access)?;
        let prefs = self.preferences()?;
        let model = match intelligence {
            "same" => model.clone(),
            "high" | "medium" | "low" => {
                let key = prefs[format!("sub_agent_model_{intelligence}")].as_str()
                    .filter(|key| !key.trim().is_empty()).ok_or("该智能程度尚未配置可用模型。")?;
                self.resolve_model(key, reasoning_effort)?
            }
            _ => return Err("intelligence必须选择same、high、medium或low。".into()),
        };
        let capacity = prefs["sub_agent_max_instances"].as_u64()
            .filter(|value| (1..=9_007_199_254_740_991).contains(value)).unwrap_or(20);
        let mut live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        let records = matching(&doc, parent);
        if records.values().filter(|record| string(record, "status") != Some("terminated")).count() as u64 >= capacity {
            return Err(format!("当前对话未终结的子智能体已达上限（{capacity} 个）。"));
        }
        if find_name(&records, name).is_some() {
            return Err(format!("名称「{name}」已经使用；继续工作请发送消息。"));
        }
        let agent_id = doc["tasks"].as_object().into_iter().flat_map(Map::values)
            .filter_map(|value| value["agent_id"].as_u64()).max().unwrap_or_default().checked_add(1)
            .ok_or("无法分配子智能体编号。")?;
        let working = working_directory(&parent.root, execution_root, &args["working_dir"], agent_id).map_err(storage)?;
        let execution = execution_root.unwrap_or(&working);
        let task_id = message_id("sub");
        let access = args["access_level"].clone();
        let time = now();
        let prompt = format!("{}\n\n你的名称：{name}\n默认工作目录：{}\n当前执行目录：{}\n\n[固定执行权限]\n{}。主智能体后续切换权限或执行环境不会改变你的档位。",
            system_base(&parent.root, fixed_access, time), working.display(), execution.display(), access.as_str().unwrap_or_default());
        let user = format!("任务：{summary}\n\n{task}\n\n默认工作目录：{}", working.display());
        let prompt = super::compression::refresh_memory(&prompt,&parent.root,self.registry.data_dir()).map_err(storage)?;
        let worktree_revision = self.parent_worktree(parent)?["revision"].as_u64().unwrap_or_default();
        let mut record = json!({"task_id":task_id,"agent_id":agent_id,"name":name,"display_name":name,
            "summary":summary,"task":task,"conversation_id":parent.conversation_id,
            "access_level":access,"workspace_root":parent.root,"working_dir":working,"execution_root":execution_root,
            "worktree_revision":worktree_revision,
            "rust_scope_identity":identity(&parent.root, &working, execution).map_err(storage)?,"rust_owner":owner,
            "model_key":model.key(),"intelligence":intelligence,"reasoning_effort":reasoning_effort,"thinking_mode":"thinking",
            "cost_tracking_version":1,
            "status":"idle","round_id":0,"created_at":time,"updated_at":time,"pending_work":[],"work_paused":false,
            "compress_threshold_tokens":prefs["sub_agent_compress_threshold_tokens"].as_u64().unwrap_or(250_000)});
        ChildScope::bind(object(&record)?, &parent.workspace_id, &parent.root, self.registry.data_dir())?;
        queue(&mut record, &user, deadline)?;
        let mut context = Context::new(&record, &prompt);
        doc["tasks"][&task_id] = record;
        sync_instance(&mut doc, &parent.conversation_id, &task_id)?;
        self.registry.store_with_context(&doc, Some(&mut context)).map_err(storage)?;
        live.models.insert(task_id.clone(), model);
        self.launch_locked(&mut live, &mut doc, parent, &task_id)?;
        let mut result = protocol::public_record(object(&doc["tasks"][&task_id])?);
        result["success"] = json!(true);
        result["message"] = json!(format!("子智能体「{name}」已在后台开始工作，结束本轮后会自动通知。"));
        Ok(result)
    }

    pub(crate) fn status(&self, parent: &Parent, arguments: &Value) -> Result<Value, String> {
        let _live = self.lock()?;
        let doc = self.registry.document().map_err(storage)?;
        let records = matching(&doc, parent);
        let project = |record: &Record| {
            let mut projection = protocol::public_record(record);
            if running(record) {
                let id = string(record, "task_id").unwrap_or_default();
                if !_live.workers.contains_key(id) { projection["status"] = json!("idle"); }
            }
            projection["found"] = json!(true);
            projection
        };
        let results = match arguments.get("names") {
            None => records.values().map(project).collect::<Vec<_>>(),
            Some(names) => names.as_array().filter(|names| !names.is_empty())
                .ok_or("names传入时必须为非空名称列表；省略可查询全部子智能体。")?
                .iter().map(|raw| match protocol::name(raw).and_then(|name| {
                    let id = find_name(&records, name).ok_or_else(|| format!("当前对话不存在子智能体「{name}」。"))?;
                    Ok(project(&records[id]))
                }) {
                    Ok(record) => record,
                    Err(error) => json!({"found":false,"name":raw,"error":error}),
                }).collect(),
        };
        Ok(json!({"success":true,"results":results}))
    }

    pub(crate) fn send(&self, parent: &Parent, args: &Value) -> Result<Value, String> {
        let name = protocol::name(&args["name"])?;
        let text = protocol::text(args, "message")?;
        let budget = protocol::timeout(args)?;
        let mut live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        let records = matching(&doc, parent);
        let id = find_name(&records, name).ok_or_else(|| format!("当前对话不存在子智能体「{name}」。"))?;
        let record = &mut doc["tasks"][id];
        let canonical_name = record["name"].clone();
        self.check_resumable(&live, parent, id, record)?;
        queue(record, text, budget)?;
        self.registry.store(&doc).map_err(storage)?;
        self.launch_locked(&mut live, &mut doc, parent, id)?;
        Ok(json!({"success":true,"name":canonical_name,"message":"消息已接收；本轮结束后会自动通知。"}))
    }

    pub(crate) fn stop_named(&self, parent: &Parent, args: &Value, terminate: bool) -> Result<Value, String> {
        let name = protocol::name(&args["name"])?;
        let mut live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        let records = matching(&doc, parent);
        let id = find_name(&records, name).ok_or_else(|| format!("当前对话不存在子智能体「{name}」。"))?;
        self.stop_locked(&mut live, &mut doc, parent, id, if terminate { Stop::Terminate } else { Stop::Master })
            .map(|mut value| { value.as_object_mut().unwrap().shift_remove("task_id"); value })
    }

    pub(super) fn stop_task(&self, parent: &Parent, id: &str, terminate: bool) -> Result<Value, String> {
        let mut live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        self.stop_locked(&mut live, &mut doc, parent, id, if terminate { Stop::Terminate } else { Stop::User })
    }

    pub(super) fn stop_all(&self, parent: &Parent, terminate: bool) -> Result<usize, String> {
        let mut live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        let mut count = 0;
        for (id, record) in matching(&doc, parent) {
            if (terminate && !matches!(string(&record, "status"), Some("terminated" | "failed")))
                || (!terminate && running(&record)) {
                self.stop_locked(&mut live, &mut doc, parent, &id, if terminate { Stop::Terminate } else { Stop::User })?;
                count += 1;
            }
        }
        Ok(count)
    }

    fn stop_locked(&self, live: &mut Live, doc: &mut Value, parent: &Parent, id: &str, stop: Stop) -> Result<Value, String> {
        let record = doc["tasks"].get(id).and_then(Value::as_object)
            .filter(|record| record_belongs(record, parent)).ok_or("子智能体任务不存在。")?;
        let name = string(record, "name").unwrap_or_default().to_owned();
        if string(record, "status") == Some("terminated") {
            if stop != Stop::Terminate { return Err("该子智能体已终结。".into()); }
            return Ok(json!({"success":true,"task_id":id,"name":name,"status":"terminated"}));
        }
        if stop != Stop::Terminate && !running(record) {
            return Err("子智能体未在运行。".into());
        }
        let active = live.workers.get(id);
        let pending_stop = active.is_some_and(|worker| worker.borrow().cancelled());
        if pending_stop && stop != Stop::Terminate {
            return Ok(json!({"success":true,"task_id":id,"name":name,"status":"idle"}));
        }
        let row = &mut doc["tasks"][id];
        row["work_paused"] = json!(true);
        row["updated_at"] = json!(now());
        if stop == Stop::Terminate {
            row["status"] = json!("terminated");
            row["end_reason"] = json!("terminated");
            row["pending_work"] = json!([]);
        } else if active.is_none() {
            row["status"] = json!("idle");
            row["end_reason"] = json!("stopped");
            if stop == Stop::User { push_stop_notice(doc, parent, &name)?; }
        }
        sync_instance(doc, &parent.conversation_id, id)?;
        self.registry.store(doc).map_err(storage)?;
        if let Some(worker) = active { worker.send_replace(stop); }
        if stop == Stop::Terminate {
            if active.is_none() { self.retire_child_processes(parent, id); }
            live.models.remove(id);
            if let Some(manager) = live.mcp_clients.remove(id)
                && let Ok(handle) = Handle::try_current()
            {
                handle.spawn(async move { manager.close().await });
            }
        }
        Ok(json!({"success":true,"task_id":id,"name":name,"status":if stop == Stop::Terminate {"terminated"} else {"idle"}}))
    }

    /// Restart recovery only runs on the authenticated conversation activation path.
    pub fn activate(&self, workspace: &RegisteredWorkspace, parent: &str) -> io::Result<()> {
        let parent = self.verified_parent(workspace, parent).map_err(io::Error::other)?;
        let mut live = self.lock().map_err(io::Error::other)?;
        let mut doc = self.registry.document()?;
        for (id, record) in matching(&doc, &parent) {
            if live.workers.contains_key(&id) || string(&record, "status") == Some("terminated") { continue; }
            if let Err(error) = self.launch_locked(&mut live, &mut doc, &parent, &id) {
                eprintln!("Child activation {id}: {error}");
            }
        }
        Ok(())
    }

    pub(super) fn verified_parent(&self, workspace: &RegisteredWorkspace, parent: &str) -> Result<Parent, super::RouteError> {
        let parent = normalize_conversation(parent).ok_or_else(super::RouteError::parent_missing)?;
        let root = self.check_workspace(workspace)?;
        let nav = self.runtime.navigation_session(&workspace.id, &parent).map_err(super::RouteError::storage)?;
        if nav.is_none() { return Err(super::RouteError::parent_missing()); }
        Ok(Parent { workspace_id: workspace.id.clone(), root, conversation_id: parent })
    }

    pub(super) fn launch_locked(&self, live: &mut Live, doc: &mut Value, parent: &Parent, id: &str) -> Result<(), String> {
        if live.shutting_down || live.workers.contains_key(id) { return Ok(()); }
        let record = doc["tasks"][id].clone();
        let fields = object(&record)?;
        if record["status"] == "terminated" { return Ok(()); }
        let queued = record["pending_work"].as_array().is_some_and(|queue| !queue.is_empty());
        let start = record["work_paused"] != true && queued;
        let interrupted = running(fields);
        if !start && !interrupted { return Ok(()); }
        let mut context = Context::load(&self.registry, id)?;
        context.threshold = record["compress_threshold_tokens"].as_u64().filter(|v| *v > 0).unwrap_or(250_000);
        if interrupted { context.complete_missing(Stop::Terminate); }
        if !start {
            context.set_status("idle", "interrupted");
            let record = &mut doc["tasks"][id];
            record["status"] = json!("idle");
            record["end_reason"] = json!("interrupted");
            record["updated_at"] = json!(now());
            sync_instance(doc, &parent.conversation_id, id)?;
            return self.registry.store_with_context(doc, Some(&mut context)).map_err(storage);
        }
        let handle = Handle::try_current().map_err(|_| "无法启动子智能体执行器。")?;
        let scope = match self.apply_parent_worktree(parent, &mut doc["tasks"][id], &mut context)? {
            Some(scope) => scope,
            None => ChildScope::bind(fields, &parent.workspace_id, &parent.root, self.registry.data_dir())?,
        };
        repair_legacy_template(&mut context, &parent.root, &record);
        let model = self.model(live, id, &record)?.with_session(id, false);
        let timeout = consume_work(&mut doc["tasks"][id], &mut context, true)?;
        sync_instance(doc, &parent.conversation_id, id)?;
        self.registry.store_with_context(doc, Some(&mut context)).map_err(storage)?;
        let (sender, receiver) = watch::channel(Stop::Continue);
        live.workers.insert(id.into(), sender);
        let app = self.clone();
        let id = id.to_owned();
        let parent = parent.clone();
        handle.spawn(async move { worker::supervise(app, parent, id, receiver, scope, context, model, timeout).await; });
        Ok(())
    }

    pub(super) fn check_resumable(&self, live: &Live, parent: &Parent, id: &str, record: &Value) -> Result<(), String> {
        if record["status"] == "terminated" { return Err("该子智能体已终结，不能继续激活。".into()); }
        if !live.workers.contains_key(id) {
            let mut context = Context::load(&self.registry, id)?;
            if self.apply_parent_worktree(parent, &mut record.clone(), &mut context)?.is_none() {
                ChildScope::bind(object(record)?, &parent.workspace_id, &parent.root, self.registry.data_dir())?;
            }
            self.model(live, id, record)?;
        }
        Ok(())
    }

    fn model(&self, live: &Live, id: &str, record: &Value) -> Result<ModelConfig, String> {
        if let Some(model) = live.models.get(id) { return Ok(model.clone()); }
        let key = record["model_key"].as_str().ok_or("原模型不可用。")?;
        self.resolve_model(key, record["reasoning_effort"].as_str())
    }

    fn resolve_model(&self, key: &str, reasoning: Option<&str>) -> Result<ModelConfig, String> {
        if let Some(model) = self.execution.fixed_model.as_ref().filter(|model| model.key() == key) {
            return Ok(model.clone());
        }
        let catalog = self.execution.catalog.as_ref().ok_or("原模型不可用。")?.load();
        ModelConfig::from_catalog(catalog.select_for_turn(Some(key), true, reasoning)?)
    }

    fn preferences(&self) -> Result<Value, String> {
        let data = self.execution.catalog.as_ref().map_or(self.registry.data_dir(), CatalogSource::host_data_dir);
        match std::fs::read(data.join("personalization.json")) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(storage),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(json!({})),
            Err(error) => Err(storage(error)),
        }
    }

    fn parent_worktree(&self, parent: &Parent) -> Result<Value, String> {
        let directory = self.registry.data_dir().join("conversations").join(&parent.workspace_id);
        if !directory.join(crate::conversation_db::FILENAME).is_file() {
            return Ok(Value::Null);
        }
        crate::conversation_db::Database::open(&directory)?
            .field(&parent.conversation_id, "metadata", "git_worktree")
            .map(Option::unwrap_or_default)
    }

    fn apply_parent_worktree(
        &self, parent: &Parent, record: &mut Value, context: &mut Context,
    ) -> Result<Option<ChildScope>, String> {
        let state = self.parent_worktree(parent)?;
        let Some(revision) = state["revision"].as_u64().filter(|revision| {
            *revision > record["worktree_revision"].as_u64().unwrap_or_default()
        }) else { return Ok(None); };
        let destination = state["directory"].as_str().map(Path::new).ok_or("父对话工作树缺少执行目录。")?;
        let inside = destination.starts_with(&parent.root);
        let verified = if inside && destination != parent.root {
            crate::worktree::Worktree::new(&parent.root).and_then(|service| service.inspect(destination))
                .map_err(|error| error.to_string())?.directory
        } else {
            parent.root.clone()
        };
        let working = record["working_dir"].as_str().map(Path::new).ok_or("子智能体工作目录无效。")?;
        let binding = identity(&parent.root, working, &verified).map_err(storage)?;
        let mut updated = record.clone();
        updated["execution_root"] = json!(verified);
        updated["rust_scope_identity"] = binding;
        updated["worktree_revision"] = json!(revision);
        let scope = ChildScope::bind(object(&updated)?, &parent.workspace_id, &parent.root, self.registry.data_dir())?;
        let branch = state["branch"].as_str().unwrap_or_default();
        context.append_worktree(&verified, branch, revision, inside);
        *record = updated;
        Ok(Some(scope))
    }

    pub(super) fn child_worktree_at_safe_point(&self, parent: &Parent, id: &str, context: &mut Context)
        -> Result<Option<ChildScope>, String>
    {
        let live = self.lock()?;
        if live.workers.get(id).is_none_or(|worker| worker.borrow().cancelled()) { return Ok(None); }
        let mut doc = self.registry.document().map_err(storage)?;
        let Some(scope) = self.apply_parent_worktree(parent, &mut doc["tasks"][id], context)? else {
            return Ok(None);
        };
        sync_instance(&mut doc, &parent.conversation_id, id)?;
        self.registry.store_with_context(&doc, Some(context)).map_err(storage)?;
        Ok(Some(scope))
    }

    pub(super) fn consume_guidance(&self, parent: &Parent, id: &str, context: &mut Context) -> Result<(), String> {
        let _live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        let record = &mut doc["tasks"][id];
        if record["work_paused"] == true || !record["pending_work"].as_array().is_some_and(|queue| !queue.is_empty()) { return Ok(()); }
        consume_work(record, context, false)?;
        sync_instance(&mut doc, &parent.conversation_id, id)?;
        self.registry.store_with_context(&doc, Some(context)).map_err(storage)
    }

    pub(super) fn finish(&self, parent: &Parent, id: &str, context: &mut Context, result: worker::End) -> Result<(), String> {
        let mut live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        let Some(worker) = live.workers.get(id) else { return Ok(()); };
        let stop = *worker.borrow();
        let natural_answer = match &result {
            worker::End::Completed(text) if stop == Stop::Continue && !text.trim().is_empty() => {
                context.pending_answer_question.as_ref().filter(|(key, generation)| {
                    live.questions.get(key).is_some_and(|question| {
                        question.target.as_deref() == Some(id) && question.generation == *generation
                    })
                }).map(|(key, _)| (key.clone(), text.trim().to_owned()))
            }
            _ => None,
        };
        let record = &mut doc["tasks"][id];
        let name = record["name"].as_str().unwrap_or_default().to_owned();
        let terminated = record["status"] == "terminated" || stop == Stop::Terminate;
        let (reason, content, notify) = match (stop, result) {
            (Stop::Continue, worker::End::Completed(text)) => {
                let text = if text.trim().is_empty() { "本轮已结束，模型未返回正文。".into() } else { text };
                ("completed", Some(text), true)
            }
            (Stop::Continue, worker::End::Timeout) => ("timeout", Some("本轮时间预算已到期。".into()), true),
            (Stop::Continue, worker::End::Failed(error)) => ("error", Some(error), true),
            (Stop::Terminate, _) => ("terminated", None, false),
            (Stop::Shutdown, _) => ("interrupted", None, false),
            (source, _) => ("stopped", None, source == Stop::User),
        };
        if stop.cancelled() { context.complete_missing(stop); }
        else if reason == "error" { context.complete_missing(Stop::Terminate); }
        let status = if terminated { "terminated" } else { "idle" };
        context.set_status(status, reason);
        record["status"] = json!(status);
        record["end_reason"] = json!(reason);
        record["updated_at"] = json!(now());
        let stopped_output = (reason == "stopped").then(|| {
            format!("{}，本轮已停止；保留上下文，可发送消息继续。", stop.tool_result())
        });
        let output = content.as_deref().or(stopped_output.as_deref()).map(|text| context.final_output(text));
        if let Some(text) = content.as_deref() {
            record["final_result"] = json!(text);
            if notify && !text.trim().is_empty() {
                push_notice(&mut doc, parent, notice(NoticeType::Output, &name, None, &message_id("msg"), text.trim()))?;
            }
        } else if notify { push_stop_notice(&mut doc, parent, &name)?; }
        sync_instance(&mut doc, &parent.conversation_id, id)?;
        self.registry.store_checkpoint(
            &doc, Some(context), output.as_ref().map(|(event, output)| (event, output)),
        ).map_err(storage)?;
        if let Some((key, text)) = natural_answer
            && let Some(question) = live.questions.remove(&key)
        {
            let _ = question.answer.send(text);
        }
        live.workers.remove(id);
        live.questions.retain(|_, question| question.child != id && (!terminated || question.target.as_deref() != Some(id)));
        if !terminated { self.launch_locked(&mut live, &mut doc, parent, id)?; }
        Ok(())
    }

    pub(super) fn fail_worker(&self, id: &str) {
        if let Ok(mut live) = self.lock() {
            live.workers.remove(id);
            live.questions.retain(|_, question| question.child != id);
        }
    }
}

fn object(value: &Value) -> Result<&Record, String> { value.as_object().ok_or_else(|| "无效的子智能体状态。".into()) }

pub(super) fn matching(doc: &Value, parent: &Parent) -> Records {
    doc["tasks"].as_object().into_iter().flat_map(|tasks| tasks.iter()).filter_map(|(id, value)| {
        let record = value.as_object()?;
        (super::valid_task_id(id) && string(record, "task_id") == Some(id) && record_belongs(record, parent)).then(|| (id.clone(), record.clone()))
    }).collect()
}

fn record_belongs(record: &Record, parent: &Parent) -> bool {
    owns_path(record, &parent.root) && string(record, "conversation_id").and_then(normalize_conversation).as_deref() == Some(&parent.conversation_id)
}

pub(super) fn find_name<'a>(records: &'a Records, name: &str) -> Option<&'a str> {
    let folded = protocol::casefold(name);
    records.iter().find(|(_, record)| string(record, "name").is_some_and(|candidate| protocol::casefold(candidate) == folded)).map(|(id, _)| id.as_str())
}

pub(super) fn queue<'a>(record: &'a mut Value, message: &str, timeout: Option<u64>) -> Result<&'a mut Value, String> {
    if record["pending_work"].is_null() { record["pending_work"] = json!([]); }
    record["work_paused"] = json!(false);
    let pending = record["pending_work"].as_array_mut().ok_or("无效的待处理子智能体消息。")?;
    pending.push(json!({"message":message,"timeout":timeout,"rust_work_id":message_id("child_work")}));
    Ok(pending.last_mut().expect("queued child work"))
}

fn consume_work(record: &mut Value, context: &mut Context, new_round: bool) -> Result<Option<Instant>, String> {
    let work = std::mem::replace(&mut record["pending_work"], json!([]));
    let work = work.as_array().ok_or("无效的待处理子智能体消息。")?;
    let mut deadline = None;
    if new_round {
        let id = record["round_id"].as_u64().unwrap_or_default().saturating_add(1);
        let timeout = work.first().and_then(|entry| entry["timeout"].as_u64());
        record["round_id"] = json!(id);
        record["timeout_seconds"] = json!(timeout);
        record["status"] = json!("running");
        record["end_reason"] = Value::Null;
        context.start(id);
        deadline = timeout.and_then(|seconds| Instant::now().checked_add(Duration::from_secs(seconds)));
    }
    for entry in work {
        context.append_work(entry)?;
    }
    record["updated_at"] = json!(now());
    Ok(deadline)
}

fn sync_instance(doc: &mut Value, parent: &str, id: &str) -> Result<(), String> {
    let record = &doc["tasks"][id];
    let row = json!({"agent_id":record["agent_id"],"display_name":record["name"],"task_id":id,
        "status":record["status"],"summary":record["summary"],"created_at":record["created_at"],
        "last_output":record["final_result"].as_str().unwrap_or_default().chars().take(500).collect::<String>()});
    let state = &mut doc["states"][parent];
    if state.is_null() { *state = json!({"conversation_id":parent,"agents":[],"pending_master_messages":[]}); }
    if state["agents"].is_null() { state["agents"] = json!([]); }
    let agents = state["agents"].as_array_mut().ok_or("无效的子智能体协作状态。")?;
    if let Some(agent) = agents.iter_mut().find(|agent| agent["task_id"] == id) {
        *agent = row;
    } else if row["agent_id"].is_u64() {
        agents.push(row);
    }
    Ok(())
}

fn push_stop_notice(doc: &mut Value, parent: &Parent, name: &str) -> Result<(), String> {
    push_notice(doc, parent, notice(NoticeType::Stopped, name, None, &message_id("msg"), &format!("[子智能体 {name} 本轮已停止]")))
}

pub(super) fn push_notice(doc: &mut Value, parent: &Parent, text: String) -> Result<(), String> {
    let state = &mut doc["states"][&parent.conversation_id];
    if state.is_null() { *state = json!({"conversation_id":parent.conversation_id,"agents":[],"pending_master_messages":[]}); }
    if state["rust_workspace_root"].as_str().is_some_and(|root| !super::same_path(Path::new(root), &parent.root)) {
        return Err("重复的主对话 ID 无法共享子智能体消息队列。".into());
    }
    state["rust_workspace_root"] = json!(parent.root);
    if state["pending_master_messages"].is_null() { state["pending_master_messages"] = json!([]); }
    state["pending_master_messages"].as_array_mut().ok_or("无效的子智能体消息队列。")?.push(json!(text));
    Ok(())
}

pub(super) fn now() -> f64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64() }
