"""Conversation-scoped, versioned goal lifecycle. Disk is the source of truth."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Union

GOAL_STATES_DIRNAME = "goal_states"
PROCESS_ID = uuid.uuid4().hex
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_STOPPED = "stopped"
ACTIVE_STATUSES = {"running", "reviewing", "retrying"}
RESUMABLE_STATUSES = {"paused", "blocked"}
REASON_IDLE_NO_TOOL = "idle_no_tool"
REASON_MAX_TOKENS = "max_tokens"
REASON_MAX_TURNS = "max_turns"
REASON_USER_CANCEL = "user_cancel"
REVIEW_MODE_READONLY = "readonly"
REVIEW_MODE_ACTIVE = "active"
PathLike = Union[str, Path]
_SAFE_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCKS: Dict[str, Any] = {}
_LOCKS_LOCK = threading.Lock()


def _validate_conversation_id(conversation_id: str) -> str:
    cid = str(conversation_id or "").strip()
    if not cid or not _SAFE_CONVERSATION_ID.fullmatch(cid):
        raise ValueError("Invalid conversation_id")
    return cid


def _empty_state() -> Dict[str, Any]:
    return {"version": 2, "revision": 0, "run_id": None, "process_id": None,
            "active": False, "status": "stopped", "goal": "", "review_mode": "readonly",
            "max_turns": None, "max_tokens": None, "started_at": None, "updated_at": None,
            "turn_count": 0, "tokens_used": 0, "tool_calls_used": 0,
            "review_history": [], "final_summary": None, "stopped_reason": None,
            "last_message": "", "retry_count": 0, "next_retry_at": None,
            "task_id": None, "no_progress_count": 0, "last_review_signature": ""}


class GoalStateManager:
    """Shared locks protect complete read/modify/write transactions.

    Process identity prevents automatic restart recovery. Run identity invalidates
    late reviews and retry callbacks when a goal is closed or replaced.
    """

    def __init__(self, data_dir: PathLike, conversation_id: str):
        self.data_dir = Path(data_dir).expanduser()
        self.conversation_id = _validate_conversation_id(conversation_id)
        with _LOCKS_LOCK:
            self.lock = _LOCKS.setdefault(str(self._path().absolute()), threading.RLock())
        self.state = self.load()

    def _path(self) -> Path:
        return self.data_dir / GOAL_STATES_DIRNAME / f"{self.conversation_id}.json"

    @classmethod
    def load_from(cls, data_dir: PathLike, conversation_id: str) -> "GoalStateManager":
        return cls(data_dir, conversation_id)

    def _read(self) -> Dict[str, Any]:
        path = self._path()
        if not path.exists():
            return _empty_state()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Invalid goal state")
            state = {**_empty_state(), **raw}
            if not isinstance(state["review_history"], list):
                state["review_history"] = []
            return state
        except (OSError, ValueError):
            return {**_empty_state(), "status": "blocked", "stopped_reason": "state_unreadable",
                    "last_message": "目标状态文件无法读取，请检查文件后重新开始。"}

    def load(self) -> Dict[str, Any]:
        with self.lock:
            self.state = self._read()
            if (self.state["status"] in ACTIVE_STATUSES
                    and self.state.get("process_id") != PROCESS_ID):
                self.state.update(status="paused", active=False, next_retry_at=None,
                                  version=2, run_id=self.state.get("run_id") or uuid.uuid4().hex,
                                  stopped_reason="process_restarted", task_id=None,
                                  last_message="应用已重启，点击继续以恢复目标。")
                self._write()
            return deepcopy(self.state)

    def _write(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.state["revision"] = int(self.state.get("revision") or 0) + 1
        self.state["updated_at"] = time.time()
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def save(self) -> None:
        """Reject stale snapshots instead of overwriting a later lifecycle change."""
        with self.lock:
            if self._read().get("revision") != self.state.get("revision"):
                raise RuntimeError("Goal state changed")
            self._write()

    def update(self, *, run_id: Optional[str] = None, statuses=None, **values) -> bool:
        with self.lock:
            self.load()
            if run_id is not None and self.state.get("run_id") != run_id:
                return False
            if statuses is not None and self.state["status"] not in statuses:
                return False
            self.state.update(values)
            self.state["active"] = self.state["status"] in ACTIVE_STATUSES
            self._write()
            return True

    def start(self, *, goal: str, review_mode: str = "readonly", max_turns=None,
              max_tokens=None, token_baseline=None, tool_call_baseline=0,
              task_id=None) -> Dict[str, Any]:
        text = str(goal or "").strip()
        if not text:
            raise ValueError("目标不能为空")
        with self.lock:
            self.load()
            if self.state["status"] in ACTIVE_STATUSES | RESUMABLE_STATUSES:
                raise RuntimeError("已有目标，请继续或关闭后再开始")
            self.state = {**_empty_state(), "revision": self.state["revision"],
                          "active": True, "status": "running", "goal": text,
                          "run_id": uuid.uuid4().hex, "process_id": PROCESS_ID,
                          "started_at": time.time(), "review_mode": review_mode,
                          "max_turns": max_turns, "max_tokens": max_tokens, "task_id": task_id}
            self._write()
            return deepcopy(self.state)

    def resume(self, *, task_id: str, limits: Optional[dict] = None) -> Dict[str, Any]:
        with self.lock:
            self.load()
            if self.state["status"] not in RESUMABLE_STATUSES:
                raise RuntimeError("目标当前不可继续")
            if limits is not None:
                self.state.update(max_turns=limits.get("max_turns"), max_tokens=limits.get("max_tokens"))
            self.state.update(turn_baseline=self.state["turn_count"],
                              token_limit_baseline=self.state["tokens_used"],
                              status="running", active=True, task_id=task_id,
                              process_id=PROCESS_ID, stopped_reason=None,
                              next_retry_at=None, retry_count=0, no_progress_count=0,
                              last_review_signature="", last_message="")
            self._write()
            return deepcopy(self.state)

    def is_active(self) -> bool:
        return self.load()["status"] in ACTIVE_STATUSES

    def get_goal(self) -> str:
        return str(self.state.get("goal") or "")

    def get_review_mode(self) -> str:
        return "active" if self.state.get("review_mode") == "active" else "readonly"

    def get_turn(self) -> int:
        return int(self.state.get("turn_count") or 0)

    def increment_turn(self) -> int:
        with self.lock:
            self.load()
            if self.state["status"] in ACTIVE_STATUSES:
                self.update(turn_count=self.get_turn() + 1)
            return self.get_turn()

    def append_review(self, main_output: str, review_reply: str) -> None:
        with self.lock:
            self.load()
            history = self.state["review_history"][-19:]
            history.append({"main_output": str(main_output)[-12000:],
                            "review_reply": str(review_reply)[-6000:]})
            self.update(review_history=history)

    def mark_done(self, summary: str, *, run_id=None) -> bool:
        return self.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="done",
                           final_summary=summary, last_message=summary,
                           stopped_reason=None, next_retry_at=None)

    def pause(self, reason: str = REASON_USER_CANCEL, *, message="", run_id=None) -> bool:
        return self.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="paused",
                           stopped_reason=reason, last_message=message, next_retry_at=None)

    def mark_stopped(self, reason: str) -> bool:
        if reason in {REASON_USER_CANCEL, REASON_MAX_TURNS, REASON_MAX_TOKENS}:
            return self.pause(reason)
        return self.update(statuses=ACTIVE_STATUSES | RESUMABLE_STATUSES, status="stopped",
                           stopped_reason=reason, next_retry_at=None)

    def clear(self) -> None:
        with self.lock:
            self.state = {**_empty_state(), "revision": self._read()["revision"]}
            self._write()

    def reached_max_turns(self) -> bool:
        limit = self.state.get("max_turns")
        return bool(limit and self.get_turn() - int(self.state.get("turn_baseline") or 0) >= int(limit))

    def reached_max_tokens(self, current_total_tokens=0) -> bool:
        limit = self.state.get("max_tokens")
        used = self.tokens_used() - int(self.state.get("token_limit_baseline") or 0)
        return bool(limit and used >= int(limit))

    def tokens_used(self, current_total_tokens=0) -> int:
        return max(0, int(self.state.get("tokens_used") or 0))

    def record_usage(self, request_id: str, total: int, *, run_id: str) -> None:
        with self.lock:
            self.load()
            seen = self.state.get("usage_request_ids") or []
            if request_id in seen:
                return
            self.update(run_id=run_id, statuses=ACTIVE_STATUSES,
                        tokens_used=self.tokens_used() + max(0, int(total)),
                        usage_request_ids=(seen + [request_id])[-512:])

    def build_review_payload_text(self, current_main_output: str) -> str:
        lines = ["【本次目标】", self.get_goal(), ""]
        for entry in self.state["review_history"][-6:]:
            lines.extend(["【此前执行总结】", str(entry.get("main_output") or ""),
                          "【此前审核】", str(entry.get("review_reply") or "")])
        lines.extend(["【当前待审核总结】", str(current_main_output or "")])
        return "\n".join(lines)

    def progress_snapshot(self, *, current_total_tokens=0, current_tool_calls=0) -> Dict[str, Any]:
        self.load()
        if not self.get_goal() and self.state["status"] != "blocked":
            return {}
        snap = deepcopy(self.state)
        snap.pop("usage_request_ids", None)
        snap.pop("review_history", None)
        snap.update(conversation_id=self.conversation_id,
                    duration_seconds=max(0, time.time() - float(self.state.get("started_at") or time.time())),
                    tool_calls=self.state.get("tool_calls_used", 0))
        return snap
