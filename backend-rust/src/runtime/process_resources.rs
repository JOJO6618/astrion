//! Bind process resources to the accepted task and expose the existing Host REST views.

use std::{io, path::Path};
#[cfg(test)]
use std::fs;

use axum::{
    Extension, Json, Router,
    extract::{Path as RoutePath, Query, State},
    http::{StatusCode, header::{CACHE_CONTROL, HeaderName}},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use uuid::Uuid;

use crate::{
    approval::{ApprovalPrincipal, ApprovalType, ExecutionMode, PermissionMode, Status as ApprovalStatus, ToolRequest},
    catalog::CatalogSource,
    process_runtime::{self, Invocation, MonitorStage, Network, Owner, SandboxAccess, Scope, terminal_allowed, tools_category},
    tools::read_file::ReadFile,

    web_auth::BrowserSession,
    workspace_catalog::RegisteredWorkspace,
};

use super::{ModelConfig, Origin, Runtime, RuntimeError, Status, TaskAdmission, TaskApprovalSettings,
    approval::{claim_process_approval, create_process_approval, task_scope}, emit_user_message, goals::verify_conversation_root, iso_time, now,
    store::Store, sub_agent_execution::notice_model, resolve_task_approval_settings, verify_workspace_binding, writable_task};

const HOST: &str = "host";
const MONITOR_CHARS: usize = 60_000;

type Reply = (StatusCode, [(HeaderName, &'static str); 1], Json<Value>);

impl Runtime {
    /// Called once with the trusted Host deployment configuration path.
    pub fn configure_host_processes(&self, deploy: &Path) -> Result<(), RuntimeError> {
        let mut store = self.store()?;
        let data = store.data_dir.clone();
        store.process_resources.configure(deploy, &data).map_err(RuntimeError::storage)
    }

    /// Called only by the normal main Agent, after selecting its fixed tools.
    pub(super) fn process_tool_schemas(&self, task_id: &str) -> Result<Vec<Value>, String> {
        let store = self.store().map_err(|error| error.message)?;
        let Ok((scope, metadata)) = live_scope(&store, task_id) else { return Ok(Vec::new()); };
        if !store.process_resources.available_mode(scope.mode) { return Ok(Vec::new()); }
        let mut names = super::tool_categories::category_tools(&store.data_dir,"terminal_realtime",&metadata)?;
        if !terminal_allowed(&store.data_dir).unwrap_or(false) {
            names.retain(|name| name == "sleep");
        }
        names.extend(super::tool_categories::category_tools(&store.data_dir,"terminal_command",&metadata)?);
        let full_access = scope.mode == ExecutionMode::Sandbox && (scope.approval_required || scope.access.write)
            && store.process_resources.available_mode(ExecutionMode::Direct);
        Ok(process_runtime::schemas().into_iter().filter(|tool| {
            tool["function"]["name"].as_str().is_some_and(|name| names.iter().any(|enabled| enabled == name))
        }).map(|mut tool| {
            if !full_access && tool["function"]["name"] == "run_command"
                && let Some(properties) = tool["function"]["parameters"]["properties"].as_object_mut()
            {
                properties.shift_remove("request_full_access");
                properties.shift_remove("full_access_reason");
            }
            tool
        }).collect())
    }

    pub(super) async fn execute_process_tool(
        &self, task_id: &str, call_id: &str, name: &str, arguments: Value, batch: &[Value],
    ) -> Result<Value, String> {
        let invocation = match Invocation::parse(name, &arguments) {
            Ok(invocation) => invocation,
            Err(error) => return Ok(failure(error)),
        };
        let Some(category) = tools_category(name) else { return Ok(failure("Unknown process tool")) };
        let admitted_scope = {
            let store = self.store().map_err(|error| error.message)?;
            match process_admission(&store, task_id, name, category, &invocation) {
                Ok(scope) => scope,
                Err(error) => return Ok(failure(error)),
            }
        };
        let full_access = matches!(invocation, Invocation::Command { full_access: true, .. });
        if full_access && (admitted_scope.mode != ExecutionMode::Sandbox
            || !(admitted_scope.approval_required || admitted_scope.access.write)
            || !self.host_direct_execution_available())
        {
            return Ok(failure("The current scope cannot request full access for a command"));
        }
        let needs_approval = full_access || admitted_scope.approval_required && invocation_needs_approval(&invocation, admitted_scope.mode);
        let approved = if needs_approval {
            let request = ToolRequest {
                tool_call_id: call_id.to_owned(),
                tool_name: name.to_owned(),
                arguments: arguments.as_object().expect("validated invocation").clone(),
                preview: json!({"command":arguments["command"],"session_name":arguments["session_name"],
                    "full_access_reason":arguments["full_access_reason"]})
                    .as_object().expect("preview object").clone(),
                approval_type: if full_access {
                    ApprovalType::FullAccess
                } else if admitted_scope.mode == ExecutionMode::Direct {
                    ApprovalType::DirectExecution
                } else {
                    ApprovalType::SandboxWrite
                },
                browser_site: None,
            };
            let item = {
                let mut store = self.store().map_err(|error| error.message)?;
                create_process_approval(&mut store, task_id, &request)?
            };
            let item = self.resolve_tool_approval(item, batch).await?;
            if item.status != ApprovalStatus::Approved {
                return Ok(failure(item.reason.as_deref().unwrap_or("该进程操作未获批准。")));
            }
            Some((request, item, batch.to_vec()))
        } else {
            None
        };
        let runtime = self.clone();
        let task_id = task_id.to_owned();
        let name = name.to_owned();
        let started = tokio::task::spawn_blocking(move || {
            let mut store = runtime.store().map_err(|error| error.message)?;
            let mut scope = process_admission(&store, &task_id, &name, category, &invocation)?;
            if scope != admitted_scope {
                return Err("Task process authority changed after approval".into());
            }
            if let Some((request, item, batch)) = approved {
                claim_process_approval(&mut store, &task_id, &request, &item, &batch)?;
                scope.access.write = matches!(&invocation, Invocation::Command { .. });
                if full_access {
                    scope.mode = ExecutionMode::Direct;
                }
            } else if full_access || scope.approval_required && invocation_needs_approval(&invocation, scope.mode) {
                return Err("Required process approval is unavailable".into());
            }
            scope.approval_required = false;
            store.process_resources.start(scope, invocation).map_err(|error| error.to_string())
        }).await.map_err(|_| "Process admission interrupted".to_owned())?;
        let operation = match started {
            Ok(operation) => operation,
            Err(error) => return Ok(failure(error)),
        };
        let (result, consumed) = operation.finish().await;
        if let Some((owner, id)) = consumed {
            self.store().map_err(|error| error.message)?.process_resources.consume(&owner, &id);
        }
        Ok(result)
    }

    /// Stop user-visible background jobs in one conversation, then mark all of
    /// its terminal records as notified. Persistent shells have their own lifecycle.
    pub fn stop_conversation_processes(&self, workspace: &str, conversation: &str) -> Result<usize, RuntimeError> {
        let mut store = self.store()?;
        verify_workspace_binding(&store, workspace, None)?;
        let owner = Owner { user: HOST.into(), workspace_id: workspace.into(), workspace: store.workspaces[workspace].clone(), conversation: conversation.into(), actor: None };
        Ok(store.process_resources.stop_background(&owner))
    }

    /// Call under the existing mutation lock for deletion or workspace detachment.
    pub(super) fn close_process_scope(store: &mut Store, workspace: &str, conversation: Option<&str>) {
        store.process_resources.close_conversation(workspace, conversation);
    }

    pub fn shutdown_process_resources(&self) -> Result<(), RuntimeError> {
        self.store()?.process_resources.shutdown();
        Ok(())
    }

    pub(super) fn conversation_has_background(store: &Store, workspace: &str, conversation: &str) -> bool {
        store.workspaces.get(workspace).is_some_and(|path| store.process_resources.has_pending(&Owner {
            user: HOST.into(), workspace_id: workspace.into(), workspace: path.clone(), conversation: conversation.into(), actor: None,
        }))
    }

    pub(super) fn process_notices_waiting(
        &self, workspace: &RegisteredWorkspace, conversation: &str,
    ) -> Result<bool, RuntimeError> {
        let store = self.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        let owner = registered_owner(workspace, conversation);
        Ok(store.process_resources.has_notices(&owner))
    }

    /// Drive ready Host process notifications independently of browser polling.
    /// The ownership comes from admitted processes, not from scanned documents
    /// or user-supplied IDs. Admission and consumption are still atomic with
    /// the inline and HTTP paths inside `dispatch_process_notice`.
    pub fn poll_idle_process_notices(&self, catalog: &CatalogSource, fixed: Option<&ModelConfig>) -> Result<(), RuntimeError> {
        let candidates = {
            let store = self.store()?;
            if store.shutting_down { return Ok(()); }
            store.process_resources.ready_notice_owners().into_iter().filter(|owner| {
                owner.user == HOST
                    && store.workspaces.get(&owner.workspace_id) == Some(&owner.workspace)
                    && !store.tasks.values().any(|task| task.workspace_id == owner.workspace_id
                        && task.conversation_id == owner.conversation && task.status == Status::Running)
            }).collect::<Vec<_>>()
        };
        for owner in candidates {
            let principal = ApprovalPrincipal::new(HOST, &owner.workspace_id).map_err(RuntimeError::storage)?;
            let workspace = RegisteredWorkspace {
                id: owner.workspace_id, path: owner.workspace, label: String::new(),
            };
            // A stale scope, paused goal or currently unavailable model must
            // not block delivery to other conversations. A later tick can retry.
            let _ = self.poll_process_notices(&workspace, &owner.conversation, principal, catalog, fixed);
        }
        Ok(())
    }

    pub(super) fn poll_process_notices(
        &self, workspace: &RegisteredWorkspace, conversation: &str, principal: ApprovalPrincipal,
        catalog: &CatalogSource, fixed: Option<&ModelConfig>,
    ) -> Result<(), RuntimeError> {
        if principal.workspace_id() != workspace.id { return Err(RuntimeError::session_not_found()); }
        if self.process_notices_waiting(workspace, conversation)? {
            let (document, _) = self.store()?.header(&workspace.id, conversation)?;
            let model = notice_model(&document["metadata"], catalog, fixed)?;
            let settings = resolve_task_approval_settings(principal, catalog)?;
            self.dispatch_process_notice(workspace, conversation, model, settings)?;
        }
        Ok(())
    }

    pub(super) fn dispatch_process_notice(
        &self, workspace: &RegisteredWorkspace, conversation: &str, model: ModelConfig,
        approval: TaskApprovalSettings,
    ) -> Result<Option<Value>, RuntimeError> {
        let mut store = self.store()?;
        let Some(owner) = request_owner(&store, workspace, Some(conversation))? else { return Ok(None); };
        if store.tasks.values().any(|task| task.workspace_id == owner.workspace_id
            && task.conversation_id == owner.conversation && task.status == Status::Running)
        {
            return Ok(None);
        }
        let notices = store.process_resources.take_notices(&owner);
        let Some((notice, remaining)) = notices.split_first() else { return Ok(None); };
        let rest = notice_ids(remaining);
        store.process_resources.restore_notices(&owner, &rest);
        let message = notice_message(notice, now(), false, crate::web_assets::personalization::english(&store.data_dir));
        let payload = json!({"conversation_id":owner.conversation,"message":message["content"],
            "model_key":model.key(),"message_source":"background_command"});
        let tools = store.workspace_tools.get(&workspace.id).unwrap_or(&self.tools).clone();
        let admission = TaskAdmission { exclusive_workspace:false, title:None, metadata:None,
            new_conversation_id:None, user_message_metadata:Some(message["metadata"].clone()),
            tools:std::sync::Arc::new(tools) };
        let result = self.admit_goal_task(&mut store, &workspace.id, &payload, &model,
            admission, Some(approval), Origin::Automatic);
        match result {
            Ok(admitted) => {
                drop(store);
                Ok(Some(self.spawn_admitted(admitted, model)))
            }
            Err(error) => {
                store.process_resources.restore_notices(&owner, &notice_ids(std::slice::from_ref(notice)));
                if error.status == 409 { Ok(None) } else { Err(error) }
            }
        }
    }

    /// Persist notices under the same writer lock as the active task.
    pub(super) fn drain_process_notices(&self, task_id: &str, preceding: Option<Value>) -> Result<Vec<Value>, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        let Ok(owner) = live_owner(&store, task_id) else { return Ok(Vec::new()); };
        let notices = store.process_resources.take_notices(&owner);
        if notices.is_empty() { return Ok(Vec::new()); }
        let timestamp = now();
        let english = crate::web_assets::personalization::english(&store.data_dir);
        let messages = notices.iter().map(|notice| notice_message(notice, timestamp, true, english)).collect::<Vec<_>>();
        let durable = preceding.into_iter().chain(messages.iter().cloned()).collect();
        if let Err(error) = store.append_messages(&owner.workspace_id, &owner.conversation, durable, timestamp, None, None) {
            store.process_resources.restore_notices(&owner, &notice_ids(&notices));
            return Err(error.message);
        }
        let task = writable_task(&mut store, task_id)?;
        for message in &messages {
            emit_user_message(task, message["content"].as_str().unwrap_or_default(), message["metadata"].clone());
        }
        task.checkpoint_events();
        Ok(messages.into_iter().map(|message| json!({"role":"user","content":message["content"]})).collect())
    }

    /// Generate an opaque display ID distinct from any model-supplied tool call ID.
    pub(super) fn process_monitor_id() -> String { format!("tool_{}", Uuid::new_v4().simple()) }

    /// Read through the existing capability, then recheck the task before caching.
    pub(super) async fn capture_process_monitor(
        &self, task_id: &str, id: &str, stage: MonitorStage, tool: &str, args: &Value,
    ) -> Option<Value> {
        let key = match tool { "read_file" => "path", "write_file" | "edit_file" => "file_path", _ => return None };
        let path = args[key].as_str()?.to_owned();
        let id = id.to_owned();
        let task_id = task_id.to_owned();
        let runtime = self.clone();
        tokio::task::spawn_blocking(move || {
            let (owner, root, data) = {
                let store = runtime.store().ok()?;
                let task = store.tasks.get(&task_id)?;
                let owner = live_owner(&store, &task_id).ok()?;
                (owner.clone(), task.execution_root.clone().unwrap_or(owner.workspace), store.data_dir.clone())
            };
            let file = ReadFile::open(&root, &data).ok()?;
            let result = file.execute(&json!({"path":path,"max_chars":MONITOR_CHARS}));
            let content = result["content"].as_str().filter(|content| !content.is_empty())?;
            let snapshot = json!({"path":result["path"],"content":content,"lines":content.lines().count()});
            let mut store = runtime.store().ok()?;
            if live_owner(&store, &task_id).ok()? != owner { return None; }
            store.process_resources.record_monitor(owner, &id, stage, snapshot.clone());
            Some(snapshot)
        }).await.ok().flatten()
    }
}

fn process_admission(
    store: &Store, task_id: &str, name: &str, category: &str, invocation: &Invocation,
) -> Result<Scope, String> {
    let (scope, metadata) = live_scope(store, task_id)?;
    if !store.process_resources.available_mode(scope.mode) {
        return Err("The selected Host execution environment is unavailable".into());
    }
    if !super::tool_categories::category_tools(&store.data_dir,category,&metadata)?.iter().any(|tool| tool == name) {
        return Err("Process tool is disabled by the current tool policy".into());
    }
    if let Some(key) = invocation.strict_key() {
        super::strict_guides::enforce(&store.data_dir, key, &metadata)?;
    }
    Ok(scope)
}

fn invocation_needs_approval(invocation: &Invocation, mode: ExecutionMode) -> bool {
    match invocation {
        Invocation::Open { .. } | Invocation::Close { .. } | Invocation::Reset { .. } | Invocation::List => true,
        Invocation::Command { command, .. } => mode == ExecutionMode::Direct || !readonly_command(command),
        Invocation::Input { .. } => mode == ExecutionMode::Direct,
        _ => false,
    }
}

// Classify only commands that can start immediately in the native read-only
// sandbox. A permissive match never changes the Seatbelt write rules.
fn readonly_command(command: &str) -> bool {
    if command.contains(['\r', '\n', ';', '>', '<', '`', '&']) || command.contains("$(") {
        return false;
    }
    let lower = command.to_ascii_lowercase();
    if [".env", ".ssh", ".aws", "token", "secret", "-exec", "-delete", "-ok"]
        .iter().any(|word| lower.contains(word)) {
        return false;
    }
    command.split('|').all(|segment| {
        let mut words = segment.split_whitespace();
        match words.next() {
            Some("ls" | "pwd" | "tree" | "cat" | "head" | "tail" | "grep" | "rg"
                | "wc" | "du" | "stat" | "file" | "find" | "echo" | "printf" | "cut" | "tr" | "uniq") => true,
            Some("git") => matches!(words.next(), Some("status" | "log" | "diff" | "show" | "rev-parse")),
            Some("sed") => words.next() == Some("-n"),
            _ => false,
        }
    })
}

fn notice_message(notice: &Value, timestamp: f64, inline: bool, english: bool) -> Value {
    use std::fmt::Write;
    let command = notice["command"].as_str().unwrap_or_default();
    let output = notice["output"].as_str().filter(|text| !text.is_empty()).unwrap_or("[no_output]");
    let mut content = String::from("[系统通知|background_command]\n");
    if inline {
        let header = if english { "[Background run_command finished]" } else { "[后台 run_command 完成]" };
        let _ = write!(content, "{header}\n{output}");
        content.truncate(content.trim_end().len());
    } else {
        let (header, command_label, exit_label, output_label) = if english {
            ("Background commands finished.", "Command: ", "Exit code: ", "Output:")
        } else {
            ("后台指令已完成。", "命令：", "退出码：", "输出：")
        };
        content.push_str(header);
        if !command.is_empty() {
            let _ = write!(content, "\n\n{command_label}{command}");
        }
        if let Some(code) = notice["return_code"].as_i64() {
            let _ = write!(content, "\n{exit_label}{code}");
        }
        let _ = write!(content, "\n\n{output_label}\n{output}");
    }
    let id = format!("background_notice_{}", notice["command_id"].as_str().unwrap_or_default());
    json!({"role":"user","content":content,"message_id":id,"metadata":{
        "message_id":id,"message_source":"background_command","auto_message_type":"background_command",
        "starts_work":false,"command_id":notice["command_id"],
        "sub_agent_notice":true,"background_command_notice":true,"timestamp":iso_time(timestamp),
    }})
}

fn notice_ids(notices: &[Value]) -> Vec<String> {
    notices.iter().filter_map(|notice| notice["command_id"].as_str()).map(str::to_owned).collect()
}

fn live_owner(store: &Store, task_id: &str) -> Result<Owner, String> {
    let task = store.tasks.get(task_id).filter(|task| task.status == Status::Running && !task.cancel_requested)
        .ok_or("Task stopped")?;
    let principal = &task.approval.as_ref().ok_or("Task lacks an authenticated execution scope")?.principal;
    if principal.username() != HOST || principal.workspace_id() != task.workspace_id {
        return Err("Task process owner does not match its authenticated workspace".into());
    }
    verify_workspace_binding(store, &task.workspace_id, None).map_err(|error| error.message)?;
    Ok(Owner { user: HOST.into(), workspace_id: task.workspace_id.clone(), workspace: store.workspaces[&task.workspace_id].clone(), conversation: task.conversation_id.clone(), actor: None })
}

pub(super) fn live_scope(store: &Store, task_id: &str) -> Result<(Scope, Value), String> {
    let owner = live_owner(store, task_id)?;
    let task = &store.tasks[task_id];
    let (header, _) = store.header(&owner.workspace_id, &owner.conversation).map_err(|error| error.message)?;
    verify_conversation_root(&owner.workspace, &header).map_err(|error| error.message)?;
    let metadata = &header["metadata"];
    if !metadata.get("conversation_kind").is_none_or(|kind| kind == "agent") || metadata["git_review"].is_object() {
        return Err("The current task does not use the main Host process scope".into());
    }
    let execution_root = task.execution_root.as_ref().unwrap_or(&owner.workspace).clone();
    if !execution_root.starts_with(&owner.workspace) {
        return Err("External worktree process authority is unavailable".into());
    }
    let (_, settings) = task_scope(store, task_id)?;
    let (permission, execution) = settings.process_modes().ok_or("Invalid task process mode")?;
    let write = permission == PermissionMode::Unrestricted;
    let approval_required = matches!(permission, PermissionMode::Approval | PermissionMode::AutoApproval);
    let network = Network::from_metadata(&metadata["network_permission"]).map_err(|error| error.to_string())?;
    let scope = Scope { owner, execution_root, access: SandboxAccess { write, network }, mode: execution, approval_required };
    Ok((scope, metadata.clone()))
}

fn request_owner(store: &Store, workspace: &RegisteredWorkspace, conversation: Option<&str>) -> Result<Option<Owner>, RuntimeError> {
    verify_workspace_binding(store, &workspace.id, Some(&workspace.path))?;
    let Some(id) = conversation.map(str::trim).filter(|id| !id.is_empty()) else { return Ok(None) };
    let (header, _) = store.header(&workspace.id, id)?;
    verify_conversation_root(&workspace.path, &header).map_err(|_| RuntimeError::session_not_found())?;
    if !header["metadata"].get("conversation_kind").is_none_or(|kind| kind == "agent") { return Ok(None); }
    Ok(Some(registered_owner(workspace, id)))
}

fn registered_owner(workspace: &RegisteredWorkspace, conversation: &str) -> Owner {
    Owner { user: HOST.into(), workspace_id: workspace.id.clone(), workspace: workspace.path.clone(), conversation: conversation.into(), actor: None }
}

/// Mount inside the existing Gateway authorization and workspace middleware.
/// This is the sole process route accepting a Host Bearer credential.
pub fn gateway_router(runtime: Runtime) -> Router {
    Router::new().route("/api/background_commands", get(background_list).options(options)).with_state(runtime)
}

/// Mount inside the authenticated Host Web router and its workspace middleware.
pub fn browser_router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/background_commands/{id}", get(background_detail).options(options))
        .route("/api/background_commands/{id}/cancel", post(background_cancel).options(options))
        .route("/api/background_commands/stop_all", post(stop_background_scope).options(options))
        .route("/api/terminals", get(terminals).options(options))
        .route("/api/terminals/{name}/output", get(terminal_output).options(options))
        .with_state(runtime)
}

/// The monitor frontend supplies an opaque execution ID, without workspace selection.
pub fn monitor_router(runtime: Runtime) -> Router {
    Router::new().route("/api/gui/monitor_snapshot", get(monitor).options(options)).with_state(runtime)
}

#[derive(Default, Deserialize)]
struct ConversationQuery {
    conversation_id: Option<String>,
    limit: Option<String>,
    lines: Option<String>,
}

async fn options() -> StatusCode { StatusCode::NO_CONTENT }

async fn background_list(
    State(runtime): State<Runtime>, Extension(workspace): Extension<RegisteredWorkspace>, Query(query): Query<ConversationQuery>,
) -> Reply {
    blocking(move || {
        let store = runtime.store()?;
        let owner = list_owner(&store, &workspace, &query)?;
        Ok(store.process_resources.background_list(owner.as_ref(), numeric(query.limit.as_deref(), 200, 1000)))
    }).await
}

async fn background_detail(
    State(runtime): State<Runtime>, Extension(workspace): Extension<RegisteredWorkspace>, Query(query): Query<ConversationQuery>,
    RoutePath(id): RoutePath<String>, browser: Option<Extension<BrowserSession>>,
) -> Reply {
    if browser.is_none() { return browser_required(); }
    blocking(move || {
        let store = runtime.store()?;
        let owner = request_owner(&store, &workspace, query.conversation_id.as_deref())?.ok_or_else(missing_conversation)?;
        store.process_resources.background_detail(&owner, &id).map_err(io_status)
    }).await
}

async fn background_cancel(
    State(runtime): State<Runtime>, Extension(workspace): Extension<RegisteredWorkspace>, Query(query): Query<ConversationQuery>,
    RoutePath(id): RoutePath<String>, browser: Option<Extension<BrowserSession>>,
) -> Reply {
    if browser.is_none() { return browser_required(); }
    let cancellation = tokio::task::spawn_blocking(move || {
        let mut store = runtime.store()?;
        let owner = request_owner(&store, &workspace, query.conversation_id.as_deref())?.ok_or_else(missing_conversation)?;
        store.process_resources.cancel_background(&owner, &id).map_err(io_status)
    }).await;
    match cancellation {
        Ok(Ok(pending)) => response(pending.finish().await.map_err(io_status)),
        Ok(Err(error)) => response(Err(error)),
        Err(_) => response(Err(interrupted())),
    }
}

async fn stop_background_scope(
    State(runtime): State<Runtime>, Extension(workspace): Extension<RegisteredWorkspace>,
    browser: Option<Extension<BrowserSession>>, Json(query): Json<ConversationQuery>,
) -> Reply {
    if browser.is_none() { return browser_required(); }
    blocking(move || {
        let mut store = runtime.store()?;
        let owner = request_owner(&store, &workspace, query.conversation_id.as_deref())?
            .ok_or_else(missing_conversation)?;
        let count = store.process_resources.stop_background(&owner);
        Ok(json!({"success":true,"data":{"stopped_count":count}}))
    }).await
}

async fn terminals(
    State(runtime): State<Runtime>, Extension(workspace): Extension<RegisteredWorkspace>, Query(query): Query<ConversationQuery>,
    browser: Option<Extension<BrowserSession>>,
) -> Reply {
    if browser.is_none() { return browser_required(); }
    blocking(move || {
        let store = runtime.store()?;
        check_terminal_policy(&store.data_dir)?;
        let owner = list_owner(&store, &workspace, &query)?;
        Ok(store.process_resources.terminal_list(owner.as_ref()))
    }).await
}

async fn terminal_output(
    State(runtime): State<Runtime>, Extension(workspace): Extension<RegisteredWorkspace>, Query(query): Query<ConversationQuery>,
    RoutePath(name): RoutePath<String>, browser: Option<Extension<BrowserSession>>,
) -> Reply {
    if browser.is_none() { return browser_required(); }
    blocking(move || {
        let store = runtime.store()?;
        check_terminal_policy(&store.data_dir)?;
        let owner = request_owner(&store, &workspace, query.conversation_id.as_deref())?.ok_or_else(missing_conversation)?;
        store.process_resources.terminal_output(&owner, &name, numeric(query.lines.as_deref(), 100, 2000)).map_err(io_status)
    }).await
}

#[derive(Deserialize)]
struct MonitorQuery {
    #[serde(rename = "executionId")]
    display_id: Option<String>,
    execution_id: Option<String>,
    id: Option<String>,
    stage: Option<String>,
}

async fn monitor(
    State(runtime): State<Runtime>, Query(query): Query<MonitorQuery>, browser: Option<Extension<BrowserSession>>,
) -> Reply {
    if browser.is_none() { return browser_required(); }
    blocking(move || {
        let id = [query.display_id, query.execution_id, query.id].into_iter().flatten().find(|id| !id.is_empty())
            .ok_or_else(|| RuntimeError::bad_request("Missing execution ID"))?;
        let stage = if query.stage.is_some_and(|value| value.eq_ignore_ascii_case("after")) { MonitorStage::After } else { MonitorStage::Before };
        let store = runtime.store()?;
        let missing = || RuntimeError::new(404, "Monitor snapshot not found");
        let (owner, snapshot) = store.process_resources.monitor(HOST, &id, stage).ok_or_else(missing)?;
        verify_workspace_binding(&store, &owner.workspace_id, Some(&owner.workspace)).map_err(|_| missing())?;
        let (header, _) = store.header(&owner.workspace_id, &owner.conversation).map_err(|_| missing())?;
        verify_conversation_root(&owner.workspace, &header).map_err(|_| missing())?;
        Ok(json!({"success":true,"snapshot":snapshot,"stage":stage.text()}))
    }).await
}

impl Runtime {
    pub(crate) fn command_terminals(&self, workspace: &RegisteredWorkspace, conversation: Option<&str>) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        let owner = if conversation.is_some() && terminal_allowed(&store.data_dir).map_err(RuntimeError::storage)? {
            request_owner(&store,workspace,conversation)?
        } else { None };
        Ok(store.process_resources.terminal_list(owner.as_ref()))
    }
}

fn check_terminal_policy(data: &Path) -> Result<(), RuntimeError> {
    if terminal_allowed(data).map_err(RuntimeError::storage)? { Ok(()) }
    else { Err(RuntimeError::new(403, "Realtime terminal has been disabled by the administrator")) }
}

fn list_owner(store: &Store, workspace: &RegisteredWorkspace, query: &ConversationQuery) -> Result<Option<Owner>, RuntimeError> {
    match request_owner(store, workspace, query.conversation_id.as_deref()) {
        Err(error) if matches!(error.status, 400 | 404) => Ok(None),
        result => result,
    }
}

fn missing_conversation() -> RuntimeError { RuntimeError::new(404, "Conversation not found") }
fn interrupted() -> RuntimeError { RuntimeError::new(500, "Process resource operation interrupted") }
fn numeric(value: Option<&str>, default: usize, maximum: i64) -> usize {
    value.and_then(|value| value.trim().parse::<i64>().ok()).map_or(default, |number| number.clamp(1, maximum) as usize)
}

fn io_status(error: io::Error) -> RuntimeError {
    let status = match error.kind() {
        io::ErrorKind::NotFound => 404,
        io::ErrorKind::PermissionDenied => 403,
        _ => 503,
    };
    RuntimeError::new(status, error.to_string())
}

fn failure(error: impl Into<String>) -> Value { json!({"success":false,"status":"error","error":error.into()}) }
fn browser_required() -> Reply { response(Err(RuntimeError::new(401, "Browser session required"))) }

fn response(result: Result<Value, RuntimeError>) -> Reply {
    let (status, value) = match result {
        Ok(value) => (StatusCode::OK, value),
        Err(error) => (StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR), json!({"success":false,"error":error.message})),
    };
    (status, [(CACHE_CONTROL, "private, no-store")], Json(value))
}

async fn blocking(run: impl FnOnce() -> Result<Value, RuntimeError> + Send + 'static) -> Reply {
    response(tokio::task::spawn_blocking(run).await.unwrap_or_else(|_| Err(interrupted())))
}

#[cfg(test)]
#[path = "../../../test/2026-11-26_Rust运行资源/process_resources.rs"]
mod tests;

#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust后台完成通知/notice_message.rs"]
mod notice_message_tests;
