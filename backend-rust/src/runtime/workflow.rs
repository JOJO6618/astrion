//! Hosts workflow state transitions within the existing Agent admission gate.

use super::{
    ModelConfig, Runtime, RuntimeError, Status, Store, TaskAdmission,
    billing::{BillingScope, ModelPrice},
    new_document, now, writable_task,
};
use crate::{
    catalog::{CatalogSource, SelectedModel},
    legacy::{LegacyDocument, LegacyKind},
    workflows::{
        Advance, Result, ReviewEvidence, WorkflowError, WorkflowRegistry, WorkflowStates,
        run_recorded_review,
    },
};
use serde_json::{Value, json};
use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
    sync::Arc,
};

#[derive(Clone)]
pub struct WorkflowRuntime {
    registry: WorkflowRegistry,
    states: WorkflowStates,
    data_dir: PathBuf,
    models: CatalogSource,
    fixed_model: Option<ModelConfig>,
    evidence: Option<Arc<dyn ReviewEvidence>>,
}

impl WorkflowRuntime {
    pub fn new(
        registry: WorkflowRegistry,
        data: impl AsRef<Path>,
        models: CatalogSource,
        fixed_model: Option<ModelConfig>,
    ) -> Self {
        Self {
            registry,
            states: WorkflowStates::new(data.as_ref()),
            data_dir: data.as_ref().to_path_buf(),
            models,
            fixed_model,
            evidence: None,
        }
    }

    pub fn with_review_evidence(mut self, evidence: Arc<dyn ReviewEvidence>) -> Self {
        self.evidence = Some(evidence);
        self
    }

    fn main_model(&self, document: Option<&LegacyDocument>) -> Result<ModelConfig> {
        if let Some(model) = &self.fixed_model {
            return Ok(model.clone());
        }
        let metadata = document
            .map(|document| &document.raw()["metadata"])
            .unwrap_or(&Value::Null);
        let key = metadata["model_key"].as_str().filter(|key| !key.is_empty());
        let thinking = metadata["thinking_mode"].as_bool().unwrap_or(matches!(
            metadata["run_mode"].as_str(),
            Some("thinking" | "deep")
        ));
        let selected = self
            .models
            .load()
            .select(key, thinking)
            .map_err(unavailable)?;
        ModelConfig::from_catalog(selected).map_err(unavailable)
    }

    fn review_model(&self) -> Result<(SelectedModel, usize)> {
        let config = match fs::read(self.data_dir.join("personalization.json")) {
            Ok(raw) => {
                serde_json::from_slice(&raw).map_err(|_| unavailable("无法解析工作流审核设置。"))?
            }
            Err(error) if error.kind() == ErrorKind::NotFound => Value::Null,
            Err(error) => return Err(unavailable(format!("无法读取工作流审核设置：{error}"))),
        };
        let settings = &config["review_agents"]["workflow_review"];
        let rounds = settings["max_rounds"]
            .as_u64()
            .and_then(|rounds| usize::try_from(rounds).ok())
            .filter(|rounds| *rounds > 0)
            .unwrap_or(10);
        let catalog = self.models.load();
        let configured = settings["model"]
            .as_str()
            .map(str::trim)
            .filter(|key| !key.is_empty());
        let public = catalog.models_response();
        let default = public["items"][0]["model_key"].as_str();
        let model = catalog
            .select(configured.or(default), true)
            .map_err(unavailable)?;
        Ok((model, rounds))
    }
}

impl Runtime {
    pub fn with_workflow(mut self, workflow: WorkflowRuntime) -> Self {
        self.workflow = Some(workflow);
        self
    }

    pub fn workflow_status(&self, workspace_id: &str, conversation_id: &str) -> Result<Value> {
        require_id(conversation_id)?;
        let workflow = self.workflow_service()?;
        let store = self.store().map_err(map_runtime)?;
        let _ = verified_document(&store, workspace_id, conversation_id)?;
        workflow.states.status_response(conversation_id)
    }

