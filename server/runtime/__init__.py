# server/runtime/__init__.py - 公共任务入口包（契约 docs/runtime_contract.md）
from server.runtime.context import (
    InternalDirectives,
    RuntimeContext,
    TaskParams,
    TrustedPrincipal,
    principal_from_session_snapshot,
)
from server.runtime.service import RuntimeService, runtime_service

__all__ = [
    "InternalDirectives",
    "RuntimeContext",
    "RuntimeService",
    "TaskParams",
    "TrustedPrincipal",
    "principal_from_session_snapshot",
    "runtime_service",
]
