use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{Arc, Mutex, MutexGuard, OnceLock},
    time::{Duration, Instant},
};

use axum::{
    Json,
    extract::{Path as RoutePath, Query, State},
    http::StatusCode,
};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use tokio::sync::oneshot;
use uuid::Uuid;

use crate::chat_mode::{ChatError, ChatMode, model, routes, store, tasks::now, tree};
use crate::{
    approval::ApprovalPrincipal,
    runtime::{ModelConfig, TaskApprovalSettings, resolve_task_approval_settings},
    tools::ToolSet,
    workspace_catalog::{RegisteredWorkspace, manage::WorkspaceRegistry},
};

use super::{
    actionable, current_runtime, decide, interrupt, materials, pause, public_state, resolve, state,
    state_mut, sync, terminal,
};

pub(super) const COUNTDOWN: Duration = Duration::from_secs(30);
const LEASE: Duration = Duration::from_secs(3);
const CONTINUE: &str = "不切换到 Agent，继续在 Chat 中处理当前任务";

pub(super) fn runtime_id() -> &'static str {
    static ID: OnceLock<String> = OnceLock::new();
    ID.get_or_init(|| Uuid::new_v4().simple().to_string())
}

type Key = (String, String);

#[derive(Default)]
struct Live {
    leases: HashMap<Key, Instant>,
    workers: HashMap<Key, (Uuid, oneshot::Sender<()>)>,
}

pub(in crate::chat_mode) struct Deletion<'a> {
    _lock: MutexGuard<'a, Live>,
}

#[derive(Clone)]
struct Target {
    registry: WorkspaceRegistry,
    path: PathBuf,
}

#[derive(Clone, Default)]
pub(in crate::chat_mode) struct Handoffs {
    live: Arc<Mutex<Live>>,
    target: Option<Target>,
}

impl Handoffs {
    fn lock(&self) -> Result<MutexGuard<'_, Live>, ChatError> {
        self.live
            .lock()
            .map_err(|_| ChatError::storage("Agent handoff lock is unavailable"))
    }
}

impl ChatMode {
    /// Install the trusted Host registry and its dedicated default project path.
    pub fn with_handoff_workspace(mut self, registry: WorkspaceRegistry, path: PathBuf) -> Self {
        self.handoffs.target = Some(Target { registry, path });
        self
    }

    /// Keep the guard alive until the caller has removed the Chat document.
    pub(in crate::chat_mode) fn cancel_handoffs(
        &self,
        conversation: &str,
    ) -> Result<Deletion<'_>, ChatError> {
        let mut live = self.handoffs.lock()?;
        self.storage(|paths| {
            let mut raw = paths.read_raw(conversation)?;
            let mut changed = false;
            if let Some(entries) = raw["metadata"]["chat"]["handoffs"].as_object_mut() {
                for (id, value) in entries {
                    if let Some(state) = value.as_object_mut() {
                        if state.get("countdown_enabled") == Some(&Value::Bool(true)) {
                            pause(state);
                            changed = true;
                        }
                        if let Some((_, signal)) =
                            live.workers.remove(&(conversation.to_owned(), id.clone()))
                        {
                            let _ = signal.send(());
                            if state.get("status").and_then(Value::as_str) == Some("preparing") {
                                interrupt(state);
                                state.insert("error".into(), json!("Handoff cancelled"));
                                changed = true;
                            }
                        }
                    }
                }
            }
            if changed {
                paths.write_raw(conversation, raw, false)?;
            }
            Ok(())
        })?;
        live.leases.retain(|(chat, _), _| chat != conversation);
        Ok(Deletion { _lock: live })
    }

    pub(in crate::chat_mode) fn handoff_presentation(
        &self,
        conversation: &str,
        identity: Option<&str>,
        release: bool,
    ) -> Result<(), ChatError> {
        let identity = identity
            .filter(|id| (1..=128).contains(&id.chars().count()))
            .ok_or_else(|| ChatError::bad_request("Invalid Chat presentation identity"))?;
        let mut live = self.handoffs.lock()?;
        let changed = self.storage(|paths| {
            let mut raw = paths.read_raw(conversation)?;
            let chat = &mut raw["metadata"]["chat"];
            if release && chat["handoff_presentation_id"] != identity {
                return Ok(false);
            }
            if !release && chat["handoff_presentation_id"] == identity {
                return Ok(false);
            }
            chat["handoff_presentation_id"] = if release {
                Value::Null
            } else {
                json!(identity)
            };
            sync(&mut raw, None)?;
            if let Some(entries) = raw["metadata"]["chat"]["handoffs"].as_object_mut() {
                for state in entries.values_mut().filter_map(Value::as_object_mut) {
                    pause(state);
                }
            }
            paths.write_raw(conversation, raw, false)?;
            Ok(true)
        })?;
        if changed {
            live.leases.retain(|(chat, _), _| chat != conversation);
        }
        Ok(())
    }
}

