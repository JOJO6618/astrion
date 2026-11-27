//! Approval orchestration for capabilities bound to an admitted Runtime task.

use std::{
    fs,
    path::{Component, Path},
    sync::Arc,
    time::Duration,
};

use serde_json::{Value, json};

use super::{
    ModelConfig, Runtime, RuntimeError,
    billing::{BillingScope, ModelPrice},
    store::{Status as TaskStatus, Store},
    writable_task,
};
use crate::{
    approval::{
        Approval, ApprovalError, ApprovalPrincipal, ApprovalType, ExecutionMode, PermissionMode,
        Status, TaskScope, ToolRequest, exempt_workspace_write,
        review::{self, ReviewSnapshot},
    },
    catalog::CatalogSource,
    model::retry::Attempt,
    tools::write_file::{EDIT_NAME, WRITE_NAME, WorkspaceFileWriter},
};

const APPROVAL_WAIT: Duration = Duration::from_secs(3600);
const POLL_INTERVAL: Duration = Duration::from_millis(100);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum WorkMode {
    Plan,
    Ask,
    Interactive,
}

/// Workspace identity and defaults resolved by the trusted task admission path.
#[derive(Clone)]
pub struct TaskApprovalSettings {
    pub(super) principal: ApprovalPrincipal,
    permission: PermissionMode,
    pub(super) work_mode: WorkMode,
    execution_mode: ExecutionMode,
    pub(super) valid_modes: bool,
    reviewer: Option<ModelConfig>,
    max_review_rounds: usize,
}

pub fn resolve_task_approval_settings(
    principal: ApprovalPrincipal,
    source: &CatalogSource,
) -> Result<TaskApprovalSettings, RuntimeError> {
    let path = source.host_data_dir().join("personalization.json");
    let preferences = match fs::read(path) {
        Ok(bytes) => serde_json::from_slice::<Value>(&bytes).map_err(RuntimeError::storage)?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => json!({}),
        Err(error) => return Err(RuntimeError::storage(error)),
    };
    if !preferences.is_object() {
        return Err(RuntimeError::storage("invalid personalization object"));
    }
    let config = &preferences["review_agents"]["auto_approval"];
    let catalog = source.load();
    let visible = catalog.models_response();
    let default = visible["items"].as_array().and_then(|models| {
        let first = models.first()?;
        let all_codex = models.iter().all(|model| model["is_chatgpt_model"] == true);
        let preferred = all_codex
            .then(|| {
                models.iter().find(|model| {
                    model["model_key"]
                        .as_str()
                        .is_some_and(|key| key.contains("-luna"))
                })
            })
            .flatten();
        preferred.unwrap_or(first)["model_key"].as_str()
    });
    let key = config["model"]
        .as_str()
        .map(str::trim)
        .filter(|key| !key.is_empty())
        .or(default);
    let reviewer = key
        .and_then(|key| catalog.select(Some(key), true).ok())
        .and_then(|model| ModelConfig::from_catalog(model).ok());
    let mut settings = TaskApprovalSettings {
        principal,
        permission: PermissionMode::Approval,
        work_mode: WorkMode::Plan,
        execution_mode: ExecutionMode::Sandbox,
        valid_modes: true,
        reviewer,
        max_review_rounds: config["max_rounds"].as_u64().unwrap_or(10).clamp(1, 50) as usize,
    };
    settings.apply_conversation_modes(&json!({
        "permission_mode":preferences["default_permission_mode"],
        "work_mode":preferences["default_work_mode"],
    }));
    Ok(settings)
}