    pub fn activate_workflow(
        &self,
        workspace_id: &str,
        requested_id: Option<&str>,
        name: &str,
    ) -> Result<Value> {
        let name = name.trim();
        if name.is_empty() {
            return Err(WorkflowError::new(400, "缺少工作流名称"));
        }
        let workflow = self.workflow_service()?;
        let mut store = self.store().map_err(map_runtime)?;
        let existing = requested_id
            .map(|id| verified_document(&store, workspace_id, id))
            .transpose()?;
        if let Some(id) = requested_id
            && running(&store, workspace_id, id)
        {
            return Err(WorkflowError::new(
                409,
                "智能体正在工作中，工作流仅可在空闲时激活。",
            ));
        }
        let (conversation_id, message_index) = if let Some(document) = existing.as_ref() {
            let count = document.raw()["messages"].as_array().map_or(0, Vec::len);
            (document.source_id().to_owned(), count + 1)
        } else {
            (fresh_conversation_id(&store, workspace_id)?, 1)
        };
        let mut accepted = None;
        let activation = workflow.states.activate(&workflow.registry, &conversation_id, name, message_index, |activation| {
            let model = workflow.main_model(existing.as_ref())?;
            let payload = json!({"conversation_id":requested_id, "message":activation.prompt, "model_key":model.key(), "message_source":"workflow"});
            let admission = self.workflow_admission(&store, workspace_id, requested_id.is_none().then(|| conversation_id.clone()), "workflow_activate");
            let task = self.admit_task(&mut store, workspace_id, &payload, &model, admission, None).map_err(map_runtime)?;
            accepted = Some((task, model));
            Ok(())
        })?;
        let snapshot = activation.snapshot;
        if let Some((admitted, _)) = &accepted {
            let mut event = snapshot.clone();
            event["conversation_id"] = json!(conversation_id);
            if let Some(task) = store.tasks.get_mut(&admitted.task_id) {
                task.emit("workflow_progress", event);
            }
        }
        drop(store);
        if let Some((admitted, model)) = accepted {
            let task_id = admitted.task_id.clone();
            let _ = self.spawn_admitted(admitted, model);
            return Ok(
                json!({"success":true, "task_id":task_id, "conversation_id":conversation_id, "snapshot":snapshot}),
            );
        }
        Ok(
            json!({"success":true, "already":true, "conversation_id":conversation_id, "snapshot":snapshot}),
        )
    }

    pub fn deactivate_workflow(&self, workspace_id: &str, conversation_id: &str) -> Result<Value> {
        require_id(conversation_id)?;
        let workflow = self.workflow_service()?;
        let mut store = self.store().map_err(map_runtime)?;
        let document = verified_document(&store, workspace_id, conversation_id)?;
        let mut reply = workflow.states.deactivate_by_user(conversation_id)?;
        let mut progress = reply["snapshot"].clone();
        progress["conversation_id"] = json!(conversation_id);
        if let Some(task) = store.tasks.values_mut().find(|task| {
            task.workspace_id == workspace_id
                && task.conversation_id == conversation_id
                && task.status == Status::Running
        }) {
            if !task.cancel_requested {
                task.emit("workflow_progress", progress);
            }
            return Ok(reply);
        }
        let Ok(model) = workflow.main_model(Some(&document)) else {
            return Ok(reply);
        };
        let notices = workflow.states.poll_notices(conversation_id)?;
        let message = notice_text(&notices);
        if message.is_empty() {
            return Ok(reply);
        }
        let payload = json!({"conversation_id":conversation_id, "message":message, "model_key":model.key(), "message_source":"workflow"});
        let admission = self.workflow_admission(&store, workspace_id, None, "workflow_notice");
        match self.admit_task(&mut store, workspace_id, &payload, &model, admission, None) {
            Ok(admitted) => {
                drop(store);
                let _ = self.spawn_admitted(admitted, model);
                reply["dispatched"] = json!(true);
            }
            Err(_) => workflow.states.restore_notices(conversation_id, notices)?,
        }
        Ok(reply)
    }

