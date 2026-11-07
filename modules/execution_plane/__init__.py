"""Shared execution resources for Agent/Chat and independent executor hosts.

Imports stay lightweight: local process/file implementations are loaded only
when an owner actually needs execution resources.
"""
from .access import get_execution_backend, is_local_backend
from .base import ExecutionBackend

__all__ = [
    "ExecutionBackend", "LocalExecutionBackend", "FakeExecutionBackend",
    "ExecutionResources", "create_local_resources", "get_execution_backend",
    "is_local_backend",
]


def __getattr__(name):
    if name == "LocalExecutionBackend":
        from .local import LocalExecutionBackend
        return LocalExecutionBackend
    if name in {"ExecutionResources", "create_local_resources"}:
        from . import resources
        return getattr(resources, name)
    if name == "FakeExecutionBackend":
        from .fake import FakeExecutionBackend
        return FakeExecutionBackend
    raise AttributeError(name)
