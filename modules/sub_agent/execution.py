"""Per-instance tool resources without mutating the parent terminal.

子实例的内置执行资源完全独立装配：
- 专属 FileManager / TerminalOperator / TerminalManager / BackgroundCommandManager
  （固定 scope、子目录写根、no-follow 原生 IO 全部沿用原链路，不与父共享对象）；
- 装配 `LocalExecutionBackend(child)` 包住上述专属资源，子实例不再把
  `execution_backend` 置空成“无后端”的隐式本地链路。

非本地父后端（远端替身等）不会被静默重建成本地工具执行：当前明确 fail-closed，
后续远程子实例需要在后端实现受限 `fork_child(scope)` 返回隔离后端后才能创建。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

from modules.background_command_manager import BackgroundCommandManager
from modules.execution_scope import ACCESS_LEVELS, ExecutionScope, bind_execution_scope
from modules.file_manager import FileManager
from modules.terminal_ops import TerminalOperator
from modules.terminal_manager import TerminalManager
from modules.sub_agent.toolkit import SUB_AGENT_TOOLS

_ALLOWED_TOOLS = frozenset(tool["function"]["name"] for tool in SUB_AGENT_TOOLS)


def _require_local_parent_backend(manager: Any) -> None:
    """父对话注入了非本地后端时拒绝创建子实例，绝不回退本机执行。

    `is_local_backend` 只承认受信 `LocalExecutionBackend` 类本身（精确类型），
    任何自称 `is_local=True` 的对象或子类都不会被当作本地后端。
    """
    parent = manager.terminal
    if getattr(parent, "execution_backend", None) is None:
        return
    from modules.execution_plane import is_local_backend

    if is_local_backend(parent):
        return
    raise ValueError(
        "父对话已注入非本地执行后端，子智能体不能静默回退到本机工具执行。"
        "该后端需实现受限的 fork_child(scope) 返回隔离后端后再创建子智能体；"
        "当前不提供远程子实例创建与持久化路径。"
    )


def build_child_terminal(manager: Any, record: Dict[str, Any]) -> Any:
    parent = manager.terminal
    from modules.execution_scope import uses_docker
    if uses_docker(parent) and not (
        manager.container_session and manager.container_session.mode == "docker"
        and manager.container_session.container_name
    ):
        raise ValueError("子智能体工具箱未就绪，不能回退到宿主机执行。")
    _require_local_parent_backend(manager)
    from modules.scoped_execution_policy import fixed_workspace_root
    root = fixed_workspace_root(ExecutionScope(
        executor_kind="sub_agent", actor_id=str(record.get("task_id", "")),
        workspace_root=Path(record["workspace_root"]), access_level=record["access_level"],
        working_dir=Path(record["working_dir"]) if record.get("working_dir") else None,
    ))
    child = copy.copy(parent)
    child.custom_tools_enabled = False
    child.custom_tool_registry = None
    child.custom_tool_executor = None
    child.project_path = str(root)
    child.context_manager = copy.copy(parent.context_manager)
    child.context_manager.project_path = root
    child.context_manager.main_terminal = child
    # 专属执行资源：不复用父对象，避免父子共享终端/文件/后台进程状态。
    child.file_manager = FileManager(str(root), container_session=manager.container_session, data_dir=str(manager.data_dir))
    child.terminal_ops = TerminalOperator(str(root), container_session=manager.container_session, data_dir=str(manager.data_dir))
    child.terminal_manager = TerminalManager(
        project_path=str(root), container_session=manager.container_session,
        network_permission_getter=parent.get_network_permission,
        terminal_readonly_getter=lambda: False,
        command_validator=child.terminal_ops._validate_command,
    )
    child.background_command_manager = BackgroundCommandManager(project_path=str(root))
    mode = "direct" if record["access_level"] == "full_access" else "sandbox"
    child.file_manager.set_host_execution_mode(mode)
    child.terminal_ops.set_host_execution_mode(mode)
    child.terminal_manager.set_host_execution_mode(mode)
    child.terminal_ops.attach_terminal_manager(child.terminal_manager)
    child._apply_execution_mode_to_runtime = lambda: None
    child.get_permission_mode = lambda: "unrestricted"
    child.get_execution_mode = lambda: mode
    child.current_permission_mode = "unrestricted"
    child.host_execution_mode = mode
    # 装配子实例自己的本地后端（owner 必须是 child 本身，包住上面新建的资源）。
    from modules.execution_plane import LocalExecutionBackend
    backend = LocalExecutionBackend(child)
    if backend.owner is not child or not getattr(backend, "is_local", False):
        raise ValueError("子智能体执行后端装配失败：本地后端未绑定到子实例自身资源。")
    child.execution_backend = backend
    return child


async def execute_child_tool(manager: Any, name: str, arguments: Dict[str, Any], instance: Any) -> str:
    scope = getattr(instance, "execution_scope", None)
    if instance.manager is not manager or scope is None or scope.access_level not in ACCESS_LEVELS:
        raise ValueError("子智能体没有有效的固定权限身份，请重新创建。")
    record = {"task_id": instance.task_id, "access_level": scope.access_level,
              "workspace_root": str(scope.workspace_root),
              "working_dir": str(scope.working_dir) if scope.working_dir else None}
    if name not in _ALLOWED_TOOLS:
        raise ValueError("该工具不属于此子智能体的工具集。")
    if "request_full_access" in arguments or "full_access_reason" in arguments:
        raise ValueError("子智能体工具参数不允许申请权限升级。")
    if name == "run_command" and arguments.get("run_in_background"):
        raise ValueError("此子智能体工具集未开放后台命令。")
    child = getattr(instance, "_execution_terminal", None)
    if child is None:
        child = build_child_terminal(manager, record)
        instance._execution_terminal = child
    with bind_execution_scope(scope):
        if name == "read_mediafile":
            from modules.sub_agent.tools import handle_read_mediafile
            # 媒体读取走子实例自己的后端文件视图：inspect_path 先授权再取元数据，
            # read_binary 复用同一安全读取；这里不做本地 exists/resolve。
            return json.dumps(
                await handle_read_mediafile(
                    scope.workspace_root, arguments, backend=child.execution_backend,
                ),
                ensure_ascii=False,
            )
        return await child.handle_tool_call(name, arguments)