    fn workflow_admission(
        &self,
        store: &Store,
        workspace_id: &str,
        new_conversation_id: Option<String>,
        marker: &str,
    ) -> TaskAdmission {
        let tools = store
            .workspace_tools
            .get(workspace_id)
            .unwrap_or(&self.tools)
            .clone();
        TaskAdmission {
            exclusive_workspace: false,
            title: None,
            metadata: None,
            new_conversation_id,
            tools: Arc::new(tools),
            user_message_metadata: Some(message_metadata(marker)),
        }
    }

    pub(super) fn workflow_tool_schemas(&self) -> Vec<Value> {
        if self.workflow.is_none() {
            return Vec::new();
        }
        WORKFLOW_TOOLS.iter().map(|tool| {
            let mut properties = serde_json::Map::new();
            let mut required = Vec::new();
            if let Some(argument) = tool.argument {
                properties.insert(argument.into(), json!({"type":"string"}));
                if tool.required {
                    required.push(argument);
                }
            }
            json!({"type":"function", "function":{"name":tool.name, "description":tool.description,
                "parameters":{"type":"object", "properties":properties, "required":required}}})
        }).collect()
    }

    pub(super) fn validate_workflow_tool(
        &self,
        name: &str,
        args: &Value,
    ) -> Option<std::result::Result<(), String>> {
        self.workflow.as_ref()?;
        let tool = WORKFLOW_TOOLS.iter().find(|tool| tool.name == name)?;
        Some(match args.as_object() {
            Some(fields)
                if fields
                    .keys()
                    .all(|key| Some(key.as_str()) == tool.argument || key == "intent")
                    && tool.argument.is_none_or(|key| {
                        fields.get(key).map_or(!tool.required, Value::is_string)
                    }) =>
            {
                Ok(())
            }
            _ => Err(format!("Invalid {name} arguments")),
        })
    }

