"""Workspace-free resource adapter; execution still uses the shared chat driver."""
from __future__ import annotations

from copy import deepcopy
import threading
import time
from uuid import uuid4

from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.tree import MessageTree
from server.chat_mode_scope import scope_for_principal
from server.conversation_view.snapshots import VIEW_LOCK

ACTIVE = {"pending", "running", "cancel_requested"}


class ChatModeTaskMixin:
    def _create_workspace_free_chat_task(self, ctx, conversation_id):
        from server.tasks.models import TaskRecord
        principal, params = ctx.principal, ctx.params
        if params.files or params.skill_context_messages or params.goal_action or params.goal_mode:
            raise ValueError("Agent execution parameters are unavailable in Chat")
        if not params.chat_operation_id:
            raise ValueError("Chat sends require an operation ID")
        scope = scope_for_principal(principal)
        storage = ChatStorage(scope.root)
        with self._lock:
            existing = [r for r in self._tasks.values()
                        if r.username == principal.username and r.workspace_id is None
                        and r.conversation_id == conversation_id]
            for record in existing:
                if record.task_params.chat_operation_id == params.chat_operation_id:
                    return record
            if any(r.status in ACTIVE for r in existing):
                raise RuntimeError("A Chat generation is already active")
            document = storage.read(conversation_id)
            if (document["messages"] and params.chat_parent_id is None
                    and not params.chat_regenerate_turn_id and not params.chat_edit_message_id):
                raise ValueError("Continuing a Chat requires an explicit parent message")
            from modules.chat_mode.media import resolve_refs
            images = resolve_refs(scope.root, params.images, "image")
            videos = resolve_refs(scope.root, params.videos, "video")
            if params.model_key or params.thinking_mode is not None or "reasoning_effort" in params.chat_settings:
                from modules.chat_mode.configuration import configure_conversation
                configure_conversation(storage, conversation_id, {
                    **({"model_key": params.model_key} if params.model_key else {}),
                    **({"thinking_mode": params.thinking_mode} if params.thinking_mode is not None else {}),
                    **({"reasoning_effort": params.chat_settings["reasoning_effort"]}
                       if "reasoning_effort" in params.chat_settings else {}),
                })
            # A restart retires in-memory workers; never implicitly resume tools.
            old_turn = document["metadata"]["chat"]["active_turn_id"]
            if old_turn:
                from modules.chat_mode.recovery import recover_turn
                recover_turn(storage, conversation_id, old_turn)
            if params.chat_operation_id in document["metadata"]["chat"]["commands"]:
                raise RuntimeError("This Chat send was already accepted; reload the conversation")
            message = None if params.chat_regenerate_turn_id else {
                "role": "user", "content": params.message, "message_id": f"msg_{uuid4().hex}",
                "media_refs": images + videos,
                "metadata": {"visibility": "chat", "starts_work": True},
            }
            receipt = storage.begin(
                conversation_id, params.chat_operation_id, parent_id=params.chat_parent_id,
                message=message, regenerate_turn_id=params.chat_regenerate_turn_id,
                edit_message_id=params.chat_edit_message_id,
                presentation_id=params.chat_presentation_id,
            )
            turn = receipt["value"]
            record = TaskRecord(str(uuid4()), ctx, conversation_id)
            record.chat_turn_id = turn["id"]
            record.trigger_message_id = turn["parent_id"]
            self._tasks[record.task_id] = record
        thread = threading.Thread(target=self._run_workspace_free_chat_task, args=(record,), daemon=True)
        record.thread = thread
        try:
            thread.start()
        except Exception:
            storage.finish(conversation_id, record.chat_turn_id, "failed")
            self.finish_display_task(record, "failed", error="Could not start Chat task")
            raise
        return record

    def _run_workspace_free_chat_task(self, record):
        from modules.chat_mode.runtime import ChatRuntime
        from server.chat_flow import run_chat_task_sync
        scope = scope_for_principal(record.principal)
        storage = ChatStorage(scope.root)
        terminal = None
        complete_payload = {}
        saw_error = False
        status = "failed"

        def sender(event, data):
            nonlocal complete_payload, saw_error
            payload = dict(data or {})
            payload.update({"task_id": record.task_id, "conversation_id": record.conversation_id,
                            "workspace_id": None, "conversation_kind": "chat"})
            if event == "task_complete":
                complete_payload = payload
                return
            if (event == "error" and payload.get("fatal", not payload.get("retry"))) or event == "quota_exceeded":
                saw_error = True
                record.error = payload.get("message") or "Chat request failed"
            self._append_event(record, event, payload)

        try:
            terminal = ChatRuntime(storage, record.conversation_id, record.chat_turn_id,
                                   preferences_dir=scope.preferences_dir,
                                   username=record.username, role=record.principal.role)
            terminal.task_id = record.task_id
            terminal.is_stop_requested = lambda: record.stop_requested
            if not record.principal.host_mode and not record.principal.is_api_user:
                from modules.chat_mode.quota import tracker_for
                terminal.usage_tracker = tracker_for(scope.root, record.principal.role)
            terminal.context_manager.set_web_terminal_callback(sender)
            terminal._display_trigger_message_id = record.trigger_message_id
            with VIEW_LOCK:
                self.bind_display(record, terminal.context_manager)
                record.status = "running"
            if record.stop_requested:
                status = "stopped"
                return
            run_chat_task_sync(
                terminal=terminal, message=record.message, images=[], sender=sender,
                client_sid=record.task_id, workspace=None, username=record.username,
                videos=[], files=[],
            )
            status = "stopped" if record.stop_requested else ("failed" if saw_error else "succeeded")
        except Exception as exc:
            record.error = str(exc)
            sender("error", {"message": str(exc), "fatal": True})
        finally:
            try:
                if terminal:
                    terminal.context_manager.auto_save_conversation(force=True)
            except Exception as exc:
                status = "failed"
                record.error = f"Chat persistence failed: {exc}"
                sender("error", {"message": record.error, "fatal": True})
            try:
                if record.stop_requested:
                    status = "stopped"
                tree_status = {"succeeded": "complete", "stopped": "stopped", "failed": "failed"}[status]
                from modules.chat_mode.recovery import recover_turn
                if status == "succeeded":
                    storage.finish(record.conversation_id, record.chat_turn_id, tree_status)
                else:
                    recover_turn(storage, record.conversation_id, record.chat_turn_id, tree_status)
                from modules.chat_mode.handoff import register
                register(storage, record.conversation_id, record.principal,
                         turn_id=record.chat_turn_id,
                         presentation_id=record.task_params.chat_presentation_id if status == "succeeded" else None)
            except Exception as exc:
                status = "failed"
                record.error = f"Chat persistence failed: {exc}"
                sender("error", {"message": record.error, "fatal": True})
            event = "task_stopped" if status == "stopped" else "task_complete"
            self.finish_display_task(record, status, event, {
                **complete_payload, "conversation_id": record.conversation_id,
                "workspace_id": None, "conversation_kind": "chat",
                "status": status, "success": status == "succeeded", "error": record.error,
                "has_running_multi_agent": False, "has_running_background_commands": False,
                "has_running_sub_agents": False,
            }, error=record.error)
