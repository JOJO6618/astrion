"""Trusted, task-local tool execution authority; never populated from tool arguments."""
from __future__ import annotations

import hashlib
import json
import math
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

ACCESS_LEVELS = frozenset({"workspace_write", "sandbox_write", "full_access"})


def command_fingerprint(arguments: Mapping[str, Any]) -> str:
    execution = {
        "command": arguments.get("command"),
        "timeout": arguments.get("timeout"),
        "run_in_background": bool(arguments.get("run_in_background", False)),
        "working_dir": arguments.get("working_dir"),
        "request_full_access": arguments.get("request_full_access", False),
        "full_access_reason": arguments.get("full_access_reason"),
    }
    encoded = json.dumps(execution, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class FullAccessGrant:
    """One-use authorization bound to a concrete executor and approved command."""

    terminal_id: int
    conversation_id: Optional[str]
    task_id: str
    tool_call_id: str
    workspace_root: Path
    fingerprint: str
    _used: bool = field(default=False, init=False, repr=False)
    _lock: Any = field(default_factory=threading.Lock, init=False, repr=False)

    def consume(self, terminal: Any, arguments: Mapping[str, Any]) -> bool:
        with self._lock:
            cm = getattr(terminal, "context_manager", None)
            scope = current_execution_scope()
            if (
                scope is None or scope.is_sub_agent
                or scope.task_id != self.task_id or scope.tool_call_id != self.tool_call_id
                or self._used
                or id(terminal) != self.terminal_id
                or getattr(cm, "current_conversation_id", None) != self.conversation_id
                or Path(getattr(cm, "project_path", terminal.project_path)).resolve() != self.workspace_root
                or command_fingerprint(arguments) != self.fingerprint
            ):
                return False
            self._used = True
            return True


@dataclass(frozen=True)
class ExecutionScope:
    executor_kind: str
    actor_id: str
    workspace_root: Path
    access_level: str
    conversation_id: Optional[str] = None
    task_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    write_granted: bool = False
    network_granted: bool = False
    full_access_grant: Optional[FullAccessGrant] = None

    @property
    def is_sub_agent(self) -> bool:
        return self.executor_kind == "sub_agent"

    @property
    def workspace_only(self) -> bool:
        return self.is_sub_agent and self.access_level == "workspace_write"

    @property
    def execution_mode(self) -> str:
        return "direct" if self.access_level == "full_access" else "sandbox"


_execution_scope: ContextVar[Optional[ExecutionScope]] = ContextVar("tool_execution_scope", default=None)


def current_execution_scope() -> Optional[ExecutionScope]:
    return _execution_scope.get()


@contextmanager
def bind_execution_scope(scope: Optional[ExecutionScope]) -> Iterator[None]:
    token = _execution_scope.set(scope)
    try:
        yield
    finally:
        _execution_scope.reset(token)


def uses_docker(terminal: Any) -> bool:
    session = getattr(terminal, "container_session", None)
    mode = getattr(session, "mode", None)
    if mode is not None:
        return mode == "docker"
    from config import TERMINAL_SANDBOX_MODE
    return TERMINAL_SANDBOX_MODE == "docker"


def validate_child_access(terminal: Any, access_level: Any) -> str:
    if not isinstance(access_level, str) or access_level not in ACCESS_LEVELS:
        raise ValueError("创建子智能体必须明确选择 access_level：workspace_write、sandbox_write 或 full_access。")
    permission = terminal.get_permission_mode()
    work_mode = terminal.get_work_mode()
    if permission == "readonly" or work_mode == "plan":
        allowed = {"workspace_write"}
    elif terminal.get_execution_mode() != "direct":
        allowed = {"workspace_write", "sandbox_write"}
    else:
        allowed = ACCESS_LEVELS
    if access_level == "full_access" and (
        permission != "unrestricted" or uses_docker(terminal)
    ):
        raise ValueError("当前环境不支持创建完全访问的子智能体。")
    if access_level not in allowed:
        raise ValueError("子智能体所选权限超过当前允许创建的权限范围。")
    return str(access_level)


def child_access_prompt(access_level: str) -> str:
    descriptions = {
        "workspace_write": "沙箱，仅创建时绑定的工作区根目录内可写；区外禁止写入。读取遵循当前路径授权。",
        "sandbox_write": "沙箱，按当前路径授权范围读写。",
        "full_access": "宿主机完全访问，拥有运行 Astrion 的系统账户权限。",
    }
    return "\n\n[固定执行权限]\n" + descriptions[access_level] + "主智能体后续切换权限或执行环境不会改变你的档位。"


def validate_full_access_request(terminal: Any, arguments: Mapping[str, Any]) -> bool:
    """Return whether explicit human authorization is needed, or fail closed."""
    request = arguments.get("request_full_access", False)
    if not isinstance(request, bool):
        raise ValueError("request_full_access 必须是布尔值。")
    if not request:
        return False
    scope = current_execution_scope()
    if scope is not None and scope.is_sub_agent:
        raise ValueError("子智能体不能申请单次完全访问。")
    if terminal.get_permission_mode() == "readonly" or terminal.get_work_mode() == "plan":
        raise ValueError("计划或只读状态不能申请单次完全访问。")
    reason = arguments.get("full_access_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("申请单次完全访问必须提供非空 full_access_reason。")
    if not isinstance(arguments.get("command"), str) or not arguments["command"].strip():
        raise ValueError("申请单次完全访问必须提供实际命令。")
    background = arguments.get("run_in_background", False)
    timeout = arguments.get("timeout")
    if not isinstance(background, bool):
        raise ValueError("run_in_background 必须是布尔值。")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= (3600 if background else 120):
        raise ValueError("请提供有效的命令超时时间：前台不超过120秒，后台不超过3600秒。")
    if uses_docker(terminal) or getattr(terminal, "execution_backend", None) is not None:
        raise ValueError("当前执行环境不支持宿主机单次完全访问。")
    return terminal.get_execution_mode() != "direct"
