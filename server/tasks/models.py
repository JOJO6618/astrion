"""简单任务 API：将聊天任务与 WebSocket 解耦，支持后台运行与轮询。"""
from __future__ import annotations
from server.tasks import tasks_bp
import mimetypes
import json
import time
import threading
import uuid
import re
from collections import deque
from pathlib import Path
from typing import Dict, Any, Optional, List

from flask import Blueprint, request, jsonify
from flask import current_app, session

from server.auth_helpers import api_login_required, get_current_username
from server.context import get_user_resources, ensure_conversation_loaded
from server.chat_flow import run_chat_task_sync
from server.main_task_gate import release_main_task_gate
from server.work_timer import finalize_conversation_work_timer
from server.state import stop_flags
from server.utils_common import debug_log, log_conn_diag
from utils.host_workspace_debug import write_host_workspace_debug
from config import DATA_DIR, WORKSPACE_SKILLS_DIRNAME
from modules.goal_state_manager import GoalStateManager, REASON_USER_CANCEL
from modules.sub_agent.state import TERMINAL_STATUSES as SUB_AGENT_TERMINAL_STATUSES
from modules.background_command_manager import BackgroundCommandManager, TERMINAL_STATUSES as BG_COMMAND_TERMINAL_STATUSES
from modules.i18n import tr


SKILL_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n?", re.S)
SKILL_FIELD_RE = re.compile(r"^(?P<key>name|description)\s*:\s*(?P<value>.*)$")



class TaskRecord:
    __slots__ = (
        "task_id",
        "username",
        "workspace_id",
        "status",
        "created_at",
        "updated_at",
        "message",
        "conversation_id",
        "events",
        "thread",
        "error",
        "model_key",
        "thinking_mode",
        "run_mode",
        "max_iterations",
        "session_data",
        "stop_requested",
        "next_event_idx",
        "runtime_pending_queue",
        "runtime_guidance_queue",
        "last_cancel_at",
        "task_type",
    )

    def __init__(
        self,
        task_id: str,
        username: str,
        workspace_id: str,
        message: str,
        conversation_id: Optional[str],
        model_key: Optional[str],
        thinking_mode: Optional[bool],
        run_mode: Optional[str],
        max_iterations: Optional[int],
        task_type: str = "chat",
    ):
        self.task_id = task_id
        self.username = username
        self.workspace_id = workspace_id
        self.status = "pending"
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.message = message
        self.conversation_id = conversation_id
        # 刷新恢复时前端会从事件流重建进行中的输出，1000 在长流式回复下会过早截断，
        # 导致“只恢复最后几个字符”。这里提高缓冲上限，优先保证重建完整性。
        self.events: deque[Dict[str, Any]] = deque(maxlen=20000)
        self.thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None
        self.model_key = model_key
        self.thinking_mode = thinking_mode
        self.run_mode = run_mode
        self.max_iterations = max_iterations
        self.session_data: Dict[str, Any] = {}
        self.stop_requested: bool = False
        self.next_event_idx: int = 0
        self.runtime_pending_queue: List[Dict[str, Any]] = []
        self.runtime_guidance_queue: List[str] = []
        self.last_cancel_at: Optional[float] = None
        self.task_type = task_type

