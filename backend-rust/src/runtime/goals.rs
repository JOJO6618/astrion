use std::{fs, path::{Path, PathBuf}, sync::Arc};

use serde_json::{Value, json};

use super::{ModelConfig, Runtime, RuntimeError, Status, TaskAdmission, TaskApprovalSettings, emit_user_message, now, verify_workspace_binding, writable_task};
use super::store::{Store, validate_workspace};
use crate::{
    catalog::CatalogSource,
    goals::{Control, Dispatch, GoalError, GoalFailure, GoalReviewer, GoalRun, Origin, Outcome, RetryTicket, TurnResult},
    legacy::{LegacyDocument, LegacyKind},
    workspace_catalog::RegisteredWorkspace,
};

pub(super) struct Recovery {
    workspace_id: String,
    project_path: PathBuf,
    ticket: RetryTicket,
    approval: Option<TaskApprovalSettings>,
}

impl Runtime {
    pub fn with_goal_reviewer(mut self, catalog: CatalogSource, pricing_config: &Path) -> Self {
        self.goal_reviewer = Some(GoalReviewer::new(self.goals.clone(), catalog, pricing_config));
        self
    }

    pub fn control_goal(
        &self, workspace: &RegisteredWorkspace, conversation_id: &str, payload: &Value,
    ) -> Result<Value, RuntimeError> {
        let mut store = self.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        let document = store.read(&workspace.id, conversation_id)?;
        if document.kind() != LegacyKind::Agent { return Err(RuntimeError::session_not_found()); }
        verify_conversation_root(&workspace.path, document.raw())?;
        verify_goal_identity(&store, &workspace.id, conversation_id)?;
        let progress = self.goals.control(conversation_id, Control::parse(payload)?)?;
        if let Some(task) = store.tasks.values_mut().find(|task| {
            task.workspace_id == workspace.id && task.conversation_id == conversation_id && task.status == Status::Running
        }) {
            task.emit("goal_progress", progress.clone());
        }
        Ok(json!({"success":true,"data":progress}))
    }

    pub fn conversation_running_status(
        &self, workspace: &RegisteredWorkspace, conversation_id: &str,
    ) -> Result<Value, RuntimeError> {
        let conversation_id = conversation_id.trim();
        let id = if conversation_id.starts_with("conv_") { conversation_id.to_owned() } else { format!("conv_{conversation_id}") };
        let (task, goal_progress, background, (children_running, children_pending)) = {
            let store = self.store()?;
            verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
            let (document, _) = store.header(&workspace.id, &id)?;
            let task = store.tasks.values().find(|task| {
                task.workspace_id == workspace.id && task.conversation_id == id && task.status == Status::Running
            }).map(|task| task.start.id.clone());
            let goal_progress = self.read_goal_progress_header(&store, &workspace.id, &id, &document)?;
            let background = Self::conversation_has_background(&store, &workspace.id, &id);
            let children = self.child_activity(&store, &workspace.id, &id);
            (task, goal_progress, background, children)
        };
        Ok(json!({"success":true,"data":{
            "conversation_id":id,
            "is_main_running":task.is_some(),
            "main_task_id":task.as_deref(),
            "main_task_type":task.as_ref().map(|_| "chat"),
            "has_running_sub_agents":false,
            "has_running_background_commands":background,
            "has_running_multi_agent":children_running || children_pending,
            "is_truly_active":task.is_some() || background || children_running || children_pending,
            "goal_progress":goal_progress.unwrap_or_else(|| json!({})),
        }}))
    }

    pub(super) fn read_goal_progress(
        &self, store: &Store, workspace_id: &str, document: &LegacyDocument,
    ) -> Result<Option<Value>, RuntimeError> {
        self.read_goal_progress_header(store, workspace_id, document.source_id(), document.raw())
    }

    fn read_goal_progress_header(&self, store: &Store, workspace_id: &str, id: &str, header: &Value) -> Result<Option<Value>, RuntimeError> {
        if !header["metadata"].get("conversation_kind").is_none_or(|kind| kind == "agent") { return Ok(None); }
        verify_goal_identity(store, workspace_id, id)?;
        if let Some(root) = store.workspaces.get(workspace_id) {
            verify_conversation_root(root, header)?;
        }
        let progress = self.goals.progress(id)?;
        Ok(progress.get("status").is_some().then_some(progress))
    }

