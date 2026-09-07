"""RuntimeService：公共任务受理与控制入口（契约 docs/runtime_contract.md §4.2）。

定位：Web（HTTP 适配层）、CLI、未来的定时触发器等调用方共用的任务入口。
本服务只做「受理裁决 + 显式上下文转发 + 控制委托」，不持有任务状态——
任务记录、事件流、门闸、保存保护仍由既有 TaskManager / main_task_gate /
conversation_manager 承载（契约 §2 状态责任表不变）。

兼容期说明：create_task 内部把 RuntimeContext 转换为既有
session_data 快照传入 create_chat_task；任务线程已改为显式 RuntimeIdentity
驱动资源装配（test_request_context 桥已拆除），session_data 快照仍承载
门闸 token、事件回放等内部指令的跨线程传递。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from modules.i18n import tr
from server.runtime.context import RuntimeContext


class RuntimeService:
    """公共任务入口。无状态：全部状态委托给 task_manager 单例。"""

    # ---- 受理 ----

    def create_task(self, ctx: RuntimeContext):
        """受理一轮 Run：校验显式上下文 → 互斥裁决（create_chat_task 内）→ 登记 → 起执行。

        抛错契约（适配层负责映射 HTTP 状态码）：
        - ValueError：上下文/参数非法（400）
        - RuntimeError：同对话已有运行中 chat 任务等业务冲突（409）
        """
        ctx.validate()
        # 延迟导入避免循环：server.tasks 的 blueprint 链不依赖本包
        from server.tasks import task_manager

        params = ctx.params
        return task_manager.create_chat_task(
            ctx.principal.username,
            ctx.principal.workspace_id,
            params.message,
            list(params.images or []),
            params.conversation_id,
            videos=list(params.videos or []),
            model_key=params.model_key,
            thinking_mode=params.thinking_mode,
            run_mode=params.run_mode,
            max_iterations=params.max_iterations,
            session_data=ctx.to_session_data(),
            message_source=params.message_source,
            goal_mode=params.goal_mode,
            skill_context_messages=list(params.skill_context_messages or []),
            files=list(params.files or []),
            task_type=params.task_type,
        )

    # ---- 控制 ----

    def cancel_task(self, username: str, task_id: str) -> bool:
        """停止主 Run（不触碰后台工作者；后台任务有独立控制入口）。"""
        from server.tasks import task_manager

        return task_manager.cancel_task(username, task_id)

    def enqueue_runtime_guidance(
        self, username: str, task_id: str, message: str, source: Optional[str] = None
    ) -> Dict[str, Any]:
        from server.tasks import task_manager

        return task_manager.enqueue_runtime_guidance(username, task_id, message, source=source)

    def enqueue_runtime_pending_message(
        self, username: str, task_id: str, message: str, files: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        from server.tasks import task_manager

        return task_manager.enqueue_runtime_pending_message(username, task_id, message, files=files)

    def remove_runtime_pending_message(self, username: str, task_id: str, message_id: str) -> Dict[str, Any]:
        from server.tasks import task_manager

        return task_manager.remove_runtime_pending_message(username, task_id, message_id)

    def promote_runtime_pending_to_guidance(self, username: str, task_id: str, message_id: str) -> Dict[str, Any]:
        from server.tasks import task_manager

        return task_manager.promote_runtime_pending_to_guidance(username, task_id, message_id)

    # ---- 观察（内部接口；后台调用方不必为观察任务发 HTTP 请求）----

    def get_task(self, username: str, task_id: str):
        from server.tasks import task_manager

        return task_manager.get_task(username, task_id)

    def get_task_events(
        self, username: str, task_id: str, offset: int
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[int], Optional[str]]:
        """按 offset 增量读取任务事件流（idx/offset 协议，与 REST 轮询同一语义）。

        返回 (events, next_offset, error)。error 非空表示任务不存在或无权访问。
        """
        from server.tasks import task_manager

        rec = task_manager.get_task(username, task_id)
        if not rec:
            return None, None, tr("tasks.task_not_found")
        events = task_manager.get_events_since(rec, max(0, int(offset or 0)))
        next_offset = events[-1]["idx"] + 1 if events else max(0, int(offset or 0))
        return events, next_offset, None


# 进程级单例（无状态，可安全共享）
runtime_service = RuntimeService()