    pub(super) fn execute_workflow_tool<'a>(
        &'a self,
        task_id: &'a str,
        name: &'a str,
        args: &'a Value,
        pending_batch: &'a [Value],
    ) -> super::ToolFuture<'a> {
        Box::pin(async move {
            match self.workflow_tool(task_id, name, args, pending_batch).await {
                Ok(value) => Ok(value),
                Err(error) => Ok(json!({"success":false, "error":error.message})),
            }
        })
    }

    async fn workflow_tool(
        &self,
        task_id: &str,
        name: &str,
        args: &Value,
        pending_batch: &[Value],
    ) -> Result<Value> {
        let workflow = self.workflow_service()?;
        let argument = |key: &str| args[key].as_str().unwrap_or_default();
        let (conversation_id, workspace, advance) = {
            let mut store = self.store().map_err(map_runtime)?;
            let task = writable_task(&mut store, task_id).map_err(stopped)?;
            let conversation_id = task.conversation_id.clone();
            let workspace_id = task.workspace_id.clone();
            let workspace = store
                .workspaces
                .get(&workspace_id)
                .cloned()
                .ok_or_else(|| unavailable("工作区不可用。"))?;
            let document = verified_document(&store, &workspace_id, &conversation_id)?;
            // Workflow cursors count durable messages (including the hidden
            // prefix after deep compression), not the provider's projection.
            // The current tool batch has not yet been committed, so add its
            // assistant call and any already completed results exactly once.
            let mut raw = document.into_raw();
            let history = raw["messages"].as_array_mut().expect("verified Agent messages");
            history.extend_from_slice(pending_batch);
            let states = &workflow.states;
            let advance = match name {
                "activate_workflow" => {
                    let activation = states.activate(
                        &workflow.registry,
                        &conversation_id,
                        argument("name"),
                        history.len(),
                        |_| Ok(()),
                    )?;
                    emit_progress(&mut store, task_id, &conversation_id, activation.snapshot)?;
                    return Ok(
                        json!({"success":true, "message":activation.prompt, "already":activation.already}),
                    );
                }
                "get_workflow_status" => {
                    return Ok(
                        json!({"success":true, "message":states.status_text(&conversation_id)?}),
                    );
                }
                "deactivate_workflow" => {
                    let value = states.deactivate_by_model(&conversation_id, argument("reason"))?;
                    emit_progress(
                        &mut store,
                        task_id,
                        &conversation_id,
                        json!({"active":false}),
                    )?;
                    return Ok(value);
                }
                "list_workflows" => return list_tool(&workflow.registry, argument("name")),
                "report_workflow_stage" => {
                    states.report_stage(&conversation_id, argument("summary"), history)?
                }
                "choose_workflow_branch" => {
                    states.choose_branch(&conversation_id, argument("target_node_id"), history)?
                }
                _ => return Err(WorkflowError::new(400, "未知工作流工具。")),
            };
            if let Advance::Progress(progress) = advance {
                writable_task(&mut store, task_id)
                    .map_err(stopped)?
                    .emit("workflow_progress", progress.event);
                return Ok(json!({"success":true, "message":progress.message}));
            }
            (conversation_id, workspace, advance)
        };
        let (selected, rounds) = workflow.review_model()?;
        let scope = BillingScope::review_agent(conversation_id.clone(), "workflow_review");
        let mut advance = advance;
        loop {
            match advance {
                Advance::Progress(progress) => {
                    self.emit(task_id, "workflow_progress", progress.event)
                        .map_err(stopped)?;
                    return Ok(json!({"success":true, "message":progress.message}));
                }
                Advance::Review(request) => {
                    // OpenCode endpoints reject requests without the session header, so the
                    // workflow reviewer carries this conversation's identity too.
                    let (data_dir, session) = {
                        let store = self.store().map_err(map_runtime)?;
                        let workspace_id = store.tasks.get(task_id).map(|task| task.workspace_id.clone()).unwrap_or_default();
                        let data_dir = store.data_dir.clone();
                        let session = crate::runtime::automatic_title::external_session_id(&data_dir, &workspace_id, &conversation_id).ok();
                        (data_dir, session)
                    };
                    let audit=crate::conversation_db::reviews::Record::begin(&data_dir,"workflow_review",&conversation_id,task_id,&workspace,true)?;
                    audit.event(&json!({"request":request.payload}))?;
                    let decision = run_recorded_review(
                        &request,
                        &selected,
                        session.as_deref(),
                        rounds,
                        &workspace,
                        workflow.evidence.as_deref(),
                        |data| {
                            audit.event(&data)?;
                            self.emit(task_id, "workflow_review_progress", data)
                                .map_err(stopped)
                        },
                        (
                            || {
                                self.billing.begin(
                                    scope.clone(),
                                    ModelPrice {
                                        model_key: &selected.key,
                                        model_id: &selected.model_id,
                                        identity: &selected.price_identity,
                                        responses: selected.api_protocol == "responses",
                                    },
                                )
                            },
                            |bill, usage, responses_duration| async move {
                                bill.settle_success(usage.as_ref(), responses_duration)
                                    .await
                                    .map(|_| ())
                                    .map_err(Into::into)
                            },
                        ),
                        |messages, round| audit.messages(messages,round).map_err(Into::into),
                    )
                    .await;
                    use crate::workflows::ReviewDecision;
                    let (outcome,detail)=match &decision {
                        Ok(ReviewDecision::Pass(message))=>("pass",message),
                        Ok(ReviewDecision::Reject(message))=>("reject",message),
                        Ok(ReviewDecision::Unavailable(message))=>("unavailable",message),Err(error)=>("error",&error.message),
                    };
                    audit.finish(outcome,&json!(detail))?;
                    let decision=decision?;
                    let mut store = self.store().map_err(map_runtime)?;
                    let _ = writable_task(&mut store, task_id).map_err(stopped)?;
                    advance = workflow.states.resolve_review(*request, decision)?;
                    if let Advance::Progress(progress) = advance {
                        writable_task(&mut store, task_id)
                            .map_err(stopped)?
                            .emit("workflow_progress", progress.event);
                        return Ok(json!({"success":true, "message":progress.message}));
                    }
                }
            }
        }
    }

    pub(super) fn workflow_task_started(&self, task_id: &str) -> std::result::Result<(), String> {
        let Some(workflow) = &self.workflow else {
            return Ok(());
        };
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        if task
            .start
            .message_source
            .as_deref()
            .is_none_or(|source| source == "user")
        {
            workflow
                .states
                .reset_stage_rounds(&task.conversation_id)
                .map_err(|error| error.message)?;
        }
        Ok(())
    }

    pub(super) fn workflow_before_round(
        &self,
        task_id: &str,
    ) -> std::result::Result<Option<String>, String> {
        let Some(workflow) = &self.workflow else {
            return Ok(None);
        };
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        workflow
            .states
            .tick_stage_round(&task.conversation_id)
            .map_err(|error| error.message)
    }

    pub(super) fn workflow_drain_notices(
        &self,
        task_id: &str,
        completed: Option<&Value>,
    ) -> std::result::Result<Vec<Value>, String> {
        let Some(workflow) = &self.workflow else {
            return Ok(Vec::new());
        };
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        let (workspace_id, conversation_id) =
            (task.workspace_id.clone(), task.conversation_id.clone());
        let notices = workflow
            .states
            .poll_notices(&conversation_id)
            .map_err(|error| error.message)?;
        let content = notice_text(&notices);
        if content.is_empty() {
            return Ok(Vec::new());
        }
        let metadata = message_metadata("workflow_notice");
        let message = json!({"role":"user", "content":content});
        let mut durable_message = message.clone();
        durable_message["metadata"] = metadata.clone();
        let messages = completed
            .cloned()
            .into_iter()
            .chain([durable_message])
            .collect();
        if let Err(error) =
            store.append_messages(&workspace_id, &conversation_id, messages, now(), None, None)
        {
            workflow
                .states
                .restore_notices(&conversation_id, notices)
                .map_err(|error| error.message)?;
            return Err(error.message);
        }
        let task = writable_task(&mut store, task_id)?;
        task.emit(
            "user_message",
            json!({
                "message":content, "content":content, "conversation_id":conversation_id, "message_source":"workflow",
                "workflow_notice":true, "visibility":"chat", "starts_work":true,
                "timestamp":metadata["timestamp"], "metadata":metadata,
            }),
        );
        task.checkpoint_events();
        Ok(vec![message])
    }

    fn workflow_service(&self) -> Result<&WorkflowRuntime> {
        self.workflow
            .as_ref()
            .ok_or_else(|| unavailable("工作流运行时不可用。"))
    }
}

