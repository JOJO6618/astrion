"""FakeExecutionBackend：内存替身执行器（Execution Plane 的非真实实现）。

用途：公共执行接口和 Runtime 路由的内存替身测试；契约见
``docs/execution_contract.md``，验收记录见
``test/2026-11-07_公共执行层/VALIDATION.md``。
不产生任何真实副作用：不起子进程、不写磁盘、不开终端、不落后台记录文件。

实现范围：``base.ExecutionBackend`` 完整协议
--------------------------------------------
- ``files``     -> :class:`fake_files.FakeFileSystem`（完整 FileSystemBackend 方法面）
- ``terminals`` -> :class:`fake_terminals.FakeTerminals`
- ``background``-> :class:`fake_background.FakeBackground`
- ``run_command`` / ``run_command_background`` / ``write_file`` / ``edit_file``
- ``get_environment``：**不会**探测本机，``is_local`` 恒为 False，字段是声明值。

非目标：不模拟命令真实输出语义、不模拟审批/权限裁决（那是 Runtime 编排层职责），
也不模拟远端传输/加密/Relay。它只证明"执行面可被替换且不泄漏到控制侧"。

``track_file_edit`` 是公共执行接口，替身**显式实现**为无磁盘 no-op：只在
``tracked_edits`` 记一笔调用，不读老内容、不写 data_dir、不跨到控制侧磁盘。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .fake_background import FakeBackground
from .fake_files import DEFAULT_WORKSPACE_ROOT, FakeFileSystem
from .fake_terminals import FakeTerminals

FAKE_BACKEND_NAME = "fake"


class FakeExecutionBackend:
    """内存替身；``calls`` 记录命令／文本写入／编辑入口，子资源另有内存状态。

    环境查询不写入 calls，编辑备份钩子单独记录在 tracked_edits。
    """

    def __init__(
        self,
        *,
        workspace_root: str = DEFAULT_WORKSPACE_ROOT,
        platform: Optional[Dict[str, Any]] = None,
        clock: Optional[Any] = None,
        auto_complete_background: bool = True,
    ) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.workspace_root = workspace_root or DEFAULT_WORKSPACE_ROOT
        self._clock = clock or time.time
        self._platform: Dict[str, Any] = dict(platform or {
            "platform": "fake",
            "system": "FakeOS",
            "machine": "fake-arch",
        })
        self._files = FakeFileSystem(
            workspace_root=self.workspace_root, platform=self._platform, clock=self._clock
        )
        self._terminals = FakeTerminals(clock=self._clock)
        self._background = FakeBackground(clock=self._clock, auto_complete=auto_complete_background)
        self.tracked_edits: List[Dict[str, Any]] = []
        self.created_at = float(self._clock())

    # ------------------------------------------------------------------
    # 资源视图
    # ------------------------------------------------------------------
    @property
    def files(self) -> FakeFileSystem:
        return self._files

    @property
    def terminals(self) -> FakeTerminals:
        return self._terminals

    @property
    def background(self) -> FakeBackground:
        return self._background

    @property
    def backend_name(self) -> str:
        return FAKE_BACKEND_NAME

    # 兼容旧字段名：既有测试/脚本可能直接引用 background_commands
    @property
    def background_commands(self) -> Dict[str, Dict[str, Any]]:
        return self._background.records

    # ------------------------------------------------------------------
    # 环境信息
    # ------------------------------------------------------------------
    def get_environment(self) -> Dict[str, Any]:
        """声明式环境信息：不探测本机 OS/路径，避免"伪装成本机"。"""
        return {
            "backend": FAKE_BACKEND_NAME,
            "is_local": False,
            "supports_full_access": False,
            "workspace_root": self.workspace_root,
            "cwd": self.workspace_root,
            "platform": self._platform.get("platform", "fake"),
            "host": dict(self._platform),
            "execution_target": "fake",
            "execution_mode": "fake",
            "case_sensitive": True,
            "network_permission": "unknown",
            "capabilities": {
                "files": "in-memory",
                "terminals": "in-memory",
                "background": "in-memory",
                "binary_reads": "bytes",
            },
            "notes": [
                "替身后端：无子进程、无真实磁盘、无真实终端。",
                "元数据来自内存记录，不代表任何宿主机事实。",
            ],
        }

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------
    async def run_command(
        self,
        command: str,
        *,
        timeout: float,
        sandbox_write_access: bool,
        network_permission: str,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.calls.append({
            "op": "run_command",
            "command": command,
            "timeout": timeout,
            "sandbox_write_access": sandbox_write_access,
            "network_permission": network_permission,
            "working_dir": working_dir,
        })
        return {
            "success": True,
            "status": "completed",
            "output": f"[fake-exec] {command}",
            "return_code": 0,
            "truncated": False,
            "elapsed_ms": 0,
            "working_dir": working_dir or self.workspace_root,
        }

    async def run_command_background(
        self,
        command: str,
        *,
        timeout: float,
        conversation_id: Optional[str],
        wait_seconds: float,
        network_permission: str,
        sandbox_write_access: bool,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Runtime 对后台命令是 ``await`` 的，与 ``base.py`` 的 async 声明一致。"""
        command_id = f"fake_bg_{uuid.uuid4().hex[:8]}"
        self.calls.append({
            "op": "run_command_background",
            "command": command,
            "timeout": timeout,
            "conversation_id": conversation_id,
            "sandbox_write_access": sandbox_write_access,
            "network_permission": network_permission,
            "waiting": wait_seconds,
            "working_dir": working_dir,
        })
        self._background.records[command_id] = {
            "command_id": command_id,
            "command": command,
            "status": "completed",
            "output": f"[fake-exec-bg] {command}",
            "return_code": 0,
            "pid": None,
            "created_at": float(self._clock()),
            "started_at": float(self._clock()),
            "finished_at": float(self._clock()),
            "timeout_seconds": timeout,
            "conversation_id": conversation_id,
            "is_running": False,
            "notified": False,
            "claimed": False,
        }
        return {
            "success": True,
            "command_id": command_id,
            "status": "running_background",
        }

    # ------------------------------------------------------------------
    # 文件写入（编排层的 E3/E4 入口，落到内存文件系统）
    # ------------------------------------------------------------------
    def write_file(self, path: str, content: str, *, mode: str = "w") -> Dict[str, Any]:
        result = self._files.write_file(path, content, mode=mode)
        if not result.get("success"):
            self.calls.append({
                "op": "write_file",
                "path": path,
                "mode": mode,
                "error": result.get("error"),
            })
            return result
        self.calls.append({
            "op": "write_file",
            "path": path,
            "mode": mode,
            "bytes": len(content),
        })
        return {
            "success": True,
            "path": result["path"],
            "original_file": result.get("original_file"),
            "new_file": result.get("new_file", content),
        }

    def edit_file(self, path: str, replacements: List[Dict[str, Any]]) -> Dict[str, Any]:
        result = self._files.replace_many_in_file(path, replacements)
        if not result.get("success"):
            self.calls.append({"op": "edit_file", "path": path, "error": result.get("error")})
            return result
        self.calls.append({
            "op": "edit_file",
            "path": path,
            "replacements_applied": result["replacements_applied"],
        })
        return result

    # ------------------------------------------------------------------
    # 公共接口：浅版本备份钩子（显式无磁盘 no-op）
    # ------------------------------------------------------------------
    def track_file_edit(
        self,
        path: str,
        *,
        conversation_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        """在真实后端上会备份旧内容到 data_dir；替身只记账，不碰磁盘。

        返回 ``None``，与 ``LocalExecutionBackend.track_file_edit`` 一致。
        不读旧内容、不写备份、不解析到控制侧路径，因此“备份不落控制侧”
        这条边界不会被替身悄悄绕过。
        """
        key, error = self._files._normalize(path)
        self.tracked_edits.append({
            "path": path,
            "normalized_path": None if error else key,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "backed_up": False,
            "reason": "in-memory backend has no shallow versioning store",
        })
        # 不写进 self.calls：calls 只记录执行面操作，保持 E1-E4 精确序列断言。

    async def execute_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        return {"success": False, "error": "Fake executor has no MCP clients"}

    async def execute_custom_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        return {"success": False, "error": "Fake executor has no custom tools"}

    async def list_mcp_servers(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "count": 0, "servers": [], "sync_result": None}


__all__ = ["FakeExecutionBackend", "FAKE_BACKEND_NAME"]
