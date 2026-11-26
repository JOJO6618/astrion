mod agent;
mod admission_recovery;
mod approval;
pub(crate) mod automatic_title;
mod billing;
mod chat;
mod chat_handoff;
mod child_processes;
pub mod conversation_duplicate;
mod conversation_lifecycle;
pub use conversation_lifecycle::ConversationSelection;
pub mod conversation_review;
pub mod conversation_token_statistics;
mod crosspet;
mod compression;
pub(crate) mod shallow;
mod git_review;
mod goals;
pub mod guidance;
pub mod host_policy;
mod host_modes;
mod host_policy_setup;
mod navigation;
mod mcp;
mod native_browser;
mod native_browser_files;
pub use native_browser::DesktopBrowser;
pub mod process_resources;
mod interactions;
mod scheduling;
pub(crate) use scheduling::ScheduledStart;
#[path = "preview/mod.rs"]
pub mod preview;
mod preview_tracking;
pub(crate) mod store;
mod task_list;
pub(crate) mod thinking;
pub(crate) mod thinking_auxiliary;
mod tool_loading;
mod knowledge_tools;
mod custom_tools;
mod schedule_tools;
pub mod transfer;
pub(crate) mod todos;
mod tool_categories;
mod sub_agent_execution;
mod system_prompt;
#[path = "versioning/mod.rs"]
pub mod versioning;
mod web_conversations;
pub mod web_messages;
mod web_snapshot;
mod workflow;
mod worktree;

pub use approval::{TaskApprovalSettings, resolve_task_approval_settings};
pub use chat::ChatTaskStart;
pub use git_review::GitReview;
pub use navigation::{NavigationRun, NavigationSession};
pub use web_snapshot::WebConversationSnapshot;
pub use workflow::WorkflowRuntime;

use std::{
    fmt, fs,
    future::Future,
    path::{Path, PathBuf},
    pin::Pin,
    sync::{Arc, Condvar, Mutex, MutexGuard},
    time::{SystemTime, UNIX_EPOCH},
};

use serde_json::{Value, json};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use tokio::{runtime::Handle, sync::oneshot};
use uuid::Uuid;

use crate::{
    approval::{Approval, ApprovalBackend, ApprovalError, ApprovalPrincipal, Decision},
    catalog::SelectedModel, chat::Completion, goals::{GoalRequest, GoalReviewer, Goals, Origin}, legacy::{LegacyDocument, LegacyKind}, model::{self, RequestOptions}, tools::{ToolSet, write_file::WorkspaceFileWriter},
    usage::PriceIdentity,
    workspace_catalog::RegisteredWorkspace,
};
use store::{
    Status, Store, Task, TaskStart, TokenStats, append_document, legacy_error, validate_workspace,
};

/// Local Agent runtime scoped by the trusted Host workspace binding.
#[derive(Clone)]
pub struct Runtime {
    store: Arc<Mutex<Store>>,
    tools: ToolSet,
    billing: billing::Billing,
    workflow: Option<WorkflowRuntime>,
    goals: Goals,
    goal_reviewer: Option<GoalReviewer>,
    sub_agents: Option<Arc<crate::sub_agents::SubAgents>>,
    personalization: Option<crate::web_assets::personalization::Personalization>,
    schedules: Arc<std::sync::OnceLock<crate::schedules::ScheduleLink>>,
}

type ToolFuture<'a> = Pin<Box<dyn Future<Output = Result<Value, String>> + Send + 'a>>;

trait AgentTools: Send + Sync {
    fn schemas(&self) -> Vec<Value>;
    fn validate(&self, name: &str, args: &Value) -> Result<(), String>;
    fn execute<'a>(&'a self, name: &'a str, args: Value) -> ToolFuture<'a>;
    fn writer(&self) -> Option<Arc<WorkspaceFileWriter>> { None }
    fn allows_workflow(&self) -> bool { false }
}

impl AgentTools for ToolSet {
    fn schemas(&self) -> Vec<Value> { ToolSet::schemas(self) }
    fn validate(&self, name: &str, args: &Value) -> Result<(), String> { ToolSet::validate(self, name, args) }
    fn execute<'a>(&'a self, name: &'a str, args: Value) -> ToolFuture<'a> { Box::pin(ToolSet::execute(self, name, args)) }
    fn writer(&self) -> Option<Arc<WorkspaceFileWriter>> { ToolSet::writer(self) }
    fn allows_workflow(&self) -> bool { true }
}

struct TaskAdmission {
    exclusive_workspace: bool,
    title: Option<String>,
    metadata: Option<Value>,
    new_conversation_id: Option<String>,
    user_message_metadata: Option<Value>,
    tools: Arc<dyn AgentTools>,
}

struct AdmittedTask {
    handle: Handle,
    cancel: oneshot::Receiver<()>,
    workspace_id: String,
    conversation_id: String,
    task_id: String,
    created_at: f64,
    billing_scope: billing::BillingScope,
    messages: Vec<Value>,
    tools: Arc<dyn AgentTools>,
    saved_context_tokens: Option<u64>,
    fast: bool,
    completion: Arc<TaskCompletion>,
    title_input: Option<(String, String)>,
}

/// A terminal result becomes observable after its supervisor gives up the store.
/// Waiting while holding the store lock is safe because completion needs no store access.
#[derive(Default)]
struct TaskCompletion {
    done: Mutex<bool>,
    ready: Condvar,
}

impl TaskCompletion {
    fn wait(&self) {
        let done = self.done.lock().unwrap_or_else(|error| error.into_inner());
        drop(self.ready.wait_while(done, |done| !*done));
    }
}

struct ReleaseTask(Arc<TaskCompletion>);

impl Drop for ReleaseTask {
    fn drop(&mut self) {
        *self.0.done.lock().unwrap_or_else(|error| error.into_inner()) = true;
        self.0.ready.notify_all();
    }
}