    pub(super) fn task_goal(&self, task_id: &str) -> Result<Option<GoalRun>, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        Ok(writable_task(&mut store, task_id)?.goal_run.clone())
    }

    pub(super) async fn review_goal_turn(
        &self, run: &GoalRun, assistant: &Value, messages: &[Value],
    ) -> Result<Option<Value>, String> {
        let preparation = {
            let runtime = self.clone();
            let task_id = run.task_id.clone();
            let assistant = assistant.clone();
            tokio::task::spawn_blocking(move || {
                let mut store = runtime.store().map_err(|error| error.message)?;
                let task = writable_task(&mut store, &task_id)?;
                let workspace_id = task.workspace_id.clone();
                let conversation_id = task.conversation_id.clone();
                let work_root = task.execution_root.clone().or_else(|| store.workspaces.get(&workspace_id).cloned())
                    .ok_or("Workspace binding changed before goal review")?;
                store.append_messages(&workspace_id, &conversation_id, vec![assistant], now(), None, None)
                    .map_err(|error| error.message)?;
                writable_task(&mut store, &task_id)?.checkpoint_events();
                Ok::<_, String>(work_root)
            }).await.map_err(|_| "Goal checkpoint writer exited unexpectedly")??
        };
        let content = assistant["content"].as_str().unwrap_or_default();
        let result = self.goals.review_turn(run, content, messages, self.goal_reviewer.as_ref(), &preparation, |kind, data| {
            let mut store = self.store().map_err(|error| GoalError::new(error.status, error.message))?;
            let task = writable_task(&mut store, &run.task_id).map_err(|message| GoalError::new(409, message))?;
            if kind == "goal_progress" {
                if data["revision"].as_u64() > task.goal_progress["revision"].as_u64() {
                    task.emit(kind, data);
                }
            } else if self.goals.is_active(run)? {
                task.emit(kind, data);
            }
            Ok(())
        }).await.map_err(|error| error.message)?;
        let Some(reviewed) = result else { return Ok(None) };
        let runtime = self.clone();
        let run = run.clone();
        tokio::task::spawn_blocking(move || {
            let mut store = runtime.store()?;
            let workspace_id = writable_task(&mut store, &run.task_id).map_err(|message| RuntimeError::new(409, message))?.workspace_id.clone();
            let result = runtime.goals.apply_review(reviewed, |user| {
                store.append_messages(&workspace_id, &run.conversation_id, vec![user.clone()], now(), None, None)
            })?;
            let task = store.tasks.get_mut(&run.task_id).expect("current goal task");
            match result {
                TurnResult::Stop { event, progress } => {
                    if let Some(progress) = progress { task.emit(event, progress); }
                    Ok(None)
                }
                TurnResult::Continue { mut user, progress } => {
                    let metadata = user["metadata"].take();
                    user.as_object_mut().expect("goal user message").remove("metadata");
                    emit_user_message(task, user["content"].as_str().unwrap_or_default(), metadata);
                    task.checkpoint_events();
                    task.emit("goal_progress", progress);
                    Ok(Some(user))
                }
            }
        }).await.map_err(|_| "Goal review writer exited unexpectedly")?.map_err(|error: RuntimeError| error.message)
    }

    pub(super) fn finish(&self, id: &str, result: Result<Vec<Value>, String>) -> Option<Recovery> {
        let mut store = self.store().ok()?;
        let task = store.tasks.get(id)?;
        if task.status != Status::Running { return None; }
        let assignment = task.goal_run.clone().map(|run| {
            (run, task.workspace_id.clone(), task.approval.clone())
        });
        let mut outcome = if task.cancel_requested {
            Outcome::Cancelled
        } else {
            result.as_ref().err().map_or(Outcome::Interrupted, |error| Outcome::Failed(GoalFailure::from_message(error)))
        };
        let background = Self::conversation_has_background(&store, &task.workspace_id, &task.conversation_id);
        let (children_running, children_pending) = self.child_activity(&store, &task.workspace_id, &task.conversation_id);
        let running = json!({"has_running_background_commands":background,
            "has_running_sub_agents":false,"has_running_multi_agent":children_running || children_pending});
        if let Err(error) = store.finish_with_activity(id, result, running.clone()) {
            outcome = Outcome::Failed(GoalFailure::from_message(&error.message));
            let _ = store.finish_with_activity(id, Err(error.message), running);
        }
        if store.shutting_down { return None; }
        let (run, workspace_id, approval) = assignment?;
        let finished = match self.goals.finish(&run, outcome) {
            Ok(finished) => finished,
            Err(error) => {
                eprintln!("Could not finalize goal {id}: {error}");
                return None;
            }
        };
        if let Some(progress) = finished.progress {
            store.tasks.get_mut(id)?.emit("goal_progress", progress);
        }
        let project_path = store.workspaces.get(&workspace_id)?.clone();
        Some(Recovery { workspace_id, project_path, ticket: finished.retry?, approval })
    }

    pub(super) fn goal_recovery_scheduler(
        &self, recovery: Recovery, model: ModelConfig,
    ) -> impl FnOnce() + Send + 'static + use<> {
        let store = Arc::downgrade(&self.store);
        let tools = self.tools.clone();
        let billing = self.billing.clone();
        let workflow = self.workflow.clone();
        let goals = self.goals.clone();
        let goal_reviewer = self.goal_reviewer.clone();
        let sub_agents = self.sub_agents.as_ref().map(Arc::downgrade);
        let personalization = self.personalization.clone();
        let schedules = self.schedules.clone();
        move || {
            let result = (|| -> Result<(), RuntimeError> {
                let Some(owner) = store.upgrade() else { return Ok(()) };
                let guard = owner.lock().map_err(RuntimeError::storage)?;
                if guard.shutting_down || !goals.recovery_current(&recovery.ticket)? { return Ok(()) }
                let Recovery { workspace_id, project_path, ticket, approval } = recovery;
                let dispatch_goals = goals.clone();
                goals.schedule_recovery(ticket, move |ticket| {
                    let Some(store) = store.upgrade() else { return Dispatch::Stale };
                    let runtime = Runtime {
                        store, tools: tools.clone(), billing: billing.clone(), workflow: workflow.clone(),
                        goals: dispatch_goals.clone(), goal_reviewer: goal_reviewer.clone(),
                        sub_agents: sub_agents.as_ref().and_then(std::sync::Weak::upgrade),
                        personalization: personalization.clone(),
                        schedules: schedules.clone(),
                    };
                    match runtime.recover_goal(&workspace_id, &project_path, ticket, model.clone(), approval.clone()) {
                        Ok(()) => Dispatch::Started,
                        Err(error) if error.status == 409 && error.message.starts_with("A task is already running") => Dispatch::Busy,
                        Err(error) if error.status == 503 => Dispatch::Failed,
                        Err(_) => Dispatch::Stale,
                    }
                })?;
                Ok(())
            })();
            if let Err(error) = result { eprintln!("Could not schedule goal recovery: {error}"); }
        }
    }

    fn recover_goal(
        &self, workspace_id: &str, project_path: &Path, ticket: &RetryTicket, model: ModelConfig,
        approval: Option<TaskApprovalSettings>,
    ) -> Result<(), RuntimeError> {
        let admitted = {
            let mut store = self.store()?;
            verify_workspace_binding(&store, workspace_id, Some(project_path))?;
            let tools = store.workspace_tools.get(workspace_id).unwrap_or(&self.tools).clone();
            let payload = json!({"conversation_id":ticket.run.conversation_id,"message":ticket.message(),"message_source":"goal_recovery"});
            let metadata = json!({"source":"goal_recovery","message_source":"goal_recovery","visibility":"compact","starts_work":true});
            let admission = TaskAdmission {
                exclusive_workspace: false, title: None, metadata: None, new_conversation_id: None,
                user_message_metadata: Some(metadata), tools: Arc::new(tools),
            };
            self.admit_goal_task(&mut store, workspace_id, &payload, &model, admission, approval, Origin::Recovery(ticket))?
        };
        self.spawn_admitted(admitted, model);
        Ok(())
    }
}

pub(super) fn verify_conversation_root(root: &Path, document: &Value) -> Result<(), RuntimeError> {
    if let Some(path) = document["metadata"]["project_path"].as_str().filter(|path| !path.is_empty())
        && Path::new(path) != root
        && fs::canonicalize(path).is_ok_and(|saved_root| saved_root != root)
    {
        return Err(RuntimeError::new(409, "Conversation project path does not match the registered workspace"));
    }
    Ok(())
}

pub(super) fn verify_goal_identity(store: &Store, workspace_id: &str, id: &str) -> Result<(), RuntimeError> {
    let dir = store.data_dir.join("conversations");
    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(RuntimeError::storage(error)),
    };
    for entry in entries {
        let entry = entry.map_err(RuntimeError::storage)?;
        let name = entry.file_name();
        let Some(other) = name.to_str().filter(|name| *name != workspace_id && validate_workspace(name).is_ok()) else { continue };
        if !entry.file_type().map_err(RuntimeError::storage)?.is_dir() { continue; }
        if store.has_conversation(other,id)? {return Err(RuntimeError::new(409,"Conversation ID is shared by multiple workspaces"));}
    }
    Ok(())
}
