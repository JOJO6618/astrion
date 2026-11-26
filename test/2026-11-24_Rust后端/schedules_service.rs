use super::*;
use rusqlite::{Connection, params};
use std::{
    fs,
    path::PathBuf,
    sync::{
        Barrier,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
};

struct Fixture {
    root: PathBuf,
    now: Timestamp,
    service: ScheduleService,
    adapter: Arc<Adapter>,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("schedules-service-{}", Uuid::new_v4()));
        let adapter = Arc::new(Adapter::default());
        let service = ScheduleService::open(&root, adapter.clone()).unwrap();
        Self {
            root,
            now: instant("2026-02-01T00:00:00Z").unwrap(),
            service,
            adapter,
        }
    }

    fn definition(&self, id: &str) -> Value {
        json!({"id":id,"schema_version":1,"name":"巡检","message":"Inspect","target":{"workspace_id":"ws-1"},
            "schedule":{"type":"interval","timezone":"UTC","every":1,"unit":"minutes","anchor":iso(self.now)}})
    }

    fn submit(&self, definition: Value, overwrite: bool) -> Result<Value> {
        self.service.submit_at(
            &HostPrincipal::new(None),
            &definition,
            &Value::Null,
            overwrite,
            self.now,
        )
    }

    fn act(
        &self,
        id: &str,
        action: &str,
        run: Option<&str>,
        request: Option<&str>,
        seconds: i128,
    ) -> Value {
        self.service
            .manage_at(id, action, None, run, request, self.at(seconds))
            .unwrap()
    }

    fn at(&self, seconds: i128) -> Timestamp {
        Timestamp::from_nanosecond(self.now.as_nanosecond() + seconds * 1_000_000_000).unwrap()
    }

    fn db(&self) -> Connection {
        Connection::open(self.root.join("scheduled_tasks.sqlite3")).unwrap()
    }

    fn run(&self, id: &str, run_id: &Value) -> Value {
        self.service
            .runs(id)
            .unwrap()
            .as_array()
            .unwrap()
            .iter()
            .find(|run| run["id"] == *run_id)
            .unwrap()
            .clone()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

struct Adapter {
    starts: AtomicUsize,
    cancels: AtomicUsize,
    busy: AtomicBool,
    finish: AtomicBool,
    waiting: AtomicBool,
}

impl Default for Adapter {
    fn default() -> Self {
        Self {
            starts: AtomicUsize::new(0),
            cancels: AtomicUsize::new(0),
            busy: AtomicBool::new(false),
            finish: AtomicBool::new(false),
            waiting: AtomicBool::new(false),
        }
    }
}

impl ScheduleAdapter for Adapter {
    fn validate(&self, definition: &Value, principal: &Value) -> Result<Option<Value>> {
        assert_eq!(principal["username"], "host");
        if definition["target"]["workspace_id"] != "ws-1" {
            return Err(ScheduleError::forbidden("workspace outside scope"));
        }
        Ok(Some(
            principal
                .get("configuration_snapshot")
                .cloned()
                .unwrap_or_else(|| json!({"model_key":"saved"})),
        ))
    }

    fn start(
        &self,
        run: &super::super::ScheduleInvocation,
    ) -> std::result::Result<Admission, StartError> {
        if self.busy.load(Ordering::SeqCst) {
            return Err(StartError::Busy("conversation_gate_busy".into()));
        }
        self.starts.fetch_add(1, Ordering::SeqCst);
        Ok(Admission {
            task_id: format!("task:{}", run.run_id),
            conversation_id: "conv-fixture".into(),
        })
    }

    fn observe(&self, _: &super::super::ScheduleInvocation, _: &Admission) -> Result<Observation> {
        let state = if self.finish.load(Ordering::SeqCst) {
            RunState::Success
        } else if self.waiting.load(Ordering::SeqCst) {
            RunState::Waiting
        } else {
            RunState::Running
        };
        Ok(Observation::new(state))
    }

    fn cancel(&self, _: &super::super::ScheduleInvocation, _: &Admission) -> Result<()> {
        self.cancels.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

#[test]
fn persists_revision_cas_and_run_snapshot() {
    let f = Fixture::new();
    let original = f.definition("plan");
    assert_eq!(
        f.submit(original.clone(), false).unwrap()["definition"]["revision"],
        1
    );
    assert_eq!(f.submit(original.clone(), false).unwrap_err().status, 409);
    let first = f.act("plan", "run", None, Some("same"), 1);
    let mut edit = original.clone();
    edit["revision"] = json!(1);
    edit["message"] = json!("edited");
    edit["schedule"].as_object_mut().unwrap().remove("anchor");
    assert_eq!(
        f.submit(edit.clone(), true).unwrap()["definition"]["revision"],
        2
    );
    assert_eq!(f.submit(edit, true).unwrap_err().status, 409);
    assert_eq!(
        f.service
            .manage_at("plan", "pause", Some(1), None, None, f.now)
            .unwrap_err()
            .status,
        409
    );
    assert_eq!(
        f.act("plan", "run", None, Some("same"), 2)["id"],
        first["id"]
    );
    assert_eq!(
        f.run("plan", &first["id"])["definition"]["message"],
        "Inspect"
    );
    let mut invalid = original;
    invalid["revision"] = json!(2);
    invalid["target"]["workspace_id"] = json!("other");
    assert_eq!(f.submit(invalid, true).unwrap_err().status, 403);
    assert_eq!(
        f.service.detail("plan").unwrap()["definition"]["message"],
        "edited"
    );
}

#[test]
fn concurrent_manual_retries_and_owner_lease() {
    let f = Fixture::new();
    f.submit(f.definition("plan"), false).unwrap();
    let owner = f.service.acquire_owner().unwrap();
    assert_eq!(f.service.acquire_owner().err().unwrap().status, 503);
    let gate = Barrier::new(8);
    let ids = thread::scope(|scope| {
        (0..8)
            .map(|_| {
                scope.spawn(|| {
                    gate.wait();
                    f.act("plan", "run", None, Some("same"), 1)["id"].clone()
                })
            })
            .collect::<Vec<_>>()
            .into_iter()
            .map(|thread| thread.join().unwrap())
            .collect::<Vec<_>>()
    });
    assert!(ids.iter().all(|id| id == &ids[0]));
    thread::scope(|scope| {
        for _ in 0..6 {
            scope.spawn(|| owner.tick(f.at(1)).unwrap());
        }
    });
    assert_eq!(f.adapter.starts.load(Ordering::SeqCst), 1);
    assert_eq!(f.run("plan", &ids[0])["state"], "running");
    drop(owner);
    let restored = f.service.acquire_owner().unwrap();
    let run = f.run("plan", &ids[0]);
    assert_eq!(run["state"], "interrupted");
    assert_eq!(run["reason"], "process_restarted");
    restored.tick(f.at(2)).unwrap();
    assert_eq!(f.adapter.starts.load(Ordering::SeqCst), 1);
}

#[test]
fn missed_latest_once_and_busy_merge() {
    let f = Fixture::new();
    let mut latest = f.definition("latest");
    latest["enabled"] = json!(true);
    latest["policies"] = json!({"missed":"latest","busy":"merge"});
    f.submit(latest, false).unwrap();
    let mut skipped = f.definition("skipped");
    skipped["enabled"] = json!(true);
    f.submit(skipped, false).unwrap();
    let mut once = f.definition("once");
    once["enabled"] = json!(true);
    once["schedule"] =
        json!({"type":"once","timezone":"UTC","date":"2026-02-01","times":["00:01"]});
    f.submit(once, false).unwrap();
    f.adapter.busy.store(true, Ordering::SeqCst);
    let owner = f.service.acquire_owner().unwrap();
    owner.tick(f.at(400)).unwrap();
    let latest = f.service.runs("latest").unwrap();
    assert_eq!(latest[0]["scheduled_for"], "2026-02-01T00:06:00.000000Z");
    assert_eq!(latest[0]["state"], "queued");
    assert_eq!(f.service.runs("skipped").unwrap()[0]["reason"], "missed");
    assert_eq!(
        f.service.detail("once").unwrap()["definition"]["enabled"],
        false
    );
    assert!(f.service.detail("once").unwrap()["next_at"].is_null());
    let manual = f.act("latest", "run", None, Some("newest"), 410);
    owner.tick(f.at(411)).unwrap();
    assert_eq!(
        f.run("latest", &latest[0]["id"])["reason"],
        "merged_into_latest"
    );
    f.adapter.busy.store(false, Ordering::SeqCst);
    owner.tick(f.at(412)).unwrap();
    owner.tick(f.at(413)).unwrap();
    assert_eq!(f.run("latest", &manual["id"])["state"], "running");
    assert_eq!(f.adapter.starts.load(Ordering::SeqCst), 1);
}

#[test]
fn pause_does_not_stop_running_and_timeout_does_not_manufacture_success() {
    let f = Fixture::new();
    let mut definition = f.definition("plan");
    definition["policies"] = json!({"approval_timeout_seconds":2});
    f.submit(definition, false).unwrap();
    let owner = f.service.acquire_owner().unwrap();
    let running = f.act("plan", "run", None, Some("first"), 1);
    owner.tick(f.at(1)).unwrap();
    let queued = f.act("plan", "run", None, Some("second"), 2);
    f.act("plan", "pause", None, None, 3);
    assert_eq!(f.run("plan", &queued["id"])["reason"], "plan_paused");
    assert_eq!(f.run("plan", &running["id"])["state"], "running");
    assert_eq!(f.adapter.cancels.load(Ordering::SeqCst), 0);
    f.adapter.waiting.store(true, Ordering::SeqCst);
    owner.tick(f.at(4)).unwrap();
    assert_eq!(f.run("plan", &running["id"])["state"], "waiting");
    owner.tick(f.at(6)).unwrap();
    assert_eq!(f.run("plan", &running["id"])["state"], "stopping");
    f.adapter.finish.store(true, Ordering::SeqCst);
    owner.tick(f.at(7)).unwrap();
    let failed = f.run("plan", &running["id"]);
    assert_eq!(failed["state"], "failed");
    assert_eq!(failed["reason"], "approval_timeout");
    let offers = f.service.0.store.claim_notifications(f.at(8)).unwrap();
    let offer = offers
        .iter()
        .find(|offer| offer["id"] == failed["id"])
        .unwrap();
    let id = offer["id"].as_str().unwrap();
    let token = offer["token"].as_str().unwrap();
    assert_eq!(
        f.service.acknowledge(id, "wrong", None).unwrap()["acknowledged"],
        false
    );
    assert_eq!(
        f.service.acknowledge(id, token, None).unwrap()["acknowledged"],
        true
    );
    assert_eq!(
        f.service.acknowledge(id, token, Some("too late")).unwrap()["acknowledged"],
        false
    );
    assert_eq!(
        f.run("plan", &running["id"])["notification_status"],
        "delivered"
    );
}

#[test]
fn ignores_historical_non_host_rows_and_uses_python_schema() {
    let f = Fixture::new();
    f.submit(f.definition("plan"), false).unwrap();
    let db = f.db();
    db.execute("INSERT INTO plans SELECT 'historical', 'private', revision, definition, principal, source, enabled, next_at, created_at, updated_at, deleted_at FROM plans WHERE id='plan'", []).unwrap();
    db.execute("INSERT INTO runs(id,username,schedule_id,revision,scheduled_for,trigger,occurrence_key,state,definition,principal,created_at)
        SELECT 'other-run','historical','private',revision,created_at,'manual','old-key','running',definition,principal,created_at FROM plans WHERE id='private'", []).unwrap();
    let owner = f.service.acquire_owner().unwrap();
    owner.tick(f.at(5)).unwrap();
    assert_eq!(f.service.detail("private").unwrap_err().status, 404);
    assert_eq!(f.service.runs("private").unwrap_err().status, 404);
    assert_eq!(
        db.query_row(
            "SELECT state FROM runs WHERE id=?",
            params!["other-run"],
            |row| row.get::<_, String>(0)
        )
        .unwrap(),
        "running"
    );
    assert_eq!(
        f.service.list(None, None).unwrap()["plans"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn unavailable_runtime_does_not_claim_due_or_manual_runs() {
    let f = Fixture::new();
    let mut definition = f.definition("plan");
    definition["enabled"] = json!(true);
    f.submit(definition, false).unwrap();
    let service =
        ScheduleService::open(&f.root, Arc::new(super::super::UnavailableAdapter)).unwrap();
    assert!(!service.list(None, None).unwrap()["scheduler_error"].is_null());
    assert_eq!(service.acquire_owner().err().unwrap().status, 503);
    assert_eq!(
        service
            .manage("plan", "run", None, None, Some("manual"))
            .unwrap_err()
            .status,
        503
    );
    assert!(service.runs("plan").unwrap().as_array().unwrap().is_empty());
}

#[test]
fn reopens_python_database_and_reconciles_active_runs_without_replay() {
    let root = std::env::temp_dir().join(format!("schedules-python-{}", Uuid::new_v4()));
    fs::create_dir_all(&root).unwrap();
    {
        let db = Connection::open(root.join("scheduled_tasks.sqlite3")).unwrap();
        db.execute_batch(include_str!("schedules_python_db.sql"))
            .unwrap();
    }
    let adapter = Arc::new(Adapter::default());
    let service = ScheduleService::open(&root, adapter.clone()).unwrap();
    let f = Fixture {
        root,
        now: instant("2026-02-01T00:00:00Z").unwrap(),
        service,
        adapter,
    };
    assert_eq!(
        f.service.detail("legacy").unwrap()["definition"]["message"],
        "Original Python message"
    );
    let old = f.act("legacy", "run", None, Some("already-admitted"), 0);
    assert_eq!(old["task_id"], "python-task");
    let owner = f.service.acquire_owner().unwrap();
    let stopped = f.run("legacy", &old["id"]);
    assert_eq!(stopped["state"], "interrupted");
    assert_eq!(stopped["reason"], "process_restarted");
    let queued = f.act("legacy", "run", None, Some("not-admitted-yet"), 0);
    assert_eq!(queued["state"], "queued");
    owner.tick(f.at(0)).unwrap();
    assert_eq!(f.adapter.starts.load(Ordering::SeqCst), 1);
    assert_eq!(f.run("legacy", &queued["id"])["state"], "running");
    assert_eq!(f.run("legacy", &old["id"])["state"], "interrupted");
}

#[tokio::test]
async fn owner_driver_releases_lease_and_never_starts_before_run_is_called() {
    let f = Fixture::new();
    f.submit(f.definition("plan"), false).unwrap();
    f.act("plan", "run", None, Some("driver"), 1);
    let owner = f.service.acquire_owner().unwrap();
    assert_eq!(f.adapter.starts.load(Ordering::SeqCst), 0);
    let (send, receive) = tokio::sync::watch::channel(false);
    let driver = tokio::spawn(owner.run(receive));
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        while f.adapter.starts.load(Ordering::SeqCst) == 0 {
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    send.send(true).unwrap();
    driver.await.unwrap().unwrap();
    assert!(f.service.acquire_owner().is_ok());
}
