"""Read background state without constructing a terminal; recover on activation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import threading

from server.context.data import WorkspaceData
from server.context.resources import find_runtime, get_user_resources
from server.utils_common import debug_log

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="conversation-activation")
_LOCK = threading.RLock()
_PENDING: set[tuple[str, str, str, str]] = set()
_STATE_CACHE: dict[str, tuple[tuple, dict]] = {}
_TERMINAL = {"completed", "succeeded", "failed", "timeout", "cancelled", "canceled", "stopped", "terminated"}


def persisted_sub_agents(data: WorkspaceData) -> dict:
    from modules.sub_agent.persistence import STATE_FILENAME
    path = Path(data.workspace.data_dir) / STATE_FILENAME
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    revision = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
    with _LOCK:
        cached = _STATE_CACHE.get(str(path))
        if cached is not None and cached[0] == revision:
            return deepcopy(cached[1])
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Invalid sub-agent storage")
        _STATE_CACHE[str(path)] = (revision, raw)
        return deepcopy(raw)


def _activate(data: WorkspaceData, conversation_id: str, key):
    try:
        get_user_resources(data.username, workspace_id=data.workspace_id,
                           conversation_id=conversation_id, update_session=False,
                           identity=data.identity)
    except Exception as exc:
        debug_log(f"[ConversationActivation] {data.workspace_id}/{conversation_id}: {exc}")
    finally:
        with _LOCK:
            _PENDING.discard(key)


def background_status(data: WorkspaceData, conversation_id: str, *, activate=False) -> dict:
    from server.tasks import task_manager
    terminal = find_runtime(data.username, data.workspace, conversation_id)
    if terminal is not None:
        return task_manager.get_conversation_running_status(terminal, conversation_id)
    saved = persisted_sub_agents(data)
    result = {"has_running_sub_agents": False, "has_running_background_commands": False,
              "has_running_multi_agent": False}
    tasks = [task for task in (saved.get("tasks") or {}).values()
             if isinstance(task, dict) and task.get("conversation_id") == conversation_id]
    recoverable = False
    for task in tasks:
        status = task.get("status") or "running"
        if status in _TERMINAL:
            continue
        recoverable = True
        if status == "running":
            result["has_running_multi_agent"] = True
    state = (saved.get("states") or {}).get(conversation_id) or {}
    if state.get("pending_master_messages"):
        result["has_running_multi_agent"] = True
        recoverable = True
    # Background commands are process-local. Live commands keep their owning
    # runtime alive; there is no persisted command process to resurrect here.
    if activate and recoverable:
        key = (data.username, data.workspace_id, str(data.workspace.project_path), conversation_id)
        with _LOCK:
            if key not in _PENDING:
                _PENDING.add(key)
                try:
                    _POOL.submit(_activate, data, conversation_id, key)
                except Exception:
                    _PENDING.discard(key)
                    raise
    return result
