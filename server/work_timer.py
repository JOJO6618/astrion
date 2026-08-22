"""对话工作计时器持久化与空闲判定辅助函数。

把「对话真正停止运行（没有其它前台任务、也没有后台子智能体/后台命令/压缩）」
这一时刻的 work_timer 同步到磁盘，并刷新当前 ContextManager 内存中的副本，
避免刷新页面后计时器又恢复成「工作中」。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Set

from modules.sub_agent import TERMINAL_STATUSES as SUB_AGENT_TERMINAL_STATUSES


def has_running_background_work(terminal: Any, conversation_id: Optional[str]) -> bool:
    """检测指定对话是否还有未完成的后台工作（子智能体、后台命令、待通知完成项）。"""
    if not terminal or not conversation_id:
        return False

    sub_agent_manager = getattr(terminal, "sub_agent_manager", None)
    if sub_agent_manager:
        try:
            sub_agent_manager.reconcile_task_states(conversation_id=conversation_id)
            for task_info in sub_agent_manager.tasks.values():
                if task_info.get("conversation_id") != conversation_id:
                    continue
                status = task_info.get("status")
                if status not in SUB_AGENT_TERMINAL_STATUSES.union({"terminated"}):
                    return True
                # 已完成但尚未发出通知，对话仍应视为运行中
                if status in SUB_AGENT_TERMINAL_STATUSES and not task_info.get("notified"):
                    return True
        except Exception:
            pass

    bg_manager = getattr(terminal, "background_command_manager", None)
    if bg_manager:
        try:
            bg_manager.reconcile_stale_records(conversation_id=conversation_id)
            if bg_manager.has_pending_for_conversation(conversation_id):
                return True
        except Exception:
            pass

    return False


def finalize_conversation_work_timer(
    terminal: Any,
    conversation_id: Optional[str],
    *,
    finished_at: Optional[str] = None,
    exclude_task_id: Optional[str] = None,
    active_task_ids: Optional[Set[str]] = None,
) -> bool:
    """当对话真正停止运行时，把 work_timer 持久化到后端并同步内存。

    参数：
        terminal: 当前终端实例，需携带 context_manager。
        conversation_id: 要处理的对话 ID。
        finished_at: 完成时间 ISO 字符串，默认当前时间。
        exclude_task_id: 在判断「是否还有其它前台任务」时要排除的任务 ID。
        active_task_ids: 当前仍活跃的前台任务 ID 集合。若提供且除 exclude_task_id
                         外仍有其它任务，则视为对话仍在运行，不做持久化。
    """
    if not terminal or not conversation_id:
        return False

    # 1. 空闲判定：还有其它前台任务在跑？
    if active_task_ids:
        others = {tid for tid in active_task_ids if tid and tid != exclude_task_id}
        if others:
            return False

    # 2. 空闲判定：还有后台工作？
    if has_running_background_work(terminal, conversation_id):
        return False

    context_manager = getattr(terminal, "context_manager", None)
    if not context_manager:
        return False

    cm = getattr(context_manager, "conversation_manager", None)
    if not cm:
        return False

    now_iso = finished_at or datetime.now().isoformat()

    # 3. 持久化到磁盘并同步内存副本
    try:
        return cm.mark_latest_user_work_completed(
            conversation_id, now_iso, context_manager=context_manager
        )
    except Exception:
        return False
