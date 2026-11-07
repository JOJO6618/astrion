"""Shared execution boundary used by local adapters and future remote workers.

Agent decisions, approval waits and conversation history stay in the caller.
Implementations own filesystem/process/terminal resources and enforce their
actual path and OS sandbox policy. An injected backend must never fall back to
resources on the controller when an operation is unavailable or fails.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .files import FileSystemBackend
    from .protocols import BackgroundBackend, TerminalBackend


@runtime_checkable
class ExecutionBackend(Protocol):
    """Environment-bound operations, independent of model and web runtimes.

    ``files`` returns policy-checked operations and JSON metadata; paths belong
    to the executor and must not be resolved against the controller filesystem.
    Binary reads return bytes (a transport must explicitly encode them).
    Process coroutine cancellation must stop its owned process, not merely stop
    waiting for a response. Background records and terminals belong to this
    backend and are queried/cancelled through these same resources.
    """

    @property
    def files(self) -> FileSystemBackend: ...

    @property
    def terminals(self) -> TerminalBackend: ...

    @property
    def background(self) -> Optional[BackgroundBackend]: ...

    def get_environment(self) -> Dict[str, Any]:
        """Return serializable executor identity, workspace and capabilities."""
        ...

    def track_file_edit(
        self, path: str, *, conversation_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        """Capture execution-side content for conversation-scoped undo."""
        ...

    async def run_command(
        self, command: str, *, timeout: float, sandbox_write_access: bool,
        network_permission: str, working_dir: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    async def run_command_background(
        self, command: str, *, timeout: float,
        conversation_id: Optional[str], wait_seconds: float,
        network_permission: str, sandbox_write_access: bool,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def write_file(self, path: str, content: str, *, mode: str = "w") -> Dict[str, Any]: ...

    def edit_file(self, path: str, replacements: List[Dict[str, Any]]) -> Dict[str, Any]: ...

    async def execute_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any: ...

    async def execute_custom_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any: ...

    async def list_mcp_servers(self, arguments: Dict[str, Any]) -> Dict[str, Any]: ...
