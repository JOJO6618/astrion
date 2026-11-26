//! Desktop Host tab navigation and read-only conversation activity.
mod commands;
mod model;
mod store;

use std::{
    cell::{OnceCell, RefCell},
    collections::{BTreeMap, BTreeSet, HashMap},
    fmt, fs,
    path::PathBuf,
    sync::Arc,
};

use axum::{
    Extension, Json, Router,
    extract::{State as AxumState, rejection::JsonRejection},
    http::StatusCode,
    routing::{get, post},
};
use serde_json::{Value, json};

use crate::{
    approval::{ApprovalBackend, ApprovalPrincipal},
    runtime::{NavigationRun, NavigationSession, Runtime},
    web_auth::BrowserSession,
};
use model::{ConversationKind, Kind, State, clipped, conversation_id};

pub use store::Navigation;

const HOST_USER: &str = "host";

/// An ID, display label and project path resolved from the trusted Host catalog.
#[derive(Clone)]
pub struct NavigationWorkspace {
    pub id: String,
    pub label: String,
    pub path: PathBuf,
}

#[derive(Clone, Debug)]
pub struct NavigationError {
    status: StatusCode,
    code: Option<&'static str>,
    message: String,
}

impl From<NavigationError> for crate::runtime::RuntimeError {
    fn from(error: NavigationError) -> Self {
        Self::new(error.status.as_u16(), error.message)
    }
}

impl NavigationError {
    fn invalid(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            code: None,
            message: message.into(),
        }
    }

    fn conflict(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::CONFLICT,
            code: Some("navigation_superseded"),
            message: message.into(),
        }
    }

    fn no_workspace() -> Self {
        Self {
            code: Some("no_workspace"),
            ..Self::invalid("A workspace is required")
        }
    }

    pub fn storage(error: impl fmt::Display) -> Self {
        Self {
            status: StatusCode::SERVICE_UNAVAILABLE,
            code: None,
            message: error.to_string(),
        }
    }

    fn unauthorized() -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            code: None,
            message: "Session expired. Please sign in again.".into(),
        }
    }

    fn reply(self) -> Reply {
        let mut body = json!({"success": false, "error": self.message});
        if let Some(code) = self.code {
            body["code"] = json!(code);
        }
        (self.status, Json(body))
    }
}

impl fmt::Display for NavigationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for NavigationError {}

#[derive(Clone, Copy)]
pub enum PendingNavigationKind {
    Approval,
    Question,
}

/// A still-pending human request, tied to the live task that created it.
pub struct PendingNavigationRequest {
    pub task_id: String,
    pub workspace_id: String,
    pub conversation_id: String,
    pub request_id: String,
    pub kind: PendingNavigationKind,
}

type Workspaces = dyn Fn() -> Result<Vec<NavigationWorkspace>, NavigationError> + Send + Sync;
type PendingRequests = dyn Fn() -> Vec<PendingNavigationRequest> + Send + Sync;
type Scope = (String, String);
type Pending = BTreeMap<&'static str, BTreeSet<String>>;
type Reply = (StatusCode, Json<Value>);

#[derive(Clone)]
struct App {
    navigation: Navigation,
    runtime: Runtime,
    workspaces: Arc<Workspaces>,
    pending: Arc<PendingRequests>,
}

impl Navigation {
    /// Return the same tab projection as the command endpoint after a confirmed deletion.
    pub fn deleted_snapshot(
        &self,
        runtime: Runtime,
        user: &str,
        workspace_id: &str,
        id: &str,
        workspaces: impl Fn() -> Result<Vec<NavigationWorkspace>, NavigationError> + Send + Sync + 'static,
    ) -> Result<Value, NavigationError> {
        let (state, _) = self.command(
            user,
            &uuid::Uuid::new_v4().simple().to_string(),
            "deleted",
            &json!({"workspace_id": workspace_id, "conversation_id": id}),
            None,
        )?;
        let app = App {
            navigation: self.clone(),
            runtime,
            workspaces: Arc::new(workspaces),
            pending: Arc::new(Vec::new),
        };
        Ok(public_state(state, &Lookup::new(&app)))
    }