#[derive(Deserialize)]
pub(in crate::chat_mode) struct Identity {
    handoff_id: Option<String>,
    message_id: Option<String>,
}

fn identity<'a>(handoff: Option<&'a str>, message: Option<&'a str>) -> Result<&'a str, ChatError> {
    handoff
        .filter(|id| !id.is_empty())
        .or(message)
        .filter(|id| !id.is_empty() && id.len() <= 200)
        .ok_or_else(|| ChatError::bad_request("Missing suggestion identity"))
}

type Reply = (StatusCode, Json<Value>);

pub(in crate::chat_mode) async fn get(
    State(mode): State<ChatMode>,
    RoutePath(conversation): RoutePath<String>,
    Query(query): Query<Identity>,
) -> Reply {
    run(move || {
        read_public(
            &mode,
            &conversation,
            identity(query.handoff_id.as_deref(), query.message_id.as_deref())?,
        )
    })
    .await
}

pub(in crate::chat_mode) async fn post(
    State(mode): State<ChatMode>,
    RoutePath(conversation): RoutePath<String>,
    Json(body): Json<Value>,
) -> Reply {
    run(move || {
        let identity = identity(body["handoff_id"].as_str(), body["message_id"].as_str())?;
        let operation = tree::operation_id(body.get("operation_id"))?;
        let mut live = mode.handoffs.lock()?;
        let id = mode.storage(|paths| resolve(&paths.read_raw(&conversation)?, identity))?;
        let key = (conversation, id);
        match body["decision"].as_str() {
            Some("observe" | "pause") => {
                let owner = body["presentation_id"]
                    .as_str()
                    .ok_or_else(|| ChatError::bad_request("Missing presentation identity"))?;
                observe(&mode, &key, &mut live, owner, body["decision"] == "pause")?;
            }
            Some("select") => {
                select(&mode, &key)?;
                live.leases.remove(&key);
            }
            Some("decline") => {
                decline(&mode, &key, "button")?;
                live.leases.remove(&key);
            }
            Some("accept") => {
                accept(&mode, &key, &operation, &mut live)?;
                live.leases.remove(&key);
            }
            _ => return Err(ChatError::bad_request("Invalid handoff decision")),
        }
        read_public(&mode, &key.0, &key.1)
    })
    .await
}

async fn run(operation: impl FnOnce() -> Result<Value, ChatError> + Send + 'static) -> Reply {
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(value)) => (StatusCode::OK, Json(json!({"success":true,"data":value}))),
        Ok(Err(error)) => routes::error(error),
        Err(_) => routes::error(ChatError::storage(
            "Agent handoff operation was interrupted",
        )),
    }
}

fn read_public(mode: &ChatMode, conversation: &str, identity: &str) -> Result<Value, ChatError> {
    mode.storage(|paths| {
        let document = paths.read_raw(conversation)?;
        let id = resolve(&document, identity)?;
        let mut result = public_state(state(&document, &id)?);
        result["server_time"] = json!(now());
        result["host"] = json!(true);
        Ok(result)
    })
}

fn same_branch(document: &Value, current: &Map<String, Value>) -> bool {
    let chat = &document["metadata"]["chat"];
    tree::active_turn(document).is_none()
        && current.get("leaf_id") == Some(&chat["latest_leaf_id"])
        && chat["latest_leaf_id"].is_string()
}

pub(super) fn check_decline(document: &Value, id: &str) -> Result<(), ChatError> {
    let current = state(document, id)?;
    ensure_actionable(document, current)
}