#[derive(Clone)]
pub struct ModelConfig {
    endpoint: String,
    api_key: Option<String>,
    model: String,
    key: String,
    options: RequestOptions,
    subscription: Option<SubscriptionRequest>,
    context_window: Option<u64>,
    price_identity: PriceIdentity,
    prompt_description: String,
    prompt_multimodal: &'static str,
}

#[derive(Clone)]
struct SubscriptionRequest {
    selected: crate::catalog::CodexSelection,
    instructions: String,
    session_id: String,
    fast: bool,
}

impl ModelConfig {
    pub fn fixed(endpoint: String, api_key: Option<String>, model: String) -> Self {
        Self {
            endpoint,
            api_key,
            key: model.clone(),
            prompt_description: model.clone(),
            prompt_multimodal: "text",
            model,
            options: RequestOptions::default(),
            subscription: None,
            context_window: None,
            price_identity: PriceIdentity::default(),
        }
    }

    pub fn from_catalog(selected: SelectedModel) -> Result<Self, String> {
        if !matches!(
            selected.api_protocol.as_str(),
            "chat_completions" | "responses"
        ) {
            return Err("Unsupported model API protocol".into());
        }
        if selected.codex.is_none() && selected.api_key.as_deref().is_none_or(str::is_empty) {
            return Err("This model requires API-key authentication".into());
        }
        let subscription = selected.codex.map(|selected| SubscriptionRequest {
            instructions: selected.instructions(), selected,
            session_id: Uuid::new_v4().to_string(), fast: false,
        });
        let max_output_tokens = selected.max_output_tokens.as_u64().filter(|limit| *limit > 0)
            .or_else(|| (selected.api_protocol == "chat_completions").then(|| {
                if selected.max_output_tokens.is_null() { selected.chat_default_max_output_tokens } else { 4_096 }
            }));
        let options = RequestOptions {
            headers: selected.headers,
            extra_params: selected.extra_params,
            max_output_tokens,
        };
        Ok(Self {
            endpoint: selected.endpoint,
            api_key: selected.api_key,
            model: selected.model_id,
            key: selected.key,
            options,
            subscription,
            context_window: selected.context_window,
            price_identity: selected.price_identity,
            prompt_description: selected.prompt_description,
            prompt_multimodal: selected.prompt_multimodal,
        })
    }

    pub fn key(&self) -> &str { &self.key }
    pub(crate) fn model_id(&self) -> &str { &self.model }
    pub(crate) fn price_identity(&self) -> &PriceIdentity { &self.price_identity }
    pub(crate) fn supports_images(&self) -> bool { matches!(self.prompt_multimodal, "image" | "image,video") }
    pub(crate) fn supports_videos(&self) -> bool { matches!(self.prompt_multimodal, "video" | "image,video") }

    fn uses_responses(&self) -> bool {
        self.subscription.is_some() || reqwest::Url::parse(&self.endpoint)
            .is_ok_and(|url| url.path().trim_end_matches('/').rsplit('/').next() == Some("responses"))
    }

    pub fn with_session(mut self, session_id: &str, fast: bool) -> Self {
        if let Some(subscription) = &mut self.subscription {
            if !session_id.is_empty() { subscription.session_id = session_id.to_owned(); }
            subscription.fast = fast;
        }
        self
    }

    pub fn with_price_identity(mut self, identity: PriceIdentity) -> Self {
        self.price_identity = identity;
        self
    }

    pub async fn complete(&self, messages: &[Value], tools: &[Value], on_text: impl FnMut(&str)) -> Result<Completion, String> {
        self.complete_with_budget(messages, tools, &mut model::RoundBudget::default(), on_text).await
    }

    pub(crate) async fn complete_with_budget(
        &self,
        messages: &[Value],
        tools: &[Value],
        budget: &mut model::RoundBudget,
        mut on_text: impl FnMut(&str),
    ) -> Result<Completion, String> {
        self.complete_with_updates(messages, tools, budget, |delta| {
            if let model::StreamDelta::Text(text) = delta { on_text(text); }
        }).await
    }

