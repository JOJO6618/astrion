use super::*;
use axum::{
    body::Body,
    http::{Method, Request},
};
use std::{fs, path::PathBuf, sync::Arc};
use tower::ServiceExt;
use uuid::Uuid;

use crate::schedules::{Admission, Observation, ScheduleAdapter, ScheduleInvocation, StartError};

struct ValidationOnly;

impl ScheduleAdapter for ValidationOnly {
    fn validate(&self, definition: &Value, _: &Value) -> Result<Option<Value>> {
        if definition["target"]["workspace_id"] != "ws" {
            return Err(ScheduleError::forbidden("outside workspace scope"));
        }
        Ok(Some(json!({"model_key":"fixture"})))
    }

    fn start(&self, _: &ScheduleInvocation) -> std::result::Result<Admission, StartError> {
        unreachable!()
    }
    fn observe(&self, _: &ScheduleInvocation, _: &Admission) -> Result<Observation> {
        unreachable!()
    }
    fn cancel(&self, _: &ScheduleInvocation, _: &Admission) -> Result<()> {
        unreachable!()
    }
    fn scheduler_error(&self) -> Option<&str> {
        Some("fixture has no Runtime callback")
    }
}

struct Data(PathBuf);
impl Drop for Data {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

async fn api(app: &Router, method: Method, path: &str, body: &str) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(path)
                .header("content-type", "application/json")
                .body(Body::from(body.to_owned()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 1_000_000).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap_or_default())
}

#[tokio::test]
async fn auth_crud_validate_yaml_and_scheduler_unavailable() {
    let data = Data(std::env::temp_dir().join(format!("schedules-routes-{}", Uuid::new_v4())));
    let service = ScheduleService::open(&data.0, Arc::new(ValidationOnly)).unwrap();
    let app = router(service);
    let (status, denied) = api(&app, Method::GET, "/api/schedules", "").await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(denied["success"], false);
    let app = app.layer(Extension(HostPrincipal::new(Some("ws".into()))));
    let yaml = "schema_version: 1\nname: 巡检\nmessage: '*字面星号'\nschedule:\n  type: once\n  timezone: UTC\n  date: '2095-04-02'\n  times: ['10:00']\ngoal:\n  mode: off\n";
    let request = json!({"yaml":yaml,"workspace_id":"ws"});
    let (status, validated) = api(
        &app,
        Method::POST,
        "/api/schedules/validate",
        &request.to_string(),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{validated}");
    assert_eq!(validated["data"]["definition"]["goal"]["mode"], "off");
    assert_eq!(
        validated["data"]["next_dates"][0],
        "2095-04-02T10:00:00.000000Z"
    );
    let (status, created) = api(&app, Method::POST, "/api/schedules", &request.to_string()).await;
    assert_eq!(status, StatusCode::OK, "{created}");
    let id = created["data"]["definition"]["id"].as_str().unwrap();
    let route = format!("/api/schedules/{id}");
    let (_, export) = api(&app, Method::GET, &format!("{route}/yaml"), "").await;
    let exported = yaml::parse(export["data"]["yaml"].as_str().unwrap()).unwrap();
    assert_eq!(exported, created["data"]["definition"]);
    let (_, listing) = api(
        &app,
        Method::GET,
        "/api/schedules?workspace_id=ws&enabled=false",
        "",
    )
    .await;
    assert_eq!(listing["data"]["plans"].as_array().unwrap().len(), 1);
    assert_eq!(
        listing["data"]["scheduler_error"],
        "fixture has no Runtime callback"
    );
    let (status, _) = api(
        &app,
        Method::POST,
        &format!("{route}/actions"),
        r#"{"action":"run"}"#,
    )
    .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    let (_, run) = api(&app, Method::GET, &format!("{route}/runs"), "").await;
    assert_eq!(run["data"], json!([]));
    let (status, _) = api(
        &app,
        Method::POST,
        &format!("{route}/actions"),
        r#"{"action":"delete"}"#,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        api(&app, Method::GET, &route, "").await.0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        api(&app, Method::GET, &format!("{route}/runs"), "").await.0,
        StatusCode::OK
    );
    assert_eq!(
        api(&app, Method::OPTIONS, "/api/schedules", "").await.0,
        StatusCode::OK
    );
}

#[tokio::test]
async fn rejects_duplicate_keys_aliases_nonfinite_values_and_untrusted_scope() {
    let data = Data(std::env::temp_dir().join(format!("schedules-routes-{}", Uuid::new_v4())));
    let app = router(ScheduleService::open(&data.0, Arc::new(ValidationOnly)).unwrap())
        .layer(Extension(HostPrincipal::new(Some("ws".into()))));
    for input in [
        r#"{"workspace_id":"ws","workspace_id":"other"}"#,
        r#"{"definition":{"schedule":{"type":"once","type":"daily"}}}"#,
        r#"{"definition":{"message":1e500}}"#,
        r#"{"definition":{"target":{"workspace_id":"ws"}},"overwrite":"false"}"#,
    ] {
        assert_eq!(
            api(&app, Method::POST, "/api/schedules", input).await.0,
            StatusCode::BAD_REQUEST
        );
    }
    for input in [
        "name: &test demo\nmessage: *test\n",
        "name: a\nname: b\n",
        "name: .inf\n",
        "123: value\n",
    ] {
        assert!(yaml::parse(input).is_err(), "{input}");
    }
    // Python's public YAML rejects this field; Rust must not emit a plan Python
    // cannot take over through the shared schedule store.
    let unsupported_fast = json!({
        "schema_version":1,"name":"capture","message":"ignore","target":{"workspace_id":"ws"},
        "schedule":{"type":"daily","times":["09:00"]},
        "configuration":{"mode":"custom","chatgpt_fast_mode":true},
    });
    assert_eq!(
        api(
            &app,
            Method::POST,
            "/api/schedules",
            &json!({"definition":unsupported_fast,"workspace_id":"ws"}).to_string(),
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );

    let mut definition = json!({"schema_version":1,"name":"capture","message":"ignore","target":{"workspace_id":"other"},
        "schedule":{"type":"daily","times":["09:00"]}});
    let request =
        |definition: &Value| json!({"definition":definition,"workspace_id":"ws"}).to_string();
    assert_eq!(
        api(&app, Method::POST, "/api/schedules", &request(&definition))
            .await
            .0,
        StatusCode::FORBIDDEN
    );
    definition["target"]["workspace_id"] = json!("ws");
    let request = request(&definition).replace("capture", r"\uD83D\uDE80");
    assert_eq!(
        api(&app, Method::POST, "/api/schedules", &request).await.0,
        StatusCode::OK
    );
}
