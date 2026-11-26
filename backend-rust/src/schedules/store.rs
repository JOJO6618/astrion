use std::{
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

use jiff::Timestamp;
use rusqlite::{
    Connection, OptionalExtension, Row, Transaction, TransactionBehavior, params, types::Type,
};
use serde::Serialize;
use serde_json::{Value, json};
use uuid::Uuid;

use super::{
    Admission, Result, RunState, ScheduleError, ScheduleInvocation,
    clock::{Rule, instant, iso},
};

const HOST: &str = "host";
const ACTIVE: &str = "state IN ('starting','running','waiting','stopping')";
const TERMINAL: &str = "state IN ('success','failed','skipped','stopped','interrupted')";

#[derive(Clone)]
pub(super) struct Store(PathBuf);

#[derive(Debug)]
pub(super) struct Plan {
    pub id: String,
    pub revision: i64,
    pub definition: Value,
    pub principal: Value,
    pub enabled: bool,
    pub next_at: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub deleted_at: Option<String>,
}

#[derive(Debug, Serialize)]
pub(super) struct Run {
    pub id: String,
    pub schedule_id: String,
    pub revision: i64,
    pub scheduled_for: String,
    pub trigger: String,
    #[serde(skip)]
    pub occurrence_key: String,
    pub state: RunState,
    #[serde(skip)]
    pub definition: Value,
    #[serde(skip)]
    pub principal: Value,
    pub task_id: Option<String>,
    pub conversation_id: Option<String>,
    pub reason: Option<String>,
    pub summary: Option<String>,
    pub created_at: String,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
    pub waiting_since: Option<String>,
    pub stop_requested: bool,
    pub notification_status: String,
    pub notification_error: Option<String>,
}

impl Run {
    pub fn invocation(&self) -> Result<ScheduleInvocation> {
        if self.principal["username"] != HOST
            || self.principal["host_mode"] != true
            || self.principal["is_api_user"] == true
        {
            return Err(ScheduleError::forbidden(
                "计划所属身份不受支持，仅允许宿主机身份",
            ));
        }
        Ok(ScheduleInvocation {
            run_id: self.id.clone(),
            schedule_id: self.schedule_id.clone(),
            scheduled_for: self.scheduled_for.clone(),
            occurrence_key: self.occurrence_key.clone(),
            definition: self.definition.clone(),
            principal: self.principal.clone(),
        })
    }

    pub fn admission(&self) -> Option<Admission> {
        Some(Admission {
            task_id: self.task_id.clone()?,
            conversation_id: self.conversation_id.clone()?,
        })
    }

    pub fn public(&self, detail: bool) -> Value {
        let mut result = json!(self);
        if detail {
            result["definition"] = self.definition.clone();
        }
        result
    }
}

impl Store {
    pub fn open(root: &Path) -> Result<Self> {
        fs::create_dir_all(root).map_err(ScheduleError::storage)?;
        let path = root.join("scheduled_tasks.sqlite3");
        #[cfg(unix)]
        {
            use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
            let file = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .mode(0o600)
                .open(&path)
                .map_err(ScheduleError::storage)?;
            file.set_permissions(fs::Permissions::from_mode(0o600))
                .map_err(ScheduleError::storage)?;
        }
        let store = Self(path);
        let mut db = store.connection()?;
        db.execute_batch("PRAGMA journal_mode=WAL;")?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute_batch(include_str!("schema.sql"))?;
        let columns = collect(&tx, "PRAGMA table_info(runs)", [], |row| {
            row.get::<_, String>(1)
        })?;
        for (name, statement) in [
            (
                "notification_token",
                "ALTER TABLE runs ADD COLUMN notification_token TEXT",
            ),
            (
                "notification_claimed_at",
                "ALTER TABLE runs ADD COLUMN notification_claimed_at TEXT",
            ),
        ] {
            if !columns.iter().any(|column| column == name) {
                tx.execute(statement, [])?;
            }
        }
        tx.commit()?;
        Ok(store)
    }

    pub fn owner_path(&self) -> PathBuf {
        self.0.with_extension("lock")
    }

    fn connection(&self) -> Result<Connection> {
        let db = Connection::open(&self.0)?;
        db.busy_timeout(Duration::from_secs(20))?;
        Ok(db)
    }

    fn transact<T>(&self, write: impl FnOnce(&Transaction<'_>) -> Result<T>) -> Result<T> {
        let mut db = self.connection()?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let result = write(&tx)?;
        tx.commit()?;
        Ok(result)
    }

    pub fn get(&self, id: &str, deleted: bool) -> Result<Plan> {
        get_plan(&self.connection()?, id, deleted)
    }

    pub fn list(&self) -> Result<Vec<Plan>> {
        collect(
            &self.connection()?,
            "SELECT * FROM plans WHERE username=? AND deleted_at IS NULL ORDER BY created_at DESC,id",
            [HOST],
            read_plan,
        )
    }

    pub fn put(
        &self,
        mut definition: Value,
        principal: &Value,
        source: &Value,
        overwrite: bool,
        next: Option<Timestamp>,
        now: Timestamp,
    ) -> Result<Plan> {
        let id = definition["id"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| format!("schedule_{}", Uuid::new_v4().simple()));
        let enabled = definition["enabled"] == true;
        let next = next.map(iso);
        let now = iso(now);
        self.transact(|db| {
            let (revision, created) = match find_plan(db, &id, true)? {
                Some(old) => {
                    if !overwrite || old.deleted_at.is_some() {
                        return Err(ScheduleError::conflict("计划 ID 已存在；覆盖必须显式指定 overwrite"));
                    }
                    if definition["revision"].as_i64() != Some(old.revision) {
                        return Err(ScheduleError::conflict("计划版本已更新，请重新读取后修改"));
                    }
                    (increment_revision(old.revision)?, old.created_at)
                }
                None if overwrite => return Err(ScheduleError::missing("不能覆盖不存在的计划")),
                None => (1, now.clone()),
            };
            if enabled && next.is_none() {
                return Err(ScheduleError::invalid("此时间规则没有未来执行时间，请调整时间或保存为停止状态"));
            }
            definition["id"] = json!(id);
            definition["revision"] = json!(revision);
            db.execute("INSERT INTO plans(username,id,revision,definition,principal,source,enabled,next_at,created_at,updated_at) \
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(username,id) DO UPDATE SET revision=excluded.revision, \
                definition=excluded.definition,principal=excluded.principal,source=excluded.source,enabled=excluded.enabled, \
                next_at=excluded.next_at,updated_at=excluded.updated_at",
                params![HOST, id, revision, definition.to_string(), principal.to_string(), source.to_string(), enabled, next, created, now])?;
            get_plan(db, &id, false)
        })
    }

    pub fn set_enabled(
        &self,
        id: &str,
        enabled: bool,
        expected: Option<i64>,
        next: Option<Timestamp>,
        now: Timestamp,
    ) -> Result<Plan> {
        let now = iso(now);
        self.transact(|db| {
            let mut plan = get_plan(db, id, false)?;
            if expected.is_some_and(|revision| revision != plan.revision) {
                return Err(ScheduleError::conflict("计划版本已更新"));
            }
            if enabled && next.is_none() {
                return Err(ScheduleError::invalid("此计划没有未来执行时间"));
            }
            let revision = increment_revision(plan.revision)?;
            plan.definition["revision"] = json!(revision);
            plan.definition["enabled"] = json!(enabled);
            db.execute("UPDATE plans SET enabled=?,revision=?,definition=?,next_at=?,updated_at=? WHERE username=? AND id=?",
                params![enabled, revision, plan.definition.to_string(), next.map(iso), now, HOST, id])?;
            if !enabled {
                skip_queued(db, id, "plan_paused", &now)?;
            }
            get_plan(db, id, false)
        })
    }

    pub fn delete(&self, id: &str, now: Timestamp) -> Result<()> {
        let now = iso(now);
        self.transact(|db| {
            let changed = db.execute("UPDATE plans SET deleted_at=?,enabled=0,next_at=NULL WHERE username=? AND id=? AND deleted_at IS NULL", params![now, HOST, id])?;
            if changed == 0 {
                return Err(ScheduleError::missing("计划不存在"));
            }
            skip_queued(db, id, "plan_deleted", &now)
        })
    }

    pub fn manual(&self, id: &str, request_id: &str, now: Timestamp) -> Result<Run> {
        let key = format!("manual:{HOST}:{id}:{request_id}");
        let now = iso(now);
        self.transact(|db| {
            let plan = get_plan(db, id, false)?;
            insert_run(
                db,
                &plan,
                NewRun {
                    scheduled: &now,
                    trigger: "manual",
                    key: &key,
                    state: RunState::Queued,
                    reason: None,
                    now: &now,
                },
            )?;
            Ok(db.query_row(
                "SELECT * FROM runs WHERE username=? AND occurrence_key=?",
                params![HOST, key],
                read_run,
            )?)
        })
    }

    pub fn due(&self, now: Timestamp) -> Result<Vec<Plan>> {
        collect(
            &self.connection()?,
            "SELECT * FROM plans WHERE username=? AND enabled=1 AND deleted_at IS NULL AND next_at<=? ORDER BY next_at,id",
            params![HOST, iso(now)],
            read_plan,
        )
    }

    pub fn claim_due(&self, plan: Plan, now: Timestamp) -> Result<()> {
        let now_text = iso(now);
        let expected_next = plan.next_at.as_deref().expect("due rows have next_at");
        let (rule, _) = Rule::read(&plan.definition["schedule"], now, None)?;
        let mut scheduled = instant(expected_next)?;
        let missed = now.as_nanosecond() - scheduled.as_nanosecond() > 30_000_000_000;
        if missed {
            scheduled = rule
                .previous(now)
                .filter(|latest| *latest >= scheduled)
                .unwrap_or(scheduled);
        }
        let scheduled = iso(scheduled);
        let skipped = missed && plan.definition["policies"]["missed"] == "skip";
        let key = format!("scheduled:{HOST}:{}:{}:{scheduled}", plan.id, plan.revision);
        let next = rule.next(now);
        self.transact(|db| {
            if next.is_none() {
                // The occurrence retains the definition it was claimed with.
                let mut finished = plan.definition.clone();
                finished["enabled"] = json!(false);
                let changed = db.execute("UPDATE plans SET next_at=NULL,enabled=0,definition=? \
                    WHERE username=? AND id=? AND revision=? AND next_at=? AND enabled=1 AND deleted_at IS NULL",
                    params![finished.to_string(), HOST, plan.id, plan.revision, expected_next])?;
                if changed == 0 { return Ok(()); }
            } else {
                let changed = db.execute("UPDATE plans SET next_at=? WHERE username=? AND id=? AND revision=? AND next_at=? AND enabled=1 AND deleted_at IS NULL",
                    params![next.map(iso), HOST, plan.id, plan.revision, expected_next])?;
                if changed == 0 { return Ok(()); }
            }
            insert_run(db, &plan, NewRun { scheduled: &scheduled, trigger: "schedule", key: &key,
                state: if skipped { RunState::Skipped } else { RunState::Queued }, reason: skipped.then_some("missed"), now: &now_text })?;
            Ok(())
        })
    }

    pub fn runs(&self, id: &str) -> Result<Vec<Run>> {
        collect(
            &self.connection()?,
            "SELECT * FROM runs WHERE username=? AND schedule_id=? ORDER BY created_at DESC,id DESC LIMIT 100",
            params![HOST, id],
            read_run,
        )
    }

    pub fn latest(&self, id: &str, active_only: bool) -> Result<Option<Run>> {
        let filter = if active_only {
            " AND state IN ('queued','starting','running','waiting','stopping')"
        } else {
            ""
        };
        Ok(self.connection()?.query_row(&format!("SELECT * FROM runs WHERE username=? AND schedule_id=?{filter} ORDER BY created_at DESC,id DESC LIMIT 1"),
            params![HOST, id], read_run).optional()?)
    }

    pub fn active(&self) -> Result<Vec<Run>> {
        collect(
            &self.connection()?,
            &format!(
                "SELECT * FROM runs WHERE username=? AND {ACTIVE} ORDER BY created_at DESC,id DESC LIMIT 1000"
            ),
            [HOST],
            read_run,
        )
    }

    pub fn queued(&self) -> Result<Vec<Run>> {
        collect(
            &self.connection()?,
            "SELECT * FROM runs WHERE username=? AND state='queued' ORDER BY created_at DESC,id DESC",
            [HOST],
            read_run,
        )
    }

    pub fn stop(&self, id: &str, run_id: &str, now: Timestamp) -> Result<Run> {
        self.transact(|db| {
            get_plan(db, id, false)?;
            let run = get_run(db, run_id)?;
            if run.schedule_id != id {
                return Err(ScheduleError::invalid("运行不属于此计划"));
            }
            if run.state == RunState::Queued {
                db.execute("UPDATE runs SET state='stopped',reason='user_stopped',finished_at=?,stop_requested=1 WHERE username=? AND id=?", params![iso(now), HOST, run_id])?;
            } else if run.state.active() {
                db.execute("UPDATE runs SET stop_requested=1 WHERE username=? AND id=?", params![HOST, run_id])?;
            }
            get_run(db, run_id)
        })
    }

    /// Puts the occurrence in starting before calling the runtime, protected by the owner lease.
    pub fn begin(&self, run: &Run, now: Timestamp) -> Result<Begin> {
        self.transact(|db| {
            let current = get_run(db, &run.id)?;
            if current.state != RunState::Queued {
                return Ok(Begin::Ignored);
            }
            let active = db.query_row(&format!("SELECT EXISTS(SELECT 1 FROM runs WHERE username=? AND schedule_id=? AND {ACTIVE})"),
                params![HOST, run.schedule_id], |row| row.get(0))?;
            if active {
                finish_busy(db, &current, "previous_occurrence_running", now)?;
                return Ok(Begin::Ignored);
            }
            db.execute("UPDATE runs SET state='starting',started_at=? WHERE username=? AND id=? AND state='queued'", params![iso(now), HOST, run.id])?;
            Ok(Begin::Dispatch)
        })
    }

    pub fn merged(&self, run: &Run, now: Timestamp) -> Result<()> {
        self.connection()?.execute(
            "UPDATE runs SET state='skipped',reason='merged_into_latest',finished_at=? \
            WHERE username=? AND id=? AND state='queued'",
            params![iso(now), HOST, run.id],
        )?;
        Ok(())
    }

    pub fn busy(&self, run: &Run, reason: &str, now: Timestamp) -> Result<()> {
        self.transact(|db| {
            let current = get_run(db, &run.id)?;
            if current.state == RunState::Starting {
                finish_busy(db, &current, reason, now)?;
            }
            Ok(())
        })
    }

    pub fn admitted(&self, run: &Run, admission: &Admission) -> Result<bool> {
        Ok(self.connection()?.execute(
            "UPDATE runs SET state='running',reason=NULL,task_id=?,conversation_id=? \
            WHERE username=? AND id=? AND state='starting'",
            params![admission.task_id, admission.conversation_id, HOST, run.id],
        )? == 1)
    }

    pub fn defer(&self, run: &Run, reason: &str, now: Timestamp) -> Result<()> {
        self.transact(|db| {
            let current = get_run(db, &run.id)?;
            if current.state == RunState::Starting {
                defer_start(db, &current, reason, &iso(now))?;
            }
            Ok(())
        })
    }

    pub fn observe(&self, run: &Run, update: RunUpdate, now: Timestamp) -> Result<()> {
        self.transact(|db| {
            let current = get_run(db, &run.id)?;
            if !current.state.active() {
                return Ok(());
            }
            let RunUpdate { mut state, task_id, mut reason, summary, waiting_since, timeout } = update;
            let stop = current.stop_requested || timeout;
            if timeout || current.reason.as_deref() == Some("approval_timeout") {
                reason = Some("approval_timeout".into());
            }
            if stop {
                if state.terminal() {
                    (state, reason) = if reason.as_deref() == Some("approval_timeout") {
                        (RunState::Failed, Some("approval_timeout".into()))
                    } else { (RunState::Stopped, Some("user_stopped".into())) };
                } else {
                    state = RunState::Stopping;
                }
            }
            db.execute("UPDATE runs SET state=?,task_id=COALESCE(?,task_id),reason=?,summary=COALESCE(?,summary),\
                waiting_since=?,stop_requested=?,finished_at=? WHERE username=? AND id=?",
                params![state.as_str(), task_id, reason, summary, waiting_since, stop, state.terminal().then(|| iso(now)), HOST, run.id])?;
            Ok(())
        })
    }

    pub fn recover(&self, now: Timestamp) -> Result<()> {
        self.connection()?.execute(&format!("UPDATE runs SET state='interrupted',reason='process_restarted',finished_at=? WHERE username=? AND {ACTIVE}"),
            params![iso(now), HOST])?;
        Ok(())
    }

    pub fn prepare_notifications(&self) -> Result<()> {
        self.transact(|db| {
            let runs = collect(db, &format!("SELECT * FROM runs WHERE username=? AND notification_status='pending' AND {TERMINAL} ORDER BY created_at LIMIT 100"), [HOST], read_run)?;
            for run in runs {
                let status = if run.definition["notifications"]["enabled"] == true { "available" } else { "disabled" };
                db.execute("UPDATE runs SET notification_status=?,notification_error=NULL WHERE username=? AND id=? AND notification_status='pending'",
                    params![status, HOST, run.id])?;
            }
            Ok(())
        })
    }

    pub fn claim_notifications(&self, now: Timestamp) -> Result<Vec<Value>> {
        self.transact(|db| {
            let reoffer_at = Timestamp::from_nanosecond(now.as_nanosecond() - 120_000_000_000).map(iso).unwrap_or_else(|_| iso(now));
            db.execute("UPDATE runs SET notification_status='available',notification_token=NULL \
                WHERE username=? AND notification_status='delivering' AND notification_claimed_at<?", params![HOST, reoffer_at])?;
            let rows = collect(db, "SELECT * FROM runs WHERE username=? AND notification_status='available' ORDER BY created_at LIMIT 10", [HOST], read_run)?;
            let mut result = Vec::new();
            for run in rows {
                let token = Uuid::new_v4().simple().to_string();
                db.execute("UPDATE runs SET notification_status='delivering',notification_token=?,notification_claimed_at=? WHERE username=? AND id=?",
                    params![token, iso(now), HOST, run.id])?;
                let mut public = run.public(false);
                public["token"] = json!(token);
                public["name"] = run.definition["name"].clone();
                public["workspace_id"] = run.definition["target"]["workspace_id"].clone();
                result.push(public);
            }
            Ok(result)
        })
    }

    pub fn ack_notification(&self, run_id: &str, token: &str, error: Option<&str>) -> Result<bool> {
        let status = if error.is_some() {
            "failed"
        } else {
            "delivered"
        };
        Ok(self.connection()?.execute("UPDATE runs SET notification_status=?,notification_error=?,notification_token=NULL \
            WHERE id=? AND username=? AND notification_token=? AND notification_status='delivering'",
            params![status, error, run_id, HOST, token])? == 1)
    }
}

pub(super) enum Begin {
    Dispatch,
    Ignored,
}

pub(super) struct RunUpdate {
    pub state: RunState,
    pub task_id: Option<String>,
    pub reason: Option<String>,
    pub summary: Option<String>,
    pub waiting_since: Option<String>,
    pub timeout: bool,
}

fn finish_busy(db: &Connection, run: &Run, reason: &str, now: Timestamp) -> Result<()> {
    let now = iso(now);
    let terminal = |state: RunState, reason: &str| {
        db.execute(
            "UPDATE runs SET state=?,reason=?,finished_at=? WHERE username=? AND id=?",
            params![state.as_str(), reason, now, HOST, run.id],
        )
    };
    if run.stop_requested {
        terminal(RunState::Stopped, "user_stopped")?;
    } else if run.definition["policies"]["busy"] != "merge" {
        terminal(RunState::Skipped, reason)?;
    } else if defer_start(db, run, reason, &now)? {
        db.execute("UPDATE runs SET state='skipped',reason='merged_into_latest',finished_at=? WHERE username=? AND schedule_id=? \
            AND state='queued' AND (created_at,id)<(?,?)", params![now, HOST, run.schedule_id, run.created_at, run.id])?;
    }
    Ok(())
}

fn defer_start(db: &Connection, run: &Run, reason: &str, now: &str) -> Result<bool> {
    let plan = get_plan(db, &run.schedule_id, true)?;
    let terminal = if run.stop_requested {
        Some((RunState::Stopped, "user_stopped"))
    } else if plan.deleted_at.is_some() {
        Some((RunState::Skipped, "plan_deleted"))
    } else if !plan.enabled && plan.revision != run.revision {
        Some((RunState::Skipped, "plan_paused"))
    } else {
        None
    };
    if let Some((state, reason)) = terminal {
        db.execute(
            "UPDATE runs SET state=?,reason=?,finished_at=? WHERE username=? AND id=?",
            params![state.as_str(), reason, now, HOST, run.id],
        )?;
        return Ok(false);
    }
    db.execute(
        "UPDATE runs SET state='queued',reason=?,started_at=NULL WHERE username=? AND id=?",
        params![reason, HOST, run.id],
    )?;
    Ok(true)
}

fn increment_revision(revision: i64) -> Result<i64> {
    revision
        .checked_add(1)
        .filter(|&next| next <= 9_007_199_254_740_991)
        .ok_or_else(|| ScheduleError::conflict("计划版本不能再递增"))
}

fn skip_queued(db: &Connection, id: &str, reason: &str, now: &str) -> Result<()> {
    db.execute("UPDATE runs SET state='skipped',reason=?,finished_at=? WHERE username=? AND schedule_id=? AND state='queued'", params![reason, now, HOST, id])?;
    Ok(())
}

struct NewRun<'a> {
    scheduled: &'a str,
    trigger: &'a str,
    key: &'a str,
    state: RunState,
    reason: Option<&'a str>,
    now: &'a str,
}

fn insert_run(db: &Connection, plan: &Plan, args: NewRun<'_>) -> Result<()> {
    let id = format!("run_{}", Uuid::new_v4().simple());
    db.execute("INSERT INTO runs(id,username,schedule_id,revision,scheduled_for,trigger,occurrence_key,state,definition,principal,created_at,reason,finished_at) \
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(occurrence_key) DO NOTHING",
        params![id, HOST, plan.id, plan.revision, args.scheduled, args.trigger, args.key, args.state.as_str(), plan.definition.to_string(),
            plan.principal.to_string(), args.now, args.reason, args.state.terminal().then_some(args.now)])?;
    Ok(())
}

fn find_plan(db: &Connection, id: &str, deleted: bool) -> Result<Option<Plan>> {
    let selector = if deleted {
        ""
    } else {
        " AND deleted_at IS NULL"
    };
    Ok(db
        .query_row(
            &format!("SELECT * FROM plans WHERE username=? AND id=?{selector}"),
            params![HOST, id],
            read_plan,
        )
        .optional()?)
}

fn get_plan(db: &Connection, id: &str, deleted: bool) -> Result<Plan> {
    find_plan(db, id, deleted)?.ok_or_else(|| ScheduleError::missing("计划不存在"))
}

fn get_run(db: &Connection, id: &str) -> Result<Run> {
    db.query_row(
        "SELECT * FROM runs WHERE username=? AND id=?",
        params![HOST, id],
        read_run,
    )
    .optional()?
    .ok_or_else(|| ScheduleError::missing("运行记录不存在"))
}

fn read_plan(row: &Row<'_>) -> rusqlite::Result<Plan> {
    Ok(Plan {
        id: row.get("id")?,
        revision: row.get("revision")?,
        definition: read_json(row, "definition")?,
        principal: read_json(row, "principal")?,
        enabled: row.get("enabled")?,
        next_at: row.get("next_at")?,
        created_at: row.get("created_at")?,
        updated_at: row.get("updated_at")?,
        deleted_at: row.get("deleted_at")?,
    })
}

fn read_run(row: &Row<'_>) -> rusqlite::Result<Run> {
    let state: String = row.get("state")?;
    let state = RunState::parse(&state).map_err(|error| invalid_column(row, "state", error))?;
    Ok(Run {
        id: row.get("id")?,
        schedule_id: row.get("schedule_id")?,
        revision: row.get("revision")?,
        scheduled_for: row.get("scheduled_for")?,
        trigger: row.get("trigger")?,
        occurrence_key: row.get("occurrence_key")?,
        state,
        definition: read_json(row, "definition")?,
        principal: read_json(row, "principal")?,
        task_id: row.get("task_id")?,
        conversation_id: row.get("conversation_id")?,
        reason: row.get("reason")?,
        summary: row.get("summary")?,
        created_at: row.get("created_at")?,
        started_at: row.get("started_at")?,
        finished_at: row.get("finished_at")?,
        waiting_since: row.get("waiting_since")?,
        stop_requested: row.get("stop_requested")?,
        notification_status: row.get("notification_status")?,
        notification_error: row.get("notification_error")?,
    })
}

fn read_json(row: &Row<'_>, field: &str) -> rusqlite::Result<Value> {
    let source: String = row.get(field)?;
    serde_json::from_str(&source).map_err(|error| invalid_column(row, field, error))
}

fn invalid_column(
    row: &Row<'_>,
    field: &str,
    error: impl std::error::Error + Send + Sync + 'static,
) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        row.as_ref().column_index(field).unwrap_or_default(),
        Type::Text,
        Box::new(error),
    )
}

fn collect<T>(
    db: &Connection,
    query: &str,
    params: impl rusqlite::Params,
    read: impl FnMut(&Row<'_>) -> rusqlite::Result<T>,
) -> Result<Vec<T>> {
    let mut statement = db.prepare(query)?;
    Ok(statement
        .query_map(params, read)?
        .collect::<rusqlite::Result<_>>()?)
}
