use std::{collections::HashMap, time::Duration};

use serde_json::{Value, json};
use tokio::time::{Instant, timeout_at};

use crate::{
    approval::{ApprovalPrincipal, TaskScope},
    interactions::{self, Error, Event, Kind, Plan, Question, QuestionAnswer, Wait},
    legacy::LegacyDocument,
    tools::read_file::ReadFile,
};

use super::{Runtime, approval::{WorkMode, is_plan_file, task_scope}, store::Store};

const WAIT: Duration = Duration::from_secs(3600);
const PERMISSIONS: [&str; 4] = ["readonly", "approval", "auto_approval", "unrestricted"];

impl Runtime {
    pub(crate) fn pending_interactions(
        &self,
        principal: &ApprovalPrincipal,
        kind: Kind,
        conversation_id: Option<&str>,
    ) -> Result<Vec<Value>, Error> {
        let mut store = self.store().map_err(|_| Error::Unavailable)?;
        Ok(store.interactions.pending(principal, kind, conversation_id))
    }

    pub(crate) fn answer_user_question(
        &self,
        principal: &ApprovalPrincipal,
        id: &str,
        answer: &QuestionAnswer,
    ) -> Result<Value, Error> {
        let mut store = self.store().map_err(|_| Error::Unavailable)?;
        let scope = answering_scope(&mut store, principal, Kind::Question, id)?;
        let (row, event) = store.interactions.answer_question(principal, id, answer)?;
        publish(&mut store, scope.task_id(), event);
        Ok(row)
    }

    pub(crate) fn answer_plan_approval(
        &self,
        principal: &ApprovalPrincipal,
        id: &str,
        approved: bool,
        comment: &str,
    ) -> Result<Value, Error> {
        let mut store = self.store().map_err(|_| Error::Unavailable)?;
        let scope = answering_scope(&mut store, principal, Kind::Plan, id)?;
        let (_, row) = store.interactions.target(principal, Kind::Plan, id)?;
        if approved && row["status"] == "pending" {
            approve_plan(&mut store, &scope)?;
        }
        let (row, event) = store.interactions.answer_plan(principal, id, approved, comment)?;
        publish(&mut store, scope.task_id(), event);
        Ok(row)
    }

    pub(super) fn interaction_tools(&self, task_id: &str) -> Result<Vec<Value>, String> {
        let store = self.store().map_err(|error| error.message)?;
        let Ok((_, settings)) = task_scope(&store, task_id) else { return Ok(Vec::new()); };
        Ok(if settings.valid_modes {
            interactions::schemas(settings.work_mode != WorkMode::Ask)
        } else {
            Vec::new()
        })
    }

    pub(super) async fn wait_for_questions<'a>(
        &self,
        task_id: &str,
        calls: impl IntoIterator<Item = (&'a str, &'a Value)>,
    ) -> Result<HashMap<String, Value>, String> {
        let mut results = HashMap::new();
        let mut questions = Vec::new();
        for (id, args) in calls {
            match Question::parse(id, args) {
                Ok(question) => questions.push(question),
                Err(message) => { results.insert(id.to_owned(), failure("invalid_arguments", message)); }
            }
        }
        if questions.is_empty() {
            return Ok(results);
        }
        let wait = {
            let mut store = self.store().map_err(|error| error.message)?;
            let (scope, settings) = task_scope(&store, task_id)?;
            if !settings.valid_modes || settings.work_mode == WorkMode::Ask {
                for question in questions {
                    results.insert(question.tool_call_id, failure("work_mode_blocked", "当前模式下请直接向用户提问。"));
                }
                return Ok(results);
            }
            let (wait, events) = store.interactions.open_questions(scope, questions);
            publish(&mut store, task_id, events);
            wait
        };
        for row in self.wait_for_interaction(task_id, wait).await? {
            if let Some(id) = row["tool_call_id"].as_str() {
                results.insert(id.to_owned(), interactions::question_result(&row));
            }
        }
        Ok(results)
    }

    pub(super) async fn submit_plan(
        &self, task_id: &str, call_id: &str, arguments: &Value,
    ) -> Result<Value, String> {
        let (scope, data) = {
            let store = self.store().map_err(|error| error.message)?;
            let (scope, settings) = task_scope(&store, task_id)?;
            if !settings.valid_modes || settings.work_mode != WorkMode::Plan {
                return Ok(failure("wrong_work_mode", "请在计划模式下提交计划。"));
            }
            (scope, store.data_dir.clone())
        };
        let path = arguments["plan_file"].as_str().unwrap_or_default().trim();
        if !is_plan_file(scope.workspace_root(), path) {
            return Ok(failure("invalid_plan_file", "请提交 .astrion/plan/ 下的 Markdown 计划文件。"));
        }
        let root = scope.workspace_root().to_owned();
        let read_path = path.to_owned();
        let bytes = tokio::task::spawn_blocking(move || {
            ReadFile::open(&root, &data).map_err(|error| error.to_string())?.read_bytes(&read_path)
        }).await.map_err(|_| "Plan file reader interrupted".to_owned())?;
        let bytes = match bytes {
            Ok(bytes) => bytes,
            Err(message) => return Ok(failure("plan_file_error", &message)),
        };
        let content = String::from_utf8_lossy(&bytes);
        if content.trim().is_empty() {
            return Ok(failure("plan_file_empty", "计划文件为空。"));
        }
        let plan = Plan::new(call_id, path, &content, &arguments["summary"]);
        let wait = {
            let mut store = self.store().map_err(|error| error.message)?;
            let (current, settings) = task_scope(&store, task_id)?;
            if scope != current || !settings.valid_modes || settings.work_mode != WorkMode::Plan {
                return Ok(failure("work_mode_blocked", "任务运行模式已变化，请重新提交计划。"));
            }
            let (wait, events) = store.interactions.open_plan(scope, plan);
            publish(&mut store, task_id, events);
            wait
        };
        let mut resolved = self.wait_for_interaction(task_id, wait).await?;
        let row = resolved.pop().ok_or("Missing plan decision")?;
        let approved = row["status"] == "approved";
        let message = match row["status"].as_str() {
            Some("approved") => "计划已批准，已进入执行模式。",
            Some("rejected") => "计划已被拒绝。",
            _ => "未收到计划裁决。",
        };
        let mut result = json!({"success":approved,"status":row["status"],"plan_file":path,
            "message":message,"comment":row["comment"]});
        if approved {
            result["plan_content"] = row["plan_content"].clone();
        }
        Ok(result)
    }

    async fn wait_for_interaction(&self, task_id: &str, wait: Wait) -> Result<Vec<Value>, String> {
        let deadline = Instant::now() + WAIT;
        loop {
            {
                let store = self.store().map_err(|error| error.message)?;
                if let Some(rows) = store.interactions.resolved(&wait).map_err(|_| "Interaction lost")? {
                    return Ok(rows.to_vec());
                }
            }
            if timeout_at(deadline, wait.notify.notified()).await.is_err() {
                let mut store = self.store().map_err(|error| error.message)?;
                let event = store.interactions.expire_wait(&wait, true);
                publish(&mut store, task_id, event);
            }
        }
    }
}

