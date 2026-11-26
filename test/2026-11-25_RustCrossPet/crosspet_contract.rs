use super::*;
use axum::{
    body::{Body, to_bytes},
    extract::ConnectInfo,
    http::{Method, Request, header::SET_COOKIE},
};
use std::{
    fs,
    net::SocketAddr,
    path::PathBuf,
    sync::{
        Mutex,
        atomic::{AtomicUsize, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};
use tower::ServiceExt;

use crate::web_auth::{WebAuth, WebAuthConfig};

const FILES: [&str; 3] = ["astrion-state.json", "astrion-host.json", "seen-astrion"];

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let root =
            std::env::temp_dir().join(format!("astrion-crosspet-{}", Uuid::new_v4().simple()));
        fs::create_dir(&root).unwrap();
        Self(root)
    }

    fn config(&self) -> CrossPetConfig {
        CrossPetConfig::with_paths(
            self.0.join("deployment/crosspet.json"),
            self.0.join("astrion.json"),
            self.0.join("state"),
        )
    }

    fn snapshot(&self) -> Option<Value> {
        fs::read(self.0.join("state/astrion-state.json"))
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
    }

    fn files(&self) -> Vec<Vec<u8>> {
        FILES
            .into_iter()
            .map(|name| fs::read(self.0.join("state").join(name)).unwrap())
            .collect()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

type ProbeLatch = (mpsc::Sender<()>, mpsc::Receiver<()>);

#[derive(Default)]
struct TestRuntime {
    model_calls: AtomicUsize,
    foreground_calls: AtomicUsize,
    foreground: Mutex<Option<Foreground>>,
    activity: Mutex<BTreeMap<TaskRef, TaskObservation>>,
    latch: Mutex<Option<ProbeLatch>>,
    quota_calls: Mutex<Vec<bool>>,
}

impl CrossPetRuntime for TestRuntime {
    fn model_id(&self, _key: &str) -> Option<String> {
        self.model_calls.fetch_add(1, Ordering::Relaxed);
        Some("gpt-offline".into())
    }

    fn foreground(&self) -> io::Result<Option<Foreground>> {
        self.foreground_calls.fetch_add(1, Ordering::Relaxed);
        let latch = self.latch.lock().unwrap().take();
        if let Some((entered, released)) = latch {
            entered.send(()).unwrap();
            released.recv_timeout(Duration::from_secs(4)).unwrap();
        }
        Ok(self.foreground.lock().unwrap().clone())
    }

    fn observe(&self, _tasks: &[TaskRef]) -> BTreeMap<TaskRef, TaskObservation> {
        self.activity.lock().unwrap().clone()
    }

    fn quota(&self, enabled: bool, _busy: bool) -> Option<GptQuota> {
        self.quota_calls.lock().unwrap().push(enabled);
        enabled.then(|| GptQuota {
            connected: true,
            status: QuotaStatus::Live,
            epoch: None,
            at: Some(90.0),
            rate_limits: Some(QuotaLimits {
                primary: Some(QuotaWindow {
                    used_percent: 31.0,
                    window_duration_mins: 300.0,
                    resets_at: 3600.0,
                }),
                secondary: Some(QuotaWindow {
                    used_percent: 120.0,
                    window_duration_mins: 1.0,
                    resets_at: 3600.0,
                }),
            }),
        })
    }
}

fn task(workspace: &str, id: &str, task_id: &str) -> TaskRef {
    TaskRef {
        session: SessionIdentity {
            workspace_id: workspace.into(),
            conversation_id: id.into(),
        },
        task_id: task_id.into(),
    }
}

fn action(task: &TaskRef, action: Action) -> TaskEvent {
    TaskEvent {
        task: task.clone(),
        model_key: "private-catalog-connection".into(),
        action,
    }
}

fn row<'a>(snapshot: &'a Value, task: &TaskRef) -> &'a Value {
    &snapshot["sessions"][projection::session_key(&task.session)]
}

