"""Execution Plane：Runtime ↔ 执行环境分层（docs/execution_contract.md）。

- base.ExecutionBackend：执行环境后端协议（E1-E4 首版接口面）
- fake.FakeExecutionBackend：内存替身执行器（Runtime 独立测试用）

接入方式：MainTerminal/WebTerminal 实例的 ``execution_backend`` 属性
（默认 None = 现有 Host/Docker 真实链路；注入实现后工具编排层
handle_tool_call 的 E1-E4 分支改走该后端）。
"""
from modules.execution_plane.base import ExecutionBackend
from modules.execution_plane.fake import FakeExecutionBackend

__all__ = ["ExecutionBackend", "FakeExecutionBackend"]