impl TaskApprovalSettings {
    /// Called with the selected conversation document while its admission lock is held.
    pub(super) fn apply_conversation_modes(&mut self, metadata: &Value) {
        self.valid_modes &=
            apply_mode(metadata, "permission_mode", &mut self.permission, |value| {
                Some(match value {
                    "readonly" => PermissionMode::ReadOnly,
                    "approval" => PermissionMode::Approval,
                    "auto_approval" => PermissionMode::AutoApproval,
                    "unrestricted" => PermissionMode::Unrestricted,
                    _ => return None,
                })
            });
        self.valid_modes &= apply_mode(metadata, "work_mode", &mut self.work_mode, |value| {
            Some(match value {
                "plan" => WorkMode::Plan,
                "ask" => WorkMode::Ask,
                "execute" => WorkMode::Interactive,
                _ => return None,
            })
        });
        self.valid_modes &= apply_mode(
            metadata,
            "execution_mode",
            &mut self.execution_mode,
            |value| {
                Some(match value {
                    "sandbox" => ExecutionMode::Sandbox,
                    "direct" => ExecutionMode::Direct,
                    _ => return None,
                })
            },
        );
    }

    pub(super) fn process_modes(&self) -> Option<(PermissionMode, ExecutionMode)> {
        let permission = if self.work_mode == WorkMode::Plan { PermissionMode::ReadOnly } else { self.permission };
        self.valid_modes.then_some((permission, self.effective_execution()))
    }

    pub(super) fn child_access(&self) -> crate::sub_agents::ParentAccess {
        crate::sub_agents::ParentAccess {
            limited: !self.valid_modes || self.work_mode == WorkMode::Plan || self.permission == PermissionMode::ReadOnly,
            direct: self.effective_execution() == ExecutionMode::Direct,
            unrestricted: self.permission == PermissionMode::Unrestricted,
        }
    }

    fn effective_execution(&self) -> ExecutionMode {
        if self.work_mode == WorkMode::Plan || self.permission == PermissionMode::ReadOnly {
            ExecutionMode::Sandbox
        } else {
            self.execution_mode
        }
    }

    fn route(&self, scope: &TaskScope, request: &ToolRequest) -> Result<WriteRoute, &'static str> {
        if !self.valid_modes || self.effective_execution() != ExecutionMode::Sandbox {
            return Err("当前任务的执行模式不支持工作区写入。");
        }
        if self.work_mode == WorkMode::Plan {
            let path = request
                .arguments
                .get("file_path")
                .and_then(Value::as_str)
                .unwrap_or_default();
            return is_plan_file(scope.workspace_root(), path)
                .then_some(WriteRoute::Exempt)
                .ok_or("计划模式仅可修改工作区 .astrion/plan/ 下的 Markdown 计划。");
        }
        match self.permission {
            PermissionMode::ReadOnly => Err("当前任务为只读模式。"),
            PermissionMode::Approval => Ok(WriteRoute::Approval),
            PermissionMode::AutoApproval | PermissionMode::Unrestricted => Ok(WriteRoute::Exempt),
        }
    }

    fn native_write_route(&self, scope: &TaskScope, request: &ToolRequest, external: bool) -> Result<WriteRoute, &'static str> {
        if self.valid_modes && scope.execution_mode() == ExecutionMode::Direct {
            return match self.permission {
                PermissionMode::ReadOnly => Err("当前任务为只读模式。"),
                PermissionMode::Approval => Ok(WriteRoute::Approval),
                PermissionMode::AutoApproval if external => Ok(WriteRoute::Approval),
                PermissionMode::AutoApproval | PermissionMode::Unrestricted => Ok(WriteRoute::Exempt),
            };
        }
        let route = self.route(scope, request)?;
        if external && self.permission == PermissionMode::AutoApproval {
            Ok(WriteRoute::Approval)
        } else {
            Ok(route)
        }
    }
}

