"""Single-owner scheduler, with a small injectable runtime adapter for testing."""
from __future__ import annotations

import atexit
import logging
import threading
import uuid
from datetime import timedelta
from pathlib import Path

from .clock import instant, iso, next_dates, utc_now
from .schema import normalize_definition, parse_definition
from .store import ACTIVE_STATES, TERMINAL_STATES, ScheduleStore

logger = logging.getLogger(__name__)


class BusyError(RuntimeError):
    pass


class SchedulerService:
    def __init__(self, store: ScheduleStore, adapter):
        self.store, self.adapter = store, adapter
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._tick_lock = threading.Lock()
        self._thread = None
        self._owner_file = None
        self.error = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return True
        lock = None
        try:
            path = self.store.path.with_suffix(".lock")
            lock = path.open("a+b")
            if __import__("os").name == "nt":
                import msvcrt
                if path.stat().st_size == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._owner_file = lock
            self.store.recover()
            self._thread = threading.Thread(target=self._loop, name="astrion-schedules", daemon=True)
            self._thread.start()
            atexit.register(self.stop)
            return True
        except OSError as exc:
            if lock:
                lock.close()
            self.error = str(exc)
            logger.error("schedule ownership unavailable: %s", exc)
            return False

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        if self._owner_file and not (self._thread and self._thread.is_alive()):
            self._owner_file.close()
            self._owner_file = None

    def wake(self):
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("schedule tick failed")
            self._wake.wait(1)
            self._wake.clear()

    def submit(self, username: str, value: dict | str, principal: dict, default_workspace: str | None, *, overwrite=False, source=None):
        if isinstance(value, str):
            value = parse_definition(value)
        if principal.get('username') != username:
            raise PermissionError('计划身份不匹配')
        previous = None
        if isinstance(value, dict) and value.get("id") and overwrite:
            old = self.store.get(username, value["id"])
            previous = old["definition"]
        definition = normalize_definition(value, default_workspace, previous=previous)
        if previous and definition['configuration'] == previous['configuration'] and definition['target'] == previous['target']:
            principal = {**principal, **({'configuration_snapshot': old['principal']['configuration_snapshot']} if 'configuration_snapshot' in old['principal'] else {})}
        resolved_principal = self.adapter.validate(definition, principal)
        result = self.store.put(username, definition, resolved_principal, source or {}, overwrite=overwrite)
        self.wake()
        return self.serialize_plan(result)

    def serialize_run(self, run: dict, *, detail=False):
        result = {key: value for key, value in run.items() if key not in {"principal", "username", "occurrence_key", "definition", "notification_token", "notification_claimed_at"}}
        if detail:
            result["definition"] = run["definition"]
        return result

    def serialize_plan(self, plan: dict):
        runs = self.store.runs(plan["username"], plan["id"], limit=1)
        active = self.store.runs(plan["username"], plan["id"], states=ACTIVE_STATES | {"queued"}, limit=1)
        return {"definition": plan["definition"], "next_at": plan["next_at"],
                "next_dates": next_dates(plan["definition"]["schedule"]),
                "created_at": plan["created_at"], "updated_at": plan["updated_at"],
                "last_run": self.serialize_run(runs[0]) if runs else None,
                "active_run": self.serialize_run(active[0]) if active else None}

    def list(self, username: str, workspace_id=None, enabled=None):
        return [self.serialize_plan(plan) for plan in self.store.list(username)
                if (workspace_id is None or plan["definition"]["target"]["workspace_id"] == workspace_id)
                and (enabled is None or plan["enabled"] == enabled)]

    def manage(self, username: str, schedule_id: str, action: str, *, revision=None, run_id=None, request_id=None):
        plan = self.store.get(username, schedule_id)
        if action in {"enable", "pause"}:
            if action == "enable":
                definition = {**plan["definition"], "enabled": True}
                self.adapter.validate(definition, plan["principal"])
            result = self.serialize_plan(self.store.enable(username, schedule_id, action == "enable", revision))
        elif action == "delete":
            self.store.delete(username, schedule_id)
            result = {"id": schedule_id, "deleted": True}
        elif action == "run":
            result = self.serialize_run(self.store.manual_run(username, schedule_id, request_id or uuid.uuid4().hex))
        elif action == "stop":
            if not run_id:
                raise ValueError("停止运行需要明确 run_id")
            run = self.store.run(run_id, username)
            if run["schedule_id"] != schedule_id:
                raise ValueError("运行不属于此计划")
            if run["state"] == "queued":
                self.store.update_run(run_id, expected={"queued"}, state="stopped", reason="user_stopped", finished_at=iso(utc_now()), stop_requested=1)
            elif run["state"] in ACTIVE_STATES:
                self.store.update_run(run_id, expected=ACTIVE_STATES, stop_requested=1)
                self.adapter.cancel(run)
            result = self.serialize_run(self.store.run(run_id, username))
        else:
            raise ValueError("无效计划操作")
        self.wake()
        return result

    def tick(self, now=None):
        if not self._tick_lock.acquire(blocking=False):
            return
        try:
            now = now or utc_now()
            self.store.claim_due(now)
            for run in self.store.runs(states=ACTIVE_STATES, limit=1000):
                self._observe(run, now)
            newest = set()
            for run in self.store.runs(states={"queued"}, limit=1000):
                key = (run['username'], run['schedule_id'])
                if key in newest and run['definition']['policies']['busy'] == 'merge':
                    self.store.update_run(run['id'], expected={'queued'}, state='skipped', reason='merged_into_latest', finished_at=iso(now))
                    continue
                newest.add(key)
                if not self._stop.is_set():
                    self._start(run, now)
            for run in self.store.pending_notifications():
                self._notify(run)
        finally:
            self._tick_lock.release()

    def _start(self, run, now):
        if not self.store.update_run(run["id"], expected={"queued"}, state="starting", started_at=iso(now)):
            return
        admitted = None
        try:
            if any(other["id"] != run["id"] for other in self.store.runs(run["username"], run["schedule_id"], states=ACTIVE_STATES)):
                raise BusyError("previous_occurrence_running")
            result = admitted = self.adapter.start(run)
            if not self.store.update_run(run["id"], expected={"starting"}, state="running", reason=None,
                                         task_id=result["task_id"], conversation_id=result["conversation_id"]):
                raise RuntimeError('受理后的运行记录已失效')
        except BusyError as exc:
            if run["definition"]["policies"]["busy"] == "merge":
                self.store.update_run(run["id"], expected={"starting"}, state="queued", reason=str(exc), started_at=None)
                for other in self.store.runs(run["username"], run["schedule_id"], states={"queued"}):
                    if other["id"] != run["id"] and (other["created_at"], other["id"]) < (run["created_at"], run["id"]):
                        self.store.update_run(other["id"], expected={"queued"}, state="skipped", reason="merged_into_latest", finished_at=iso(now))
            else:
                self.store.update_run(run["id"], expected={"starting"}, state="skipped", reason=str(exc), finished_at=iso(now))
        except Exception as exc:
            logger.exception("schedule admission failed: %s", run["id"])
            if admitted:
                try:
                    self.adapter.cancel({**run, **admitted})
                except Exception:
                    logger.exception('schedule admission cancellation failed: %s', run['id'])
            self.store.update_run(run["id"], expected={"starting"}, state="failed", reason=str(exc), finished_at=iso(now))

    def _observe(self, run, now):
        try:
            if run["stop_requested"]:
                self.adapter.cancel(run)
            observed = self.adapter.observe(run)
            state = observed["state"]
            values = {key: observed[key] for key in ("summary", "reason", "task_id") if key in observed}
            if state == "waiting":
                since = instant(run["waiting_since"]) if run["waiting_since"] else now
                values["waiting_since"] = iso(since)
                timeout = run["definition"]["policies"]["approval_timeout_seconds"]
                if now - since >= timedelta(seconds=timeout):
                    self.store.update_run(run["id"], expected=ACTIVE_STATES, stop_requested=1, reason="approval_timeout")
                    self.adapter.cancel(run)
                    state = "stopping"
                    values["reason"] = "approval_timeout"
            elif run["waiting_since"]:
                values["waiting_since"] = None
            if run['stop_requested'] and state not in TERMINAL_STATES:
                state = 'stopping'
            if state in TERMINAL_STATES:
                if run.get("reason") == "approval_timeout":
                    state, values["reason"] = "failed", "approval_timeout"
                elif run["stop_requested"]:
                    state, values["reason"] = "stopped", "user_stopped"
                values["finished_at"] = iso(now)
            self.store.update_run(run["id"], expected=ACTIVE_STATES, state=state, **values)
        except (PermissionError, KeyError, ValueError) as exc:
            self.adapter.cancel(run)
            self.store.update_run(run['id'], expected=ACTIVE_STATES, state='interrupted', reason=str(exc), finished_at=iso(now))
        except Exception:
            # A temporary observation failure must not manufacture success or lose the run.
            logger.exception("schedule observation failed: %s", run["id"])

    def _notify(self, run):
        if not run["definition"]["notifications"]["enabled"]:
            self.store.update_run(run["id"], notification_status="disabled")
            return
        try:
            status = self.adapter.notify(run)
            self.store.update_run(run["id"], notification_status=status or "available", notification_error=None)
        except Exception as exc:
            logger.exception("schedule notification failed: %s", run["id"])
            self.store.update_run(run["id"], notification_status="failed", notification_error=str(exc))
