"""Explicit session modes, with the same sandbox locks as terminal controls."""
from typing import Any, Dict, Optional


def session_mode_overrides(
    terminal: Any, principal: Any, work_mode: Optional[str] = None,
    permission_mode: Optional[str] = None, execution_mode: Optional[str] = None,
) -> Dict[str, str]:
    work = work_mode or terminal.get_work_mode()
    permission = permission_mode or terminal.get_permission_mode()
    execution = execution_mode or terminal.get_execution_mode()
    if work not in {"ask", "plan", "execute"}:
        raise ValueError("Invalid work_mode")
    if permission not in {"readonly", "approval", "auto_approval", "unrestricted"}:
        raise ValueError("Invalid permission_mode")
    if execution not in {"sandbox", "direct"}:
        raise ValueError("Invalid execution_mode")
    if work == "plan":
        permission, execution = "readonly", "sandbox"
    if permission != "unrestricted":
        execution = "sandbox"
    if execution == "direct" and not (principal and principal.host_mode):
        raise PermissionError("Direct execution is only available in host mode")
    return {"work_mode": work, "permission_mode": permission, "execution_mode": execution}