    pub(crate) async fn complete_with_updates(
        &self, messages: &[Value], tools: &[Value], budget: &mut model::RoundBudget,
        on_delta: impl FnMut(model::StreamDelta<'_>),
    ) -> Result<Completion, String> {
        let options = self.context_window.map(|window| {
            let mut options = self.options.clone();
            budget.apply(&mut options, window, self.endpoint.ends_with("/responses"), messages, tools);
            options
        });
        let options = options.as_ref().unwrap_or(&self.options);
        if let Some(request) = &self.subscription {
            let transport = request.selected.providers.codex_transport(&request.selected.account_id);
            let params = model::codex::CodexRequestOptions {
                instructions: &request.instructions,
                reasoning_effort: options.extra_params.get("reasoning").and_then(|value| value["effort"].as_str()),
                session_id: &request.session_id, fast: request.fast,
                max_output_tokens: options.max_output_tokens, model_info: &request.selected.model_info,
            };
            return model::codex::complete_with_updates(&transport, &self.model, messages, tools, &params, on_delta).await;
        }
        model::complete_with_updates(
            &self.endpoint, self.api_key.as_deref(), &self.model, messages, tools, options, on_delta,
        ).await
    }

    pub fn validate_model_key(&self, payload: &Value) -> Result<(), RuntimeError> {
        if optional_string(payload, "model_key")?
            .map(str::trim)
            .is_some_and(|key| !key.is_empty() && key != self.key)
        {
            return Err(RuntimeError::bad_request(
                "model_key does not match the configured model",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct RuntimeError {
    pub status: u16,
    pub message: String,
}

impl RuntimeError {
    pub(crate) fn new(status: u16, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
        }
    }

    pub(crate) fn storage(error: impl fmt::Display) -> Self {
        Self::new(503, format!("Runtime storage unavailable: {error}"))
    }

    fn bad_request(message: impl Into<String>) -> Self {
        Self::new(400, message)
    }

    fn task_not_found() -> Self {
        Self::new(404, "Task not found in workspace")
    }

    fn session_not_found() -> Self {
        Self::new(404, "Conversation not found in workspace")
    }
}

impl fmt::Display for RuntimeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for RuntimeError {}

impl Runtime {
    pub fn open(data_dir: impl AsRef<Path>) -> Result<Self, RuntimeError> {
        Self::open_with_tools(data_dir, ToolSet::default())
    }

    pub fn open_with_tools(
        data_dir: impl AsRef<Path>,
        tools: ToolSet,
    ) -> Result<Self, RuntimeError> {
        let store = Store::open(data_dir.as_ref())?;
        let billing = billing::Billing::new(&store.data_dir);
        let goals = Goals::new(&store.data_dir);
        Ok(Self { store: Arc::new(Mutex::new(store)), tools, billing, workflow: None, goals, goal_reviewer: None, sub_agents: None, personalization: None, schedules: Arc::default() })
    }

    pub fn set_schedule_service(&self, service: &crate::schedules::ScheduleService) {
        let _ = self.schedules.set(service.link());
    }

    pub fn with_personalization(mut self, settings: crate::web_assets::personalization::Personalization) -> Self {
        self.personalization = Some(settings);
        self
    }

    fn can_edit_settings(&self, task_id: &str) -> Result<bool,String> {
        let store = self.store().map_err(|error|error.message)?;
        let (_, settings) = approval::task_scope(&store, task_id)?;
        Ok(settings.process_modes().is_some_and(|(permission,_)| permission != crate::approval::PermissionMode::ReadOnly))
    }

    pub fn with_pricing(mut self, config_dir: impl AsRef<Path>) -> Self {
        self.billing.configure_pricing(config_dir.as_ref());
        self
    }

    pub fn with_chat_storage<T, E>(&self, action: impl FnOnce(&Path) -> Result<T, E>) -> Result<T, E>
    where E: From<RuntimeError> {
        let store = self.store()?;
        action(&store.data_dir.join("chat"))
    }

    pub fn bind_workspace(
        &self,
        workspace_id: &str,
        project_path: impl AsRef<Path>,
    ) -> Result<(), RuntimeError> {
        self.bind_workspace_with_tools(workspace_id, project_path, self.tools.clone())
    }

    pub fn bind_workspace_with_tools(
        &self, workspace_id: &str, project_path: impl AsRef<Path>, tools: ToolSet,
    ) -> Result<(), RuntimeError> {
        self.bind_registered_workspace(workspace_id, project_path, tools, || Ok(()))
    }

    pub fn bind_registered_workspace(
        &self, workspace_id: &str, project_path: impl AsRef<Path>, tools: ToolSet,
        verify_registration: impl FnOnce() -> Result<(), RuntimeError>,
    ) -> Result<(), RuntimeError> {
        validate_workspace(workspace_id)?;
        let path = fs::canonicalize(project_path).map_err(RuntimeError::storage)?;
        if !path.is_dir() {
            return Err(RuntimeError::bad_request(
                "Workspace project path must be a directory",
            ));
        }
        if !tools.matches_workspace(&path) {
            return Err(RuntimeError::bad_request("Tools are bound to a different workspace"));
        }
        let mut store = self.store()?;
        // Registry removal takes this same lock before editing the catalog and evicting tools.
        verify_registration()?;
        if !tools.matches_workspace(&path) {
            return Err(RuntimeError::new(409, "Workspace changed before binding tools"));
        }
        if store.workspaces.get(workspace_id).is_some_and(|existing| existing != &path) {
            return Err(RuntimeError::new(409, "Workspace is already bound to a different project path"));
        }
        store.workspaces.insert(workspace_id.into(), path);
        store.workspace_tools.insert(workspace_id.into(), tools);
        Ok(())
    }

    pub fn bound_workspace(&self, workspace_id: &str) -> Result<Option<PathBuf>, RuntimeError> {
        validate_workspace(workspace_id)?;
        Ok(self.store()?.workspaces.get(workspace_id).cloned())
    }

    pub fn remove_idle_workspace<T>(
        &self, workspace_id: &str, operation: impl FnOnce() -> Result<T, RuntimeError>,
    ) -> Result<T, RuntimeError> {
        validate_workspace(workspace_id)?;
        let mut store = self.store()?;
        if store.tasks.values().any(|task| task.workspace_id == workspace_id && task.status == Status::Running) {
            return Err(RuntimeError::new(409, "A task is already running in this workspace"));
        }
        let result = operation()?;
        Self::release_mcp_scope(&mut store, workspace_id, None);
        Self::close_process_scope(&mut store, workspace_id, None);
        store.workspaces.remove(workspace_id);
        store.workspace_tools.remove(workspace_id);
        Ok(result)
    }

    pub fn create_session(&self, workspace_id: &str, payload: &Value) -> Result<Value, RuntimeError> {
        self.create_session_at(workspace_id, None, payload)
    }

    pub fn create_registered_session(
        &self,
        workspace: &crate::workspace_catalog::RegisteredWorkspace,
        payload: &Value,
    ) -> Result<Value, RuntimeError> {
        self.create_session_at(&workspace.id, Some(&workspace.path), payload)
    }

    fn create_session_at(
        &self, workspace_id: &str, expected_path: Option<&Path>, payload: &Value,
    ) -> Result<Value, RuntimeError> {
        validate_payload(workspace_id, payload)?;
        let store = self.store()?;
        verify_workspace_binding(&store, workspace_id, expected_path)?;
        let document = new_document(&store, workspace_id, payload, now())?;
        let id = document.source_id().to_owned();
        store.persist(workspace_id, document, true)?;
        versioning::set_enabled_on_create(&store, workspace_id, &id)?;
        Ok(json!({"success":true,"conversation_id":id}))
    }

    /// Durably accepts the user message before starting the model on Tokio.
    pub fn create_task(
        &self,
        workspace_id: &str,
        payload: &Value,
        model: ModelConfig,
    ) -> Result<Value, RuntimeError> {
        if matches!(payload["message_source"].as_str(), Some("git_review" | "workflow" | "schedule" | "goal_prompt" | "goal_review" | "goal_recovery" | "background_command" | "sub_agent")) {
            return Err(RuntimeError::bad_request("Reserved task message source"));
        }
        self.create_regular_task(workspace_id, None, payload, model, None)
    }

    pub fn create_task_scoped(
        &self,
        workspace: &RegisteredWorkspace,
        payload: &Value,
        model: ModelConfig,
        settings: TaskApprovalSettings,
    ) -> Result<Value, RuntimeError> {
        if matches!(payload["message_source"].as_str(), Some("git_review" | "workflow" | "schedule" | "goal_prompt" | "goal_review" | "goal_recovery" | "background_command" | "sub_agent")) {
            return Err(RuntimeError::bad_request("Reserved task message source"));
        }
        self.create_regular_task(&workspace.id, Some(&workspace.path), payload, model, Some(settings))
    }

    fn create_regular_task(
        &self, workspace_id: &str, expected_workspace: Option<&Path>, payload: &Value, model: ModelConfig,
        approval: Option<TaskApprovalSettings>,
    ) -> Result<Value, RuntimeError> {
        let admitted = {
            let mut store = self.store()?;
            verify_workspace_binding(&store, workspace_id, expected_workspace)?;
            let tools = store.workspace_tools.get(workspace_id).unwrap_or(&self.tools).clone();
            self.admit_task(&mut store, workspace_id, payload, &model, TaskAdmission {
                exclusive_workspace: false, title: None, metadata: None,
                new_conversation_id: None, user_message_metadata: None, tools: Arc::new(tools),
            }, approval)?
        };
        Ok(self.spawn_admitted(admitted, model))
    }

    fn create_task_internal(
        &self, workspace_id: &str, payload: &Value, model: ModelConfig, admission: TaskAdmission,
    ) -> Result<Value, RuntimeError> {
        let admitted = self.admit_task(&mut *self.store()?, workspace_id, payload, &model, admission, None)?;
        Ok(self.spawn_admitted(admitted, model))
    }

    fn admit_task(
        &self,
        store: &mut Store,
        workspace_id: &str,
        payload: &Value,
        model: &ModelConfig,
        admission: TaskAdmission,
        approval: Option<TaskApprovalSettings>,
    ) -> Result<AdmittedTask, RuntimeError> {
        let origin = if admission.exclusive_workspace {
            Origin::Passive
        } else if matches!(payload["message_source"].as_str(), Some("workflow" | "schedule")) {
            Origin::Automatic
        } else {
            Origin::User
        };
        self.admit_goal_task(store, workspace_id, payload, model, admission, approval, origin)
    }

    fn admit_goal_task(
        &self,
        store: &mut Store,
        workspace_id: &str,
        payload: &Value,
        model: &ModelConfig,
        admission: TaskAdmission,
        approval: Option<TaskApprovalSettings>,
        origin: Origin<'_>,
    ) -> Result<AdmittedTask, RuntimeError> {
        self.admit_goal_task_with_notice(store, workspace_id, payload, model, admission, approval, origin, None)
    }

    fn admit_goal_task_with_notice(
        &self,
        store: &mut Store,
        workspace_id: &str,
        payload: &Value,
        model: &ModelConfig,
        admission: TaskAdmission,
        mut approval: Option<TaskApprovalSettings>,
        origin: Origin<'_>,
        hidden_user_notice: Option<&str>,
    ) -> Result<AdmittedTask, RuntimeError> {
        validate_payload(workspace_id, payload)?;
        if store.shutting_down { return Err(RuntimeError::new(503, "Runtime is shutting down")); }
        verify_workspace_binding(store, workspace_id, None)?;
        let request = GoalRequest::parse(payload)?;
        let message = required_message(payload)?;
        let requested_id = optional_string(payload, "conversation_id")?.filter(|id| !id.is_empty());
        if request.action == Some(crate::goals::Action::Continue) && requested_id.is_none() {
            return Err(RuntimeError::bad_request("Continuing a goal requires conversation_id"));
        }
        if model.endpoint.trim().is_empty() || model.model.trim().is_empty() {
            return Err(RuntimeError::new(503, "Model connection is unavailable"));
        }
        model.validate_model_key(payload)?;
        let handle = Handle::try_current()
            .map_err(|_| RuntimeError::new(503, "No Tokio runtime is available"))?;
        if store.tasks.values().any(|task| {
            task.workspace_id == workspace_id && task.status == Status::Running
                && (admission.exclusive_workspace || requested_id == Some(task.conversation_id.as_str()))
        }) {
            let scope = if admission.exclusive_workspace { "workspace" } else { "conversation" };
            return Err(RuntimeError::new(409, format!("A task is already running in this {scope}")));
        }
        let created_at = now();
        let create = requested_id.is_none();
        let document = match requested_id {
            Some(id) => {
                versioning::recover_pending(store, workspace_id, id)?;
                store.read(workspace_id, id)?
            }
            None => new_document_with_id(store, workspace_id, payload, created_at, admission.new_conversation_id.as_deref())?,
        };
        if document.kind() != LegacyKind::Agent {
            return Err(RuntimeError::new(422, "Agent tasks require an Agent conversation"));
        }
        let conversation_id = document.source_id().to_owned();
        let workspace = &store.workspaces[workspace_id];
        goals::verify_conversation_root(workspace, document.raw())?;
        if !matches!(origin, Origin::Passive) {
            goals::verify_goal_identity(store, workspace_id, &conversation_id)?;
        }
        let mut document = document.into_raw();
        if !create && matches!(origin, Origin::User) {
            admission_recovery::recover_idle_agent(store, &mut document)?;
        }
        if let Some(title) = admission.title {
            document["title"] = json!(title);
            document["metadata"]["title"] = json!(title);
            document["metadata"]["title_locked"] = json!(true);
        }
        if let Some(Value::Object(metadata)) = admission.metadata {
            document["metadata"].as_object_mut()
                .ok_or_else(|| RuntimeError::bad_request("Invalid conversation metadata"))?
                .extend(metadata);
        }
        apply_turn_settings(&mut document["metadata"], payload)?;
        let fast = document["metadata"]["chatgpt_fast_mode"].as_bool().unwrap_or(false);
        if document["metadata"]["git_review"].is_object() && !admission.exclusive_workspace {
            return Err(RuntimeError::new(422, "Git review conversations require their fixed review tools"));
        }
        let execution_root = if admission.tools.allows_workflow() {
            Self::worktree_execution_root(store, workspace_id, &document["metadata"])?
        } else {
            None
        };
        let tools: Arc<dyn AgentTools> = if let Some(root) = &execution_root {
            let base = store.workspace_tools.get(workspace_id).unwrap_or(&self.tools);
            Arc::new(base.with_execution_root(root, &store.data_dir).map_err(RuntimeError::storage)?)
        } else {
            admission.tools
        };
        let worktree_notice = tools.allows_workflow().then(|| worktree::notice(&mut document["metadata"])).flatten();
        if approval.as_ref().is_some_and(|settings| settings.principal.workspace_id() != workspace_id) {
            return Err(RuntimeError::new(403, "Task approval workspace does not match"));
        }
        let user = json!({"role":"user","content":message});
        let mut durable_user = user.clone();
        let message_source = optional_string(payload, "message_source")?.map(str::to_owned);
        let message_metadata = admission.user_message_metadata.or_else(|| message_source.as_ref().map(|source| json!({"message_source":source})));
        if let Some(metadata) = &message_metadata {
            if let Some(id) = metadata["message_id"].as_str().filter(|id| !id.is_empty()) {
                durable_user["message_id"] = json!(id);
            }
            durable_user["metadata"] = metadata.clone();
        }
        let task_id = Uuid::new_v4().to_string();
        let (sender, cancel) = oneshot::channel();
        let start = TaskStart {
            id: task_id.clone(), created_at, message: message.into(),
            model_key: Some(model.key.clone()), message_source,
        };
        let completion = Arc::new(TaskCompletion::default());
        let mut task = Task::new(start, workspace_id, conversation_id.clone(), sender);
        task.max_iterations=payload["max_iterations"].as_u64().filter(|n| *n>0);
        task.completion = Some(completion.clone());
        task.execution_root = execution_root;
        let mut prepared = None;
        self.goals.admit(crate::goals::Admission {
            conversation_id: &conversation_id, task_id: &task_id, message, request: &request, origin,
        }, |assignment| -> Result<(), RuntimeError> {
            if assignment.is_some() {
                self.goals.enforce_admission_modes(&mut document["metadata"]);
            }
            if let Some(settings) = approval.as_mut() {
                settings.apply_conversation_modes(&document["metadata"]);
            }
            task.approval = approval;
            system_prompt::freeze(store, workspace_id, &mut document, model, tools.writer().is_some())?;
            guidance::admit(&document, &mut task);
            let document = LegacyDocument::from_value(document).map_err(legacy_error)?;
            let token_statistics = &document.raw()["token_statistics"];
            let billing_scope = billing::BillingScope::main(conversation_id.clone(), token_statistics);
            let saved_context_tokens = token_statistics["current_context_tokens"].as_u64();
            let cursor = store.cleared_through.get(&(workspace_id.to_owned(),conversation_id.clone())).copied().unwrap_or_default();
            let mut messages = document.agent_history_after_clear(
                cursor, &store.data_dir, workspace, task.execution_root.as_deref().unwrap_or(workspace),
            ).map_err(legacy_error)?;
            let mut durable = Vec::new();
            if let Some(notice) = worktree_notice {
                let text = notice["content"].as_str().unwrap_or_default();
                messages.push(json!({"role":"user","content":text}));
                emit_user_message(&mut task, text, notice["metadata"].clone());
                durable.push(notice);
            }
            let (submitted, model_input) = if guidance::has_attachments(payload) {
                guidance::initial_media(
                    payload, durable_user, &store.data_dir, workspace,
                    task.execution_root.as_deref().unwrap_or(workspace), &document,
                )?
            } else {
                (vec![durable_user], vec![user])
            };
            let mut submitted_count = submitted.len();
            durable.extend(submitted);
            let mut model_input = model_input;
            if let Some(notice) = hidden_user_notice {
                let model_notice = json!({"role":"user","content":notice});
                let mut durable_notice = model_notice.clone();
                durable_notice["metadata"] = json!({"visibility":"hidden","source":"runtime_notice",
                    "message_source":"runtime_notice","starts_work":false});
                durable.push(durable_notice);
                model_input.push(model_notice);
                submitted_count += 1;
            }
            let prior_count = document.raw()["messages"].as_array().unwrap().len();
            let document = append_document(document, durable, created_at, None, Some(&model.key))?;
            let title_input = (prior_count == 0 && matches!(origin, Origin::User)
                && !message.trim().is_empty())
                .then(|| automatic_title::eligible(document.raw())).flatten()
                .map(|title| (title.to_owned(), message.to_owned()));
            task.version_message_id = document.raw()["messages"].as_array()
                .and_then(|rows| rows.len().checked_sub(submitted_count).and_then(|index| rows.get(index)))
                .and_then(|message| message["message_id"].as_str()).map(str::to_owned);
            if create {
                store.persist(workspace_id,document,true)?;
                if let Err(error) = versioning::set_enabled_on_create(store, workspace_id, &conversation_id) {
                    task.emit("versioning_error",json!({"conversation_id":conversation_id,"error":error.message}));
                }
            } else {
                store.persist_admission(workspace_id,document,prior_count)?;
            }
            if let Some(metadata) = message_metadata {
                emit_user_message(&mut task, message, metadata);
            }
            messages.extend(model_input);
            if let Some(assignment) = assignment {
                task.goal_run = Some(assignment.run.clone());
            }
            task.checkpoint_events();
            prepared = Some((billing_scope, saved_context_tokens, messages, title_input));
            Ok(())
        }).map(|assignment| {
            if let Some(assignment) = assignment {
                task.emit("goal_progress", assignment.progress);
            }
        })?;
        let (billing_scope, saved_context_tokens, messages, title_input) = prepared.expect("admission persisted the task");
        store.register_task(task);
        Ok(AdmittedTask {
            handle, cancel, workspace_id: workspace_id.into(), conversation_id,
            task_id, created_at, billing_scope, messages, tools, saved_context_tokens, fast, completion,
            title_input,
        })
    }

    fn spawn_admitted(&self, admitted: AdmittedTask, model: ModelConfig) -> Value {
        let AdmittedTask { handle, cancel, workspace_id, conversation_id, task_id, created_at,
            billing_scope, messages, tools, saved_context_tokens, fast, completion, title_input } = admitted;
        let model = self.with_conversation_session(model, &workspace_id, &conversation_id, fast);
        if let Some((title, first_message)) = title_input {
            self.spawn_automatic_title(&workspace_id, &conversation_id, &title, &first_message);
        }
        let runtime = self.clone();
        let worker_runtime = self.clone();
        let worker_id = task_id.clone();
        let recovery_model = model.clone();
        let worker = handle.spawn(async move {
            tokio::select! {
                biased;
                _ = cancel => Err("Task stopped".into()),
                result = agent::run(&worker_runtime, &worker_id, model, messages, tools.as_ref(), billing_scope, saved_context_tokens) => result,
            }
        });
        let supervisor_id = task_id.clone();
        handle.spawn(async move {
            let result = worker.await.unwrap_or_else(|_| Err("Task worker exited unexpectedly".into()));
            let _ = tokio::task::spawn_blocking(move || {
                let release = ReleaseTask(completion);
                let runtime = runtime;
                let recovery = runtime.finish(&supervisor_id, result);
                let schedule = recovery.map(|recovery| runtime.goal_recovery_scheduler(recovery, recovery_model));
                drop(runtime);
                drop(release);
                if let Some(schedule) = schedule { schedule(); }
            }).await;
        });
        json!({"success":true,"data":{"task_id":task_id,"workspace_id":workspace_id,
            "conversation_id":conversation_id,"created_at":created_at,"status":Status::Running}})
    }

    pub fn poll_task(
        &self,
        workspace_id: &str,
        task_id: &str,
        from: u64,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        let task = store
            .tasks
            .get(task_id)
            .filter(|task| task.workspace_id == workspace_id)
            .ok_or_else(RuntimeError::task_not_found)?;
        let mut result = task.poll(from);
        if workspace_id.is_empty() {
            result["data"]["title_pending"] = json!(store.pending_titles.contains(
                &(String::new(), task.conversation_id.clone()),
            ));
        }
        Ok(result)
    }

    pub fn stop_task_admission(&self) -> Result<(), RuntimeError> {
        self.store()?.shutting_down = true;
        Ok(())
    }

    pub fn cancel_task(&self, workspace_id: &str, task_id: &str) -> Result<Value, RuntimeError> {
        let mut store = self.store()?;
        let pause_goal = !store.shutting_down;
        let task = store
            .tasks
            .get_mut(task_id)
            .filter(|task| task.workspace_id == workspace_id)
            .ok_or_else(RuntimeError::task_not_found)?;
        if task.status == Status::Running && !task.cancel_requested {
            if pause_goal
                && let Some(run) = &task.goal_run
                && let Some(progress) = self.goals.pause_task(run)?
            {
                task.emit("goal_progress", progress);
            }
            task.cancel_requested = true;
            if let Some(sender) = task.cancel.take() {
                let _ = sender.send(());
            }
            store.approvals.expire_task(task_id);
            interactions::expire_task(&mut store, task_id);
        }
        Ok(json!({"success":true}))
    }

    pub fn list_sessions(
        &self,
        workspace_id: &str,
        limit: usize,
        offset: usize,
        quick_entry: Option<bool>,
    ) -> Result<Value, RuntimeError> {
        let mut rows = self.store()?.session_rows(workspace_id)?;
        rows.retain(|row| quick_entry.is_none_or(|value| (row["quick_entry"] == true) == value));
        rows.sort_by(|a, b| {
            b["updated_at"]
                .as_str()
                .cmp(&a["updated_at"].as_str())
                .then_with(|| a["id"].as_str().cmp(&b["id"].as_str()))
        });
        let total = rows.len();
        let conversations: Vec<_> = rows.into_iter().skip(offset).take(limit).collect();
        Ok(
            json!({"success":true,"conversations":conversations,"total":total,"limit":limit,
            "offset":offset,"has_more":offset.saturating_add(limit) < total}),
        )
    }

    pub fn session_history(
        &self,
        workspace_id: &str,
        conversation_id: &str,
    ) -> Result<Value, RuntimeError> {
        let document = self.store()?.read(workspace_id, conversation_id)?;
        Ok(json!({"success":true,"conversation":public_document(document)}))
    }

    pub fn session_model_key(
        &self,
        workspace_id: &str,
        conversation_id: &str,
    ) -> Result<Option<String>, RuntimeError> {
        let store = self.store()?;
        let row = store
            .session_index(workspace_id, conversation_id)?
            .ok_or_else(RuntimeError::session_not_found)?;
        if let Some(key) = row["model_key"].as_str() {
            return Ok(Some(key.into()));
        }
        let document = store.read(workspace_id, conversation_id)?;
        Ok(document.raw()["metadata"]["model_key"]
            .as_str()
            .map(str::to_owned))
    }

    pub fn session_token_stats(
        &self,
        workspace_id: &str,
        conversation_id: &str,
    ) -> Result<Value, RuntimeError> {
        let (raw,count) = self.store()?.header(workspace_id, conversation_id)?;
        let mut stats = json!(TokenStats::from_document(&raw["token_statistics"]));
        stats["model_key"] = raw["metadata"]["model_key"].clone();
        stats["title"] = raw["title"].clone();
        stats["message_count"] = json!(count);
        Ok(json!({"success":true,"stats":stats}))
    }

    fn store(&self) -> Result<MutexGuard<'_, Store>, RuntimeError> {
        self.store.lock().map_err(RuntimeError::storage)
    }


    fn emit(&self, id: &str, kind: &str, data: Value) -> Result<(), String> {
        let mut store = self.store().map_err(|error| error.message)?;
        writable_task(&mut store, id)?.emit(kind, data);
        Ok(())
    }

    fn commit_tool_batch(
        &self,
        id: &str,
        sequence: usize,
        messages: Vec<Value>,
    ) -> Result<Vec<Value>, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        writable_task(&mut store, id)?;
        store
            .commit_tool_batch(id, sequence, messages)
            .map_err(|error| error.message)
    }

    fn task_has_approval_scope(&self, id: &str) -> Result<bool, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        Ok(writable_task(&mut store, id)?.approval.is_some())
    }

    fn skill_roots(&self, task_id: &str, source: &str) -> Result<(PathBuf,PathBuf),String> {
        let mut store = self.store().map_err(|error|error.message)?;
        let task = writable_task(&mut store,task_id)?;
        let execution = task.execution_root.clone();
        let workspace = task.workspace_id.clone();
        let root = store.workspaces.get(&workspace).ok_or("Missing skill workspace")?.clone();
        let authorized = host_policy::native_path_root(&store,task_id,source,true)?;
        let source_root = authorized.or(execution).unwrap_or_else(||root.clone());
        Ok((root,source_root))
    }

    fn media_roots(&self, task_id: &str, path: &str, aliases: &Value) -> Result<(PathBuf, PathBuf), String> {
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        let execution = task.execution_root.clone();
        let workspace = task.workspace_id.clone();
        let root = store.workspaces.get(&workspace).ok_or("Missing media workspace")?.clone();
        let execution = execution.unwrap_or_else(|| root.clone());
        if aliases.get(path).is_none() {
            if let Some(external) = host_policy::native_path_root(&store, task_id, path, false)? {
                return Ok((external.clone(), external));
            }
        }
        Ok((root, execution))
    }

    fn web_request_context(&self, task_id: &str) -> Result<Option<PathBuf>, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        let id = task.conversation_id.clone();
        let workspace = task.workspace_id.clone();
        let path = store.conversation_path(&workspace, &id).map_err(|error| error.message)?;
        let db = crate::conversation_db::Database::open(path.parent().ok_or("Missing conversation directory")?)?;
        let restricted = db.field(&id, "metadata", "network_permission")?.is_some_and(|permission| permission == "none");
        Ok((!restricted).then(|| store.data_dir.clone()))
    }

    fn record_usage(&self, id: &str, usage: &Value) -> Result<(), String> {
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, id)?;
        let workspace_id = task.workspace_id.clone();
        let conversation_id = task.conversation_id.clone();
        let (header, _) = store.header(&workspace_id, &conversation_id).map_err(|error| error.message)?;
        let mut stats = TokenStats::from_document(&header["token_statistics"]);
        if stats.record(usage) {
            store.append_messages(&workspace_id, &conversation_id, Vec::new(), now(), Some(&stats), None)
                .map_err(|error| error.message)?;
            let threshold = crate::web_assets::personalization::automatic_deep_limit(&store.data_dir,&header["metadata"]).map_err(|e| e.to_string())?;
            let task = writable_task(&mut store,id)?;
            if task.kind=="chat" && task.compression_stage.is_none() && !task.compression_pending
                && stats.current_context_tokens.zip(threshold).is_some_and(|(count,limit)| count>=limit) {
                task.compression_pending=true;
                task.auto_compression=true;
            }
            task.emit("token_update", stats.event_data());
        }
        Ok(())
    }
}