fn ensure_actionable(document: &Value, current: &Map<String, Value>) -> Result<(), ChatError> {
    if !actionable(current) || current.get("present") == Some(&Value::Bool(false)) {
        return Err(ChatError::conflict(
            "Suggestion has already been decided or removed",
        ));
    }
    if !same_branch(document, current) {
        return Err(ChatError::conflict("Chat has moved to another branch"));
    }
    Ok(())
}

fn select(mode: &ChatMode, key: &Key) -> Result<(), ChatError> {
    mode.storage(|paths| {
        let mut document = paths.read_raw(&key.0)?;
        let current = state(&document, &key.1)?;
        ensure_actionable(&document, current)?;
        super::before_send(&document, "")?;
        let current = state_mut(&mut document, &key.1)?;
        current.insert("status".into(), json!("selecting"));
        pause(current);
        paths.write_raw(&key.0, document, false)
    })
}

fn observe(
    mode: &ChatMode,
    key: &Key,
    live: &mut Live,
    owner: &str,
    suspend: bool,
) -> Result<(), ChatError> {
    let expire = mode.storage(|paths| {
        let mut document = paths.read_raw(&key.0)?;
        let current = state(&document, &key.1)?;
        if current.get("status").and_then(Value::as_str) != Some("pending")
            || current.get("countdown_enabled") != Some(&Value::Bool(true))
            || current.get("presentation_id").and_then(Value::as_str) != Some(owner)
        {
            return Ok(false);
        }
        let due = current
            .get("deadline")
            .and_then(Value::as_f64)
            .is_some_and(|deadline| now() >= deadline);
        let fresh = live
            .leases
            .get(key)
            .is_some_and(|last| last.elapsed() <= LEASE);
        if suspend
            || !current_runtime(current)
            || !same_branch(&document, current)
            || document["metadata"]["chat"]["handoff_presentation_id"] != owner
            || (live.leases.contains_key(key) && !fresh)
            || (due && !fresh)
        {
            pause(state_mut(&mut document, &key.1)?);
            paths.write_raw(&key.0, document, false)?;
            live.leases.remove(key);
            return Ok(false);
        }
        live.leases.insert(key.clone(), Instant::now());
        Ok(due)
    })?;
    // Only a live observation received at or after the deadline may take action.
    if expire {
        decline(mode, key, "timeout")?;
        live.leases.remove(key);
    }
    Ok(())
}

fn decline(mode: &ChatMode, key: &Key, source: &str) -> Result<(), ChatError> {
    let leaf = mode.storage(|paths| {
        let mut doc = paths.read_raw(&key.0)?;
        let current = state(&doc, &key.1)?;
        if terminal(current)
            || (current.get("status").and_then(Value::as_str) == Some("preparing")
                && current_runtime(current))
        {
            return Ok(None);
        }
        if current.get("status").and_then(Value::as_str) == Some("preparing") {
            interrupt(state_mut(&mut doc, &key.1)?);
            paths.write_raw(&key.0, doc.clone(), false)?;
        }
        let current = state(&doc, &key.1)?;
        if !same_branch(&doc, current) {
            decide(state_mut(&mut doc, &key.1)?, "superseded", "branch");
            paths.write_raw(&key.0, doc, false)?;
            return Ok(None);
        }
        Ok(current
            .get("leaf_id")
            .and_then(Value::as_str)
            .map(str::to_owned))
    })?;
    let Some(leaf) = leaf else {
        return Ok(());
    };
    let accepted = mode.create_task(&json!({
        "conversation_id":key.0,"parent_id":leaf,"operation_id":format!("{}:decline",key.1),"message":CONTINUE,
    }))?;
    mode.storage(|paths| {
        let mut doc = paths.read_raw(&key.0)?;
        let current = state_mut(&mut doc, &key.1)?;
        decide(current, "declined", source);
        current.insert("task_id".into(), accepted["task_id"].clone());
        paths.write_raw(&key.0, doc, false)
    })
}