struct WorkflowTool {
    name: &'static str,
    description: &'static str,
    argument: Option<&'static str>,
    required: bool,
}

const WORKFLOW_TOOLS: &[WorkflowTool] = &[
    WorkflowTool {
        name: "activate_workflow",
        description: "在当前对话中激活一个已注册的工作流。返回流程约定与当前节点。",
        argument: Some("name"),
        required: true,
    },
    WorkflowTool {
        name: "report_workflow_stage",
        description: "汇报当前阶段的完成内容、结果及证据，并推进工作流。",
        argument: Some("summary"),
        required: true,
    },
    WorkflowTool {
        name: "choose_workflow_branch",
        description: "当前停在分支点时，从候选路径中选择目标节点。",
        argument: Some("target_node_id"),
        required: true,
    },
    WorkflowTool {
        name: "get_workflow_status",
        description: "查询当前工作流的阶段、审核历史和分支候选。",
        argument: None,
        required: false,
    },
    WorkflowTool {
        name: "deactivate_workflow",
        description: "退出当前工作流，保留已经记录的历史。",
        argument: Some("reason"),
        required: true,
    },
    WorkflowTool {
        name: "list_workflows",
        description: "查询内置和用户工作流。传入名称时返回原始 WORKFLOW.md。",
        argument: Some("name"),
        required: false,
    },
];