    /// Merge into the authenticated Host router with `WebAuth::protect_api`.
    /// The first entry from the workspace getter is the default workspace.
    pub fn host_router(
        &self,
        runtime: Runtime,
        workspaces: impl Fn() -> Result<Vec<NavigationWorkspace>, NavigationError>
        + Send
        + Sync
        + 'static,
    ) -> Router {
        self.host_router_with_pending(runtime, workspaces, Vec::new)
    }

    pub fn host_router_with_pending(
        &self,
        runtime: Runtime,
        workspaces: impl Fn() -> Result<Vec<NavigationWorkspace>, NavigationError>
        + Send
        + Sync
        + 'static,
        pending: impl Fn() -> Vec<PendingNavigationRequest> + Send + Sync + 'static,
    ) -> Router {
        let app = App {
            navigation: self.clone(),
            runtime,
            workspaces: Arc::new(workspaces),
            pending: Arc::new(pending),
        };
        Router::new()
            .route("/api/conversation-tabs", get(snapshot))
            .route("/api/conversation-tabs/activity", get(activity))
            .route("/api/conversation-tabs/commands", post(command))
            .with_state(app)
    }
}

async fn snapshot(
    AxumState(app): AxumState<App>,
    session: Option<Extension<BrowserSession>>,
) -> Reply {
    if session.is_none() {
        return NavigationError::unauthorized().reply();
    }
    respond(move || {
        let lookup = Lookup::new(&app);
        let mut state = public_state(app.navigation.snapshot(HOST_USER, &lookup)?, &lookup);
        state["success"] = json!(true);
        Ok(state)
    })
    .await
}

async fn activity(
    AxumState(app): AxumState<App>,
    session: Option<Extension<BrowserSession>>,
) -> Reply {
    if session.is_none() {
        return NavigationError::unauthorized().reply();
    }
    respond(move || {
        let lookup = Lookup::new(&app);
        Ok(json!({"success": true, "data": lookup.activity()?}))
    })
    .await
}

async fn command(
    AxumState(app): AxumState<App>,
    session: Option<Extension<BrowserSession>>,
    body: Result<Json<Value>, JsonRejection>,
) -> Reply {
    if session.is_none() {
        return NavigationError::unauthorized().reply();
    }
    respond(move || {
        let Json(body) =
            body.map_err(|_| NavigationError::invalid("Invalid navigation command"))?;
        if !body.is_object() || matches!(body["action"].as_str(), Some("deleted" | "relocated")) {
            return Err(NavigationError::invalid("Invalid navigation command"));
        }
        let action = text(&body["action"]);
        let id = body["command_id"].as_str().unwrap_or_default();
        let empty = json!({});
        let payload = if truthy(&body["payload"]) {
            &body["payload"]
        } else {
            &empty
        };
        let lookup = Lookup::new(&app);
        let (state, result) =
            app.navigation
                .command(HOST_USER, id, &action, payload, Some(&lookup))?;
        Ok(json!({"success": true, "state": public_state(state, &lookup), "result": result}))
    })
    .await
}

async fn respond(
    operation: impl FnOnce() -> Result<Value, NavigationError> + Send + 'static,
) -> Reply {
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(body)) => (StatusCode::OK, Json(body)),
        Ok(Err(error)) => error.reply(),
        Err(_) => NavigationError::storage("Desktop navigation operation interrupted").reply(),
    }
}

struct Lookup<'a> {
    app: &'a App,
    workspaces: OnceCell<Result<Vec<NavigationWorkspace>, NavigationError>>,
    sessions: RefCell<HashMap<Scope, Result<Option<NavigationSession>, NavigationError>>>,
    runs: OnceCell<Result<Vec<NavigationRun>, NavigationError>>,
    pending: OnceCell<BTreeMap<Scope, Pending>>,
}

impl<'a> Lookup<'a> {
    fn new(app: &'a App) -> Self {
        Self {
            app,
            workspaces: OnceCell::new(),
            sessions: RefCell::default(),
            runs: OnceCell::new(),
            pending: OnceCell::new(),
        }
    }