fn accept(mode: &ChatMode, key: &Key, operation: &str, live: &mut Live) -> Result<(), ChatError> {
    if live.workers.contains_key(key) {
        return Ok(());
    }
    let handle = tokio::runtime::Handle::try_current()
        .map_err(|_| ChatError::unavailable("No Tokio runtime is available"))?;
    let job = mode.storage(|paths| {
        let mut document = paths.read_raw(&key.0)?;
        let current = state(&document, &key.1)?;
        if current.get("status").and_then(Value::as_str) == Some("completed") {
            return Ok(None);
        }
        if current.get("status").and_then(Value::as_str) == Some("preparing") {
            interrupt(state_mut(&mut document, &key.1)?);
        }
        let current = state(&document, &key.1)?;
        ensure_actionable(&document, current)?;
        super::before_send(&document, "")?;
        if !materials::valid_id(&key.1) {
            return Err(ChatError::bad_request("Invalid Agent handoff identity"));
        }
        let snapshot = materials::freeze(paths.root(), &document, &key.1, &current["leaf_id"])?;
        let current = state_mut(&mut document, &key.1)?;
        let agent_id = current
            .get("agent_conversation_id")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| format!("conv_{}", key.1));
        current.insert("operation_id".into(), json!(operation));
        current.insert("status".into(), json!("preparing"));
        current.insert("runtime_id".into(), json!(runtime_id()));
        current.insert("error".into(), json!(""));
        current.insert("selection".into(), json!({}));
        pause(current);
        paths.write_raw(&key.0, document, false)?;
        Ok(Some(Job {
            key: key.clone(),
            snapshot,
            agent_id,
            operation: operation.to_owned(),
            generation: Uuid::new_v4(),
        }))
    })?;
    if let Some(job) = job {
        let (signal, cancel) = oneshot::channel();
        live.workers.insert(key.clone(), (job.generation, signal));
        let mode = mode.clone();
        handle.spawn(async move {
            execute(mode, job, cancel).await;
        });
    }
    Ok(())
}

struct Job {
    key: Key,
    snapshot: Value,
    agent_id: String,
    operation: String,
    generation: Uuid,
}

async fn execute(mode: ChatMode, job: Job, mut cancel: oneshot::Receiver<()>) {
    let job = Arc::new(job);
    let prepared = tokio::select! {
        biased;
        _ = &mut cancel => Err(ChatError::conflict("Handoff cancelled")),
        prepared = prepare(&mode, job.clone()) => prepared,
    };
    let _ = tokio::task::spawn_blocking(move || {
        let Ok(mut live) = mode.handoffs.lock() else {
            return;
        };
        if live
            .workers
            .get(&job.key)
            .is_none_or(|(id, _)| *id != job.generation)
        {
            return;
        }
        live.workers.remove(&job.key);
        let result = prepared.and_then(|prepared| launch(&mode, &job, prepared));
        if let Err(error) = result {
            let _ = mode.storage(|paths| {
                let mut doc = paths.read_raw(&job.key.0)?;
                let current = state_mut(&mut doc, &job.key.1)?;
                if is_job(current, &job) {
                    current.insert("status".into(), json!("failed"));
                    current.insert("error".into(), json!(error.message));
                    current.insert("error_status".into(), json!(error.status));
                    pause(current);
                    paths.write_raw(&job.key.0, doc, false)?;
                }
                Ok(())
            });
        }
    })
    .await;
}

fn is_job(current: &Map<String, Value>, job: &Job) -> bool {
    current_runtime(current)
        && current.get("status").and_then(Value::as_str) == Some("preparing")
        && current.get("operation_id").and_then(Value::as_str) == Some(job.operation.as_str())
}

struct Prepared {
    workspace: RegisteredWorkspace,
    summary: String,
}

async fn prepare(mode: &ChatMode, job: Arc<Job>) -> Result<Prepared, ChatError> {
    let configured = mode
        .handoffs
        .target
        .clone()
        .ok_or_else(|| ChatError::unavailable("Agent default workspace is unavailable"))?;
    let local = mode.clone();
    let locked_job = job.clone();
    let (workspace, previous_summary) = tokio::task::spawn_blocking(move || {
        let workspace = target(&local, &configured)?;
        let summary =
            materials::existing_summary(&workspace.path, &locked_job.key.1, &locked_job.snapshot)?;
        Ok::<_, ChatError>((workspace, summary))
    })
    .await
    .map_err(ChatError::storage)??;
    if let Some(summary) = previous_summary {
        return Ok(Prepared { workspace, summary });
    }
    let summary = summarize(mode, &job).await?;
    let local = mode.clone();
    tokio::task::spawn_blocking(move || {
        materials::export(
            &local.root,
            &workspace.path,
            &job.key.1,
            &job.snapshot,
            &summary,
        )?;
        Ok(Prepared { workspace, summary })
    })
    .await
    .map_err(ChatError::storage)?
}

