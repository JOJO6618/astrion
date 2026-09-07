"""显式身份模型与角色解析（契约 docs/runtime_contract.md §4.1）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from server.auth_helpers import get_current_user_role


class NoWorkspaceError(RuntimeError):
    """宿主机模式下尚未创建任何工作区。

    与一般的 resource_busy 区分：前端可据 code=no_workspace 进入
    「引导创建工作区」流程，而不是视为系统繁忙。
    """


@dataclass(frozen=True)
class RuntimeIdentity:
    """资源装配的显式身份与偏好快照（契约 docs/runtime_contract.md §4.1）。

    传入 get_user_resources 后，资源装配完全不读写 Flask session；
    为 None 时保持既有行为（HTTP 适配层在请求上下文内读取 session）。
    """

    host_mode: bool = False
    host_workspace_id: Optional[str] = None
    is_api_user: bool = False
    role: Optional[str] = None
    preferred_model_key: Optional[str] = None
    preferred_run_mode: Optional[str] = None
    preferred_thinking_mode: Optional[bool] = None


def _resolve_user_role(identity: Optional[RuntimeIdentity], record, default: str = "user") -> str:
    """统一角色解析：显式身份模式不触碰 Flask session（无请求上下文时安全）。"""
    if identity is not None:
        if identity.role:
            return identity.role
        return (record.role if record and getattr(record, "role", None) else default)
    return get_current_user_role(record)
