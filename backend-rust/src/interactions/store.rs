use std::{
    collections::{HashMap, VecDeque},
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde_json::{Value, json};
use tokio::sync::Notify;
use uuid::Uuid;

use super::{Error, Plan, Question, QuestionAnswer, tools::clip};
use crate::approval::{ApprovalPrincipal, TaskScope};

const RESOLVED_TTL: Duration = Duration::from_secs(3600);
const ANSWER_WINDOW: Duration = Duration::from_secs(60);
const ANSWER_LIMIT: usize = 120;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum Kind {
    Question,
    Plan,
}

#[derive(Debug)]
pub(crate) struct Event {
    pub(crate) name: &'static str,
    pub(crate) data: Value,
}

/// Only one worker waits on a group. Notify retains a permit when an answer
/// arrives after the state check and before the worker starts awaiting it.
#[derive(Clone)]
pub(crate) struct Wait {
    pub(crate) id: String,
    pub(crate) notify: Arc<Notify>,
}

/// Access while holding the same lock as the live task and tool approvals.
#[derive(Default)]
pub struct InteractionStore {
    groups: HashMap<String, Group>,
    index: HashMap<String, (String, usize)>,
    attempts: HashMap<(String, Kind), VecDeque<Instant>>,
}

struct Group {
    kind: Kind,
    scope: TaskScope,
    rows: Vec<Value>,
    pending: usize,
    completed: Option<Instant>,
    notify: Arc<Notify>,
}

impl InteractionStore {
    pub(crate) fn open_questions(
        &mut self,
        scope: TaskScope,
        questions: Vec<Question>,
    ) -> (Wait, Vec<Event>) {
        debug_assert!(!questions.is_empty());
        let id = new_id("question_batch");
        let count = questions.len();
        let created = timestamp();
        let mut events = Vec::with_capacity(count + 1);
        let rows = questions
            .iter()
            .enumerate()
            .map(|(index, question)| {
                let question_id = new_id("question");
                events.push(waiting_action(
                    &question.tool_call_id,
                    "awaiting_user_answer",
                    "question_id",
                    &question_id,
                    "正在等待用户回答。",
                ));
                let mut row = json!(question);
                row["question_id"] = json!(question_id);
                row["batch_id"] = json!(id);
                row["batch_index"] = json!(index + 1);
                row["batch_total"] = json!(count);
                row["answer"] = Value::Null;
                row["answered_at"] = Value::Null;
                add_scope(&mut row, &scope, created);
                row
            })
            .collect::<Vec<_>>();
        events.push(Event {
            name: "user_questions_required",
            data: json!({"batch_id":id, "questions":rows, "conversation_id":scope.conversation_id()}),
        });
        (self.insert(id, Kind::Question, scope, rows), events)
    }

    pub(crate) fn open_plan(&mut self, scope: TaskScope, plan: Plan) -> (Wait, [Event; 2]) {
        let id = new_id("plan_approval");
        let mut row = json!(plan);
        row["approval_id"] = json!(id);
        row["resolved_at"] = Value::Null;
        row["comment"] = json!("");
        add_scope(&mut row, &scope, timestamp());
        let events = [
            waiting_action(
                row["tool_call_id"].as_str().expect("serialized tool call ID"),
                "awaiting_plan_approval",
                "approval_id",
                &id,
                "正在等待用户批准计划。",
            ),
            Event {
                name: "plan_approval_required",
                data: json!({"approval":row, "conversation_id":scope.conversation_id()}),
            },
        ];
        (self.insert(id, Kind::Plan, scope, vec![row]), events)
    }

    fn insert(&mut self, id: String, kind: Kind, scope: TaskScope, rows: Vec<Value>) -> Wait {
        self.prune();
        for (index, row) in rows.iter().enumerate() {
            let row_id = row[kind.id_field()].as_str().expect("interaction ID");
            self.index.insert(row_id.to_owned(), (id.clone(), index));
        }
        let notify = Arc::new(Notify::new());
        self.groups.insert(
            id.clone(),
            Group {
                kind,
                scope,
                pending: rows.len(),
                rows,
                completed: None,
                notify: notify.clone(),
            },
        );
        Wait { id, notify }
    }

    pub(crate) fn pending(
        &mut self,
        principal: &ApprovalPrincipal,
        kind: Kind,
        conversation_id: Option<&str>,
    ) -> Vec<Value> {
        self.prune();
        let mut rows = self
            .groups
            .values()
            .filter(|group| {
                group.pending > 0
                    && group.kind == kind
                    && group.scope.principal() == principal
                    && conversation_id.is_none_or(|id| id == group.scope.conversation_id())
            })
            .flat_map(|group| &group.rows)
            .filter(|row| row["status"] == "pending")
            .cloned()
            .collect::<Vec<_>>();
        rows.sort_by(|a, b| {
            a["created_at"].as_f64().unwrap_or_default()
                .total_cmp(&b["created_at"].as_f64().unwrap_or_default())
                .then_with(|| a["batch_index"].as_u64().cmp(&b["batch_index"].as_u64()))
                .then_with(|| a[kind.id_field()].as_str().cmp(&b[kind.id_field()].as_str()))
        });
        rows
    }

    pub(crate) fn limit_answer(
        &mut self,
        principal: &ApprovalPrincipal,
        kind: Kind,
    ) -> Result<(), Error> {
        let now = Instant::now();
        self.attempts.retain(|_, attempts| {
            while attempts.front().is_some_and(|at| now.duration_since(*at) >= ANSWER_WINDOW) {
                attempts.pop_front();
            }
            !attempts.is_empty()
        });
        let attempts = self.attempts.entry((principal.username().to_owned(), kind)).or_default();
        if attempts.len() >= ANSWER_LIMIT {
            return Err(Error::RateLimited);
        }
        attempts.push_back(now);
        Ok(())
    }

    /// The authenticated principal binds both reads and mutations. The runtime
    /// uses the returned scope to check the active task before changing a pending row.
    pub(crate) fn target(
        &mut self,
        principal: &ApprovalPrincipal,
        kind: Kind,
        id: &str,
    ) -> Result<(&TaskScope, &Value), Error> {
        self.prune();
        let (group_id, index) = self.find(principal, kind, id)?;
        let group = &self.groups[group_id];
        Ok((&group.scope, &group.rows[index]))
    }

    fn find(
        &self,
        principal: &ApprovalPrincipal,
        kind: Kind,
        id: &str,
    ) -> Result<(&str, usize), Error> {
        let (group_id, index) = self.index.get(id).ok_or(Error::Missing)?;
        let group = &self.groups[group_id];
        if group.kind != kind {
            return Err(Error::Missing);
        }
        if group.scope.principal() != principal {
            return Err(Error::Forbidden);
        }
        Ok((group_id, *index))
    }

    pub(crate) fn answer_question(
        &mut self,
        principal: &ApprovalPrincipal,
        id: &str,
        answer: &QuestionAnswer,
    ) -> Result<(Value, Option<Event>), Error> {
        self.resolve(principal, Kind::Question, id, |row| {
            row["answer"] = answer.apply(row)?;
            row["answered_at"] = json!(timestamp());
            row["status"] = json!("answered");
            Ok(())
        })
    }

    pub(crate) fn answer_plan(
        &mut self,
        principal: &ApprovalPrincipal,
        id: &str,
        approved: bool,
        comment: &str,
    ) -> Result<(Value, Option<Event>), Error> {
        self.resolve(principal, Kind::Plan, id, |row| {
            row["comment"] = json!(clip(comment.trim(), 2000));
            row["resolved_at"] = json!(timestamp());
            row["status"] = json!(if approved { "approved" } else { "rejected" });
            Ok(())
        })
    }

    fn resolve(
        &mut self,
        principal: &ApprovalPrincipal,
        kind: Kind,
        id: &str,
        change: impl FnOnce(&mut Value) -> Result<(), Error>,
    ) -> Result<(Value, Option<Event>), Error> {
        let (group_id, index) = self.find(principal, kind, id)?;
        let group_id = group_id.to_owned();
        let group = self.groups.get_mut(&group_id).expect("indexed group");
        let row = &mut group.rows[index];
        if row["status"] != "pending" {
            return Ok((row.clone(), None));
        }
        change(row)?;
        let row = row.clone();
        group.pending -= 1;
        let event = (group.pending == 0).then(|| group.complete(&group_id, None));
        Ok((row, event))
    }

    pub(crate) fn resolved(&self, wait: &Wait) -> Result<Option<&[Value]>, Error> {
        let group = self.groups.get(&wait.id).ok_or(Error::Missing)?;
        Ok((group.pending == 0).then_some(group.rows.as_slice()))
    }

    pub(crate) fn expire_wait(&mut self, wait: &Wait, timeout: bool) -> Option<Event> {
        self.groups.get_mut(&wait.id)?.expire(&wait.id, timeout)
    }

    pub(crate) fn expire_task(&mut self, task_id: &str) -> Vec<Event> {
        self.prune();
        self.groups.iter_mut()
            .filter(|(_, group)| group.scope.task_id() == task_id)
            .filter_map(|(id, group)| group.expire(id, false))
            .collect()
    }

    fn prune(&mut self) {
        let before = self.groups.len();
        self.groups.retain(|_, group| group.completed.is_none_or(|at| at.elapsed() < RESOLVED_TTL));
        if self.groups.len() != before {
            self.index.retain(|_, (group, _)| self.groups.contains_key(group));
        }
    }
}

impl Group {
    fn expire(&mut self, id: &str, timeout: bool) -> Option<Event> {
        if self.pending == 0 {
            return None;
        }
        let time_field = match self.kind {
            Kind::Question => "answered_at",
            Kind::Plan => "resolved_at",
        };
        let now = timestamp();
        for row in self.rows.iter_mut().filter(|row| row["status"] == "pending") {
            row["status"] = json!("expired");
            row[time_field] = json!(now);
        }
        self.pending = 0;
        Some(self.complete(id, timeout.then_some("timeout")))
    }

    fn complete(&mut self, id: &str, decision: Option<&str>) -> Event {
        self.completed = Some(Instant::now());
        self.notify.notify_one();
        let conversation_id = self.scope.conversation_id();
        match self.kind {
            Kind::Question => {
                let ids = self.rows.iter().map(|row| &row["question_id"]).collect::<Vec<_>>();
                Event {
                    name: "user_questions_resolved",
                    data: json!({"batch_id":id, "question_ids":ids, "conversation_id":conversation_id}),
                }
            }
            Kind::Plan => {
                let row = &self.rows[0];
                Event {
                    name: "plan_approval_resolved",
                    data: json!({"approval_id":id, "decision":decision.unwrap_or_else(|| row["status"].as_str().expect("plan status")),
                        "comment":row["comment"], "conversation_id":conversation_id}),
                }
            }
        }
    }
}

impl Kind {
    fn id_field(self) -> &'static str {
        match self {
            Self::Question => "question_id",
            Self::Plan => "approval_id",
        }
    }
}

fn add_scope(row: &mut Value, scope: &TaskScope, created_at: f64) {
    row["username"] = json!(scope.principal().username());
    row["conversation_id"] = json!(scope.conversation_id());
    row["task_id"] = json!(scope.task_id());
    row["status"] = json!("pending");
    row["created_at"] = json!(created_at);
}

fn waiting_action(call: &str, status: &str, field: &str, id: &str, message: &str) -> Event {
    Event {
        name: "update_action",
        data: json!({"preparing_id":call, "status":status, "message":message,
            "result":{"success":false, "status":status, (field):id, "message":message}}),
    }
}

fn new_id(prefix: &str) -> String {
    format!("{prefix}_{}", Uuid::new_v4().simple())
}

fn timestamp() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()
}