fn apply_mode<T>(
    metadata: &Value,
    key: &str,
    mode: &mut T,
    parse: impl FnOnce(&str) -> Option<T>,
) -> bool {
    match &metadata[key] {
        Value::Null => true,
        raw => match raw.as_str().and_then(parse) {
            Some(trusted) => {
                *mode = trusted;
                true
            }
            None => false,
        },
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum WriteRoute {
    Exempt,
    Approval,
}

pub(super) fn is_plan_file(root: &Path, raw: &str) -> bool {
    let raw = raw.trim();
    let path = Path::new(raw);
    let path = if let Some(relative) = raw.strip_prefix("/workspace/") {
        Path::new(relative)
    } else if path.is_absolute() {
        let Ok(relative) = path.strip_prefix(root) else {
            return false;
        };
        relative
    } else {
        path
    };
    let path = path.strip_prefix(".").unwrap_or(path);
    path.starts_with(".astrion/plan")
        && path
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
        && path
            .extension()
            .and_then(|ext| ext.to_str())
            .is_some_and(|ext| ext.eq_ignore_ascii_case("md"))
}

pub(super) fn workspace_write_request(
    tool_call_id: &str,
    tool_name: &str,
    args: Value,
) -> Result<ToolRequest, String> {
    let Value::Object(arguments) = args else {
        return Err("Invalid write tool arguments".into());
    };
    if !matches!(tool_name, WRITE_NAME | EDIT_NAME)
        || arguments
            .keys()
            .any(|key| key.starts_with("_approval_") || key.starts_with("_execution_"))
    {
        return Err("Invalid workspace write authority".into());
    }
    let preview = json!({"file_path": arguments.get("file_path")});
    Ok(ToolRequest {
        tool_call_id: tool_call_id.into(),
        tool_name: tool_name.into(),
        arguments,
        preview: preview.as_object().expect("preview object").clone(),
        approval_type: ApprovalType::SandboxWrite,
        browser_site: None,
    })
}

impl Runtime {
    pub(super) async fn approve_host_mutation(
        &self, task_id: &str, call_id: &str, name: &str, args: &Value, batch: &[Value],
    ) -> Result<(), Value> {
        let denied = |reason: &str| tool_failure("approval_authority_denied", reason);
        let arguments = args.as_object().cloned().ok_or_else(|| denied("工具参数必须是对象。"))?;
        let preview = ["source_dir","file_path","schedule_id","action","intent"].into_iter()
            .filter_map(|key| args.get(key).cloned().map(|value| (key.into(),value))).collect();
        let (request, item) = {
            let mut store = self.store().map_err(|error| denied(&error.message))?;
            let (_, settings) = task_scope(&store, task_id).map_err(|error| denied(&error))?;
            let (permission, execution) = settings.process_modes().ok_or_else(|| denied("当前任务权限无效。"))?;
            match permission {
                PermissionMode::ReadOnly => return Err(denied("只读或计划模式不允许该操作。")),
                PermissionMode::Unrestricted => return Ok(()),
                PermissionMode::Approval | PermissionMode::AutoApproval => {}
            }
            let approval_type = if execution == ExecutionMode::Direct { ApprovalType::DirectExecution }
                else { ApprovalType::SandboxWrite };
            let request = ToolRequest { tool_call_id:call_id.into(), tool_name:name.into(), arguments,
                preview, approval_type, browser_site:None };
            let item = create_process_approval(&mut store, task_id, &request).map_err(|error| denied(&error))?;
            (request, item)
        };
        let item = self.resolve_tool_approval(item, batch).await.map_err(|error| denied(&error))?;
        if item.status != Status::Approved {
            return Err(denied(item.reason.as_deref().unwrap_or("工具调用未获批准。")));
        }
        let mut store = self.store().map_err(|error| denied(&error.message))?;
        claim_process_approval(&mut store, task_id, &request, &item, batch).map_err(|error| denied(&error))
    }

    pub(super) async fn execute_workspace_write(
        &self,
        writer: Arc<WorkspaceFileWriter>,
        task_id: &str,
        request: ToolRequest,
        history: &[Value],
        batch: &[Value],
    ) -> Result<Value, String> {
        self.execute_workspace_write_inner(writer, task_id, request, history, batch, true).await
    }

    pub(super) async fn execute_generated_workspace_write(
        &self,
        writer: Arc<WorkspaceFileWriter>,
        task_id: &str,
        request: ToolRequest,
        history: &[Value],
        batch: &[Value],
    ) -> Result<Value, String> {
        self.execute_workspace_write_inner(writer, task_id, request, history, batch, false).await
    }

    async fn execute_workspace_write_inner(
        &self,
        writer: Arc<WorkspaceFileWriter>,
        task_id: &str,
        mut request: ToolRequest,
        history: &[Value],
        batch: &[Value],
        record_edit: bool,
    ) -> Result<Value, String> {
        let (approval, external_root) = {
            let mut store = self.store().map_err(|error| error.message)?;
            let (scope, settings) = match task_scope(&store, task_id) {
                Ok(context) => context,
                Err(error) => return Ok(tool_failure("tool_authority_unavailable", &error)),
            };
            if scope.execution_mode() == ExecutionMode::Direct {
                if !store.process_resources.available_mode(ExecutionMode::Direct) {
                    return Ok(tool_failure("tool_authority_unavailable", "Direct Host execution is unavailable"));
                }
                request.approval_type = ApprovalType::DirectExecution;
            }
            let raw = request.arguments.get("file_path").and_then(Value::as_str).unwrap_or_default();
            let external_root = match super::host_policy::native_file_root(&store, task_id, raw, true) {
                Ok(root) => root,
                Err(error) => return Ok(tool_failure("file_authority_denied", &error)),
            };
            let external_grant = matches!(&external_root, Some(crate::tools::read_file::HostFileRoot::Authorized(_)));
            let approval = match settings.native_write_route(&scope, &request, external_grant) {
                Err(reason) => return Ok(tool_failure("readonly_denied", reason)),
                Ok(WriteRoute::Exempt) => None,
                Ok(WriteRoute::Approval) => {
                    let item = store
                        .approvals
                        .create(&scope, &request, settings.permission, false)
                        .map_err(|error| error.to_string())?;
                    emit_required(&mut store, &item)?;
                    Some(item)
                }
            };
            (approval, external_root)
        };
        let resolved = if let Some(item) = approval {
            let item = self.resolve_tool_approval(item, batch).await?;
            if item.status != Status::Approved {
                let code = item
                    .review_error_code
                    .as_deref()
                    .unwrap_or(match item.status {
                        Status::Expired => "approval_expired",
                        _ => "approval_rejected",
                    });
                let reason = item.reason.as_deref().unwrap_or("该工具调用未获批准。");
                return Ok(tool_failure(code, reason));
            }
            Some(item)
        } else {
            None
        };

        let runtime = self.clone();
        let task_id = task_id.to_owned();
        let batch = batch.to_vec();
        let messages = if writer.requires_read()? { history.iter().chain(&batch).cloned().collect::<Vec<_>>() } else { Vec::new() };
        tokio::task::spawn_blocking(move || {
            let mut store = runtime.store().map_err(|error| error.message)?;
            let (scope, settings) = task_scope(&store, &task_id)?;
            let raw = request.arguments.get("file_path").and_then(Value::as_str).unwrap_or_default();
            if !super::host_policy::native_file_root(&store, &task_id, raw, true)
                .is_ok_and(|root| root == external_root)
            {
                return Ok(tool_failure("file_authority_denied", "Host file authorization changed before launch"));
            }
            let external_grant = matches!(&external_root, Some(crate::tools::read_file::HostFileRoot::Authorized(_)));
            let authority = match (settings.native_write_route(&scope, &request, external_grant), resolved) {
                (Ok(WriteRoute::Exempt), None) => exempt_workspace_write(&scope, &request),
                (Ok(WriteRoute::Approval), Some(item)) => {
                    let fingerprint = item
                        .requirements
                        .auto_review_required()
                        .then(|| capture(&store, &item, &batch))
                        .transpose()?;
                    let current = fingerprint.as_ref().map(|snapshot|snapshot.fingerprint());
                    store
                        .approvals
                        .claim_execution(&item.approval_id, &scope, &request, current)
                        .and_then(|grant| {
                            store
                                .approvals
                                .consume_execution(grant, &scope, &request, current)
                        })
                }
                _ => Err(ApprovalError::ScopeMismatch),
            };
            match authority {
                Ok(authority) => {
                    if external_root.is_none()
                        && let Some(path) = request.arguments.get("file_path").and_then(Value::as_str)
                        && let Err(error) = super::versioning::track_edit(&store, &task_id, path) {
                        return Ok(tool_failure("versioning_backup_failed", &error.message));
                    }
                    if record_edit && external_root.is_none() {
                        let (result, receipt) = writer.execute_recorded(authority, scope.conversation_id(), &messages);
                        if let Some(receipt) = receipt
                            && let Err(error) = super::modify_history::record(&mut store, &task_id, scope.workspace_root(), receipt,
                                if request.tool_name == EDIT_NAME { "edit" } else { "write" })
                        {
                            if let Some(task) = store.tasks.get_mut(&task_id) {
                                task.emit("edit_history_error", json!({"phase":"write","message":error.message}));
                            }
                        }
                        return Ok(result);
                    }
                    let write = |injected| writer.execute_with_host_root(authority, scope.conversation_id(),
                        &messages, external_root.as_ref(), injected);
                    if let Some(crate::tools::read_file::HostFileRoot::ProjectMemory { workspace }) = &external_root {
                        let result = super::knowledge_tools::write_workspace_memory(&store, scope.principal().workspace_id(),
                            workspace, scope.conversation_id(), std::path::Path::new(raw), write);
                        return Ok(result.unwrap_or_else(|error| tool_failure("workspace_memory_error", &error)));
                    }
                    Ok(write(false))
                }
                Err(error) => Ok(tool_failure(
                    "approval_authority_denied",
                    &error.to_string(),
                )),
            }
        })
        .await
        .map_err(|_| "Workspace writer stopped unexpectedly".to_owned())?
    }

    pub(super) async fn resolve_tool_approval(
        &self,
        item: Approval,
        batch: &[Value],
    ) -> Result<Approval, String> {
        let automatic = item.requirements.auto_review_required();
        let audit=if automatic {
            let data=self.store().map_err(|e|e.message)?.data_dir.clone();
            Some(Arc::new(crate::conversation_db::reviews::Record::begin(&data,"auto_approval",item.scope().conversation_id(),&item.approval_id,item.scope().workspace_root(),true).map_err(|e|e.to_string())?))
        }else{None};
        let wait = async {
            if let Some(log) = &audit && self.review_tool(&item, batch, log).await.is_err() {
                return self
                    .store()
                    .map_err(|error| error.message)?
                    .approvals
                    .fail_pending_review(
                        item.scope(),
                        &item.approval_id,
                        "review_context_failed",
                        "无法校验自动审核的任务上下文。",
                    )
                    .map_err(|error| error.to_string());
            }
            self.wait_for_approval(&item).await
        };
        let waited = if automatic { Ok(wait.await) } else { tokio::time::timeout(APPROVAL_WAIT, wait).await };
        let result = match waited {
            Ok(result) => result?,
            Err(_) => {
                let mut store = self.store().map_err(|error| error.message)?;
                let approvals = &mut store.approvals;
                let expired = approvals
                    .fail_pending_review(
                        item.scope(),
                        &item.approval_id,
                        "review_timeout",
                        "等待自动审核超时。",
                    )
                    .map_err(|error| error.to_string())?;
                if expired.status == Status::Pending {
                    approvals
                        .expire(item.scope(), &item.approval_id)
                        .map_err(|error| error.to_string())?
                } else {
                    expired
                }
            }
        };
        if let Some(event) = result.resolved_event_payload() {
            if let Some(audit)=&audit {audit.finish(event["decision"].as_str().unwrap_or("error"),&event).map_err(|e|e.to_string())?;}
            if automatic {
                self.emit_review_progress(
                    &result,
                    json!({"stage":"done", "decision":event["decision"], "code":event["code"]}),
                )?;
            }
            self.emit(result.scope().task_id(), "tool_approval_resolved", event)?;
        }
        Ok(result)
    }

    async fn wait_for_approval(&self, item: &Approval) -> Result<Approval, String> {
        loop {
            let current = self
                .store()
                .map_err(|error| error.message)?
                .approvals
                .get(item.scope().principal(), &item.approval_id)
                .map_err(|error| error.to_string())?;
            if current.status != Status::Pending {
                return Ok(current);
            }
            tokio::time::sleep(POLL_INTERVAL).await;
        }
    }

    async fn review_tool(&self, item: &Approval, batch: &[Value], audit: &Arc<crate::conversation_db::reviews::Record>) -> Result<(), String> {
        let id = item.scope().task_id();
        for attempt in 0..2 {
            let (settings, snapshot, ticket) = {
                let mut store = self.store().map_err(|error| error.message)?;
                let (scope, settings) = task_scope(&store, id)?;
                let current = store
                    .approvals
                    .get(scope.principal(), &item.approval_id)
                    .map_err(|error| error.to_string())?;
                if current.status != Status::Pending {
                    return Ok(());
                }
                let snapshot = capture(&store, item, batch)?;
                let ticket = store
                    .approvals
                    .start_auto_review(&scope, &item.approval_id, snapshot.fingerprint())
                    .map_err(|error| error.to_string())?;
                let reviewing = store
                    .approvals
                    .get(scope.principal(), &item.approval_id)
                    .map_err(|error| error.to_string())?;
                emit_required(&mut store, &reviewing)?;
                (settings, snapshot, ticket)
            };
            let Some(reviewer) = settings.reviewer else {
                let mut store = self.store().map_err(|error| error.message)?;
                store
                    .approvals
                    .fail_auto_review(
                        &ticket,
                        snapshot.fingerprint(),
                        "review_configuration_unavailable",
                        "没有可用的审核模型。",
                    )
                    .map_err(|error| error.to_string())?;
                return Ok(());
            };
            self.emit_review_progress(item, json!({"stage":"start"}))?;
            // OpenCode endpoints reject requests without the session header, so the
            // reviewer must carry the same per-conversation identity as the main agent.
            let session = self.opencode_session(
                item.scope().principal().workspace_id(),
                item.scope().conversation_id(),
            );
            let reviewer = Arc::new(reviewer.with_opencode_headers(session.as_deref(), false));
            let item_ref = Arc::new(item.clone());
            let mut seen = 0;
            let reviewing = review::run(
                &snapshot,
                settings.max_review_rounds,
                |messages, tools, round| {
                    let reviewer = reviewer.clone();
                    let item = item_ref.clone();
                    let audit = Arc::clone(audit);
                    let start = seen;
                    seen = messages.len()+1;
                    let runtime = self.clone();
                    Box::pin(async move {
                        runtime.emit_review_progress(
                            &item,
                            json!({"stage":"model_call", "round":round}),
                        )?;
                        audit.messages(&messages[start..],round).map_err(|e|e.to_string())?;
                        let mut attempt = 0;
                        loop {
                            attempt += 1;
                            let retry = Attempt::new(attempt, true);
                            let mut billing = runtime
                                .billing
                                .begin(
                                    BillingScope::review(item.scope().conversation_id().to_owned()),
                                    ModelPrice {
                                        model_key: &reviewer.key,
                                        model_id: &reviewer.model,
                                        identity: &reviewer.price_identity,
                                        responses: reviewer
                                            .endpoint
                                            .trim_end_matches('/')
                                            .ends_with("/responses"),
                                    },
                                )
                                .await;
                            match reviewer
                                .complete(messages, tools, |delta| billing.observe_text(delta))
                                .await
                            {
                                Ok(completion) => {
                                    audit.messages(std::slice::from_ref(&completion.message),round).map_err(|e|e.to_string())?;
                                    billing
                                        .settle_success(
                                            completion.usage.as_ref(),
                                            completion.responses_duration_ms,
                                        )
                                        .await
                                        .map_err(|_| "Could not persist review model usage".to_owned())?;
                                    break Ok(completion);
                                }
                                Err(error) if retry.should_retry(&error) => {
                                    retry.wait().await;
                                }
                                Err(error) => break Err(error),
                            }
                        }
                    })
                },
            );
            let result = tokio::select! {
                result = reviewing => result,
                terminal = self.wait_for_approval(item) => { terminal?; return Ok(()); }
            };
            let mut store = self.store().map_err(|error| error.message)?;
            let current = capture(&store, item, batch)?;
            if current.fingerprint() != snapshot.fingerprint() {
                if attempt == 0 {
                    continue;
                }
                store
                    .approvals
                    .fail_auto_review(
                        &ticket,
                        snapshot.fingerprint(),
                        "review_context_changed",
                        "审核期间任务上下文持续变化。",
                    )
                    .map_err(|error| error.to_string())?;
            } else {
                match result {
                    Ok((decision, reason)) => store.approvals.finish_auto_review(
                        &ticket,
                        current.fingerprint(),
                        decision,
                        Some(&reason),
                    ),
                    Err(error) => store.approvals.fail_auto_review(
                        &ticket,
                        current.fingerprint(),
                        error.code,
                        error.reason,
                    ),
                }
                .map_err(|error| error.to_string())?;
            }
            return Ok(());
        }
        Ok(())
    }

    fn emit_review_progress(&self, item: &Approval, progress: Value) -> Result<(), String> {
        self.emit(item.scope().task_id(), "auto_approval_progress", json!({"approval_id":item.approval_id, "conversation_id":item.scope().conversation_id(), "progress":progress}))
    }
}

pub(super) fn create_process_approval(
    store: &mut Store, task_id: &str, request: &ToolRequest,
) -> Result<Approval, String> {
    let (scope, settings) = task_scope(store, task_id)?;
    if !process_approval_allowed(&settings, request) {
        return Err("当前任务的权限档位不可申请此进程操作。".into());
    }
    let auto_full = request.approval_type == ApprovalType::FullAccess
        && super::store::read_json(&store.data_dir.join("personalization.json"))
            .map_err(|error| error.message)?.is_some_and(|settings| settings["allow_auto_approve_full_access"] == true);
    let item = store.approvals.create(&scope, request, settings.permission, auto_full).map_err(|error| error.to_string())?;
    emit_required(store, &item)?;
    Ok(item)
}

pub(super) fn claim_process_approval(
    store: &mut Store, task_id: &str, request: &ToolRequest, item: &Approval, batch: &[Value],
) -> Result<(), String> {
    let (scope, settings) = task_scope(store, task_id)?;
    if !process_approval_allowed(&settings, request) {
        return Err("任务权限已变化，请重新申请。".into());
    }
    let snapshot = item.requirements.auto_review_required().then(|| capture(store, item, batch)).transpose()?;
    let fingerprint = snapshot.as_ref().map(|snapshot|snapshot.fingerprint());
    let grant = store.approvals.claim_execution(&item.approval_id, &scope, request, fingerprint)
        .map_err(|error| error.to_string())?;
    store.approvals.consume_execution(grant, &scope, request, fingerprint)
        .map_err(|error| error.to_string())?;
    Ok(())
}

fn process_approval_allowed(settings: &TaskApprovalSettings, request: &ToolRequest) -> bool {
    match settings.process_modes() {
        Some((PermissionMode::Approval | PermissionMode::AutoApproval | PermissionMode::Unrestricted, ExecutionMode::Sandbox))
            if request.approval_type == ApprovalType::FullAccess => true,
        Some((PermissionMode::Approval | PermissionMode::AutoApproval, _)) => request.approval_type != ApprovalType::FullAccess,
        _ => false,
    }
}

fn emit_required(store: &mut Store, item: &Approval) -> Result<(), String> {
    if let Some(event) = item.required_event_payload() {
        writable_task(store, item.scope().task_id())?.emit("tool_approval_required", event);
    }
    Ok(())
}

pub(super) fn task_scope(store: &Store, task_id: &str) -> Result<(TaskScope, TaskApprovalSettings), String> {
    let task = store
        .tasks
        .get(task_id)
        .filter(|task| task.status == TaskStatus::Running && !task.cancel_requested)
        .ok_or("Task stopped")?;
    let settings = task
        .approval
        .as_ref()
        .ok_or("Task lacks an authenticated execution scope")?;
    let workspace = &task.workspace_id;
    if settings.principal.workspace_id() != workspace {
        return Err("Task workspace and approvals do not match".into());
    }
    let workspace_root = store
        .workspaces
        .get(workspace)
        .ok_or("Task workspace is not bound")?;
    let root = task.execution_root.as_ref().unwrap_or(workspace_root);
    let scope = TaskScope::new(
        settings.principal.clone(),
        &task.conversation_id,
        task_id,
        &task.start.id,
        root.clone(),
        settings.effective_execution(),
    )
    .map_err(|error| error.to_string())?;
    Ok((scope, settings.clone()))
}

pub(super) struct CachedReview {
    pub(super) task_id:String,
    approval:String,
    batch_hash:Vec<u8>,
    goal:Option<String>,
    database:crate::conversation_db::Database,
    revision:i64,
    snapshot:Arc<ReviewSnapshot>,
}

fn capture(store: &Store, item: &Approval, batch: &[Value]) -> Result<Arc<ReviewSnapshot>, String> {
    let scope = item.scope();
    let (current, _) = task_scope(store, scope.task_id())?;
    if &current != scope {
        return Err("Approval task scope changed".into());
    }
    let task = &store.tasks[scope.task_id()];
    let batch_hash=ring::digest::digest(&ring::digest::SHA256,&serde_json::to_vec(batch).map_err(|e|e.to_string())?).as_ref().to_vec();
    let goal=task.goal_progress["goal"].as_str();
    let mut cache=store.review_snapshot.borrow_mut();
    if let Some(last)=cache.as_ref() && last.approval==item.approval_id && last.batch_hash==batch_hash
        && last.goal.as_deref()==goal && last.database.data_version()?==last.revision {
        return Ok(last.snapshot.clone());
    }
    let path=store.conversation_path(scope.principal().workspace_id(),scope.conversation_id()).map_err(|e|e.message)?;
    let database=crate::conversation_db::Database::open(path.parent().unwrap())?;
    let revision=database.data_version()?;
    let document=database.read(scope.conversation_id())?.ok_or("Review history unavailable")?;
    let rows=document["messages"].as_array().ok_or("Invalid review history")?;
    let mut pending=batch.to_vec();
    if let Some(guidance)=document["metadata"]["runtime_message_queue"]["guidance"].as_array() {
        for item in guidance {
            let text=item.as_str().or(item["text"].as_str()).unwrap_or_default();
            pending.push(json!({"role":"user","content":text,"metadata":{"message_source":"guidance","runtime_guidance_original":text}}));
        }
    }
    let snapshot=Arc::new(ReviewSnapshot::capture(item,&task.start.message,rows,&pending,goal));
    *cache=Some(CachedReview{task_id:scope.task_id().to_owned(),approval:item.approval_id.clone(),batch_hash,goal:goal.map(str::to_owned),database,revision,snapshot:snapshot.clone()});
    Ok(snapshot)
}

fn tool_failure(code: &str, reason: &str) -> Value {
    json!({"success":false,"code":code,"error":reason})
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/approval_runtime_contract.rs"]
mod tests;
