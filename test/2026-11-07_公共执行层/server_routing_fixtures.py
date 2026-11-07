"""server 侧执行出口回归的公共夹具（非测试模块，不被 discover 收集）。

所有 terminal 都注入显式 backend spy；本机 ``background_command_manager`` /
``file_manager`` 换成「一被访问就记录并抛错」的 trap，配合 ``accessed`` 断言
本机路径从未被触达。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
_PROJECT_PATH = str(ROOT)


class LocalTrap:
    """本机执行入口陷阱：任何属性访问都会记录，绝不返回可用实现。"""

    def __init__(self, label: str) -> None:
        self.label = label
        self.accessed: List[str] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        self.accessed.append(name)
        raise AssertionError(f"{self.label} 本机入口被访问: {name}")


class SpyBackground:
    """后台替身：记录契约调用，返回可控结果。"""

    def __init__(
        self,
        *,
        updates: Optional[List[Dict[str, Any]]] = None,
        pending: bool = False,
        waiting: Optional[List[Dict[str, Any]]] = None,
        records: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.updates = list(updates or [])
        self.pending = pending
        self.waiting = list(waiting or [])
        self.records = list(records or [])
        self.calls: List[Any] = []

    def poll_updates(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        self.calls.append(("poll_updates", conversation_id))
        return [dict(item) for item in self.updates]

    def mark_notified(self, command_id: str) -> None:
        self.calls.append(("mark_notified", command_id))

    def unmark_notified(self, command_id: str) -> None:
        self.calls.append(("unmark_notified", command_id))

    def mark_all_notified(self, conversation_id: Optional[str]) -> int:
        self.calls.append(("mark_all_notified", conversation_id))
        return len(self.records)

    def has_pending_for_conversation(self, conversation_id: Optional[str]) -> bool:
        self.calls.append(("has_pending_for_conversation", conversation_id))
        return self.pending

    def reconcile_stale_records(self, conversation_id: Optional[str] = None) -> int:
        self.calls.append(("reconcile_stale_records", conversation_id))
        return 0

    def list_waiting_items(self, conversation_id: Optional[str]) -> List[Dict[str, Any]]:
        self.calls.append(("list_waiting_items", conversation_id))
        return [dict(item) for item in self.waiting]

    def list_records(self, *, conversation_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        self.calls.append(("list_records", conversation_id, limit))
        return [dict(item) for item in self.records]

    def cancel_command(self, command_id: str) -> Dict[str, Any]:
        self.calls.append(("cancel_command", command_id))
        return {"status": "cancelled", "command_id": command_id}

    def get_record(self, command_id: str) -> Optional[Dict[str, Any]]:
        self.calls.append(("get_record", command_id))
        for record in self.records:
            if record.get("command_id") == command_id:
                return dict(record)
        return None

    def get_record_with_output(self, command_id: str) -> Optional[Dict[str, Any]]:
        self.calls.append(("get_record_with_output", command_id))
        for record in self.records:
            if record.get("command_id") == command_id:
                payload = dict(record)
                payload["output"] = "synthetic output"
                return payload
        return None


class SpyFiles:
    def __init__(self, *, inspected: Optional[Dict[str, Any]] = None, content: Any = "") -> None:
        self.content_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        self.inspected = dict(inspected or {
            "success": True,
            "path": "demo.py",
            "relative_path": "demo.py",
            "exists": True,
            "is_file": True,
            "is_dir": False,
            "within_workspace": True,
            "size": len(self.content_bytes),
            "suffix": ".py",
        })
        self.calls: List[Any] = []

    def inspect_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        self.calls.append(("inspect_path", path, access))
        return dict(self.inspected)

    def resolve_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        self.calls.append(("resolve_path", path, access))
        if not self.inspected.get("within_workspace", True):
            return {"success": False, "error": "synthetic-outside"}
        relative = self.inspected.get("relative_path")
        return {
            "success": True,
            "path": relative or path,
            "relative_path": relative,
            "access": access,
            "within_workspace": True,
        }

    def read_binary(self, path: str) -> bytes:
        self.calls.append(("read_binary", path))
        return self.content_bytes


class SpyBackend:
    def __init__(self, *, background: Any = None, files: Any = None) -> None:
        self.background = background
        self.files = files


def make_terminal(backend: SpyBackend, *, data_dir: Optional[str] = None) -> SimpleNamespace:
    """带显式 backend 的 terminal；本机入口全部换成 trap。"""
    return SimpleNamespace(
        execution_backend=backend,
        background_command_manager=LocalTrap("background_command_manager"),
        file_manager=LocalTrap("file_manager"),
        data_dir=data_dir or tempfile.mkdtemp(prefix="routing-test-"),
        context_manager=SimpleNamespace(
            current_conversation_id="conv_synthetic",
            add_conversation=lambda *a, **k: {"role": "user"},
        ),
        project_path=_PROJECT_PATH,
    )


class SpyShallowVersioningManager:
    """ShallowVersioningManager 替身：只记录构造参数与 rewind 目标。"""

    instances: List["SpyShallowVersioningManager"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.rewound: List[str] = []
        SpyShallowVersioningManager.instances.append(self)

    def rewind(self, message_id: str) -> Dict[str, Any]:
        self.rewound.append(message_id)
        return {"success": True, "message_id": message_id, "files_changed": ["tracked.txt"]}
