//! Named child tools and notice delivery through the existing Agent writer gate.

use std::sync::Arc;

use serde_json::{Value, json};

use crate::{
    approval::ApprovalPrincipal, catalog::CatalogSource,
    sub_agents::{Create, Parent, ParentAccess, SubAgents, protocol},
    workspace_catalog::RegisteredWorkspace,
};

use super::{
    ModelConfig, Origin, Runtime, RuntimeError, Status, Store, TaskAdmission, TaskApprovalSettings,
    emit_user_message, iso_time, now, resolve_task_approval_settings, verify_workspace_binding, writable_task,
};

impl Runtime {
    pub fn with_sub_agents(mut self, children: SubAgents) -> Self {
        self.sub_agents = Some(Arc::new(children));
        self
    }

    pub(super) fn child_activity(&self, store: &Store, workspace: &str, conversation: &str) -> (bool, bool) {
        let Some(children) = &self.sub_agents else { return (false, false); };
        let Some(root) = store.workspaces.get(workspace) else { return (false, false); };
        let workspace = RegisteredWorkspace { id: workspace.into(), label: String::new(), path: root.clone() };
        let parent = Parent { workspace_id: workspace.id.clone(), root: root.clone(), conversation_id: conversation.into() };
        children.activity_for_bound_parent(&workspace, &parent).unwrap_or_default()
    }

    pub(super) fn sub_agent_tool_schemas(&self) -> Vec<Value> {
        self.sub_agents.as_ref().map_or_else(Vec::new, |_| protocol::main_schemas())
    }

    pub(super) fn validate_sub_agent_tool(&self, name: &str, args: &Value) -> Option<Result<(), String>> {
        self.sub_agents.as_ref()?;
        matches!(name, protocol::CREATE | protocol::SEND | protocol::STATUS | protocol::STOP | protocol::TERMINATE | protocol::ANSWER)
            .then(|| args.as_object().map(|_| ()).ok_or_else(|| format!("Invalid {name} arguments")))
    }

    pub(super) async fn execute_sub_agent_tool(&self, task_id: &str, model: &ModelConfig, name: &str, args: &Value) -> Result<Value, String> {
        let runtime = self.clone();
        let task = task_id.to_owned();
        let model = model.clone();
        let name = name.to_owned();
        let args = args.clone();
        let reply = tokio::task::spawn_blocking(move || runtime.sub_agent_tool(&task, &model, &name, &args)).await
            .map_err(|_| "Child tool worker exited unexpectedly".to_owned())?;
        Ok(reply.unwrap_or_else(|error| json!({"success":false,"error":error})))
    }

    fn sub_agent_tool(&self, task_id: &str, model: &ModelConfig, name: &str, args: &Value) -> Result<Value, String> {
        let children = self.sub_agents.as_ref().ok_or("子智能体运行时不可用。")?;
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        let workspace_id = task.workspace_id.clone();
        let conversation_id = task.conversation_id.clone();
        let execution_root = task.execution_root.clone();
        let owner = task.approval.as_ref().map(|settings| settings.principal.username().to_owned());
        let access = task.approval.as_ref().map_or(ParentAccess { limited:true, direct:false, unrestricted:false }, TaskApprovalSettings::child_access);
        verify_workspace_binding(&store, &workspace_id, None).map_err(|error| error.message)?;
        let root = store.workspaces[&workspace_id].clone();
        let (document, _) = store.header(&workspace_id, &conversation_id).map_err(|error| error.message)?;
        super::strict_guides::enforce(&store.data_dir, super::strict_guides::SUB_AGENT_SETTING, &document["metadata"])?;
        let reasoning = document["metadata"]["reasoning_effort"].as_str();
        let parent = Parent { workspace_id, root, conversation_id };
        // During admission/cancellation the main task lock stays held until a
        // child mutation is committed. Child workers never acquire this lock.
        match name {
            protocol::CREATE => children.create(&parent, Create {
                model, access, owner: owner.as_deref().unwrap_or("host"), execution_root: execution_root.as_deref(),
                reasoning_effort: reasoning, args,
            }),
            protocol::SEND => children.send(&parent, args),
            protocol::STATUS => children.status(&parent, args),
            protocol::STOP => children.stop_named(&parent, args, false),
            protocol::TERMINATE => children.stop_named(&parent, args, true),
            protocol::ANSWER => children.answer(&parent, None, args),
            _ => Err("不支持的子智能体工具。".into()),
        }
    }