impl ApprovalBackend for Runtime {
    fn list_pending(&self, principal: &ApprovalPrincipal, conversation_id: Option<&str>) -> Result<Vec<Approval>, ApprovalError> {
        self.store().map_err(|_| ApprovalError::Unavailable)?.approvals.list_pending(principal, conversation_id)
    }

    fn decide(&self, principal: &ApprovalPrincipal, approval_id: &str, decision: Decision, remember_site: bool) -> Result<Approval, ApprovalError> {
        self.store().map_err(|_| ApprovalError::Unavailable)?.approvals.decide(principal, approval_id, decision, remember_site)
    }
}

fn emit_user_message(task: &mut Task, message: &str, metadata: Value) {
    let mut event = metadata.as_object().cloned().unwrap_or_default();
    event.insert("message".into(), json!(message));
    event.insert("content".into(), json!(message));
    event.insert("metadata".into(), metadata);
    task.emit("user_message", Value::Object(event));
}

fn verify_workspace_binding(store: &Store, workspace_id: &str, expected: Option<&Path>) -> Result<(), RuntimeError> {
    validate_workspace(workspace_id)?;
    let bound = store.workspaces.get(workspace_id).ok_or_else(|| {
        if expected.is_some() {
            RuntimeError::new(409, "Workspace binding changed before runtime operation")
        } else {
            RuntimeError::bad_request("Workspace is not bound")
        }
    })?;
    if expected.is_some_and(|path| path != bound)
        || fs::canonicalize(bound).ok().as_ref() != Some(bound)
        || !bound.is_dir()
        || store.workspace_tools.get(workspace_id).is_some_and(|tools| !tools.matches_workspace(bound))
    {
        return Err(RuntimeError::new(409, "Workspace binding changed before runtime operation"));
    }
    Ok(())
}