#[test]
fn projection_preserves_approval_waits_task_identity_and_actual_completion() {
    let active = task("workspace", "conv_same", "internal-task-id");
    let elsewhere = task("other", "conv_same", "elsewhere-task");
    let runtime = TestRuntime::default();
    let mut projection = projection::Projection::default();
    let instance = Uuid::new_v4();
    assert_ne!(
        projection::session_key(&active.session),
        projection::session_key(&elsewhere.session)
    );
    let identity = SessionIdentity {
        workspace_id: "workspace.一".into(),
        conversation_id: "conv_🙂".into(),
    };
    assert_eq!(
        projection::session_key(&identity),
        "a4afb5057f15ec85e98941dc42df76fa"
    );

    projection.apply(action(&active, Action::Started), 100.0);
    projection.apply(action(&elsewhere, Action::Started), 100.0);
    projection.apply(
        action(&active, Action::Waiting(WaitReason::ToolApproval)),
        101.0,
    );
    projection.apply(
        action(&active, Action::Waiting(WaitReason::UserQuestions)),
        102.0,
    );
    projection.apply(
        action(&active, Action::Resolved(WaitReason::ToolApproval)),
        103.0,
    );
    let tool = Action::ToolStarted {
        call_id: "internal-call-id".into(),
        name: "read_file".into(),
    };
    projection.apply(action(&active, tool.clone()), 104.0);
    projection.apply(action(&active, tool), 104.5);
    projection.apply(action(&active, Action::Response { error: true }), 105.0);
    projection.inspect(&runtime, 105.0);
    projection.resolve_models(&runtime);
    let waiting = projection.snapshot(instance, 1, 105.0, None);
    assert_eq!(row(&waiting, &active)["pose"], "asking");
    assert_eq!(row(&waiting, &active)["tools"], 1);
    assert_eq!(row(&waiting, &active)["active"], true);
    assert_eq!(row(&waiting, &active)["model"], "gpt-offline");
    let heartbeat = projection.snapshot(instance, 2, 107.0, None);
    assert_eq!(
        row(&heartbeat, &active)["revision"],
        row(&waiting, &active)["revision"]
    );
    assert_eq!(row(&heartbeat, &active)["event_ts"], 104.0);
    assert_eq!(row(&heartbeat, &active)["ts"], 107.0);
    for forbidden in [
        "internal-task-id",
        "internal-call-id",
        "workspace",
        "conv_same",
        "private-catalog-connection",
    ] {
        assert!(!heartbeat.to_string().contains(forbidden));
    }

    projection.apply(
        action(&active, Action::Finished(TerminalStatus::Succeeded)),
        110.0,
    );
    let unknown = projection.snapshot(instance, 3, 110.0, None);
    assert_eq!(row(&unknown, &active)["pose"], "delegating");
    runtime.activity.lock().unwrap().insert(
        active.clone(),
        TaskObservation {
            terminal: Some(TerminalStatus::Succeeded),
            background: Some(false),
        },
    );
    projection.inspect(&runtime, 111.0);
    let complete = projection.snapshot(instance, 4, 111.0, None);
    assert_eq!(row(&complete, &active)["pose"], "oops");
    assert_eq!(row(&complete, &active)["active"], false);

    let successor = task("workspace", "conv_same", "next-task");
    projection.apply(action(&successor, Action::Started), 115.0);
    projection.apply(action(&active, Action::Error { retry: false }), 116.0);
    let migrated = task("workspace", "conv_compacted", "next-task");
    projection.apply(
        action(&migrated, Action::Compression { active: true }),
        117.0,
    );
    let after = projection.snapshot(instance, 5, 117.0, None);
    assert!(row(&after, &active).is_null());
    assert_eq!(row(&after, &migrated)["pose"], "compact");
    assert_eq!(row(&after, &migrated)["tools"], 0);
}

#[test]
fn snapshot_caps_the_wire_session_count_and_retains_foreground() {
    let mut projection = projection::Projection::default();
    let focused = task("first", "conv_focus", "task-focus");
    let runtime = TestRuntime::default();
    *runtime.foreground.lock().unwrap() = Some(Foreground {
        session: focused.session.clone(),
        model_key: "mapped".into(),
    });
    projection.apply(action(&focused, Action::Started), 1.0);
    for index in 1..=300 {
        let id = index.to_string();
        projection.apply(
            action(&task("first", &format!("conv_{id}"), &id), Action::Started),
            f64::from(index),
        );
    }
    projection.inspect(&runtime, 302.0);
    projection.resolve_models(&runtime);
    let value = projection.snapshot(Uuid::new_v4(), 1, 302.0, None);
    assert_eq!(value["sessions"].as_object().unwrap().len(), 256);
    assert_eq!(
        value["foreground"]["session"],
        projection::session_key(&focused.session)
    );
    assert_eq!(row(&value, &focused)["pose"], "thinking");
    assert_eq!(value["foreground"]["character"], "gpt");
}