    pub(super) fn activate_sub_agents_for_task(&self, task_id: &str) -> Result<(), String> {
        let Some(children) = &self.sub_agents else { return Ok(()); };
        let (workspace, parent) = {
            let mut store = self.store().map_err(|error| error.message)?;
            let task = writable_task(&mut store, task_id)?;
            let workspace_id = task.workspace_id.clone();
            let parent = task.conversation_id.clone();
            let path = store.workspaces[&workspace_id].clone();
            (RegisteredWorkspace { id:workspace_id, label:String::new(), path }, parent)
        };
        children.activate(&workspace, &parent).map_err(|error| error.to_string())
    }

    pub(super) fn drain_sub_agent_notices(&self, task_id: &str, preceding: Option<Value>) -> Result<Vec<Value>, String> {
        let Some(children) = &self.sub_agents else { return Ok(Vec::new()); };
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        let (workspace_id, conversation_id) = (task.workspace_id.clone(), task.conversation_id.clone());
        let root = store.workspaces.get(&workspace_id).cloned().ok_or("工作区不可用。")?;
        let parent = Parent { workspace_id, conversation_id, root };
        let queue = children.pending_notices(&parent)?;
        if queue.is_empty() { return Ok(Vec::new()); }
        let timestamp = now();
        let fresh = fresh_notices(&store, &parent, &queue, timestamp).map_err(|error| error.message)?;
        if !fresh.is_empty() {
            let durable = preceding.into_iter().chain(fresh.iter().cloned()).collect();
            store.append_messages(&parent.workspace_id, &parent.conversation_id, durable, timestamp, None, None)
                .map_err(|error| error.message)?;
            let task = writable_task(&mut store, task_id)?;
            for message in &fresh {
                emit_user_message(task, message["content"].as_str().unwrap_or_default(), message["metadata"].clone());
            }
            task.checkpoint_events();
        }
        ack(children, &parent, &queue);
        Ok(fresh.into_iter().map(|message| json!({"role":"user","content":message["content"]})).collect())
    }

    /// Call after authenticating the current workspace and conversation. A
    /// pending child question may wake an idle main task while the child runs.
    pub fn poll_sub_agents(&self, workspace: &RegisteredWorkspace, parent: &str,
        principal: ApprovalPrincipal, catalog: &CatalogSource, fixed: Option<&ModelConfig>,
    ) -> Result<Option<Value>, RuntimeError> {
        let Some(children) = &self.sub_agents else { return Ok(None); };
        if principal.workspace_id() != workspace.id { return Err(RuntimeError::session_not_found()); }
        let (document, _) = self.store()?.header(&workspace.id, parent)?;
        if !is_agent(&document) { return Ok(None); }
        children.activate(workspace, parent).map_err(RuntimeError::storage)?;
        if !children.has_pending(workspace, parent).map_err(RuntimeError::storage)? {
            return Ok(None);
        }
        let model = notice_model(&document["metadata"], catalog, fixed)?;
        let settings = resolve_task_approval_settings(principal, catalog)?;
        self.dispatch_sub_agent_notices(workspace, parent, model, settings)
    }

    pub(crate) fn poll_background_notices(
        &self, workspace: &RegisteredWorkspace, conversation: &str, principal: ApprovalPrincipal,
        catalog: &CatalogSource, fixed: Option<&ModelConfig>,
    ) -> Result<(), RuntimeError> {
        if principal.workspace_id() != workspace.id {
            return Err(RuntimeError::session_not_found());
        }
        if self.poll_sub_agents(workspace, conversation, principal.clone(), catalog, fixed)?.is_some() {
            return Ok(());
        }
        self.poll_process_notices(workspace, conversation, principal, catalog, fixed)
    }

