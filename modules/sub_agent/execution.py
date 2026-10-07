"""Per-instance tool resources without mutating the parent terminal."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

from modules.execution_scope import ACCESS_LEVELS, ExecutionScope, bind_execution_scope
from modules.file_manager import FileManager
from modules.terminal_ops import TerminalOperator
from modules.terminal_manager import TerminalManager
from modules.sub_agent.toolkit import SUB_AGENT_TOOLS

_ALLOWED_TOOLS = frozenset(tool["function"]["name"] for tool in SUB_AGENT_TOOLS)


def build_child_terminal(manager: Any, record: Dict[str, Any]) -> Any:
    parent = manager.terminal
    from modules.execution_scope import uses_docker
    if uses_docker(parent) and not (
        manager.container_session and manager.container_session.mode == "docker"
        and manager.container_session.container_name
    ):
        raise ValueError("子智能体工具箱未就绪，不能回退到宿主机执行。")
    from modules.scoped_execution_policy import fixed_workspace_root
    root = fixed_workspace_root(ExecutionScope(
        executor_kind="sub_agent", actor_id=str(record.get("task_id", "")),
        workspace_root=Path(record["workspace_root"]), access_level=record["access_level"],
    ))
    child = copy.copy(parent)
    child.custom_tools_enabled = False
    child.custom_tool_registry = None
    child.custom_tool_executor = None
    child.project_path = str(root)
    child.context_manager = copy.copy(parent.context_manager)
    child.context_manager.project_path = root
    child.context_manager.main_terminal = child
    child.file_manager = FileManager(str(root), container_session=manager.container_session, data_dir=str(manager.data_dir))
    child.terminal_ops = TerminalOperator(str(root), container_session=manager.container_session, data_dir=str(manager.data_dir))
    child.terminal_manager = TerminalManager(
        project_path=str(root), container_session=manager.container_session,
        network_permission_getter=parent.get_network_permission,
        terminal_readonly_getter=lambda: False,
        command_validator=child.terminal_ops._validate_command,
    )
    mode = "direct" if record["access_level"] == "full_access" else "sandbox"
    child.file_manager.set_host_execution_mode(mode)
    child.terminal_ops.set_host_execution_mode(mode)
    child.terminal_manager.set_host_execution_mode(mode)
    child.terminal_ops.attach_terminal_manager(child.terminal_manager)
    child.execution_backend = None
    child._apply_execution_mode_to_runtime = lambda: None
    child.get_permission_mode = lambda: "unrestricted"
    child.get_execution_mode = lambda: mode
    child.current_permission_mode = "unrestricted"
    child.host_execution_mode = mode
    return child


async def execute_child_tool(manager: Any, name: str, arguments: Dict[str, Any], instance: Any) -> str:
    scope = getattr(instance, "execution_scope", None)
    if instance.manager is not manager or scope is None or scope.access_level not in ACCESS_LEVELS:
        raise ValueError("子智能体没有有效的固定权限身份，请重新创建。")
    record = {"access_level": scope.access_level, "workspace_root": str(scope.workspace_root)}
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
            path = str(arguments.get("path") or "")
            valid, error, resolved = child.file_manager._validate_path(path)
            if not valid:
                raise ValueError(error)
            allowed, error = child.file_manager._ensure_host_access(resolved, "read")
            if not allowed:
                raise ValueError(error)
            import json
            return json.dumps(await handle_read_mediafile(scope.workspace_root, arguments, file_manager=child.file_manager), ensure_ascii=False)
        return await child.handle_tool_call(name, arguments)