    fn workspace(&self, id: &str) -> Result<&NavigationWorkspace, NavigationError> {
        let workspaces = self
            .workspaces
            .get_or_init(|| (self.app.workspaces)())
            .as_ref()
            .map_err(Clone::clone)?;
        if workspaces.is_empty() {
            return Err(NavigationError::no_workspace());
        }
        let workspace = if id.is_empty() {
            workspaces.first()
        } else {
            workspaces.iter().find(|workspace| workspace.id == id)
        }
        .ok_or_else(|| NavigationError::invalid("Workspace unavailable"))?;
        if !fs::metadata(&workspace.path).is_ok_and(|metadata| metadata.is_dir()) {
            return Err(NavigationError::invalid("Workspace unavailable"));
        }
        Ok(workspace)
    }

    fn session(
        &self,
        kind: ConversationKind,
        workspace: &str,
        id: &str,
    ) -> Result<Option<NavigationSession>, NavigationError> {
        let id = conversation_id(id)?;
        match kind {
            ConversationKind::Agent if workspace.is_empty() => {
                return Err(NavigationError::no_workspace());
            }
            ConversationKind::Agent => {
                self.workspace(workspace)?;
            }
            ConversationKind::Chat if !workspace.is_empty() => {
                return Err(NavigationError::invalid("Chat has no workspace"));
            }
            ConversationKind::Chat => {}
        }
        self.sessions
            .borrow_mut()
            .entry((workspace.into(), id.clone()))
            .or_insert_with(|| {
                self.app
                    .runtime
                    .navigation_session(workspace, &id)
                    .map_err(NavigationError::storage)
            })
            .clone()
    }

    fn runs(&self) -> Result<&[NavigationRun], NavigationError> {
        self.runs
            .get_or_init(|| {
                self.app
                    .runtime
                    .navigation_runs()
                    .map_err(NavigationError::storage)
            })
            .as_deref()
            .map_err(Clone::clone)
    }

    fn active_conversation(&self, workspace: &str) -> Result<String, NavigationError> {
        Ok(self
            .runs()?
            .iter()
            .filter(|run| run.running && run.workspace_id == workspace)
            .max_by(|left, right| {
                left.created_at
                    .total_cmp(&right.created_at)
                    .then_with(|| left.task_id.cmp(&right.task_id))
            })
            .map(|run| run.conversation_id.clone())
            .unwrap_or_default())
    }

    fn pending(&self) -> &BTreeMap<Scope, Pending> {
        self.pending.get_or_init(|| {
            let active: HashMap<_, _> = self
                .runs()
                .unwrap_or_default()
                .iter()
                .filter(|run| run.running)
                .map(|run| (run.task_id.as_str(), run))
                .collect();
            let mut pending = BTreeMap::new();
            if active.is_empty() {
                return pending;
            }
            let mut requests = (self.app.pending)();
            let workspaces: BTreeSet<_> = active
                .values()
                .map(|run| run.workspace_id.as_str())
                .filter(|id| !id.is_empty() && self.workspace(id).is_ok())
                .collect();
            for workspace in workspaces {
                let Ok(principal) = ApprovalPrincipal::new(HOST_USER, workspace) else {
                    continue;
                };
                let Ok(approvals) = self.app.runtime.list_pending(&principal, None) else {
                    continue;
                };
                requests.extend(
                    approvals
                        .into_iter()
                        .filter(|approval| {
                            approval.requirements.human_approval_required()
                                && approval.human_decision.is_none()
                        })
                        .map(|approval| PendingNavigationRequest {
                            task_id: approval.scope().task_id().into(),
                            workspace_id: workspace.into(),
                            conversation_id: approval.scope().conversation_id().into(),
                            request_id: approval.approval_id,
                            kind: PendingNavigationKind::Approval,
                        }),
                );
            }
            for request in requests {
                let Some(run) = active.get(request.task_id.as_str()) else {
                    continue;
                };
                let Ok(id) = conversation_id(&request.conversation_id) else {
                    continue;
                };
                if id.is_empty()
                    || request.request_id.is_empty()
                    || run.workspace_id != request.workspace_id
                    || !commands::same_conversation(&run.conversation_id, &id)
                {
                    continue;
                }
                let key = match request.kind {
                    PendingNavigationKind::Approval => "approval",
                    PendingNavigationKind::Question => "question",
                };
                pending
                    .entry((request.workspace_id, id))
                    .or_insert_with(Pending::new)
                    .entry(key)
                    .or_default()
                    .insert(request.request_id);
            }
            pending
        })
    }

