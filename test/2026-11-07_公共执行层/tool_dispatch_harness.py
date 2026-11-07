"""工具分发路由测试夹具（2026-11-07）。

目标
----
用真实的 ``MainTerminalToolsExecutionMixin`` + ``MainTerminalToolsReadMixin``
调用 ``handle_tool_call``，证明每个工具都落到 ``get_execution_backend(self)``
暴露的协议方法上，而不是控制端的 ``file_manager`` / ``terminal_manager`` /
``terminal_ops`` / ``background_command_manager``。

关键设计
--------
1. 控制端资源用"毒化对象"：任何属性访问都抛 ``PoisonedAccessError``。
   该异常继承 ``BaseException`` 而不是 ``Exception``，因此不会被工具分发里
   密集的 ``except Exception: pass`` 静默吞掉——一旦有人回退到控制端资源，
   测试必然报错，而不是"看起来通过"。
2. 执行面用 ``FakeExecutionBackend``（内存实现，不碰真实磁盘/子进程）或
   其 spy 子类，断言协议方法被调用、参数被原样透传。
3. 运行态隔离复用 ``read_routing_env.prepare_isolated_runtime``，不读真实
   ``.env`` / ``settings.json``。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 必须在导入 config / modules 之前隔离运行态。
from read_routing_env import prepare_isolated_runtime  # noqa: E402

TEST_HOME = prepare_isolated_runtime("astrion_tool_dispatch_")

from core.main_terminal_parts.tools_execution import (  # noqa: E402
    MainTerminalToolsExecutionMixin,
)
from core.main_terminal_parts.tools_read import MainTerminalToolsReadMixin  # noqa: E402
from modules.execution_plane.fake import FakeExecutionBackend  # noqa: E402


class PoisonedAccessError(BaseException):
    """控制端资源被访问。继承 BaseException，确保不会被 except Exception 吞掉。"""


class PoisonedResource:
    """毒化代理：任何属性访问都视为"工具绕过执行后端直连控制端资源"。"""

    def __init__(self, label: str) -> None:
        object.__setattr__(self, "_label", label)

    def __getattr__(self, name: str) -> Any:
        raise PoisonedAccessError(
            f"工具分发访问了控制端资源 {object.__getattribute__(self, '_label')}.{name}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise PoisonedAccessError(
            f"工具分发写入了控制端资源 {object.__getattribute__(self, '_label')}.{name}"
        )

    def __repr__(self) -> str:  # 失败信息里也保持清晰
        return f"<PoisonedResource {object.__getattribute__(self, '_label')}>"


class ContextManagerStub:
    """只提供分发路径真正会碰到的 ContextManager 能力。"""

    def __init__(self, *, project_path: str = "/workspace") -> None:
        self.conversation_metadata: Dict[str, Any] = {}
        self.project_path = project_path
        self.current_conversation_id: Optional[str] = "conv-dispatch"
        self.current_shallow_message_id = None
        self.conversation_manager = None
        self.file_annotations: Dict[str, str] = {}
        self.saved_annotations = 0
        self.load_file_calls: list = []
        self._web_terminal_callback = None

    def load_file(self, path: str, content: Any = None) -> None:
        self.load_file_calls.append((path, content))

    def _get_meta_flag(self, key: str, default: Any = None) -> Any:
        return self.conversation_metadata.get(key, default)

    def _set_meta_flag(self, key: str, value: Any) -> None:
        self.conversation_metadata[key] = value

    def update_annotation(self, path: str, annotation: str) -> None:
        self.file_annotations[path] = annotation

    def save_annotations(self) -> None:
        self.saved_annotations += 1


class RegistryStub:
    """自定义工具注册表替身：可注入工具定义。"""

    def __init__(self, tools: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.tools = dict(tools or {})
        self.reloads = 0

    def reload(self) -> None:
        self.reloads += 1

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        return self.tools.get(name)


class SpyBackend(FakeExecutionBackend):
    """记录 MCP / 自定义工具调用，用哨兵返回值证明"确实走了后端"。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.mcp_calls: list = []
        self.custom_calls: list = []
        self.mcp_response: Dict[str, Any] = {
            "success": True,
            "served_by": "spy-backend",
            "server": "demo",
            "tool": "echo",
        }
        self.custom_response: Dict[str, Any] = {
            "success": True,
            "served_by": "spy-backend",
            "output": "custom-output",
        }

    async def execute_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        self.mcp_calls.append({"tool_name": tool_name, "arguments": dict(arguments)})
        return dict(self.mcp_response)

    async def execute_custom_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        self.custom_calls.append({"tool_name": tool_name, "arguments": dict(arguments)})
        return dict(self.custom_response)


