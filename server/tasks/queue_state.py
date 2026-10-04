"""待消费消息归属对话；任务结束/重启后仍可恢复，停止只暂停自动发送。"""
from __future__ import annotations

from copy import deepcopy
import time
import uuid

QUEUE_KEY = "runtime_message_queue"


def guidance_to_pending(items):
    result = []
    for index, raw in enumerate(items or []):
        item = deepcopy(raw) if isinstance(raw, dict) else {"text": str(raw or "")}
        if not str(item.get("text") or "").strip():
            continue
        item.setdefault("id", str(uuid.uuid5(uuid.NAMESPACE_URL, f"guidance:{index}:{item['text']}")))
        item.setdefault("created_at", 0)
        item.setdefault("source", "guidance")
        result.append(item)
    return result


def initialize_queue(rec):
    from server.context import RuntimeIdentity, get_user_resources
    p = rec.principal
    terminal, _ = get_user_resources(
        p.username, workspace_id=p.workspace_id, update_session=False,
        conversation_id=rec.conversation_id,
        identity=RuntimeIdentity(host_mode=p.host_mode, host_workspace_id=p.host_workspace_id,
                                 is_api_user=p.is_api_user, role=p.role),
    )
    rec.queue_manager = terminal.context_manager._get_conversation_manager_for_id(rec.conversation_id)


def load_queue(rec):
    data = rec.queue_manager.load_conversation(rec.conversation_id) or {}
    state = (data.get("metadata") or {}).get(QUEUE_KEY) or {}
    rec.runtime_pending_queue = deepcopy(state.get("pending") or [])
    rec.runtime_guidance_queue = guidance_to_pending(state.get("guidance") or [])
    rec.runtime_queue_paused = bool(state.get("paused"))
    selected_id = rec.task_params.queued_message_id
    if selected_id:
        selected = next((item for item in rec.runtime_pending_queue + rec.runtime_guidance_queue if item.get("id") == selected_id), None)
        if selected is None:
            raise ValueError("queued message not found")


def consume_selected_message(rec):
    selected_id = rec.task_params.queued_message_id
    if not selected_id:
        return
    rec.runtime_pending_queue = [item for item in rec.runtime_pending_queue if item.get("id") != selected_id]
    rec.runtime_guidance_queue = [item for item in rec.runtime_guidance_queue if item.get("id") != selected_id]
    save_queue(rec)


def save_queue(rec):
    manager = rec.queue_manager
    if manager is None or not rec.conversation_id:
        return
    with manager._io_lock:
        data = manager.load_conversation(rec.conversation_id)
        if not data:
            raise RuntimeError("runtime queue conversation disappeared")
        data.setdefault("metadata", {})[QUEUE_KEY] = {
            "pending": deepcopy(rec.runtime_pending_queue),
            "guidance": deepcopy(rec.runtime_guidance_queue),
            "paused": rec.runtime_queue_paused,
        }
        manager._atomic_write_json(manager._get_conversation_file_path(rec.conversation_id), data)


def finish_queue(rec, *, paused=False):
    # 引导没来得及注入时回到可见提前输入队列，保留附件和来源。
    rec.runtime_pending_queue.extend(guidance_to_pending(rec.runtime_guidance_queue))
    rec.runtime_guidance_queue = []
    rec.runtime_queue_paused = bool(paused)
    save_queue(rec)


def queue_snapshot(manager, conversation_id, *, include_guidance=True):
    data = manager.load_conversation(conversation_id) or {}
    state = (data.get("metadata") or {}).get(QUEUE_KEY) or {}
    return {
        "messages": deepcopy(state.get("pending") or []) + (guidance_to_pending(state.get("guidance")) if include_guidance else []),
        "paused": bool(state.get("paused")),
    }
