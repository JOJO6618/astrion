"""内存后台进程替身：``FakeExecutionBackend.background`` 的实现。

只有记录与状态机，没有线程、没有子进程、没有轮询线程。状态流转由测试显式驱动
（``complete`` / ``fail`` / ``cancel``），因此断言是确定性的。

与真实 ``BackgroundCommandManager`` 的差异（测试必须看得见）
-----------------------------------------------------------
- 不启动进程，``pid`` 恒为 ``None``，``output`` 由用例给定而不是进程输出；
- 不做超时判定：``wait_for_completion`` 不做真实等待，只按 ``auto_complete``
  决定是否把 ``running`` 直接翻成 ``completed``；
- 不落盘持久化（真实管理器把记录写进会话目录），重开进程即丢失；
- 不模拟通知水位持久化，``notified``/``claimed`` 只在内存;
- ``reconcile_stale_records`` 不检查真实进程存活，一律把残留 ``running`` 标成
  固定原因（``"fake-stale"``）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

TERMINAL_STATUSES = ("completed", "failed", "cancelled", "timeout")


class FakeBackground:
    """后台命令记录表（内存）。

    Parameters
    ----------
    clock:
        可注入时钟（epoch 秒），默认 ``time.time``。
    auto_complete:
        ``wait_for_completion`` 是否把仍在 ``running`` 的记录直接完成
        （默认 True，方便断言"等待后拿到输出"）。False 时返回
        ``{"success": False, "status": "running"}``，模拟超时。
    """

    def __init__(self, *, clock: Optional[Any] = None, auto_complete: bool = True) -> None:
        self._clock = clock or time.time
        self.auto_complete = auto_complete
        self.records: Dict[str, Dict[str, Any]] = {}
        self.calls: List[Dict[str, Any]] = []
        self._seq = 0

    # ------------------------------------------------------------------
    # 创建与状态驱动（测试专用，不在协议里）
    # ------------------------------------------------------------------
    def create_record(
        self,
        command: str,
        *,
        conversation_id: Optional[str] = None,
        timeout: Optional[float] = None,
        output: str = "",
        auto_complete: bool = False,
    ) -> Dict[str, Any]:
        """登记一条后台记录；默认保持 ``running``。"""
        self._seq += 1
        command_id = f"fake_bg_{self._seq:04d}"
        now = float(self._clock())
        record = {
            "command_id": command_id,
            "command": command,
            "status": "completed" if auto_complete else "running",
            "output": output,
            "return_code": 0 if auto_complete else None,
            "pid": None,
            "created_at": now,
            "started_at": now,
            "finished_at": now if auto_complete else None,
            "timeout_seconds": timeout,
            "conversation_id": conversation_id,
            "is_running": not auto_complete,
            "notified": False,
            "claimed": False,
        }
        self.records[command_id] = record
        return dict(record)

    def complete(self, command_id: str, output: str, return_code: int = 0) -> Dict[str, Any]:
        record = self.records.get(command_id)
        if record is None:
            return {"success": False, "error": "command not found"}
        record.update({
            "status": "completed",
            "output": output,
            "return_code": return_code,
            "is_running": False,
            "finished_at": float(self._clock()),
        })
        return {"success": True, "command_id": command_id, "status": "completed"}

    def fail(self, command_id: str, output: str, return_code: int = 1) -> Dict[str, Any]:
        record = self.records.get(command_id)
        if record is None:
            return {"success": False, "error": "command not found"}
        record.update({
            "status": "failed",
            "output": output,
            "return_code": return_code,
            "is_running": False,
            "finished_at": float(self._clock()),
        })
        return {"success": True, "command_id": command_id, "status": "failed"}

    # ------------------------------------------------------------------
    # BackgroundBackend 协议
    # ------------------------------------------------------------------
    def get_record(self, command_id: str) -> Optional[Dict[str, Any]]:
        record = self.records.get(command_id)
        return dict(record) if record is not None else None

    def get_record_with_output(self, command_id: str) -> Optional[Dict[str, Any]]:
        return self.get_record(command_id)

    def list_records(
        self,
        *,
        conversation_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        items = []
        for record in self.records.values():
            if conversation_id is not None and record.get("conversation_id") != conversation_id:
                continue
            items.append(dict(record))
        max_limit = max(1, min(int(limit or 200), 1000))
        return sorted(items, key=lambda item: item["created_at"], reverse=True)[:max_limit]

    def wait_for_completion(
        self,
        command_id: str,
        timeout_seconds: Optional[float] = None,
        claim: bool = False,
    ) -> Dict[str, Any]:
        self.calls.append({
            "op": "wait_for_completion",
            "command_id": command_id,
            "timeout_seconds": timeout_seconds,
            "claim": claim,
        })
        record = self.records.get(command_id)
        if record is None:
            return {"success": False, "error": "command not found"}
        if record["status"] == "running" and self.auto_complete:
            record.update({
                "status": "completed",
                "output": record.get("output") or f"[fake-bg] {record['command']}",
                "return_code": 0,
                "is_running": False,
                "finished_at": float(self._clock()),
            })
        if record["status"] == "running":
            return {
                "success": False,
                "command_id": command_id,
                "status": "running",
                "waited_seconds": 0.0,
                "timeout": True,
            }
        if claim:
            record["claimed"] = True
        return {
            "success": record["status"] == "completed",
            "command_id": command_id,
            "status": record["status"],
            "output": record.get("output", ""),
            "return_code": record.get("return_code"),
            "waited_seconds": 0.0,
            "timeout": False,
        }

    def cancel_command(self, command_id: str) -> Dict[str, Any]:
        self.calls.append({"op": "cancel_command", "command_id": command_id})
        record = self.records.get(command_id)
        if record is None:
            return {"success": False, "error": "command not found"}
        if record["status"] in TERMINAL_STATUSES:
            return {
                "success": False,
                "command_id": command_id,
                "status": record["status"],
                "error": f"command already {record['status']}",
            }
        record.update({
            "status": "cancelled",
            "is_running": False,
            "finished_at": float(self._clock()),
        })
        return {"success": True, "command_id": command_id, "status": "cancelled"}

    def poll_updates(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        updates = []
        for record in self.records.values():
            if record["status"] in ("running",):
                continue
            if record.get("notified"):
                continue
            if conversation_id is not None and record.get("conversation_id") != conversation_id:
                continue
            updates.append({
                "command_id": record["command_id"],
                "status": record["status"],
                "output": record.get("output", ""),
                "return_code": record.get("return_code"),
            })
        return updates

    def mark_notified(self, command_id: str) -> None:
        record = self.records.get(command_id)
        if record is not None:
            record["notified"] = True

    def unmark_notified(self, command_id: str) -> None:
        """把通知水位回退（例如通知发送失败需要重投）。"""
        record = self.records.get(command_id)
        if record is not None:
            record["notified"] = False

    def mark_all_notified(self, conversation_id: Optional[str]) -> int:
        """把该对话下**全部**未通知终态记录标为已通知，返回标记数量。

        语义与新增契约一致：
        - 只处理终态（非 ``running``）且 ``notified`` 为假的记录；
        - 不截断、不分页（不能只处理前 1000 条）；
        - ``conversation_id`` 为 None 时匹配 ``conversation_id is None`` 的记录，
          而不是“全部对话”。
        """
        count = 0
        for record in self.records.values():
            if record["status"] == "running":
                continue
            if record.get("conversation_id") != conversation_id:
                continue
            if record.get("notified"):
                continue
            record["notified"] = True
            count += 1
        return count

    def mark_claimed(self, command_id: str) -> None:
        record = self.records.get(command_id)
        if record is not None:
            record["claimed"] = True

    def has_pending_for_conversation(self, conversation_id: Optional[str]) -> bool:
        return any(
            record["status"] == "running"
            and record.get("conversation_id") == conversation_id
            for record in self.records.values()
        )

    def list_waiting_items(self, conversation_id: Optional[str]) -> List[Dict[str, Any]]:
        return [
            {
                "command_id": record["command_id"],
                "command": record["command"],
                "created_at": record["created_at"],
            }
            for record in self.records.values()
            if record["status"] == "running"
            and record.get("conversation_id") == conversation_id
        ]

    def reconcile_stale_records(self, conversation_id: Optional[str] = None) -> int:
        count = 0
        for record in self.records.values():
            if record["status"] != "running":
                continue
            if conversation_id is not None and record.get("conversation_id") != conversation_id:
                continue
            record.update({
                "status": "failed",
                "is_running": False,
                "return_code": None,
                "output": record.get("output", ""),
                "stale_reason": "fake-stale",
                "finished_at": float(self._clock()),
            })
            count += 1
        return count

    @property
    def supports_cancel(self) -> bool:
        return True


__all__ = ["FakeBackground", "TERMINAL_STATUSES"]
