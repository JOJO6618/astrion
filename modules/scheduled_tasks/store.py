"""Transactional schedule/occurrence storage. No execution while holding a DB lock."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .clock import instant, iso, next_occurrence, previous_occurrence, utc_now

TERMINAL_STATES = {"success", "failed", "skipped", "stopped", "interrupted"}
ACTIVE_STATES = {"starting", "running", "waiting", "stopping"}


class ConflictError(ValueError):
    pass


class ScheduleStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS plans (
                    username TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                    definition TEXT NOT NULL, principal TEXT NOT NULL, source TEXT NOT NULL,
                    enabled INTEGER NOT NULL, next_at TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, deleted_at TEXT,
                    PRIMARY KEY(username,id));
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL, schedule_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, scheduled_for TEXT NOT NULL, trigger TEXT NOT NULL,
                    occurrence_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
                    definition TEXT NOT NULL, principal TEXT NOT NULL,
                    task_id TEXT, conversation_id TEXT, reason TEXT, summary TEXT,
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                    waiting_since TEXT, stop_requested INTEGER NOT NULL DEFAULT 0,
                    notification_status TEXT NOT NULL DEFAULT 'pending', notification_error TEXT);
                CREATE INDEX IF NOT EXISTS plans_due ON plans(enabled,next_at);
                CREATE INDEX IF NOT EXISTS runs_owner ON runs(username,schedule_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS runs_state ON runs(state);
            """)
        with self.connection() as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(runs)')}
            for name in ('notification_token', 'notification_claimed_at'):
                if name not in columns:
                    db.execute(f'ALTER TABLE runs ADD COLUMN {name} TEXT')
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _plan(row):
        if row is None:
            raise KeyError("计划不存在")
        result = dict(row)
        for key in ("definition", "principal", "source"):
            result[key] = json.loads(result[key])
        result["enabled"] = bool(result["enabled"])
        return result

    @staticmethod
    def _run(row):
        if row is None:
            raise KeyError("运行记录不存在")
        result = dict(row)
        for key in ("definition", "principal"):
            result[key] = json.loads(result[key])
        result["stop_requested"] = bool(result["stop_requested"])
        return result

    def get(self, username: str, schedule_id: str, *, deleted=False) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM plans WHERE username=? AND id=?" + ("" if deleted else " AND deleted_at IS NULL"), (username, schedule_id)).fetchone()
        return self._plan(row)

    def list(self, username: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM plans WHERE username=? AND deleted_at IS NULL ORDER BY created_at DESC,id", (username,)).fetchall()
        return [self._plan(row) for row in rows]

    def put(self, username: str, definition: dict, principal: dict, source: dict, *, overwrite=False, now=None) -> dict:
        now = now or utc_now()
        schedule_id = definition.get("id") or f"schedule_{uuid.uuid4().hex}"
        with self.transaction() as db:
            old = db.execute("SELECT * FROM plans WHERE username=? AND id=?", (username, schedule_id)).fetchone()
            if old:
                if not overwrite or old["deleted_at"]:
                    raise ConflictError("计划 ID 已存在；覆盖必须显式指定 overwrite")
                if definition.get("revision") != old["revision"]:
                    raise ConflictError("计划版本已更新，请重新读取后修改")
                revision, created = old["revision"] + 1, old["created_at"]
            else:
                if overwrite:
                    raise KeyError("不能覆盖不存在的计划")
                revision, created = 1, iso(now)
            definition = {**definition, "id": schedule_id, "revision": revision}
            next_at = next_occurrence(definition["schedule"], now) if definition["enabled"] else None
            if definition["enabled"] and next_at is None:
                raise ValueError("此时间规则没有未来执行时间，请调整时间或保存为停止状态")
            db.execute("""INSERT INTO plans(username,id,revision,definition,principal,source,enabled,next_at,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(username,id) DO UPDATE SET
                          revision=excluded.revision,definition=excluded.definition,principal=excluded.principal,
                          source=excluded.source,enabled=excluded.enabled,next_at=excluded.next_at,updated_at=excluded.updated_at""",
                       (username, schedule_id, revision, json.dumps(definition, ensure_ascii=False), json.dumps(principal), json.dumps(source, ensure_ascii=False), int(definition["enabled"]), iso(next_at), created, iso(now)))
        return self.get(username, schedule_id)

    def enable(self, username: str, schedule_id: str, enabled: bool, revision: int | None = None) -> dict:
        now = utc_now()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM plans WHERE username=? AND id=? AND deleted_at IS NULL", (username, schedule_id)).fetchone()
            plan = self._plan(row)
            if revision is not None and plan["revision"] != revision:
                raise ConflictError("计划版本已更新")
            definition = plan["definition"]
            definition["revision"] += 1
            definition["enabled"] = enabled
            next_at = next_occurrence(definition["schedule"], now) if enabled else None
            if enabled and next_at is None:
                raise ValueError("此计划没有未来执行时间")
            db.execute("UPDATE plans SET enabled=?,revision=?,definition=?,next_at=?,updated_at=? WHERE username=? AND id=?",
                       (int(enabled), definition["revision"], json.dumps(definition, ensure_ascii=False), iso(next_at), iso(now), username, schedule_id))
            if not enabled:
                db.execute("UPDATE runs SET state='skipped',reason='plan_paused',finished_at=? WHERE username=? AND schedule_id=? AND state='queued'", (iso(now), username, schedule_id))
        return self.get(username, schedule_id)

    def delete(self, username: str, schedule_id: str):
        now = iso(utc_now())
        with self.transaction() as db:
            changed = db.execute("UPDATE plans SET deleted_at=?,enabled=0,next_at=NULL WHERE username=? AND id=? AND deleted_at IS NULL", (now, username, schedule_id)).rowcount
            if not changed:
                raise KeyError("计划不存在")
            db.execute("UPDATE runs SET state='skipped',reason='plan_deleted',finished_at=? WHERE username=? AND schedule_id=? AND state='queued'", (now, username, schedule_id))

    def _insert_run(self, db, plan: dict, scheduled_for: datetime, trigger: str, key: str, state="queued", reason=None, now=None) -> str | None:
        run_id, created = f"run_{uuid.uuid4().hex}", iso(now or utc_now())
        changed = db.execute("""INSERT OR IGNORE INTO runs(id,username,schedule_id,revision,scheduled_for,trigger,
                     occurrence_key,state,definition,principal,created_at,reason,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (run_id, plan["username"], plan["id"], plan["revision"], iso(scheduled_for), trigger, key, state,
                    json.dumps(plan["definition"], ensure_ascii=False), json.dumps(plan["principal"]), created, reason,
                    created if state in TERMINAL_STATES else None)).rowcount
        return run_id if changed else None

    def manual_run(self, username: str, schedule_id: str, request_id: str) -> dict:
        with self.transaction() as db:
            plan = self._plan(db.execute("SELECT * FROM plans WHERE username=? AND id=? AND deleted_at IS NULL", (username, schedule_id)).fetchone())
            key = f"manual:{username}:{schedule_id}:{request_id}"
            self._insert_run(db, plan, utc_now(), "manual", key)
            row = db.execute("SELECT * FROM runs WHERE occurrence_key=?", (key,)).fetchone()
        return self._run(row)

    def claim_due(self, now: datetime | None = None, grace_seconds=30) -> list[dict]:
        now = now or utc_now()
        claimed = []
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM plans WHERE enabled=1 AND deleted_at IS NULL AND next_at<=?", (iso(now),)).fetchall()
            for row in rows:
                plan = self._plan(row)
                definition = plan["definition"]
                scheduled = instant(plan["next_at"])
                missed = (now - scheduled).total_seconds() > grace_seconds
                if missed:
                    scheduled = previous_occurrence(definition["schedule"], now) or scheduled
                skip = missed and definition["policies"]["missed"] == "skip"
                state, reason = ("skipped", "missed") if skip else ("queued", None)
                key = f"scheduled:{plan['username']}:{plan['id']}:{plan['revision']}:{iso(scheduled)}"
                run_id = self._insert_run(db, plan, scheduled, "schedule", key, state, reason, now)
                next_at = next_occurrence(definition["schedule"], now)
                if next_at is None:
                    definition["enabled"] = False
                db.execute("UPDATE plans SET next_at=?,enabled=?,definition=? WHERE username=? AND id=?",
                           (iso(next_at), int(definition["enabled"]), json.dumps(definition, ensure_ascii=False), plan["username"], plan["id"]))
                if run_id:
                    claimed.append(run_id)
        return [self.run(run_id) for run_id in claimed]

    def run(self, run_id: str, username: str | None = None) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?" + (" AND username=?" if username is not None else ""), (run_id, username) if username is not None else (run_id,)).fetchone()
        return self._run(row)

    def runs(self, username: str | None = None, schedule_id: str | None = None, states: set[str] | None = None, limit=100) -> list[dict]:
        conditions, params = [], []
        for key, value in (("username", username), ("schedule_id", schedule_id)):
            if value is not None:
                conditions.append(f"{key}=?")
                params.append(value)
        if states:
            conditions.append("state IN (" + ",".join("?" for _ in states) + ")")
            params.extend(sorted(states))
        params.append(min(max(int(limit), 1), 1000))
        with self.connection() as db:
            rows = db.execute("SELECT * FROM runs" + (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY created_at DESC,id DESC LIMIT ?", params).fetchall()
        return [self._run(row) for row in rows]

    def update_run(self, run_id: str, *, expected: set[str] | None = None, **values) -> bool:
        allowed = {"state", "task_id", "conversation_id", "reason", "summary", "started_at", "finished_at", "waiting_since", "stop_requested", "notification_status", "notification_error"}
        if not values or set(values) - allowed:
            raise ValueError("invalid occurrence update")
        conditions, params = ["id=?"], [run_id]
        if expected:
            conditions.append("state IN (" + ",".join("?" for _ in expected) + ")")
            params.extend(sorted(expected))
        with self.transaction() as db:
            changed = db.execute("UPDATE runs SET " + ",".join(f"{key}=?" for key in values) + " WHERE " + " AND ".join(conditions), [*values.values(), *params]).rowcount
        return bool(changed)

    def pending_notifications(self) -> list[dict]:
        states = sorted(TERMINAL_STATES)
        with self.connection() as db:
            rows = db.execute("SELECT * FROM runs WHERE notification_status='pending' AND state IN (" + ",".join("?" for _ in states) + ") ORDER BY created_at LIMIT 100", states).fetchall()
        return [self._run(row) for row in rows]

    def claim_notifications(self, username: str, *, now=None) -> list[dict]:
        now = now or utc_now()
        with self.transaction() as db:
            db.execute("UPDATE runs SET notification_status='available',notification_token=NULL WHERE username=? AND notification_status='delivering' AND notification_claimed_at<?",
                       (username, iso(now - timedelta(minutes=2))))
            rows = db.execute("SELECT * FROM runs WHERE username=? AND notification_status='available' ORDER BY created_at LIMIT 10", (username,)).fetchall()
            result = []
            for row in rows:
                token = uuid.uuid4().hex
                db.execute("UPDATE runs SET notification_status='delivering',notification_token=?,notification_claimed_at=? WHERE id=?", (token, iso(now), row['id']))
                item = self._run(row)
                item['notification_token'] = token
                result.append(item)
        return result

    def ack_notification(self, username: str, run_id: str, token: str, *, error=None) -> bool:
        with self.transaction() as db:
            return bool(db.execute("UPDATE runs SET notification_status=?,notification_error=?,notification_token=NULL WHERE id=? AND username=? AND notification_token=? AND notification_status='delivering'",
                                   ('failed' if error else 'delivered', error, run_id, username, token)).rowcount)

    def recover(self):
        now = iso(utc_now())
        with self.transaction() as db:
            db.execute("UPDATE runs SET state='interrupted',reason='process_restarted',finished_at=? WHERE state IN ('starting','running','waiting','stopping')", (now,))
