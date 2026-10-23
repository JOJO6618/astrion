"""Run-scoped compression requests, consumed only at complete response/tool boundaries."""
from __future__ import annotations

from server.conversation_view.snapshots import VIEW_LOCK, normalize_id
from modules.i18n import tr
from server.utils_common import debug_log


class CompressionRequestMixin:
    def request_compression(self, username: str, workspace_id: str, conversation_id: str):
        """Queue on the existing writer; None means an idle compression task is needed."""
        with VIEW_LOCK:
            with self._lock:
                rec = next((item for item in self._tasks.values()
                            if item.username == username and item.workspace_id == workspace_id
                            and normalize_id(item.conversation_id) == normalize_id(conversation_id)
                            and item.status in {"pending", "running", "cancel_requested"}), None)
                if rec is None:
                    return None
                if rec.stop_requested or rec.compression_requests_closed:
                    raise RuntimeError(tr("tasks.task_already_running"))
                enqueue = rec.task_type != "compression" and not rec.compression_request
                stage = ("waiting_for_tools" if enqueue or rec.compression_request == "pending"
                         else "generating_summary")
            # VIEW_LOCK keeps the safe-point consumer out until the event is published.
            if enqueue:
                self._append_event(rec, "compression_state", {
                    "conversation_id": rec.conversation_id, "in_progress": True,
                    "mode": "requested", "stage": stage,
                })
                debug_log(f"[CompressionRequest] queued task={rec.task_id} conv={rec.conversation_id}")
            return {"task_id": rec.task_id, "queued": True,
                    "compression": {"conversation_id": rec.conversation_id,
                                    "in_progress": True, "mode": "requested", "stage": stage}}

    def take_compression_request(self, username: str, task_id: str, *, close_if_empty=False) -> bool:
        """Atomically consume or close admission before the writer finishes."""
        with VIEW_LOCK, self._lock:
            rec = self._tasks.get(task_id)
            if (rec is None or rec.username != username or rec.stop_requested
                    or rec.status not in {"pending", "running"}):
                return False
            if rec.compression_request == "pending":
                rec.compression_request = "running"
                debug_log(f"[CompressionRequest] consumed task={task_id} conv={rec.conversation_id}")
                return True
            if close_if_empty:
                rec.compression_requests_closed = True
            return False


async def run_requested_compression(*, web_terminal, workspace, conversation_id,
                                    username, task_id, sender, close_if_empty=False):
    """The caller already owns the conversation gate and has filled every tool result."""
    from server.tasks import task_manager
    from server.deep_compression import run_deep_compression

    if not task_manager.take_compression_request(username, task_id, close_if_empty=close_if_empty):
        return None
    result = await run_deep_compression(
        web_terminal=web_terminal, workspace=workspace, conversation_id=conversation_id,
        mode="requested", sender=sender,
    )
    if not result.get("success"):
        raise RuntimeError(result.get("error") or tr("tool_loop.deep_compression_failed"))
    return result
