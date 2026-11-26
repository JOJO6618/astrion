use axum::{
    Extension, Json, Router,
    body::to_bytes,
    extract::{Path, RawQuery, Request, State},
    http::{StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde_json::{Value, json};

use super::{HostPrincipal, Result, ScheduleError, ScheduleService, schema, yaml};

const MAX_BODY_BYTES: usize = 300_000;

/// Merge under Host authentication and set `HostPrincipal` from that trusted boundary.
/// The HTML `/schedules` entry is served by the host's existing authenticated SPA router.
pub fn router(service: ScheduleService) -> Router {
    Router::new()
        .route(
            "/api/schedules",
            get(list).post(submit).options(get_post_options),
        )
        .route(
            "/api/schedules/validate",
            post(validate).options(post_options),
        )
        .route("/api/schedules/options", get(options).options(get_options))
        .route("/api/schedules/{id}", get(detail).options(get_options))
        .route(
            "/api/schedules/{id}/yaml",
            get(export_yaml).options(get_options),
        )
        .route("/api/schedules/{id}/runs", get(runs).options(get_options))
        .route(
            "/api/schedules/{id}/actions",
            post(action).options(post_options),
        )
        .route(
            "/api/schedules/notifications/claim",
            post(claim).options(post_options),
        )
        .route(
            "/api/schedules/notifications/{run_id}/ack",
            post(acknowledge).options(post_options),
        )
        .route_layer(middleware::from_fn(require_host))
        .with_state(service)
}

async fn require_host(request: Request, next: Next) -> Response {
    if request.extensions().get::<HostPrincipal>().is_none() {
        return ScheduleError::new(401, "请先登录").into_response();
    }
    next.run(request).await
}

impl IntoResponse for ScheduleError {
    fn into_response(self) -> Response {
        if self.storage_detail.is_some() {
            eprintln!("schedule API failed: {self}");
        }
        let status = StatusCode::from_u16(self.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        (status, Json(json!({"success":false,"error":self.message}))).into_response()
    }
}

type Reply = Result<Json<Value>>;

async fn respond(call: impl FnOnce() -> Result<Value> + Send + 'static) -> Reply {
    let data = tokio::task::spawn_blocking(call)
        .await
        .map_err(ScheduleError::storage)??;
    Ok(Json(json!({"success":true,"data":data})))
}

async fn payload(request: Request) -> Result<Value> {
    let is_json = request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(';').next())
        .map(str::trim)
        .is_some_and(|mime| {
            let mime = mime.to_ascii_lowercase();
            mime == "application/json"
                || (mime.starts_with("application/") && mime.ends_with("+json"))
        });
    if !is_json {
        return Err(ScheduleError::invalid("请求必须是 JSON 对象"));
    }
    let bytes = to_bytes(request.into_body(), MAX_BODY_BYTES)
        .await
        .map_err(|_| ScheduleError::invalid("计划配置过大"))?;
    let value = yaml::parse_json(&bytes)?;
    if !value.is_object() {
        return Err(ScheduleError::invalid("请求必须是 JSON 对象"));
    }
    Ok(value)
}

async fn list(State(service): State<ScheduleService>, RawQuery(query): RawQuery) -> Reply {
    let query =
        serde_urlencoded::from_str::<Vec<(String, String)>>(query.as_deref().unwrap_or_default())
            .map_err(|_| ScheduleError::invalid("无效查询参数"))?;
    respond(move || {
        let find = |key| {
            query
                .iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value.as_str())
        };
        service.list(
            find("workspace_id"),
            find("enabled").map(|value| value == "true"),
        )
    })
    .await
}

async fn submit(
    State(service): State<ScheduleService>,
    Extension(host): Extension<HostPrincipal>,
    request: Request,
) -> Reply {
    let body = payload(request).await?;
    let fields = schema::object(
        &body,
        &["definition", "yaml", "overwrite", "workspace_id"],
        "请求",
    )?;
    let overwrite = fields
        .get("overwrite")
        .map(|value| schema::boolean(value, "overwrite"))
        .transpose()?
        .unwrap_or(false);
    respond(move || service.submit(&host, definition(&body), &body["workspace_id"], overwrite))
        .await
}

async fn validate(
    State(service): State<ScheduleService>,
    Extension(host): Extension<HostPrincipal>,
    request: Request,
) -> Reply {
    let body = payload(request).await?;
    respond(move || service.validate(&host, definition(&body), &body["workspace_id"])).await
}

fn definition(body: &Value) -> &Value {
    body.get("yaml").unwrap_or(&body["definition"])
}

async fn options(
    State(service): State<ScheduleService>,
    Extension(host): Extension<HostPrincipal>,
) -> Reply {
    respond(move || service.options(&host)).await
}

async fn detail(State(service): State<ScheduleService>, Path(id): Path<String>) -> Reply {
    respond(move || service.detail(&id)).await
}

async fn export_yaml(State(service): State<ScheduleService>, Path(id): Path<String>) -> Reply {
    respond(move || service.export_yaml(&id)).await
}

async fn runs(State(service): State<ScheduleService>, Path(id): Path<String>) -> Reply {
    respond(move || service.runs(&id)).await
}

async fn action(
    State(service): State<ScheduleService>,
    Path(id): Path<String>,
    request: Request,
) -> Reply {
    let body = payload(request).await?;
    schema::object(
        &body,
        &["action", "revision", "run_id", "request_id"],
        "操作",
    )?;
    respond(move || {
        let action = body["action"].as_str().unwrap_or_default();
        let revision = if body["revision"].is_null() {
            None
        } else {
            Some(schema::integer(
                &body["revision"],
                1,
                9_007_199_254_740_991,
                "revision",
            )?)
        };
        let run = optional_string(&body["run_id"], "run_id")?;
        let request = optional_string(&body["request_id"], "request_id")?;
        service.manage(&id, action, revision, run, request)
    })
    .await
}

async fn claim(State(service): State<ScheduleService>) -> Reply {
    respond(move || service.claim_notifications()).await
}

async fn acknowledge(
    State(service): State<ScheduleService>,
    Path(run_id): Path<String>,
    request: Request,
) -> Reply {
    let body = payload(request).await?;
    respond(move || {
        let token = body["token"]
            .as_str()
            .filter(|value| !value.is_empty())
            .ok_or_else(|| ScheduleError::invalid("缺少通知凭据"))?;
        service.acknowledge(&run_id, token, optional_string(&body["error"], "error")?)
    })
    .await
}

fn optional_string<'a>(value: &'a Value, label: &str) -> Result<Option<&'a str>> {
    if value.is_null() {
        Ok(None)
    } else {
        value
            .as_str()
            .map(Some)
            .ok_or_else(|| ScheduleError::invalid(format!("{label} 必须是文字")))
    }
}

async fn get_options() -> impl IntoResponse {
    [(header::ALLOW, "GET, HEAD, OPTIONS")]
}

async fn post_options() -> impl IntoResponse {
    [(header::ALLOW, "POST, OPTIONS")]
}

async fn get_post_options() -> impl IntoResponse {
    [(header::ALLOW, "GET, HEAD, POST, OPTIONS")]
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/schedules_routes.rs"]
mod tests;