async fn eventually<T>(probe: impl Fn() -> Option<T>) -> T {
    let end = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(value) = probe() {
            return value;
        }
        assert!(
            Instant::now() < end,
            "timed out waiting for CrossPet publication"
        );
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

async fn request(
    app: &Router,
    method: Method,
    path: &str,
    headers: &[(&str, &str)],
    data: Option<Value>,
) -> (StatusCode, axum::http::HeaderMap, Value) {
    let peer: SocketAddr = "127.0.0.1:12345".parse().unwrap();
    let mut req = Request::builder()
        .method(method)
        .uri(path)
        .header("host", "127.0.0.1:8493")
        .extension(ConnectInfo(peer));
    for (name, value) in headers {
        req = req.header(*name, *value)
    }
    let body = if let Some(data) = data {
        req = req.header("content-type", "application/json");
        Body::from(data.to_string())
    } else {
        Body::empty()
    };
    let response = app.clone().oneshot(req.body(body).unwrap()).await.unwrap();
    let (parts, body) = response.into_parts();
    let bytes = to_bytes(body, 1024 * 1024).await.unwrap();
    (
        parts.status,
        parts.headers,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

fn cookie(headers: &axum::http::HeaderMap) -> String {
    headers[SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned()
}

async fn login(app: &Router) -> (String, String) {
    let (status, headers, response) = request(app, Method::GET, "/api/csrf-token", &[], None).await;
    assert_eq!(status, StatusCode::OK);
    let challenge = cookie(&headers);
    let (status, headers, _) = request(
        app,
        Method::POST,
        "/host-login",
        &[
            ("cookie", &challenge),
            ("origin", "http://127.0.0.1:8493"),
            ("x-csrf-token", response["token"].as_str().unwrap()),
        ],
        Some(json!({})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let session = cookie(&headers);
    let (_, _, response) = request(
        app,
        Method::GET,
        "/api/csrf-token",
        &[("cookie", &session)],
        None,
    )
    .await;
    (session, response["token"].as_str().unwrap().to_string())
}

#[tokio::test]
async fn authenticated_switch_writes_three_files_and_restores_opt_in_after_restart() {
    let scratch = Scratch::new();
    let config = scratch.config();
    let runtime = Arc::new(TestRuntime::default());
    let service = CrossPet::start(config.clone(), runtime.clone()).unwrap();
    let auth = WebAuth::new(WebAuthConfig::new(&scratch.0, 8493)).unwrap();
    let app = auth.router().merge(auth.protect_api(service.router()));
    let path = "/api/crosspet";
    assert_eq!(
        request(&app, Method::GET, path, &[], None).await.0,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(
            &app,
            Method::GET,
            path,
            &[("authorization", "Bearer synthetic")],
            None
        )
        .await
        .0,
        StatusCode::UNAUTHORIZED
    );
    let (cookie, token) = login(&app).await;
    let read = [("cookie", cookie.as_str())];
    let write = [
        ("cookie", cookie.as_str()),
        ("origin", "http://127.0.0.1:8493"),
        ("x-csrf-token", token.as_str()),
    ];
    assert_eq!(
        request(&app, Method::GET, path, &read, None).await.2,
        json!({"success":true,"supported":true,"enabled":false})
    );
    assert_eq!(
        request(&app, Method::HEAD, path, &read, None).await.2,
        Value::Null
    );
    assert_eq!(
        request(&app, Method::OPTIONS, path, &read, None).await.1[ALLOW],
        "GET, HEAD, POST, OPTIONS"
    );
    assert_eq!(
        request(
            &app,
            Method::POST,
            path,
            &read,
            Some(json!({"enabled":true}))
        )
        .await
        .0,
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        request(&app, Method::POST, path, &write, Some(json!({"enabled":1})))
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    let active = task("one", "conv_shared", "task-a");
    service.events().event(action(&active, Action::Started));
    tokio::time::sleep(Duration::from_millis(250)).await;
    assert!(!scratch.0.join("deployment").exists());
    assert!(!scratch.0.join("state").exists());
    assert_eq!(runtime.foreground_calls.load(Ordering::Relaxed), 0);
    assert_eq!(runtime.model_calls.load(Ordering::Relaxed), 0);
    assert!(runtime.quota_calls.lock().unwrap().is_empty());

    assert_eq!(
        request(
            &app,
            Method::POST,
            path,
            &write,
            Some(json!({"enabled":true}))
        )
        .await
        .0,
        StatusCode::OK
    );
    let first = eventually(|| {
        scratch
            .snapshot()
            .filter(|_| scratch.0.join("state/seen-astrion").exists())
    })
    .await;
    let files = scratch.files();
    assert_eq!(
        json!({"version": 1, "enabled": true}),
        serde_json::from_slice::<Value>(
            &fs::read(scratch.0.join("deployment/crosspet.json")).unwrap()
        )
        .unwrap()
    );
    assert_eq!(
        first["foreground"],
        serde_json::from_slice::<Value>(&files[1]).unwrap()
    );
    assert_eq!(
        first["ts"].as_f64().unwrap() as u64,
        std::str::from_utf8(&files[2])
            .unwrap()
            .parse::<u64>()
            .unwrap()
    );
    assert_eq!(row(&first, &active)["model"], "gpt-offline");
    assert_eq!(
        first["quota"]["gpt"]["rateLimits"],
        json!({"primary":{"usedPercent":31.0,"windowDurationMins":300.0,"resetsAt":3600.0}})
    );

    assert_eq!(
        request(
            &app,
            Method::POST,
            path,
            &write,
            Some(json!({"enabled":false}))
        )
        .await
        .0,
        StatusCode::OK
    );
    service
        .events()
        .event(action(&active, Action::Finished(TerminalStatus::Stopped)));
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert_eq!(scratch.files(), files);
    assert_eq!(runtime.quota_calls.lock().unwrap().last(), Some(&false));

    let prior_instance = first["instance"].clone();
    assert_eq!(
        request(
            &app,
            Method::POST,
            path,
            &write,
            Some(json!({"enabled":true}))
        )
        .await
        .0,
        StatusCode::OK
    );
    service.shutdown();
    let second = CrossPet::start(config, runtime).unwrap();
    let restarted = eventually(|| {
        scratch
            .snapshot()
            .filter(|current| current["instance"] != prior_instance)
    })
    .await;
    assert_eq!(restarted["sequence"], 1);
    assert_eq!(restarted["sessions"], json!({}));
    second.shutdown();
}

#[tokio::test]
async fn disabling_during_an_in_flight_probe_prevents_publication() {
    let scratch = Scratch::new();
    let runtime = Arc::new(TestRuntime::default());
    let service = CrossPet::start(scratch.config(), runtime.clone()).unwrap();
    service.0.set_enabled(true).unwrap();
    eventually(|| {
        scratch
            .snapshot()
            .filter(|_| scratch.0.join("state/seen-astrion").exists())
    })
    .await;
    let before = scratch.files();
    let (started_tx, started_rx) = mpsc::channel();
    let (resume_tx, resume_rx) = mpsc::channel();
    *runtime.latch.lock().unwrap() = Some((started_tx, resume_rx));
    *runtime.foreground.lock().unwrap() = Some(Foreground {
        session: task("work", "conv_other", "task").session,
        model_key: "updated".into(),
    });
    service.wake();
    tokio::task::spawn_blocking(move || started_rx.recv_timeout(Duration::from_secs(3)).unwrap())
        .await
        .unwrap();
    service.0.set_enabled(false).unwrap();
    resume_tx.send(()).unwrap();
    tokio::time::sleep(Duration::from_millis(350)).await;
    let calls = runtime.quota_calls.lock().unwrap();
    assert_eq!(calls.last(), Some(&false));
    assert_eq!(calls.iter().filter(|&&enabled| enabled).count(), 1);
    assert_eq!(scratch.files(), before);
    drop(calls);
    service.shutdown();
}

#[tokio::test]
async fn disabled_progress_is_discarded_but_periodic_refresh_observes_external_opt_in() {
    let scratch = Scratch::new();
    let config = scratch.config();
    let runtime = Arc::new(TestRuntime::default());
    let service = CrossPet::start(config.clone(), runtime.clone()).unwrap();
    let active = task("work", "conv_active", "task");
    let events = service.events();
    let before_progress = service.0.notifications();
    assert!(!events.observes_progress());
    events.event(action(&active, Action::Started));
    for index in 0..4096 {
        events.event(action(
            &active,
            Action::ToolStarted {
                call_id: index.to_string(),
                name: "read_file".into(),
            },
        ));
        events.event(action(&active, Action::ToolFinished { failed: false }));
        events.wake();
    }
    assert_eq!(service.0.notifications(), before_progress);
    assert_eq!(runtime.foreground_calls.load(Ordering::Relaxed), 0);
    assert!(scratch.snapshot().is_none());
    // Change the deployment preference without the API or a worker wakeup.
    config.save_enabled(true).unwrap();
    let initial = eventually(|| scratch.snapshot()).await;
    assert!(events.observes_progress());
    assert_eq!(service.0.notifications(), before_progress);
    assert_eq!(row(&initial, &active)["tools"], 0);
    assert_eq!(row(&initial, &active)["pose"], "thinking");

    events.event(action(
        &active,
        Action::ToolStarted {
            call_id: "live".into(),
            name: "read_file".into(),
        },
    ));
    let with_tool = eventually(|| {
        scratch
            .snapshot()
            .filter(|value| row(value, &active)["tools"] == 1)
    })
    .await;
    assert_eq!(row(&with_tool, &active)["pose"], "reading");
    assert_eq!(service.0.notifications(), before_progress + 1);
    config.save_enabled(false).unwrap();
    eventually(|| (runtime.quota_calls.lock().unwrap().last() == Some(&false)).then_some(())).await;
    assert!(!events.observes_progress());
    assert_eq!(scratch.snapshot().unwrap(), with_tool);
    service.shutdown();
}

#[test]
fn config_rejects_invalid_documents_and_honors_destination_precedence() {
    let scratch = Scratch::new();
    let config = scratch.config();
    assert_eq!(config.destination().unwrap(), scratch.0.join("state"));
    let from_config = scratch.0.join("custom");
    fs::write(
        scratch.0.join("astrion.json"),
        json!({"version":1,"enabled":false,"state_dir":from_config}).to_string(),
    )
    .unwrap();
    assert_eq!(config.destination().unwrap(), from_config);
    let overridden = config
        .clone()
        .with_destination_override(Some(scratch.0.join("override")));
    assert_eq!(
        overridden.destination().unwrap(),
        scratch.0.join("override")
    );
    assert!(
        config
            .clone()
            .with_destination_override(Some(PathBuf::from("relative")))
            .destination()
            .is_err()
    );
    assert!(!config.enabled().unwrap());
    fs::create_dir(scratch.0.join("deployment")).unwrap();
    fs::write(
        scratch.0.join("deployment/crosspet.json"),
        "{\"version\":1,\"enabled\":1}",
    )
    .unwrap();
    assert!(config.enabled().is_err());
}

#[cfg(unix)]
#[test]
fn symbolic_link_targets_are_rejected_before_enabling() {
    use std::os::unix::fs::symlink;
    let scratch = Scratch::new();
    let target = scratch.0.join("elsewhere");
    fs::write(&target, b"unchanged").unwrap();
    fs::create_dir(scratch.0.join("state")).unwrap();
    symlink(&target, scratch.0.join("state/seen-astrion")).unwrap();
    let config = scratch.config();
    let service = CrossPet::start(config.clone(), Arc::new(TestRuntime::default())).unwrap();
    assert!(service.0.set_enabled(true).is_err());
    assert!(!config.enabled().unwrap());
    assert!(scratch.snapshot().is_none());
    assert_eq!(fs::read(&target).unwrap(), b"unchanged");
    service.shutdown();
}
