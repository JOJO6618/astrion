"""Pure mode resolution shared by display queries and execution restoration."""
from __future__ import annotations

PERMISSIONS = {"readonly", "approval", "auto_approval", "unrestricted"}
WORK_MODES = {"plan", "ask", "execute"}
EXECUTION_MODES = {"sandbox", "direct"}
NETWORK_MODES = {"restricted", "full", "none"}


def _mode(value, allowed, default):
    value = str(value or "").strip().lower()
    return value if value in allowed else default


def resolve_conversation_modes(metadata, defaults=None, *, host_mode=True):
    defaults = defaults or {}
    work = _mode(metadata.get("work_mode"), WORK_MODES,
                 _mode(defaults.get("work_mode"), WORK_MODES, "plan"))
    permission = _mode(metadata.get("permission_mode"), PERMISSIONS,
                       _mode(defaults.get("permission_mode"), PERMISSIONS, "unrestricted"))
    execution = _mode(metadata.get("execution_mode"), EXECUTION_MODES,
                      _mode(defaults.get("execution_mode"), EXECUTION_MODES, "sandbox"))
    network = _mode(metadata.get("network_permission"), NETWORK_MODES,
                    _mode(defaults.get("network_permission"), NETWORK_MODES, "restricted"))
    if work == "plan":
        permission = "readonly"
    if not host_mode or permission != "unrestricted" or work == "plan":
        execution = "sandbox"
    return {"work_mode": work, "permission_mode": permission,
            "execution_mode": execution, "network_permission": network}


def runtime_modes(terminal):
    return {"work_mode": terminal.get_work_mode(),
            "permission_mode": terminal.get_permission_mode(),
            "execution_mode": terminal.get_execution_mode(),
            "network_permission": terminal.get_network_permission()}


def restore_conversation_modes(terminal, metadata):
    modes = resolve_conversation_modes(metadata, {
        "work_mode": terminal.default_work_mode,
        "permission_mode": terminal.default_permission_mode,
        "execution_mode": terminal.get_execution_mode(),
        "network_permission": terminal.get_network_permission(),
    }, host_mode=terminal._is_host_mode())
    from modules.goal_policy import goal_for_terminal
    if goal_for_terminal(terminal):
        # Admission persists the goal before this conversation is materialized.
        # Restore its effective execution policy, not the old ask/plan snapshot.
        modes["work_mode"] = "execute"
        if modes["permission_mode"] not in {"auto_approval", "unrestricted"}:
            modes["permission_mode"] = "auto_approval"
            modes["execution_mode"] = "sandbox"
    terminal.set_work_mode(modes["work_mode"], persist=False)
    terminal.set_permission_mode(modes["permission_mode"], persist=False)
    terminal.set_execution_mode(modes["execution_mode"])
    terminal.set_network_permission(modes["network_permission"])
    return modes