pub(super) fn expire_task(store: &mut Store, task_id: &str) {
    let events = store.interactions.expire_task(task_id);
    publish(store, task_id, events);
}

fn answering_scope(
    store: &mut Store, principal: &ApprovalPrincipal, kind: Kind, id: &str,
) -> Result<TaskScope, Error> {
    store.interactions.limit_answer(principal, kind)?;
    let (scope, row) = store.interactions.target(principal, kind, id)?;
    let scope = scope.clone();
    if row["status"] == "pending"
        && !task_scope(store, scope.task_id()).is_ok_and(|(current, _)| current == scope)
    {
        return Err(Error::Unavailable);
    }
    Ok(scope)
}

fn approve_plan(store: &mut Store, scope: &TaskScope) -> Result<(), Error> {
    let (_, mut settings) = task_scope(store, scope.task_id()).map_err(|_| Error::Unavailable)?;
    if !settings.valid_modes || settings.work_mode != WorkMode::Plan {
        return Err(Error::Unavailable);
    }
    let workspace = scope.principal().workspace_id();
    let document = store.read(workspace, scope.conversation_id()).map_err(|_| Error::Unavailable)?;
    let mut raw = document.into_raw();
    let metadata = raw["metadata"].as_object_mut().ok_or(Error::Unavailable)?;
    let prefs = super::store::read_json(&store.data_dir.join("personalization.json"))
        .map_err(|_| Error::Unavailable)?.unwrap_or_default();
    let prior = metadata.remove("pre_plan_permission_mode");
    let permission = prior.as_ref().and_then(Value::as_str)
        .filter(|mode| PERMISSIONS.contains(mode))
        .or_else(|| prefs["default_permission_mode"].as_str().filter(|mode| PERMISSIONS.contains(mode)))
        .unwrap_or("approval");
    let prior_execution = metadata.remove("pre_plan_execution_mode");
    let execution = if permission != "readonly" && prior_execution.is_some_and(|mode| mode == "direct") {
        "direct"
    } else {
        "sandbox"
    };
    metadata.insert("work_mode".into(), json!("execute"));
    metadata.insert("permission_mode".into(), json!(permission));
    metadata.insert("execution_mode".into(), json!(execution));
    settings.apply_conversation_modes(&raw["metadata"]);
    let document = LegacyDocument::from_value(raw).map_err(|_| Error::Unavailable)?;
    store.persist(workspace, document, false).map_err(|_| Error::Unavailable)?;
    store.process_resources.close_terminals(Some(workspace), Some(scope.conversation_id()));
    if let Some(task) = store.tasks.get_mut(scope.task_id()) {
        task.approval = Some(settings);
    }
    Ok(())
}

fn publish(store: &mut Store, task_id: &str, events: impl IntoIterator<Item = Event>) {
    if let Some(task) = store.tasks.get_mut(task_id) {
        for event in events {
            task.emit(event.name, event.data);
        }
    }
}

fn failure(status: &str, message: &str) -> Value {
    json!({"success":false,"status":status,"message":message})
}