    pub fn dispatch_sub_agent_notices(&self, workspace: &RegisteredWorkspace, parent: &str,
        model: ModelConfig, approval: TaskApprovalSettings,
    ) -> Result<Option<Value>, RuntimeError> {
        let Some(children) = &self.sub_agents else { return Ok(None); };
        let mut store = self.store()?;
        let root = workspace.path.canonicalize().map_err(RuntimeError::storage)?;
        verify_workspace_binding(&store, &workspace.id, Some(&root))?;
        let (document, _) = store.header(&workspace.id, parent)?;
        if !is_agent(&document) { return Ok(None); }
        let parent = Parent { workspace_id:workspace.id.clone(), conversation_id:parent.to_owned(), root };
        if main_running(&store, &parent) { return Ok(None); }
        let queue = children.pending_notices(&parent).map_err(RuntimeError::storage)?;
        let timestamp = now();
        let timestamp_text = iso_time(timestamp).unwrap_or_default();
        let mut stale = 0;
        for message in &queue {
            let metadata = protocol::notice_metadata(message, &timestamp_text);
            if !stored_notice(&store, &parent, &metadata["message_id"])? { break; }
            stale += 1;
        }
        ack(children, &parent, &queue[..stale]);
        let Some(text) = queue.get(stale) else { return Ok(None); };
        let mut metadata = protocol::notice_metadata(text, &timestamp_text);
        metadata["inline"] = json!(false);
        let payload = json!({"conversation_id":parent.conversation_id,"message":text,"model_key":model.key(),"message_source":"sub_agent"});
        let tools = store.workspace_tools.get(&workspace.id).unwrap_or(&self.tools).clone();
        let admission = TaskAdmission { exclusive_workspace:false, title:None, metadata:None,
            new_conversation_id:None, user_message_metadata:Some(metadata), tools:Arc::new(tools) };
        let admitted = match self.admit_goal_task(&mut store, &workspace.id, &payload, &model, admission, Some(approval), Origin::Automatic) {
            Ok(task) => task,
            Err(error) if error.status == 409 => return Ok(None),
            Err(error) => return Err(error),
        };
        ack(children, &parent, &queue[stale..=stale]);
        drop(store);
        Ok(Some(self.spawn_admitted(admitted, model)))
    }
}

pub(super) fn notice_model(metadata: &Value, catalog: &CatalogSource, fixed: Option<&ModelConfig>) -> Result<ModelConfig, RuntimeError> {
    if let Some(model) = fixed {
        return Ok(model.clone());
    }
    let thinking = metadata["thinking_mode"].as_bool().unwrap_or(matches!(metadata["run_mode"].as_str(), Some("thinking" | "deep")));
    let model = catalog.load().select_for_turn(metadata["model_key"].as_str(), thinking, metadata["reasoning_effort"].as_str())
        .map_err(|error| RuntimeError::new(503, error))?;
    ModelConfig::from_catalog(model).map_err(|error| RuntimeError::new(503, error))
}

fn is_agent(document: &Value) -> bool {
    document["metadata"].get("conversation_kind").is_none_or(|kind| kind == "agent")
}

fn main_running(store: &Store, parent: &Parent) -> bool {
    store.tasks.values().any(|task| task.workspace_id == parent.workspace_id &&
        task.conversation_id == parent.conversation_id && task.status == Status::Running)
}

fn fresh_notices(store: &Store, parent: &Parent, queue: &[String], timestamp: f64) -> Result<Vec<Value>, RuntimeError> {
    let timestamp = iso_time(timestamp).unwrap_or_default();
    let mut ids = std::collections::HashSet::new();
    let mut messages = Vec::new();
    for text in queue {
        let metadata = protocol::notice_metadata(text, &timestamp);
        if let Some(id) = metadata["message_id"].as_str()
            && ids.insert(id.to_owned()) && !stored_notice(store, parent, &metadata["message_id"])?
        {
            messages.push(json!({"role":"user","content":text,"message_id":id,"metadata":metadata}));
        }
    }
    Ok(messages)
}

fn stored_notice(store: &Store, parent: &Parent, id: &Value) -> Result<bool, RuntimeError> {
    let id = id.as_str().ok_or_else(|| RuntimeError::storage("Child notice lacks message identity"))?;
    store.has_user_message_id(&parent.workspace_id, &parent.conversation_id, id)
}

fn ack(children: &SubAgents, parent: &Parent, queue: &[String]) {
    if let Err(error) = children.ack_notices(parent, queue) {
        // A durable message identity also makes the next poll safe when the
        // registry write failed after the main conversation committed it.
        eprintln!("Could not acknowledge child notices: {error}");
    }
}
