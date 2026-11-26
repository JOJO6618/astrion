use axum::{
    Extension, Json, Router,
    extract::{Path, Query, State, rejection::{JsonRejection, QueryRejection}},
    http::StatusCode,
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{Error, Kind, QuestionAnswer};
use crate::{approval::ApprovalPrincipal, runtime::Runtime};

/// Mount inside the gateway's authentication and trusted workspace resolver.
pub fn router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/user-questions/pending", get(pending).layer(Extension(Kind::Question)))
        .route("/api/user-questions/{id}/answer", post(answer_question))
        .route("/api/plan-approvals/pending", get(pending).layer(Extension(Kind::Plan)))
        .route("/api/plan-approvals/{id}/answer", post(answer_plan))
        .with_state(runtime)
}

#[derive(Deserialize)]
struct PendingQuery {
    conversation_id: Option<String>,
}

#[derive(Deserialize)]
struct PlanAnswer {
    approved: Option<bool>,
    comment: Option<String>,
}

type Reply = (StatusCode, Json<Value>);

async fn pending(
    State(runtime): State<Runtime>,
    Extension(kind): Extension<Kind>,
    principal: Option<Extension<ApprovalPrincipal>>,
    query: Result<Query<PendingQuery>, QueryRejection>,
) -> Reply {
    let Some(Extension(principal)) = principal else {
        return missing_principal();
    };
    let Ok(Query(query)) = query else {
        return failure(StatusCode::BAD_REQUEST, "Invalid interaction query");
    };
    let id = query.conversation_id.as_deref().map(str::trim).filter(|id| !id.is_empty());
    match runtime.pending_interactions(&principal, kind, id) {
        Ok(items) => (StatusCode::OK, Json(json!({"success":true, "items":items, "conversation_id":id}))),
        Err(error) => interaction_failure(kind, error),
    }
}

async fn answer_question(
    State(runtime): State<Runtime>,
    principal: Option<Extension<ApprovalPrincipal>>,
    Path(id): Path<String>,
    body: Result<Json<QuestionAnswer>, JsonRejection>,
) -> Reply {
    let Some(Extension(principal)) = principal else {
        return missing_principal();
    };
    let Json(body) = match body {
        Ok(body) => body,
        Err(error) => return invalid_body(error),
    };
    let result = runtime.answer_user_question(&principal, &id, &body);
    item_reply(Kind::Question, result)
}

async fn answer_plan(
    State(runtime): State<Runtime>,
    principal: Option<Extension<ApprovalPrincipal>>,
    Path(id): Path<String>,
    body: Result<Json<PlanAnswer>, JsonRejection>,
) -> Reply {
    let Some(Extension(principal)) = principal else {
        return missing_principal();
    };
    let Json(body) = match body {
        Ok(body) => body,
        Err(error) => return invalid_body(error),
    };
    let result = tokio::task::spawn_blocking(move || {
        runtime.answer_plan_approval(
            &principal, &id, body.approved.unwrap_or(false), body.comment.as_deref().unwrap_or_default(),
        )
    }).await.unwrap_or(Err(Error::Unavailable));
    item_reply(Kind::Plan, result)
}

fn item_reply(kind: Kind, result: Result<Value, Error>) -> Reply {
    match result {
        Ok(item) => (StatusCode::OK, Json(json!({"success":true, "item":item}))),
        Err(error) => interaction_failure(kind, error),
    }
}

fn interaction_failure(kind: Kind, error: Error) -> Reply {
    let (status, message) = match error {
        Error::Invalid(message) => (StatusCode::BAD_REQUEST, message),
        Error::Missing => (StatusCode::NOT_FOUND, match kind {
            Kind::Question => "User question not found",
            Kind::Plan => "Plan approval not found",
        }),
        Error::Forbidden => (StatusCode::FORBIDDEN, "Interaction is outside the authenticated scope"),
        Error::Unavailable => (StatusCode::SERVICE_UNAVAILABLE, "Interaction state is unavailable"),
        Error::RateLimited => (StatusCode::TOO_MANY_REQUESTS, "Too many answer attempts"),
    };
    failure(status, message)
}

fn invalid_body(error: JsonRejection) -> Reply {
    let status = match error.status() {
        StatusCode::UNPROCESSABLE_ENTITY => StatusCode::BAD_REQUEST,
        status => status,
    };
    failure(status, "Invalid interaction answer body")
}

fn missing_principal() -> Reply {
    failure(StatusCode::UNAUTHORIZED, "Authenticated interaction scope is required")
}

fn failure(status: StatusCode, message: &str) -> Reply {
    (status, Json(json!({"success":false, "error":message})))
}
