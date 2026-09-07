"""Execution Plane 契约：Runtime ↔ 执行环境分层接口（docs/execution_contract.md §3）。

定位：工具编排层（core/main_terminal_parts/tools_execution.py）通过本接口触达
执行环境；现有 Host/Docker 实现仍由 terminal_ops/file_manager 等承载（默认路径），
本接口是「替身执行器 / 未来远端执行后端」的统一接入点。

首版接口面（E1-E4，归纳自现状调用点，未发明新能力）：
- E1 run_command（前台命令）
- E2 run_command_background（后台命令登记）
- E3 write_file / E4 edit_file（文件写）

结果结构与现有真实后端保持一致（调用方按同一结构消费）：
- run_command: {success, status, output, return_code, truncated, elapsed_ms}
  status ∈ {"completed", "timeout", "error", "cancelled"}
- run_command_background: {success, command_id, status}（status="running_background"）
- write_file/edit_file: {success, path, original_file, new_file}
  （original_file/new_file 为写前/后全文，编辑摘要链路依赖）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ExecutionBackend(Protocol):
    """执行环境后端协议。实现方：FakeExecutionBackend（替身）、未来 Host/Docker 适配器。

    注意：本协议只承诺「执行语义」，不承诺安全边界——权限裁决（permission mode）、
    路径授权（_validate_path）、命令校验（_validate_command）在 Runtime 编排层完成，
    与本接口正交（契约 §4）。
    """

    async def run_command(
        self,
        command: str,
        *,
        timeout: float,
        sandbox_write_access: bool,
        network_permission: str,
    ) -> Dict[str, Any]:
        """E1 前台命令执行。"""
        ...

    def run_command_background(
        self,
        command: str,
        *,
        timeout: float,
        conversation_id: Optional[str],
        wait_seconds: float,
        network_permission: str,
        sandbox_write_access: bool,
    ) -> Dict[str, Any]:
        """E2 后台命令登记（立即返回 command_id，执行异步进行）。"""
        ...

    def write_file(self, path: str, content: str, *, mode: str = "w") -> Dict[str, Any]:
        """E3 文件写入（mode "w" 覆盖 / "a" 追加）。"""
        ...

    def edit_file(self, path: str, replacements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """E4 文件精确替换（replacements 元素含 old_string/new_string/replace_all）。"""
        ...
