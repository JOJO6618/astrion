"""后台进程视图：LocalExecutionBackend.background 的稳定接口面。

定位：把 `modules.background_command_manager.BackgroundCommandManager` 的现有
方法原样暴露给执行层调用方（不新增语义、不改记录结构），让「后端」与「管理器」
在调用侧看起来是同一个对象，同时为未来远端实现保留同一份方法名。

约定：
- 本类只做转发，不复制记录、不缓存、不改状态；
- 全部方法为同步方法（管理器本身同步，阻塞等待由调用方用 asyncio.to_thread 包）；
- 未列出的管理器方法仍可经 `manager` 属性直达，`__getattr__` 也会转发，
  但契约面以本类显式方法为准。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class BackgroundProcesses:
    """`BackgroundCommandManager` 的执行层视图（纯转发）。"""

    # 契约面方法名（供探测/文档用）
    CONTRACT_METHODS = (
        "create_background_command",
        "cancel_command",
        "wait_for_completion",
        "poll_updates",
        "mark_notified",
        "unmark_notified",
        "mark_all_notified",
        "mark_claimed",
        "get_record",
        "get_record_with_output",
        "list_records",
        "has_pending_for_conversation",
        "list_waiting_items",
        "reconcile_stale_records",
    )

    def __init__(self, manager: Any) -> None:
        if manager is None:
            raise ValueError("BackgroundProcesses 需要有效的 background_command_manager。")
        self._manager = manager

    # ---- 访问器 ----

    @property
    def manager(self) -> Any:
        """底层 BackgroundCommandManager（逃生口，不推荐在契约层使用）。"""
        return self._manager

    @property
    def project_path(self) -> Any:
        return getattr(self._manager, "project_path", None)

    def __getattr__(self, name: str) -> Any:
        # 仅当本类没有该属性时触发：把未显式转发的方法交给管理器，
        # 避免上游管理器新增能力后立刻在本层「失联」。
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._manager, name)

    # ---- 创建 / 取消 / 等待 ----

    def create_background_command(self, **kwargs: Any) -> Dict[str, Any]:
        return self._manager.create_background_command(**kwargs)

    def cancel_command(self, command_id: str) -> Dict[str, Any]:
        return self._manager.cancel_command(command_id)

    def wait_for_completion(
        self,
        command_id: str,
        timeout_seconds: Optional[float] = None,
        claim: bool = False,
    ) -> Dict[str, Any]:
        return self._manager.wait_for_completion(command_id, timeout_seconds, claim)

    # ---- 查询 / 通知水位 ----

    def get_record(self, command_id: str) -> Optional[Dict[str, Any]]:
        return self._manager.get_record(command_id)

    def get_record_with_output(self, command_id: str) -> Optional[Dict[str, Any]]:
        return self._manager.get_record_with_output(command_id)

    def list_records(
        self, *, conversation_id: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """列出后台命令记录（与管理器同签名：关键字参数 conversation_id/limit）。"""
        return self._manager.list_records(conversation_id=conversation_id, limit=limit)

    def poll_updates(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._manager.poll_updates(conversation_id)

    def mark_notified(self, command_id: str) -> None:
        return self._manager.mark_notified(command_id)

    def unmark_notified(self, command_id: str) -> None:
        """清除单条记录的已通知水位（派发失败回滚）。"""
        return self._manager.unmark_notified(command_id)

    def mark_all_notified(self, conversation_id: Optional[str]) -> int:
        """全量标记该对话未通知终态记录，返回标记条数（不截断）。"""
        return self._manager.mark_all_notified(conversation_id)

    def mark_claimed(self, command_id: str) -> None:
        return self._manager.mark_claimed(command_id)

    def has_pending_for_conversation(self, conversation_id: Optional[str]) -> bool:
        return self._manager.has_pending_for_conversation(conversation_id)

    def list_waiting_items(self, conversation_id: Optional[str]) -> List[Dict[str, Any]]:
        return self._manager.list_waiting_items(conversation_id)

    def reconcile_stale_records(self, conversation_id: Optional[str] = None) -> int:
        return self._manager.reconcile_stale_records(conversation_id)

    # ---- 显式声明：进程回收能力 ----

    @property
    def supports_cancel(self) -> bool:
        """本实现支持按 command_id 取消（pid/进程组级）。"""
        return True


__all__ = ["BackgroundProcesses"]