fn public_document(document: LegacyDocument) -> Value {
    let messages = if document.kind() == crate::legacy::LegacyKind::Chat {
        document.public_chat_path(None).unwrap_or_default()
    } else {
        document.public_messages()
    };
    let mut raw = document.into_raw();
    raw["messages"] = json!(messages);
    raw
}

fn writable_task<'a>(store: &'a mut Store, id: &str) -> Result<&'a mut Task, String> {
    store
        .tasks
        .get_mut(id)
        .filter(|task| task.status == Status::Running && !task.cancel_requested)
        .ok_or_else(|| "Task stopped".into())
}

fn new_document(
    store: &Store,
    workspace_id: &str,
    payload: &Value,
    timestamp: f64,
) -> Result<LegacyDocument, RuntimeError> {
    new_document_with_id(store, workspace_id, payload, timestamp, None)
}

fn new_document_with_id(
    store: &Store, workspace_id: &str, payload: &Value, timestamp: f64, requested_id: Option<&str>,
) -> Result<LegacyDocument, RuntimeError> {
    let root = store
        .workspaces
        .get(workspace_id)
        .ok_or_else(|| RuntimeError::bad_request("Workspace is not bound"))?;
    let time = iso_time(timestamp)
        .ok_or_else(|| RuntimeError::storage("invalid conversation timestamp"))?;
    let mut milliseconds = (timestamp * 1000.0) as i64;
    let id = if let Some(id) = requested_id {
        store.conversation_path(workspace_id, id)?;
        id.to_owned()
    } else { loop {
        let date = OffsetDateTime::from_unix_timestamp(milliseconds / 1000)
            .map_err(RuntimeError::storage)?;
        let id = format!(
            "conv_{:04}{:02}{:02}_{:02}{:02}{:02}_{:03}",
            date.year(),
            date.month() as u8,
            date.day(),
            date.hour(),
            date.minute(),
            date.second(),
            milliseconds % 1000
        );
        if !store.conversation_id_exists(&id)? {
            break id;
        }
        milliseconds += 1;
    }};
    let mut raw = json!({
        "id":id,"title":"新对话","created_at":time,"updated_at":time,"messages":[],"todo_list":null,
        "metadata":{
            "project_path":root,"project_relative_path":null,"thinking_mode":false,"run_mode":"fast",
            "model_key":optional_string(payload, "model_key")?,"quick_entry":payload["quick_entry"] == true,
            "execution_mode":"sandbox","pending_permission_mode":null,
            "pending_execution_mode":null,"frozen_system_prompt":null,
            "workspace_memory_enabled":true,"has_images":false,"has_videos":false,"total_messages":0,"total_tools":0,
            "status":"active","compression_count":0,"deep_compression_records":[]
        },
        "token_statistics":{}
    });
    let tool_preferences = crate::web_assets::personalization::runtime_creation_tools(&store.data_dir)
        .map_err(RuntimeError::storage)?;
    raw["metadata"].as_object_mut().expect("new conversation metadata").extend(tool_preferences);
    if let Some(overrides) = store.draft_categories.get(workspace_id) {
        raw["metadata"]["runtime_tool_category_overrides"] = overrides.clone();
    }
    apply_turn_settings(&mut raw["metadata"], payload)?;
    LegacyDocument::from_value(raw).map_err(legacy_error)
}

