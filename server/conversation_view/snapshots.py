"""One display snapshot and its exact event cursor under a shared transaction."""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
from typing import Any
import threading
import time
import uuid

from .projector import RuntimeProjection

VIEW_LOCK = threading.RLock()
ACTIVE = {"pending", "running", "cancel_requested"}


def view_transaction(function):
    """Serialize task registration, projection publication and snapshot capture."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with VIEW_LOCK:
            return function(*args, **kwargs)
    return wrapped


def normalize_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("conv_")


class TaskViewMixin:
    @view_transaction
    def bind_display(self, rec, context_manager) -> None:
        """Freeze storage, not the possibly compressed model context, before output."""
        if not rec.display_valid:
            raise RuntimeError("Conversation display was invalidated before task binding")
        manager = context_manager._get_conversation_manager_for_id(rec.conversation_id)
        data = manager.read_conversation(rec.conversation_id)
        if not data:
            raise RuntimeError("Conversation display source is unavailable")
        preceding_ids = {
            (item.get("payload") or {}).get("message_id")
            for item in (rec.directives.preceding_user_notices or [])
            if isinstance(item, dict)
        } - {None, ""}
        rec.display_base = deepcopy([
            msg for msg in data.get("messages") or []
            if msg.get("message_id") not in preceding_ids
        ])
        rec.display_context = context_manager
        # Events may be published before resource binding (for example load errors).
        if rec.display_projection is None:
            rec.display_projection = RuntimeProjection(rec.task_id, rec.conversation_id, rec.model_key)
        rec.display_revision = str(uuid.uuid4())
        rec.trigger_message_id = rec.trigger_message_id or context_manager._generate_message_id()
        # Only the latest bound task owns a full display prefix for this view.
        with self._lock:
            for previous in self._tasks.values():
                if (previous is not rec and previous.username == rec.username
                        and previous.workspace_id == rec.workspace_id
                        and normalize_id(previous.conversation_id) == normalize_id(rec.conversation_id)):
                    previous.display_valid = False
                    previous.display_base = previous.display_projection = previous.display_context = None

    @view_transaction
    def finish_display_task(self, rec, status: str, event_type=None, data=None, error=None) -> None:
        """Publish a terminal task status and its final display as one transaction."""
        with self._lock:
            rec.status = status
            rec.updated_at = time.time()
            if error is not None:
                rec.error = error
        if event_type:
            self._append_event(rec, event_type, data or {})
        elif rec.display_projection is not None:
            rec.display_projection._close_streams()

    def publish_display(self, rec, event: dict) -> None:
        """Called inside the same VIEW_LOCK transaction as event append."""
        if not getattr(rec, "display_valid", True):
            return
        projection = getattr(rec, "display_projection", None)
        if projection is not None:
            projection.apply(event)

    @view_transaction
    def capture_display(self, username: str, conversation_id: str, manager, *, workspace_id: str) -> dict | None:
        """Capture only the requested owner's workspace, without replaying a deque."""
        data = manager.read_conversation(conversation_id)
        if not data:
            return None
        with self._lock:
            records = [
                rec for rec in self._tasks.values()
                if rec.username == username and rec.workspace_id == workspace_id
                and normalize_id(rec.conversation_id) == normalize_id(conversation_id)
                and getattr(rec, "display_valid", True)
            ]
            latest = max(records, key=lambda rec: rec.created_at, default=None)
            task = task_summary(latest) if latest is not None else None
            projected = {"messages": [], "state": {}}
            base = deepcopy(data.get("messages") or [])
            cursor = latest.next_event_idx if latest is not None else 0
            revision = latest.display_revision if latest is not None else None
            if latest is not None:
                if latest.display_projection is not None:
                    projected = latest.display_projection.snapshot()
                if latest.display_base is not None:
                    base = deepcopy(latest.display_base)
                elif latest.status in ACTIVE:
                    # Registration precedes resource binding. Keep the completed view
                    # until binding; the cursor still belongs to the new task.
                    predecessor = max((rec for rec in records if rec is not latest
                        and rec.display_base is not None and rec.display_projection is not None),
                        key=lambda rec: rec.created_at, default=None)
                    if predecessor is not None:
                        previous = predecessor.display_projection.snapshot()
                        base = deepcopy(predecessor.display_base)
                        projected["messages"] = previous["messages"] + projected["messages"]
        # Persisted metadata overlays identity only; content never participates.
        persisted = {msg.get("message_id"): msg for msg in data.get("messages", [])
                     if msg.get("message_id")}
        for msg in [*base, *projected["messages"]]:
            saved = persisted.get(msg.get("message_id"))
            if saved:
                msg["metadata"] = deepcopy(saved.get("metadata") or {})
                for key in ("images", "videos", "media_refs"):
                    if key in saved:
                        msg[key] = deepcopy(saved[key])
        return {"data": data, "messages": base, "display": {
            "revision": revision, "messages": projected["messages"],
            "state": projected["state"], "task": task, "next_event_idx": cursor,
        }}

    @view_transaction
    def rebase_display(self, rec) -> None:
        """A committed compression changes the prefix and the snapshot epoch."""
        context = getattr(rec, "display_context", None)
        if context is None or not rec.display_valid:
            return
        manager = context._get_conversation_manager_for_id(rec.conversation_id)
        data = manager.read_conversation(rec.conversation_id)
        if data:
            state = rec.display_projection.snapshot()["state"] if rec.display_projection else None
            rec.display_base = deepcopy(data.get("messages") or [])
            rec.display_projection = RuntimeProjection(rec.task_id, rec.conversation_id, rec.model_key)
            if state:
                rec.display_projection.state.update(state)
                rec.display_projection.state.update(streaming=False, api_request_pending=False)
            rec.display_revision = str(uuid.uuid4())

    @view_transaction
    def invalidate_display(self, conversation_id: str, *, username: str, workspace_id: str) -> None:
        """Checkpoint restore retires epochs only in the restored owner/workspace."""
        with self._lock:
            for rec in self._tasks.values():
                if (rec.username == username and rec.workspace_id == workspace_id
                        and normalize_id(rec.conversation_id) == normalize_id(conversation_id)):
                    rec.display_valid = False

    @view_transaction
    def capture_event_window(self, rec, offset: int) -> dict:
        with self._lock:
            events = [deepcopy(event) for event in rec.events if event["idx"] >= offset]
            return {"events": events,
                "next_offset": events[-1]["idx"] + 1 if events else rec.next_event_idx,
                "window_start": rec.events[0]["idx"] if rec.events else rec.next_event_idx,
                "status": rec.status, "updated_at": rec.updated_at}


def task_summary(rec) -> dict:
    return {"task_id": rec.task_id, "conversation_id": rec.conversation_id,
        "workspace_id": rec.workspace_id, "status": rec.status,
        "created_at": rec.created_at, "updated_at": rec.updated_at,
        "task_type": rec.task_type, "goal_mode": bool(rec.task_params.goal_mode),
        "goal_progress": deepcopy(rec.goal_progress)}