class TaskManager:
    """线程内存版任务管理器，后续可替换为 Redis/DB。"""

    def __init__(self):
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def cleanup_old_tasks(self, max_age_seconds: int = 3600) -> int:
        """清理超过指定时间的已完成/已停止任务。

        终态集合说明：用户取消的实际终态是 "stopped"（历史代码误写为从未被赋值的
        "canceled"，导致 stopped 任务永不清理，此处修正并保留 canceled 兼容）。
        cancel_requested 正常是 cancel_task 到收尾之间的秒级瞬态；若因进程异常残留
        超过 max_age，必为死记录，一并清理兜底（updated_at 在打标时已刷新，正常
        收尾中的任务不可能存活到 max_age）。
        """
        now = time.time()
        with self._lock:
            to_remove = []
            for task_id, rec in self._tasks.items():
                if rec.status in {"succeeded", "failed", "stopped", "canceled", "cancel_requested"}:
                    age = now - rec.updated_at
                    if age > max_age_seconds:
                        to_remove.append(task_id)

            for task_id in to_remove:
                del self._tasks[task_id]

            return len(to_remove)

    # ---- public APIs ----
    def create_chat_task(
        self,
        username: str,
        workspace_id: str,
        message: str,
        images: List[Any],
        conversation_id: Optional[str],
        videos: Optional[List[Any]] = None,
        model_key: Optional[str] = None,
        thinking_mode: Optional[bool] = None,
        run_mode: Optional[str] = None,
        max_iterations: Optional[int] = None,
        session_data: Optional[Dict[str, Any]] = None,
        message_source: Optional[str] = None,
        goal_mode: bool = False,
        skill_context_messages: Optional[List[Dict[str, str]]] = None,
        files: Optional[List[str]] = None,
        task_type: str = "chat",
    ) -> TaskRecord:
        if run_mode:
            normalized = str(run_mode).lower()
            if normalized not in {"fast", "thinking", "deep"}:
                raise ValueError(tr("tasks.invalid_run_mode"))
            run_mode = normalized
        normalized_task_type = str(task_type or "chat").strip().lower() or "chat"
        # 单对话互斥：普通 chat 任务禁止同一对话并发（防串写对话历史）；
        # 同工作区不同对话允许并行（对话级 terminal 隔离）。
        # notice（通知触发）任务允许与已完成的 chat 任务共存，用于后台通知重入。
        if normalized_task_type == "chat":
            def _norm_cid(cid):
                cid = str(cid or "").strip()
                return cid[5:] if cid.startswith("conv_") else cid
            target_cid = _norm_cid(conversation_id)
            existing = [
                t for t in self.list_tasks(username, workspace_id)
                if t.status in {"pending", "running"}
                and getattr(t, "task_type", "chat") == "chat"
                and _norm_cid(getattr(t, "conversation_id", None)) == target_cid
            ]
            if existing:
                raise RuntimeError(tr("tasks.task_already_running"))
        task_id = str(uuid.uuid4())
        record = TaskRecord(task_id, username, workspace_id, message, conversation_id, model_key, thinking_mode, run_mode, max_iterations, task_type=normalized_task_type)
        # 记录当前 session 快照，便于后台线程内使用
        if session_data is not None:
            snapshot = dict(session_data)
            snapshot.setdefault("workspace_id", workspace_id)
            if message_source is not None:
                snapshot.setdefault("message_source", str(message_source))
            snapshot["goal_mode"] = bool(goal_mode)
            if skill_context_messages:
                snapshot["skill_context_messages"] = list(skill_context_messages)
            try:
                snapshot.setdefault("host_mode", session.get("host_mode"))
                if snapshot.get("host_mode"):
                    snapshot.setdefault("host_workspace_id", session.get("host_workspace_id") or workspace_id)
            except Exception:
                if snapshot.get("host_mode"):
                    snapshot.setdefault("host_workspace_id", workspace_id)
            record.session_data = snapshot
        else:
            try:
                record.session_data = {
                    "username": session.get("username"),
                    "role": session.get("role"),
                    "is_api_user": session.get("is_api_user"),
                    "host_mode": session.get("host_mode"),
                    "host_workspace_id": session.get("host_workspace_id") or workspace_id,
                    "workspace_id": workspace_id,
                    "run_mode": session.get("run_mode"),
                    "thinking_mode": session.get("thinking_mode"),
                    "model_key": session.get("model_key"),
                    "message_source": str(message_source) if message_source is not None else None,
                    "goal_mode": bool(goal_mode),
                    "skill_context_messages": list(skill_context_messages or []),
                }
            except Exception:
                record.session_data = {}
        with self._lock:
            self._tasks[task_id] = record
        thread = threading.Thread(target=self._run_chat_task, args=(record, images, videos or [], files or []), daemon=True)
        record.thread = thread
        record.status = "running"
        record.updated_at = time.time()
        thread.start()
        return record

    def get_task(self, username: str, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return None
            return rec

    def get_events_since(self, rec: TaskRecord, offset: int) -> List[Dict[str, Any]]:
        """按 offset 过滤事件。

        rec.events 由任务工作线程持续追加（流式输出期间非常频繁），
        直接迭代会在并发追加时抛 RuntimeError: deque mutated during iteration。
        先在锁内做 O(n) 浅拷贝快照，再在锁外过滤。
        """
        with self._lock:
            snapshot = list(rec.events)
        return [e for e in snapshot if e["idx"] >= offset]

    def list_tasks(self, username: str, workspace_id: Optional[str] = None) -> List[TaskRecord]:
        with self._lock:
            return [
                rec
                for rec in self._tasks.values()
                if rec.username == username and (workspace_id is None or rec.workspace_id == workspace_id)
            ]

    def cancel_task(self, username: str, task_id: str) -> bool:
        """取消主智能体任务。只停主智能体，不触碰后台任务。

        后台任务（子智能体、后台指令）的终止/暂停由独立的 API 处理，
        参见 /api/sub_agents/stop_all 和 /api/background_commands/stop_all。
        """
        rec = self.get_task(username, task_id)
        if not rec:
            debug_log(f"[TaskCancel] cancel_task 找不到任务: task_id={task_id}")
            return False

        task_id = rec.task_id
        status_at_entry = rec.status

        # 取终端引用（仅用于停止目标模式等副作用）
        entry = stop_flags.get(task_id)
        terminal = None
        if isinstance(entry, dict):
            terminal = entry.get('terminal')
        if not terminal and rec.workspace_id:
            try:
                terminal, _ = get_user_resources(username, rec.workspace_id, conversation_id=rec.conversation_id)
            except Exception:
                pass

        debug_log(
            f"[TaskCancel] 入口: task_id={task_id}, status={status_at_entry}, "
            f"conv={rec.conversation_id}"
        )

        # 1. 硬取消主 asyncio task（如果引用还在）
        if isinstance(entry, dict):
            loop = entry.get('loop')
            task = entry.get('task')
            if loop and task and not task.done():
                try:
                    loop.call_soon_threadsafe(task.cancel)
                    debug_log(f"[TaskCancel] 已投递硬取消: task_id={task_id}")
                except Exception as exc:
                    debug_log(f"[TaskCancel] 硬取消失败: task_id={task_id}, error={exc}")
            entry['stop'] = True
        else:
            stop_flags[task_id] = {'stop': True, 'task': None, 'terminal': None, 'loop': None}
            entry = stop_flags[task_id]

        rec.stop_requested = True

        # 2. 停止目标模式
        if rec.workspace_id:
            try:
                _, workspace = get_user_resources(username, rec.workspace_id, conversation_id=rec.conversation_id)
                if workspace and rec.conversation_id:
                    gsm = GoalStateManager(workspace.data_dir, rec.conversation_id)
                    if gsm.is_active():
                        gsm.mark_stopped(REASON_USER_CANCEL)
                        debug_log(f"[Goal] 用户取消任务 {task_id}，同步停止本对话目标模式")
            except Exception as exc:
                debug_log(f"[Goal] 取消任务时停止目标模式失败: {exc}")

        # 3. 丢弃已经引导的内容（预输入队列保持不变，正常结束后再插入）
        with self._lock:
            rec.runtime_guidance_queue = []
            rec.updated_at = time.time()

        # 4. 标记为 cancel_requested。
        #    _run_chat_task finally 会并发把 status 改为 stopped 并发 task_stopped 事件。
        #    此处仅做软标记，不强制覆盖 finally 即将设的终态。
        now = time.time()
        with self._lock:
            if rec.status in {"running", "pending"}:
                rec.status = "cancel_requested"
            rec.updated_at = now
            rec.last_cancel_at = now
        debug_log(
            f"[TaskCancel] 已取消主智能体: task_id={task_id}, "
            f"status_at_entry={status_at_entry}, current_status={rec.status}"
        )
        return True

    @staticmethod
    def _normalize_runtime_pending_queue(raw_queue: Any) -> List[Dict[str, Any]]:
        now_ts = time.time()
        normalized: List[Dict[str, Any]] = []
        if not isinstance(raw_queue, list):
            return normalized
        for raw_item in raw_queue:
            if isinstance(raw_item, dict):
                item_id = str(raw_item.get("id") or "").strip()
                text = str(raw_item.get("text") or "").strip()
                created_at = raw_item.get("created_at")
                raw_files = raw_item.get("files")
            else:
                item_id = ""
                text = str(raw_item or "").strip()
                created_at = None
                raw_files = None
            if not text:
                continue
            if not item_id:
                item_id = str(uuid.uuid4())
            try:
                created_at_float = float(created_at)
            except Exception:
                created_at_float = now_ts
            entry = {
                "id": item_id,
                "text": text,
                "created_at": created_at_float,
            }
            if isinstance(raw_files, list):
                files = [
                    str(p).strip()
                    for p in raw_files
                    if isinstance(p, str) and str(p).strip()
                ][:9]
                if files:
                    entry["files"] = files
            normalized.append(entry)
        return normalized

    @staticmethod
    def _runtime_pending_public(queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for item in queue or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if not text or not item_id:
                continue
            entry = {
                "id": item_id,
                "text": text,
                "created_at": item.get("created_at"),
            }
            if isinstance(item.get("files"), list) and item["files"]:
                entry["files"] = list(item["files"])
            result.append(entry)
        return result

    def enqueue_runtime_pending_message(
        self,
        username: str,
        task_id: str,
        message: str,
        max_queue_size: int = 5,
        files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        text = str(message or "").strip()
        if not text:
            return {"success": False, "code": "empty_message", "error": tr("tasks.message_empty")}
        limit = int(max(1, max_queue_size))
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return {"success": False, "code": "task_not_found", "error": tr("tasks.task_not_found")}
            if rec.status not in {"pending", "running", "cancel_requested"}:
                return {"success": False, "code": "task_not_running", "error": tr("tasks.task_not_running_append_message")}
            queue = self._normalize_runtime_pending_queue(getattr(rec, "runtime_pending_queue", None))
            if len(queue) >= limit:
                return {
                    "success": False,
                    "code": "queue_full",
                    "error": tr("tasks.queue_full_max", limit=limit),
                }
            item = {
                "id": str(uuid.uuid4()),
                "text": text,
                "created_at": time.time(),
            }
            if isinstance(files, list):
                normalized_files = [
                    str(p).strip() for p in files if isinstance(p, str) and str(p).strip()
                ][:9]
                if normalized_files:
                    item["files"] = normalized_files
            queue.append(item)
            rec.runtime_pending_queue = queue
            rec.updated_at = time.time()
            return {
                "success": True,
                "task_id": rec.task_id,
                "item": item,
                "messages": self._runtime_pending_public(queue),
            }

    def remove_runtime_pending_message(
        self, username: str, task_id: str, message_id: str
    ) -> Dict[str, Any]:
        target_id = str(message_id or "").strip()
        if not target_id:
            return {"success": False, "code": "invalid_message_id", "error": tr("tasks.invalid_message_id")}
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return {"success": False, "code": "task_not_found", "error": tr("tasks.task_not_found")}
            queue = self._normalize_runtime_pending_queue(getattr(rec, "runtime_pending_queue", None))
            remove_idx = -1
            for idx, item in enumerate(queue):
                if str(item.get("id") or "") == target_id:
                    remove_idx = idx
                    break
            if remove_idx < 0:
                return {"success": False, "code": "message_not_found", "error": tr("tasks.message_not_found")}
            queue.pop(remove_idx)
            rec.runtime_pending_queue = queue
            rec.updated_at = time.time()
            return {
                "success": True,
                "task_id": rec.task_id,
                "messages": self._runtime_pending_public(queue),
            }

    def promote_runtime_pending_to_guidance(
        self,
        username: str,
        task_id: str,
        message_id: str,
        max_guidance_queue_size: int = 5,
    ) -> Dict[str, Any]:
        target_id = str(message_id or "").strip()
        if not target_id:
            return {"success": False, "code": "invalid_message_id", "error": tr("tasks.invalid_message_id")}
        guidance_limit = int(max(1, max_guidance_queue_size))
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return {"success": False, "code": "task_not_found", "error": tr("tasks.task_not_found")}
            if rec.status not in {"pending", "running", "cancel_requested"}:
                return {"success": False, "code": "task_not_running", "error": tr("tasks.task_not_running_guide")}
            queue = self._normalize_runtime_pending_queue(getattr(rec, "runtime_pending_queue", None))
            guidance_queue = getattr(rec, "runtime_guidance_queue", None)
            if not isinstance(guidance_queue, list):
                guidance_queue = []
            if len(guidance_queue) >= guidance_limit:
                return {
                    "success": False,
                    "code": "guidance_queue_full",
                    "error": tr("tasks.guidance_queue_full_max", guidance_limit=guidance_limit),
                }
            selected = None
            remain_queue: List[Dict[str, Any]] = []
            for item in queue:
                if selected is None and str(item.get("id") or "") == target_id:
                    selected = item
                    continue
                remain_queue.append(item)
            if not selected:
                return {"success": False, "code": "message_not_found", "error": tr("tasks.message_not_found")}
            selected_text = str(selected.get("text") or "").strip()
            if not selected_text:
                return {"success": False, "code": "empty_message", "error": tr("tasks.message_content_empty")}
            selected_files = selected.get("files")
            if isinstance(selected_files, list) and selected_files:
                guidance_queue.append({"text": selected_text, "files": list(selected_files)[:9]})
            else:
                guidance_queue.append(selected_text)
            rec.runtime_guidance_queue = guidance_queue
            rec.runtime_pending_queue = remain_queue
            rec.updated_at = time.time()
            return {
                "success": True,
                "task_id": rec.task_id,
                "queued_count": len(guidance_queue),
                "messages": self._runtime_pending_public(remain_queue),
            }

    def get_runtime_pending_messages(self, username: str, task_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return []
            queue = self._normalize_runtime_pending_queue(getattr(rec, "runtime_pending_queue", None))
            rec.runtime_pending_queue = queue
            return self._runtime_pending_public(queue)

    def enqueue_runtime_guidance(
        self,
        username: str,
        task_id: str,
        message: str,
        max_queue_size: int = 5,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        text = str(message or "").strip()
        if not text:
            return {"success": False, "code": "empty_message", "error": tr("tasks.guidance_content_empty")}
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return {"success": False, "code": "task_not_found", "error": tr("tasks.task_not_found")}
            if rec.status not in {"pending", "running", "cancel_requested"}:
                return {"success": False, "code": "task_not_running", "error": tr("tasks.task_not_running_append_guidance")}
            queue = getattr(rec, "runtime_guidance_queue", None)
            if not isinstance(queue, list):
                queue = []
                rec.runtime_guidance_queue = queue
            if len(queue) >= int(max(1, max_queue_size)):
                return {
                    "success": False,
                    "code": "queue_full",
                    "error": tr("tasks.guidance_queue_full_max", guidance_limit=int(max(1, max_queue_size))),
                }
            normalized_source = str(source or "").strip().lower()
            if normalized_source:
                queue.append({"text": text, "source": normalized_source})
            else:
                queue.append(text)
            rec.updated_at = time.time()
            return {
                "success": True,
                "queued_count": len(queue),
                "task_id": rec.task_id,
            }

    def pop_runtime_guidance_for_injection(self, username: str, task_id: str) -> Optional[Any]:
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return None
            queue = getattr(rec, "runtime_guidance_queue", None)
            if not isinstance(queue, list) or not queue:
                return None
            item = queue.pop(0)
            rec.updated_at = time.time()
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if not text:
                    return None
                src = str(item.get("source") or "").strip().lower()
                return {"text": text, "source": src} if src else {"text": text}
            text = str(item or "").strip()
            return text or None

    def consume_runtime_guidance_messages(self, username: str, task_id: str) -> List[str]:
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return []
            queue = getattr(rec, "runtime_guidance_queue", None)
            if not isinstance(queue, list) or not queue:
                return []
            items: List[str] = []
            for item in queue:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                else:
                    text = str(item or "").strip()
                if text:
                    items.append(text)
            rec.runtime_guidance_queue = []
            rec.updated_at = time.time()
            return items

    def consume_runtime_guidance_for_injection(self, username: str, task_id: str) -> List[Any]:
        """按原始结构取出整批引导/通知消息（支持 str 与 {text,source}）。"""
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.username != username:
                return []
            queue = getattr(rec, "runtime_guidance_queue", None)
            if not isinstance(queue, list) or not queue:
                return []
            items: List[Any] = []
            for item in queue:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    if not text:
                        continue
                    src = str(item.get("source") or "").strip().lower()
                    entry = {"text": text, "source": src} if src else {"text": text}
                    raw_files = item.get("files")
                    if isinstance(raw_files, list) and raw_files:
                        entry["files"] = [
                            str(p).strip()
                            for p in raw_files
                            if isinstance(p, str) and str(p).strip()
                        ][:9]
                    items.append(entry)
                else:
                    text = str(item or "").strip()
                    if text:
                        items.append(text)
            rec.runtime_guidance_queue = []
            rec.updated_at = time.time()
            return items

    # ---- internal helpers ----
    def _cleanup_background_tasks(self, rec: TaskRecord, terminal: Optional[Any]) -> bool:
        """清理指定任务/对话下的所有后台子智能体和后台命令，返回是否清理到任何任务。"""
        has_running_background = False
        if not terminal or not rec.conversation_id:
            return has_running_background

        sub_agent_manager = getattr(terminal, 'sub_agent_manager', None)
        if sub_agent_manager:
            try:
                sub_agent_manager.reconcile_task_states(conversation_id=rec.conversation_id)
                for task_info in list(sub_agent_manager.tasks.values()):
                    if task_info.get('conversation_id') != rec.conversation_id:
                        continue
                    status = task_info.get('status')
                    if status not in SUB_AGENT_TERMINAL_STATUSES.union({"terminated"}):
                        has_running_background = True
                        try:
                            sub_agent_manager.terminate_sub_agent(task_id=task_info.get('task_id'))
                        except Exception as exc:
                            debug_log(f"[TaskCancel] 终止子智能体失败: {exc}")
            except Exception as exc:
                debug_log(f"[TaskCancel] 检查后台子智能体失败: {exc}")

        bg_manager = getattr(terminal, 'background_command_manager', None)
        if bg_manager:
            try:
                bg_manager.reconcile_stale_records(conversation_id=rec.conversation_id)
                waiting_items = bg_manager.list_waiting_items(rec.conversation_id)
                for item in waiting_items:
                    has_running_background = True
                    try:
                        bg_manager.cancel_command(item.get('command_id'))
                    except Exception as exc:
                        debug_log(f"[TaskCancel] 取消后台命令失败: {exc}")
                # 把该对话下所有未通知的终态记录也标为已通知，避免后续幽灵通知
                try:
                    with bg_manager._lock:
                        for record in bg_manager._records.values():
                            if record.get('conversation_id') != rec.conversation_id:
                                continue
                            if record.get('status') in BG_COMMAND_TERMINAL_STATUSES and not record.get('notified'):
                                record['notified'] = True
                                record['updated_at'] = time.time()
                except Exception as exc:
                    debug_log(f"[TaskCancel] 标记后台通知状态失败: {exc}")
            except Exception as exc:
                debug_log(f"[TaskCancel] 检查后台命令失败: {exc}")

        return has_running_background

    @staticmethod
    def get_conversation_running_status(terminal: Optional[Any], conversation_id: Optional[str]) -> Dict[str, bool]:
        """按对话聚合后台运行状态（REST 对账接口用）。

        返回三类后台工作，语义互不重叠：
        - has_running_sub_agents: 传统后台子智能体（排除多智能体实例任务）
        - has_running_background_commands: 后台命令（含终态未通知）
        - has_running_multi_agent: 多智能体实例在跑、非终态多智能体任务、
          或有待消费的 pending master 消息
        """
        result = {
            "has_running_sub_agents": False,
            "has_running_background_commands": False,
            "has_running_multi_agent": False,
        }
        if not terminal or not conversation_id:
            return result

        sub_agent_manager = getattr(terminal, 'sub_agent_manager', None)
        if sub_agent_manager:
            try:
                sub_agent_manager.reconcile_task_states(conversation_id=conversation_id)
                terminal_statuses = SUB_AGENT_TERMINAL_STATUSES.union({"terminated"})
                for task_info in sub_agent_manager.tasks.values():
                    if task_info.get('conversation_id') != conversation_id:
                        continue
                    status = task_info.get('status')
                    if status in terminal_statuses:
                        continue
                    if task_info.get('multi_agent_mode'):
                        # idle = 本轮输出结束、保留上下文等待指令，不算运行中。
                        # 对齐 socket task_complete 语义（chat_flow_task_main.py：
                        # 仅 running 实例 + pending master 消息算活跃），否则
                        # REST 对账会把已空闲的多智能体对话误判为运行中（前端
                        # 幽灵轮询），且回收器永远不敢回收该对话的 terminal。
                        if status == "idle":
                            continue
                        result["has_running_multi_agent"] = True
                    else:
                        result["has_running_sub_agents"] = True
            except Exception as exc:
                debug_log(f"[Task] 对账检查子智能体失败: {exc}")
            try:
                state = sub_agent_manager.get_multi_agent_state(conversation_id)
                if state:
                    has_running_instance = any(a.status == "running" for a in state.list_all())
                    if has_running_instance or state.has_pending_master_messages():
                        result["has_running_multi_agent"] = True
            except Exception as exc:
                debug_log(f"[Task] 对账检查多智能体状态失败: {exc}")

        bg_manager = getattr(terminal, 'background_command_manager', None)
        if bg_manager:
            try:
                bg_manager.reconcile_stale_records(conversation_id=conversation_id)
                waiting_items = bg_manager.list_waiting_items(conversation_id)
                if waiting_items:
                    result["has_running_background_commands"] = True
            except Exception as exc:
                debug_log(f"[Task] 对账检查后台命令失败: {exc}")

        return result

    @staticmethod
    def _has_running_background(rec: TaskRecord, terminal: Optional[Any]) -> Dict[str, bool]:
        """检测指定任务/对话是否还有运行中的后台子智能体或后台命令。

        兼容旧语义：多智能体实例任务也计入 sub_agents。
        """
        result = {"has_running_sub_agents": False, "has_running_background_commands": False}
        if not rec:
            return result
        status = TaskManager.get_conversation_running_status(terminal, rec.conversation_id)
        result["has_running_sub_agents"] = (
            status["has_running_sub_agents"] or status["has_running_multi_agent"]
        )
        result["has_running_background_commands"] = status["has_running_background_commands"]
        return result

    def _append_event(self, rec: TaskRecord, event_type: str, data: Dict[str, Any]):
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("task_id", rec.task_id)
            if rec.conversation_id:
                data.setdefault("conversation_id", rec.conversation_id)
            if rec.workspace_id:
                data.setdefault("workspace_id", rec.workspace_id)
        with self._lock:
            if event_type in {"goal_progress", "goal_completed", "goal_stopped"} and isinstance(data, dict):
                rec.session_data["goal_progress"] = dict(data)
            idx = getattr(rec, "next_event_idx", None)
            if idx is None:
                idx = rec.events[-1]["idx"] + 1 if rec.events else 0
            rec.next_event_idx = idx + 1
            rec.events.append({
                "idx": idx,
                "type": event_type,
                "data": data,
                "ts": time.time(),
            })
            rec.updated_at = time.time()

    def _run_chat_task(self, rec: TaskRecord, images: List[Any], videos: List[Any], files: Optional[List[str]] = None):
        username = rec.username
        workspace_id = rec.workspace_id
        terminal = None
        workspace = None
        stop_hint = False
        try:
            # 为后台线程构造最小请求上下文，填充 session
            from server.app import app as flask_app
            with flask_app.test_request_context():
                try:
                    for k, v in (rec.session_data or {}).items():
                        if v is not None:
                            session[k] = v
                    if session.get("host_mode"):
                        session["workspace_id"] = workspace_id
                        session["host_workspace_id"] = session.get("host_workspace_id") or workspace_id
                        write_host_workspace_debug(
                            "tasks.run_chat_task.apply_host_session",
                            task_id=rec.task_id,
                            workspace_id=workspace_id,
                            host_workspace_id=session.get("host_workspace_id"),
                        )
                except Exception:
                    pass
                terminal, workspace = get_user_resources(username, workspace_id=workspace_id, conversation_id=rec.conversation_id)
            if not terminal or not workspace:
                raise RuntimeError(tr("tasks.system_not_initialized"))
            stop_hint = bool(stop_flags.get(rec.task_id, {}).get("stop"))

            def _apply_requested_model_mode():
                # API 传入的模型/模式配置
                if rec.model_key:
                    try:
                        terminal.set_model(rec.model_key)
                    except Exception as exc:
                        debug_log(f"[Task] 设置模型失败 {rec.model_key}: {exc}")
                if rec.run_mode:
                    try:
                        terminal.set_run_mode(rec.run_mode)
                    except Exception as exc:
                        debug_log(f"[Task] 设置运行模式失败 {rec.run_mode}: {exc}")
                elif rec.thinking_mode is not None:
                    try:
                        terminal.set_run_mode("thinking" if rec.thinking_mode else "fast")
                    except Exception as exc:
                        debug_log(f"[Task] 设置思考模式失败: {exc}")

            _apply_requested_model_mode()
            if rec.max_iterations:
                try:
                    terminal.max_iterations_override = int(rec.max_iterations)
                except Exception:
                    terminal.max_iterations_override = None
            try:
                debug_log(
                    "[Task] effective terminal state "
                    f"model_key={getattr(terminal, 'model_key', None)!r} "
                    f"run_mode={getattr(terminal, 'run_mode', None)!r} "
                    f"thinking_mode={getattr(terminal, 'thinking_mode', None)!r} "
                    f"reasoning_effort={getattr(terminal, 'reasoning_effort', None)!r}"
                )
            except Exception:
                pass

            # 确保会话加载
            conversation_id = rec.conversation_id
            try:
                conversation_id, _ = ensure_conversation_loaded(terminal, conversation_id, workspace=workspace)
                rec.conversation_id = conversation_id
            except Exception as exc:
                raise RuntimeError(tr("tasks.conversation_load_failed", error=exc)) from exc

            # 对话加载会按会话元数据恢复历史模型/模式，这里再覆盖一次用户本次请求参数
            _apply_requested_model_mode()
            try:
                debug_log(
                    "[Task] post-conversation effective state "
                    f"model_key={getattr(terminal, 'model_key', None)!r} "
                    f"run_mode={getattr(terminal, 'run_mode', None)!r} "
                    f"thinking_mode={getattr(terminal, 'thinking_mode', None)!r} "
                    f"reasoning_effort={getattr(terminal, 'reasoning_effort', None)!r}"
                )
            except Exception:
                pass

            # 仅对“后台通知触发的新任务”补发 user_message 事件到任务事件流。
            # 这样前端轮询能即时看到这条 user 消息，而不是刷新后才从历史中看到。
            try:
                if bool((rec.session_data or {}).get("auto_user_message_event")):
                    # 先回放本批「通知池」里的前置完成通知（除触发消息外的 N-1 条），
                    # 保证轮询客户端按时间顺序看到所有完成通知，且不各自触发新一轮工作。
                    preceding_notices = (rec.session_data or {}).get("preceding_user_notices") or []
                    if isinstance(preceding_notices, list):
                        for item in preceding_notices:
                            if not isinstance(item, dict):
                                continue
                            notice_msg = str(item.get("message") or "").strip()
                            if not notice_msg:
                                continue
                            notice_payload = dict(item.get("payload") or {})
                            notice_payload["starts_work"] = False
                            notice_event = {
                                "message": notice_msg,
                                "conversation_id": rec.conversation_id,
                                "task_id": rec.task_id,
                            }
                            notice_event.update(notice_payload)
                            self._append_event(rec, "user_message", notice_event)
                    extra_payload = (rec.session_data or {}).get("auto_user_message_payload") or {}
                    if not isinstance(extra_payload, dict):
                        extra_payload = {}
                    payload = {
                        "message": rec.message,
                        "conversation_id": rec.conversation_id,
                        "task_id": rec.task_id,
                    }
                    payload.update(extra_payload)
                    self._append_event(rec, "user_message", payload)
            except Exception as exc:
                debug_log(f"[Task] 注入 user_message 事件失败: {exc}")

            def sender(event_type, data):
                if isinstance(data, dict):
                    data = dict(data)
                    if event_type == "compression_finished":
                        migrated_conversation_id = str(data.get("conversation_id") or "").strip()
                        if migrated_conversation_id:
                            with self._lock:
                                rec.conversation_id = migrated_conversation_id
                                rec.updated_at = time.time()
                    data.setdefault("task_id", rec.task_id)
                    if rec.conversation_id:
                        data.setdefault("conversation_id", rec.conversation_id)
                    if rec.workspace_id:
                        data.setdefault("workspace_id", rec.workspace_id)
                # 记录事件
                self._append_event(rec, event_type, data)
                # 在线用户仍然收到实时推送（房间 user_{username}）
                try:
                    from server.extensions import socketio
                    socketio.emit(event_type, data, room=f"user_{username}")
                except Exception:
                    pass

            # 轮询模式需要把 context_manager 的回调切到当前任务 sender，
            # 否则 token_update 等事件只走 websocket，前端任务轮询拿不到实时更新。
            previous_ctx_callback = None
            try:
                if terminal and getattr(terminal, "context_manager", None):
                    previous_ctx_callback = getattr(terminal.context_manager, "_web_terminal_callback", None)
                    terminal.context_manager.set_web_terminal_callback(sender)
            except Exception as exc:
                debug_log(f"[Task] 设置上下文回调失败: {exc}")

            # 将 task_id 作为 client_sid，供 stop_flags 检测
            previous_auto_user_event = None
            previous_message_source = None
            previous_auto_user_payload = None
            previous_goal_mode_requested = None
            previous_skill_context_messages = None
            try:
                previous_auto_user_event = getattr(terminal, "_auto_user_message_event", False)
                previous_message_source = getattr(terminal, "_current_user_message_source", None)
                previous_auto_user_payload = getattr(terminal, "_auto_user_message_payload", None)
                previous_goal_mode_requested = getattr(terminal, "_goal_mode_requested", False)
                previous_skill_context_messages = getattr(terminal, "_skill_context_messages", None)
                setattr(
                    terminal,
                    "_auto_user_message_event",
                    bool((rec.session_data or {}).get("auto_user_message_event")),
                )
                setattr(
                    terminal,
                    "_auto_user_message_payload",
                    dict((rec.session_data or {}).get("auto_user_message_payload") or {}),
                )
                setattr(
                    terminal,
                    "_current_user_message_source",
                    str((rec.session_data or {}).get("message_source") or "user"),
                )
                setattr(
                    terminal,
                    "_goal_mode_requested",
                    bool((rec.session_data or {}).get("goal_mode")),
                )
                setattr(
                    terminal,
                    "_skill_context_messages",
                    list((rec.session_data or {}).get("skill_context_messages") or []),
                )
            except Exception:
                previous_auto_user_event = None
                previous_message_source = None
                previous_auto_user_payload = None
                previous_goal_mode_requested = None
                previous_skill_context_messages = None

            try:
                run_chat_task_sync(
                    terminal=terminal,
                    message=rec.message,
                    images=images,
                    sender=sender,
                    client_sid=rec.task_id,
                    workspace=workspace,
                    username=username,
                    videos=videos,
                    files=files or [],
                    # 通知链任务认领轮询器预占的门闸（其余任务为 None，走竞争获取）
                    main_task_gate_token=(rec.session_data or {}).get("main_task_gate_token"),
                )
            finally:
                try:
                    if previous_auto_user_event is not None:
                        setattr(terminal, "_auto_user_message_event", previous_auto_user_event)
                    if previous_auto_user_payload is not None:
                        setattr(terminal, "_auto_user_message_payload", previous_auto_user_payload)
                    if previous_message_source is not None:
                        setattr(terminal, "_current_user_message_source", previous_message_source)
                    if previous_goal_mode_requested is not None:
                        setattr(terminal, "_goal_mode_requested", previous_goal_mode_requested)
                    if previous_skill_context_messages is not None:
                        setattr(terminal, "_skill_context_messages", previous_skill_context_messages)
                    else:
                        setattr(terminal, "_skill_context_messages", [])
                    if terminal and getattr(terminal, "context_manager", None):
                        terminal.context_manager.set_web_terminal_callback(previous_ctx_callback)
                except Exception as exc:
                    debug_log(f"[Task] 恢复上下文回调失败: {exc}")

            # 结束状态
            canceled_flag = rec.stop_requested or stop_hint or bool(stop_flags.get(rec.task_id, {}).get("stop"))
            if canceled_flag:
                # 用户取消：主任务一律以 stopped 终态收尾。
                # cancel_requested 仅作为 cancel_task 到本收尾之间的瞬态（此期间仍视为
                # 活跃，防止前端对账在收尾间隙误清运行态/重复重放）。
                # 后台任务（子智能体/后台命令）有独立的停止入口与运行状态，主任务不再
                # 为其保持 cancel_requested 等待「第二下点击」——该交互已废弃，保持
                # cancel_requested 只会让任务永久卡在活跃集合，被 bootstrap / 对账当作
                # 「最新活跃主任务」反复重放死任务事件流（显示回退事故的根因）。
                bg_state = self._has_running_background(rec, terminal)
                has_bg = bg_state["has_running_sub_agents"] or bg_state["has_running_background_commands"]
                with self._lock:
                    new_status = "stopped"
                    rec.status = new_status
                    rec.updated_at = time.time()
                debug_log(
                    f"[TaskRun] 任务线程结束: task_id={rec.task_id}, canceled_flag={canceled_flag}, "
                    f"new_status={new_status}, bg_state={bg_state}"
                )
                # 统一发送 task_stopped，携带后台任务状态
                try:
                    from server.extensions import socketio
                    stopped_payload = {
                        'message': tr("task_main.task_stopped"),
                        'reason': 'user_requested',
                        'task_id': rec.task_id,
                        'conversation_id': rec.conversation_id,
                        'has_running_sub_agents': bg_state["has_running_sub_agents"],
                        'has_running_background_commands': bg_state["has_running_background_commands"],
                    }
                    socketio.emit('task_stopped', stopped_payload, room=f"user_{rec.username}")
                    # 同时写入轮询事件队列，确保 websocket 丢失时前端仍能收到
                    self._append_event(rec, "task_stopped", stopped_payload)
                    debug_log(
                        f"[TaskRun] 已发送 task_stopped: task_id={rec.task_id}, "
                        f"has_bg={has_bg}, room=user_{rec.username}"
                    )
                except Exception as exc:
                    debug_log(f"[TaskRun] 发送 task_stopped 失败: {exc}")
            else:
                with self._lock:
                    rec.status = "succeeded"
                    rec.updated_at = time.time()

            # 任务线程结束：仅当对话真正空闲（无其它前台/后台任务）时才把 work_timer 标记为完成，
            # 避免智能体已停、但后台子智能体/后台命令/压缩仍在进行时提前停止计时。
            try:
                if terminal and rec.conversation_id:
                    active_task_ids = {
                        t.task_id
                        for t in self.list_tasks(username, rec.workspace_id)
                        if t.status in {"pending", "running"}
                    }
                    finalized = finalize_conversation_work_timer(
                        terminal,
                        rec.conversation_id,
                        exclude_task_id=rec.task_id,
                        active_task_ids=active_task_ids,
                    )
            except Exception as exc:
                pass
        except Exception as exc:
            debug_log(f"[Task] 后台任务失败: {exc}")
            self._append_event(rec, "error", {"message": str(exc)})
            with self._lock:
                rec.status = "failed"
                rec.error = str(exc)
                rec.updated_at = time.time()
        finally:
            # 清理 stop_flags
            stop_flags.pop(rec.task_id, None)
            # 主任务门闸兜底释放：若任务线程在 process_message_task 认领前异常退出，
            # 按 session_data 中的 token 释放，避免门闸泄漏导致对话永久被占用。
            # 正常路径下 process_message_task 已在 finally 释放，此处为无操作。
            try:
                gate_token = (rec.session_data or {}).get("main_task_gate_token")
                if gate_token and terminal:
                    release_main_task_gate(terminal, gate_token)
            except Exception:
                pass
            # 清理一次性配置
            if terminal and hasattr(terminal, "max_iterations_override"):
                try:
                    delattr(terminal, "max_iterations_override")
                except Exception:
                    terminal.max_iterations_override = None

def start_task_cleanup_scheduler():
    """启动任务清理定时器"""
    def cleanup_loop():
        while True:
            try:
                from server.tasks import task_manager
                count = task_manager.cleanup_old_tasks(3600)
                if count > 0:
                    debug_log(f"[Task] 清理了 {count} 个旧任务")
            except Exception as e:
                debug_log(f"[Task] 清理任务失败: {e}")
            time.sleep(600)  # 每 10 分钟

    thread = threading.Thread(target=cleanup_loop, daemon=True, name="TaskCleanup")
    thread.start()
    debug_log("[Task] 任务清理定时器已启动")