fn apply_turn_settings(metadata: &mut Value, payload: &Value) -> Result<(), RuntimeError> {
    if let Some(value) = payload.get("chatgpt_fast_mode") {
        metadata["chatgpt_fast_mode"] = json!(value.as_bool().ok_or_else(|| RuntimeError::bad_request("chatgpt_fast_mode must be a boolean"))?);
    }
    let mode = optional_string(payload, "run_mode")?.filter(|mode| matches!(*mode, "fast" | "thinking" | "deep"));
    let thinking = match &payload["thinking_mode"] {
        Value::Bool(value) => Some(*value),
        Value::Null => mode.map(|mode| mode != "fast"),
        _ => return Err(RuntimeError::bad_request("thinking_mode must be a boolean")),
    };
    if let Some(thinking) = thinking {
        metadata["thinking_mode"] = json!(thinking);
        metadata["run_mode"] = json!(match (thinking, mode) {
            (true, Some("deep")) => "deep",
            (true, _) => "thinking",
            (false, _) => "fast",
        });
    }
    if payload.get("reasoning_effort").is_some() {
        let effort = optional_string(payload, "reasoning_effort")?
            .map(str::trim)
            .filter(|effort| !effort.is_empty());
        metadata["reasoning_effort"] = json!(effort);
    }
    Ok(())
}