class _DenyingFiles:
    """包装内存文件系统：对指定路径的元数据探测直接失败（权限不足/不可达）。"""

    def __init__(self, inner: Any, denied: set) -> None:
        self._inner = inner
        self._denied = set(denied)

    def inspect_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        key = str(path or "").strip().lstrip("/")
        if key in self._denied:
            return {"success": False, "error": "permission denied by executor"}
        return self._inner.inspect_path(path, access)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class DenyInspectBackend(FakeExecutionBackend):
    """元数据探测被拒的后端：用于验证"拒绝 ≠ 新文件"。"""

    def __init__(self, denied_paths: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._denied_paths = set(denied_paths)
        self._denying_files = _DenyingFiles(self._files, self._denied_paths)

    @property
    def files(self) -> Any:
        return self._denying_files


class ToolDispatchTerminal(MainTerminalToolsExecutionMixin, MainTerminalToolsReadMixin):
    """真实分发 mixin + 毒化控制端资源的最小终端替身。"""

    def __init__(
        self,
        backend: Any,
        *,
        context_manager: Optional[ContextManagerStub] = None,
        data_dir: Optional[str] = None,
        project_path: str = "/workspace",
    ) -> None:
        self.execution_backend = backend
        self.context_manager = context_manager or ContextManagerStub(project_path=project_path)
        self.project_path = project_path
        self.data_dir = data_dir or str(TEST_HOME / "runtime" / "data")
        self.container_session = SimpleNamespace(mode="host")
        self.pending_image_view = None
        self.pending_video_view = None
        self.host_network_permission = "restricted"
        self.custom_tools_enabled = False
        self.user_role = "user"
        self.custom_tool_registry = RegistryStub()
        self.mcp_tools_enabled = False
        self.enabled_skill_ids: set = set()

        # 控制端本地资源全部毒化：分发只能用 backend。
        for label in (
            "file_manager",
            "terminal_manager",
            "terminal_ops",
            "background_command_manager",
            "background_manager",
        ):
            setattr(self, label, PoisonedResource(label))

    # ---- 分发路径会调用的终端侧能力 ----
    async def confirm_action(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        return True

    def get_permission_mode(self) -> str:
        return "unrestricted"

    def get_work_mode(self) -> str:
        return "execute"

    def get_execution_mode(self) -> str:
        return "sandbox"

    # ---- 断言辅助 ----
    def ops(self, name: str) -> list:
        """backend.calls 中指定 op 的调用记录。"""
        return [call for call in getattr(self.execution_backend, "calls", []) if call.get("op") == name]


async def call_tool(terminal: ToolDispatchTerminal, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """调用 handle_tool_call 并把 JSON 字符串解析成 dict。"""
    raw = await terminal.handle_tool_call(name, arguments)
    result = json.loads(raw)
    assert isinstance(result, dict), f"工具 {name} 返回的不是对象: {raw!r}"
    return result


def make_seeded_backend(**kwargs: Any) -> FakeExecutionBackend:
    """带初态文件的内存后端。"""
    backend = FakeExecutionBackend(**kwargs)
    backend.files.seed("notes.md", "line-a\nline-b\nline-c\n")
    backend.files.seed("docs/readme.txt", "hello world\n")
    backend.files.seed("assets/pic.png", b"\x89PNG\r\n\x1a\nfake-bytes")
    backend.files.seed("assets/clip.mp4", b"\x00\x00\x00\x18ftypmp42")
    backend.files.seed("plain.txt", "not media\n")
    return backend


__all__ = [
    "PoisonedAccessError",
    "PoisonedResource",
    "ContextManagerStub",
    "RegistryStub",
    "SpyBackend",
    "DenyInspectBackend",
    "ToolDispatchTerminal",
    "call_tool",
    "make_seeded_backend",
    "TEST_HOME",
]