fn list_tool(registry: &WorkflowRegistry, name: &str) -> Result<Value> {
    let name = name.trim();
    if !name.is_empty() {
        return Ok(
            json!({"success":true, "workflow_name":name, "message":registry.read_markdown(name)?}),
        );
    }
    let mut list = registry.list_response()?;
    let workflows = std::mem::take(&mut list["workflows"]);
    let count = workflows.as_array().map_or(0, Vec::len);
    Ok(json!({"success":true, "count":count, "workflows":workflows}))
}

fn emit_progress(
    store: &mut Store,
    task_id: &str,
    conversation_id: &str,
    mut event: Value,
) -> Result<()> {
    event["conversation_id"] = json!(conversation_id);
    writable_task(store, task_id)
        .map_err(stopped)?
        .emit("workflow_progress", event);
    Ok(())
}

fn verified_document(
    store: &Store,
    workspace_id: &str,
    conversation_id: &str,
) -> Result<LegacyDocument> {
    let document = store
        .read(workspace_id, conversation_id)
        .map_err(map_runtime)?;
    if document.kind() != LegacyKind::Agent {
        return Err(WorkflowError::new(404, "Conversation not found"));
    }
    if duplicate_id(store, workspace_id, conversation_id)? {
        return Err(WorkflowError::new(
            409,
            "多个工作区存在同一对话 ID，无法安全读取工作流状态。",
        ));
    }
    Ok(document)
}

fn fresh_conversation_id(store: &Store, workspace_id: &str) -> Result<String> {
    let created_at = now();
    for offset in 0..1000 {
        let document = new_document(
            store,
            workspace_id,
            &json!({}),
            created_at + f64::from(offset) / 1000.0,
        )
        .map_err(map_runtime)?;
        let id = document.source_id();
        if !duplicate_id(store, workspace_id, id)?
            && !store
                .data_dir
                .join("workflow_states")
                .join(id)
                .try_exists()?
        {
            return Ok(id.into());
        }
    }
    Err(WorkflowError::new(409, "无法分配唯一的工作流对话 ID。"))
}

// Python stores workflow states by conversation ID in Host data. Ambiguous
// legacy IDs must never be able to reuse another workspace's state.
fn duplicate_id(store: &Store, workspace_id: &str, conversation_id: &str) -> Result<bool> {
    let root = store.data_dir.join("conversations");
    let entries = match fs::read_dir(&root) {
        Ok(entries) => entries,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.into()),
    };
    for entry in entries {
        let entry = entry?;
        let directory = entry.file_name();
        let Some(other) = directory.to_str().filter(|id| *id != workspace_id) else {
            continue;
        };
        if super::validate_workspace(other).is_ok() && entry.file_type()?.is_dir() {
            if store.has_conversation(other,conversation_id).map_err(map_runtime)? {return Ok(true);}
        }
    }
    Ok(false)
}

fn require_id(id: &str) -> Result<()> {
    if id.is_empty() {
        Err(WorkflowError::new(400, "缺少 conversation_id"))
    } else {
        Ok(())
    }
}

fn running(store: &Store, workspace_id: &str, conversation_id: &str) -> bool {
    store.tasks.values().any(|task| {
        task.workspace_id == workspace_id
            && task.conversation_id == conversation_id
            && task.status == Status::Running
    })
}

fn message_metadata(marker: &str) -> Value {
    let timestamp = super::iso_time(now());
    let mut metadata = json!({"message_source":"workflow", "visibility":"chat", "starts_work":true, "timestamp":timestamp});
    metadata[marker] = json!(true);
    metadata
}

fn notice_text(notices: &[Value]) -> String {
    notices
        .iter()
        .filter_map(|notice| notice["message"].as_str())
        .map(str::trim)
        .filter(|message| !message.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn map_runtime(error: RuntimeError) -> WorkflowError {
    WorkflowError::new(error.status, error.message)
}

fn unavailable(message: impl Into<String>) -> WorkflowError {
    WorkflowError::new(503, message)
}

fn stopped(message: String) -> WorkflowError {
    WorkflowError::new(409, message)
}