fn validate_payload(workspace_id: &str, payload: &Value) -> Result<(), RuntimeError> {
    validate_workspace(workspace_id)?;
    if !payload.is_object() {
        return Err(RuntimeError::bad_request("Expected a JSON object"));
    }
    Ok(())
}

fn required_message(payload: &Value) -> Result<&str, RuntimeError> {
    let mut message = optional_string(payload, "message")?
        .unwrap_or_default()
        .trim();
    for field in ["images", "videos", "files"] {
        if !payload[field].is_null() && !payload[field].is_array() {
            return Err(RuntimeError::bad_request(format!("{field} must be an array")));
        }
    }
    if message.is_empty() && guidance::has_attachments(payload) {
        message = guidance::ATTACHMENT_PROMPT;
    }
    if message.is_empty() {
        return Err(RuntimeError::bad_request("message is required"));
    }
    if message.chars().count() > 200_000 {
        return Err(RuntimeError::bad_request("message is too long"));
    }
    let skills = &payload["skill_refs"];
    if !skills.is_null() && !skills.as_array().is_some_and(Vec::is_empty) {
        return Err(RuntimeError::bad_request("skill_refs are unsupported by this runtime"));
    }
    Ok(message)
}

fn optional_string<'a>(payload: &'a Value, field: &str) -> Result<Option<&'a str>, RuntimeError> {
    match &payload[field] {
        Value::Null => Ok(None),
        Value::String(value) => Ok(Some(value)),
        _ => Err(RuntimeError::bad_request(format!(
            "{field} must be a string"
        ))),
    }
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn iso_time(timestamp: f64) -> Option<String> {
    let milliseconds = (timestamp * 1000.0).round() as i128;
    OffsetDateTime::from_unix_timestamp_nanos(milliseconds.checked_mul(1_000_000)?)
        .ok()?
        .format(&Rfc3339)
        .ok()
}

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/runtime_contract.rs"]
mod tests;
