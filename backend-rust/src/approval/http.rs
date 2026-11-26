use std::sync::Arc;

use axum::{
    Extension, Json, Router,
    extract::{
        Path, Query,
        rejection::{JsonRejection, QueryRejection},
    },
    http::StatusCode,
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{Approval, ApprovalError, ApprovalPrincipal, Decision};

/// Implement with the same lock that protects the admitted task state.
pub trait ApprovalBackend: Send + Sync {
    fn list_pending(
        &self,
        principal: &ApprovalPrincipal,
        conversation_id: Option<&str>,
    ) -> Result<Vec<Approval>, ApprovalError>;

    fn decide(
        &self,
        principal: &ApprovalPrincipal,
        approval_id: &str,
        decision: Decision,
        remember_site: bool,
    ) -> Result<Approval, ApprovalError>;
}

/// Installed by the gateway after it authenticates and resolves the workspace.
#[derive(Clone)]
pub struct ApprovalAccess {
    principal: ApprovalPrincipal,
    backend: Arc<dyn ApprovalBackend>,
}

impl ApprovalAccess {
    pub fn new(principal: ApprovalPrincipal, backend: Arc<dyn ApprovalBackend>) -> Self {
        Self { principal, backend }
    }
}

pub fn router<S: Clone + Send + Sync + 'static>() -> Router<S> {
    Router::new()
        .route("/api/tool-approvals/pending", get(list_pending))
        .route("/api/tool-approvals/{approval_id}/decision", post(decide))
}

#[derive(Deserialize)]
struct PendingQuery {
    conversation_id: Option<String>,
}

#[derive(Deserialize)]
struct DecisionBody {
    decision: String,
    #[serde(default)]
    remember_site: Value,
}

type Reply = (StatusCode, Json<Value>);

async fn list_pending(
    access: Option<Extension<ApprovalAccess>>,
    query: Result<Query<PendingQuery>, QueryRejection>,
) -> Reply {
    let Some(Extension(access)) = access else {
        return missing_access();
    };
    let Ok(Query(query)) = query else {
        return failure(StatusCode::BAD_REQUEST, "Invalid approval query");
    };
    let conversation_id = query
        .conversation_id
        .as_deref()
        .map(str::trim)
        .filter(|id| !id.is_empty());
    match access
        .backend
        .list_pending(&access.principal, conversation_id)
    {
        Ok(items) => (
            StatusCode::OK,
            Json(json!({"success":true, "items":items, "conversation_id":conversation_id})),
        ),
        Err(error) => approval_failure(error),
    }
}

async fn decide(
    access: Option<Extension<ApprovalAccess>>,
    Path(id): Path<String>,
    body: Result<Json<DecisionBody>, JsonRejection>,
) -> Reply {
    let Some(Extension(access)) = access else {
        return missing_access();
    };
    let body = match body {
        Ok(Json(body)) => body,
        Err(error) => {
            let status = match error.status() {
                StatusCode::UNPROCESSABLE_ENTITY => StatusCode::BAD_REQUEST,
                status => status,
            };
            return failure(status, "Invalid approval decision body");
        }
    };
    let result = Decision::parse(&body.decision).and_then(|decision| {
        access.backend.decide(
            &access.principal,
            &id,
            decision,
            body.remember_site == Value::Bool(true),
        )
    });
    match result {
        Ok(item) => (StatusCode::OK, Json(json!({"success":true, "item":item}))),
        Err(error) => approval_failure(error),
    }
}

fn approval_failure(error: ApprovalError) -> Reply {
    let status = match error {
        ApprovalError::Invalid(_) => StatusCode::BAD_REQUEST,
        ApprovalError::NotFound => StatusCode::NOT_FOUND,
        ApprovalError::Forbidden | ApprovalError::ScopeMismatch => StatusCode::FORBIDDEN,
        ApprovalError::NotApproved | ApprovalError::ReviewChanged => StatusCode::CONFLICT,
        ApprovalError::Unavailable => StatusCode::SERVICE_UNAVAILABLE,
    };
    failure(status, &error.to_string())
}

fn missing_access() -> Reply {
    failure(
        StatusCode::UNAUTHORIZED,
        "Authenticated approval scope is required",
    )
}

fn failure(status: StatusCode, message: &str) -> Reply {
    (status, Json(json!({"success":false, "error":message})))
}