fn target(mode: &ChatMode, config: &Target) -> Result<RegisteredWorkspace, ChatError> {
    let path = config
        .path
        .to_str()
        .ok_or_else(|| ChatError::storage("Invalid default workspace path"))?;
    let updated = config
        .registry
        .create(path, "Default", false)
        .map_err(ChatError::storage)?;
    let id = updated.details["workspace"]["workspace_id"]
        .as_str()
        .ok_or_else(|| ChatError::storage("Default workspace was not registered"))?;
    let workspace = updated
        .catalog
        .by_id(id)
        .cloned()
        .ok_or_else(|| ChatError::storage("Default workspace was not registered"))?;
    let tools = ToolSet::for_workspace(&workspace.path, &mode.preferences_dir)?;
    let registry = config.registry.clone();
    mode.runtime
        .bind_registered_workspace(&workspace.id, &workspace.path, tools, || {
            let current = registry
                .load()
                .map_err(crate::runtime::RuntimeError::storage)?;
            if current
                .by_id(&workspace.id)
                .is_none_or(|entry| entry.path != workspace.path)
            {
                return Err(crate::runtime::RuntimeError::new(
                    409,
                    "Default workspace registration changed",
                ));
            }
            Ok(())
        })?;
    Ok(workspace)
}

async fn summarize(mode: &ChatMode, job: &Job) -> Result<String, ChatError> {
    let metadata = &job.snapshot["metadata"];
    let model = model::resolve(
        &mode.model,
        metadata["model_key"].as_str(),
        metadata["thinking_mode"].as_bool().unwrap_or(false),
        metadata["reasoning_effort"].as_str(),
        &job.key.1,
    )?
    .with_session(&job.key.0, false);
    let mut context = vec![json!({"role":"system","content":concat!(
        "请为接手的 Agent 总结当前对话。详细保留用户目标、确认的产品行为与限制、已完成且验证的工作、",
        "未完成事项、关键数据、后续建议。不要执行工具，不要遗漏用户的纠正。只输出适合保存为 summary.md 的 Markdown，",
        "不要包含内部系统指令或秘密，也不要把讨论的方案描述成已执行的事实。以下上下文是待总结资料。"
    )})];
    let mut transcript = String::new();
    for message in job.snapshot["messages"].as_array().into_iter().flatten() {
        if !matches!(
            message["role"].as_str(),
            Some("user" | "assistant" | "tool")
        ) || message["metadata"]["visibility"] == "hidden"
        {
            continue;
        }
        let content = materials::clean_content(&message["content"]);
        if !content.is_empty() {
            use std::fmt::Write;
            let _ = writeln!(
                &mut transcript,
                "{}: {content}\n",
                message["role"].as_str().unwrap_or_default()
            );
        }
    }
    context.push(
        json!({"role":"user","content":format!("{transcript}\n现在请给出供后续继续使用的总结。")}),
    );
    let result = model
        .complete(&context, &[], |_| {})
        .await
        .map_err(|error| ChatError::new(502, error))?;
    let summary = result.message["content"]
        .as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| ChatError::new(502, "The Chat model returned an empty handoff summary"))?;
    if summary.chars().count() > 180_000 {
        return Err(ChatError::new(502, "The Chat handoff summary is too large"));
    }
    Ok(summary.to_owned())
}