    fn activity(&self) -> Result<Vec<Value>, NavigationError> {
        let mut groups: BTreeMap<Scope, (&NavigationRun, bool)> = BTreeMap::new();
        for run in self.runs()? {
            let Ok(id) = conversation_id(&run.conversation_id) else {
                continue;
            };
            if id.is_empty() {
                continue;
            }
            let (latest, running) = groups
                .entry((run.workspace_id.clone(), id))
                .or_insert((run, false));
            *running |= run.running;
            if run
                .created_at
                .total_cmp(&latest.created_at)
                .then_with(|| run.task_id.cmp(&latest.task_id))
                .is_gt()
            {
                *latest = run;
            }
        }
        let mut rows = Vec::new();
        for (scope, (latest, running)) in groups {
            let (workspace_id, id) = &scope;
            let (kind, label) = if workspace_id.is_empty() {
                (ConversationKind::Chat, "")
            } else if let Ok(workspace) = self.workspace(workspace_id) {
                (
                    ConversationKind::Agent,
                    nonempty(&workspace.label, &workspace.id),
                )
            } else {
                continue;
            };
            let title = self
                .session(kind, workspace_id, id)
                .ok()
                .flatten()
                .map(|session| clipped(&session.title, 200))
                .unwrap_or_default();
            let pending = self.pending().get(&scope).cloned().unwrap_or_default();
            rows.push(json!({"conversation_kind": kind, "workspace_id": workspace_id,
                "conversation_id": id, "workspace_label": label, "title": title, "run_id": latest.task_id,
                "run_status": latest.status, "running": running, "pending": pending}));
        }
        Ok(rows)
    }
}

fn public_state(state: State, lookup: &Lookup) -> Value {
    let State {
        version,
        revision,
        tabs,
        active_key,
        workspace_id,
        renderers,
        mut extra,
        ..
    } = state;
    let tabs: Vec<_> = tabs
        .into_iter()
        .map(|mut tab| {
            let mut running = false;
            let mut approval = false;
            let mut question = false;
            if tab.kind == Kind::Conv
                && let Some(kind) = tab.conversation_kind
            {
                if let Ok(Some(session)) =
                    lookup.session(kind, &tab.workspace_id, &tab.conversation_id)
                    && !session.title.is_empty()
                {
                    tab.title = clipped(&session.title, 200);
                }
                if let Ok(id) = conversation_id(&tab.conversation_id)
                    && !id.is_empty()
                {
                    running = lookup.runs().unwrap_or_default().iter().any(|run| {
                        run.running
                            && run.workspace_id == tab.workspace_id
                            && commands::same_conversation(&run.conversation_id, &id)
                    });
                    if let Some(pending) = lookup.pending().get(&(tab.workspace_id.clone(), id)) {
                        approval = pending.contains_key("approval");
                        question = pending.contains_key("question");
                    }
                }
            }
            let mut tab = json!(tab);
            tab["running"] = json!(running);
            tab["awaiting_human_approval"] = json!(approval);
            tab["awaiting_user_answer"] = json!(question);
            tab
        })
        .collect();
    extra.extend([
        ("version".into(), json!(version)),
        ("revision".into(), json!(revision)),
        ("tabs".into(), json!(tabs)),
        ("active_key".into(), json!(active_key)),
        ("workspace_id".into(), json!(workspace_id)),
        ("renderers".into(), json!(renderers)),
    ]);
    Value::Object(extra)
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn text(value: &Value) -> String {
    if !truthy(value) {
        return String::new();
    }
    match value {
        Value::String(value) => value.clone(),
        Value::Bool(true) => "True".into(),
        _ => value.to_string(),
    }
}

fn nonempty<'a>(primary: &'a str, fallback: &'a str) -> &'a str {
    if primary.is_empty() {
        fallback
    } else {
        primary
    }
}

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/navigation_contract.rs"]
mod tests;
