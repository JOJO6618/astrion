"""Bind approved tool calls to trusted, non-reusable execution authority."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from modules.execution_scope import (
    ExecutionScope, FullAccessGrant, bind_execution_scope, command_fingerprint,
)


def approval_execution_scope(
    terminal: Any, arguments: Dict[str, Any], item: Dict[str, Any],
    *, task_id: str, tool_call_id: str, expected_root: Path,
    manager: Any, tool_name: str, fingerprint: Optional[str] = None,
) -> ExecutionScope:
    if item.get("status") != "approved":
        raise ValueError("工具审批未通过。")
    cm = terminal.context_manager
    root = Path(cm.project_path).resolve()
    if (
        root != Path(expected_root)
        or item.get("workspace_root") != str(root)
        or item.get("executor_id") != id(terminal)
        or item.get("conversation_id") != cm.current_conversation_id
        or item.get("tool_name") != tool_name
        or item.get("task_id") != task_id
        or item.get("tool_call_id") != tool_call_id
        or item.get("arguments") != arguments
    ):
        raise ValueError("审批不属于当前工作区、任务或工具参数。")
    full = item.get("approval_type") == "full_access"
    if full and (
        tool_name != "run_command" or not fingerprint
        or command_fingerprint(arguments) != fingerprint
    ):
        raise ValueError("单次完全访问的命令与审批快照不一致。")
    # The manager validates both decisions and claims the persisted snapshot
    # under one lock. An approved dict alone cannot mint execution authority.
    manager.claim_execution(item.get("approval_id"), item)
    grant = None
    if full:
        grant = FullAccessGrant(
            terminal_id=id(terminal), conversation_id=cm.current_conversation_id,
            task_id=task_id, tool_call_id=tool_call_id, workspace_root=root,
            fingerprint=fingerprint,
        )
    return ExecutionScope(
        executor_kind="main", actor_id=str(id(terminal)), workspace_root=root,
        access_level="full_access" if full else "sandbox_write",
        conversation_id=cm.current_conversation_id, task_id=task_id,
        tool_call_id=tool_call_id, write_granted=True,
        network_granted=full, full_access_grant=grant,
    )


@dataclass(frozen=True)
class DeniedExecution:
    error: str


def resolve_approval_authority(*args, **kwargs):
    try:
        return approval_execution_scope(*args, **kwargs)
    except (ValueError, KeyError) as exc:
        return DeniedExecution(str(exc))


async def execute_with_authority(
    terminal: Any, tool_name: str, arguments: Dict[str, Any],
    scope: Optional[ExecutionScope | DeniedExecution], stop_check=None,
) -> str:
    if stop_check is not None and stop_check():
        return json.dumps({"success": False, "code": "task_cancelled", "error": "任务已取消，未启动工具。"}, ensure_ascii=False)
    if isinstance(scope, DeniedExecution):
        return json.dumps({"success": False, "code": "approval_authority_denied", "error": scope.error}, ensure_ascii=False)
    if scope and scope.full_access_grant and tool_name != "run_command":
        return json.dumps({"success": False, "code": "full_access_denied", "error": "单次完全访问仅可用于已审批命令。"}, ensure_ascii=False)
    with bind_execution_scope(scope):
        return await terminal.handle_tool_call(tool_name, arguments)