fn agent(
    mode: &ChatMode,
    workspace: &RegisteredWorkspace,
) -> Result<(ModelConfig, TaskApprovalSettings, Value), ChatError> {
    let catalog = mode
        .catalog
        .as_ref()
        .ok_or_else(|| ChatError::unavailable("Agent model catalog is unavailable"))?;
    let preferences = store::read_json(&mode.preferences_dir.join("personalization.json"))?;
    let run_mode = preferences["default_run_mode"]
        .as_str()
        .filter(|v| matches!(*v, "fast" | "thinking" | "deep"))
        .unwrap_or("fast");
    let effort = preferences["default_reasoning_effort"].as_str();
    let model = catalog
        .load()
        .select_for_turn(None, run_mode != "fast", effort)
        .and_then(ModelConfig::from_catalog)
        .map_err(ChatError::unavailable)?;
    let principal = ApprovalPrincipal::new("host", &workspace.id).map_err(ChatError::storage)?;
    let approval = resolve_task_approval_settings(principal, catalog)?;
    let mut metadata = mode
        .personalization
        .as_ref()
        .map(|prefs| prefs.agent_creation_preferences())
        .transpose()?
        .unwrap_or_else(|| json!({}));
    let work = preferences["default_work_mode"]
        .as_str()
        .filter(|v| matches!(*v, "plan" | "ask" | "execute"))
        .unwrap_or("plan");
    let permission = preferences["default_permission_mode"]
        .as_str()
        .filter(|v| {
            matches!(
                *v,
                "readonly" | "approval" | "auto_approval" | "unrestricted"
            )
        })
        .unwrap_or("approval");
    metadata["work_mode"] = json!(work);
    metadata["permission_mode"] = json!(if work == "plan" {
        "readonly"
    } else {
        permission
    });
    metadata["execution_mode"] = json!("sandbox");
    metadata["network_permission"] = json!("restricted");
    if work == "plan" {
        metadata["pre_plan_permission_mode"] = json!(permission);
    }
    let payload = json!({"model_key":model.key(),"run_mode":run_mode,"thinking_mode":run_mode!="fast",
        "reasoning_effort":effort,"message_source":"chat_handoff","handoff_agent_metadata":metadata});
    Ok((model, approval, payload))
}

fn launch(mode: &ChatMode, job: &Job, prepared: Prepared) -> Result<(), ChatError> {
    let (conversation, id) = &job.key;
    mode.storage(|paths| {
        let document = paths.read_raw(conversation)?;
        let current = state(&document, id)?;
        if !is_job(current, job) || !same_branch(&document, current) {
            return Err(ChatError::conflict("Chat has moved to another branch"));
        }
        Ok(())
    })?;
    let workspace = &prepared.workspace;
    let (model, approval, mut payload) = agent(mode, workspace)?;
    let relative = materials::relative_path(id);
    let summary_path = format!("{relative}/summary.md");
    let conversation_path = format!("{relative}/conversation.md");
    payload["message"] = json!(format!(
        concat!(
            "请从 Chat 交接内容继续处理用户的原始任务。先阅读交接摘要和对话记录，必要时查看同目录的 media。",
            "遵循当前 Agent 自身的系统规则和权限。以下路径均相对于当前工作区根目录。\n\n",
            "交接摘要：{}\n对话记录：{}\n\n{}"
        ),
        summary_path,
        conversation_path,
        prepared.summary.trim()
    ));
    payload["handoff_operation_id"] = json!(job.operation);
    let result = mode.runtime.start_chat_handoff(
        workspace,
        &job.agent_id,
        conversation,
        id,
        &payload,
        model,
        approval,
    )?;
    let agent_url = format!(
        "/{}?workspace_id={}",
        job.agent_id.trim_start_matches("conv_"),
        workspace.id
    );
    mode.storage(|paths| {
        let mut document = paths.read_raw(conversation)?;
        let current = state_mut(&mut document, id)?;
        let leaf = current["leaf_id"].clone();
        decide(current, "completed", "button");
        current.insert("agent_conversation_id".into(), json!(job.agent_id));
        current.insert("agent_task_id".into(), result["data"]["task_id"].clone());
        current.insert("target_workspace_id".into(), json!(workspace.id));
        current.insert("workspace_id".into(), json!(workspace.id));
        current.insert("agent_url".into(), json!(agent_url));
        current.insert("materials".into(), json!(relative));
        current.insert("error".into(), json!(""));
        current.remove("error_status");
        if let Some(entries) = document["metadata"]["chat"]["handoffs"].as_object_mut() {
            for (other, current) in entries
                .iter_mut()
                .filter_map(|(id, value)| Some((id, value.as_object_mut()?)))
            {
                if other != id && actionable(current) && current.get("leaf_id") == Some(&leaf) {
                    decide(current, "superseded", "sibling_switched");
                }
            }
        }
        paths.write_raw(conversation, document, false)
    })
}
