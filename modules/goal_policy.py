"""Goal-mode policy shared by tool definition, execution and child resources."""
from __future__ import annotations

from modules.goal_state_manager import GoalStateManager

INTERACTIVE_TOOLS = frozenset({"ask_user", "submit_plan"})
# Workflow reviews and agent-to-agent questions are automatic internal operations.
DENIED_MESSAGE = "目标模式不允许等待人工交互或申请完全访问权限。请在当前权限内继续；无法推进时说明具体阻塞原因。"


def goal_for_terminal(terminal):
    cm = getattr(terminal, "context_manager", None)
    cid = getattr(cm, "current_conversation_id", None)
    data_dir = getattr(terminal, "data_dir", None) or getattr(cm, "data_dir", None)
    if not cid or not data_dir:
        return None
    manager = GoalStateManager(data_dir, cid)
    return manager if manager.is_active() else None


def goal_blocks_background(data_dir, conversation_id) -> bool:
    """Paused/blocked goals retain notifications until an explicit continuation."""
    if not data_dir or not conversation_id:
        return False
    return GoalStateManager(data_dir, conversation_id).state["status"] in {"paused", "blocked"}


def interaction_denied(terminal, name: str, arguments=None) -> bool:
    args = arguments or {}
    if name not in INTERACTIVE_TOOLS and not args.get("request_full_access"):
        return False
    return goal_for_terminal(terminal) is not None


def enforce_goal_modes(terminal, conversation_id: str) -> None:
    # Leaving plan must happen before applying writable permissions. Do not restore
    # pre-plan permissions temporarily (they can restore direct execution).
    terminal.set_work_mode("execute", conversation_id=conversation_id)
    if terminal.get_permission_mode() not in {"auto_approval", "unrestricted"}:
        terminal.set_permission_mode("auto_approval", conversation_id=conversation_id)
    updates = {}
    if getattr(terminal, "pending_permission_mode", None) not in {None, "", "auto_approval", "unrestricted"}:
        terminal.pending_permission_mode = None
        updates["pending_permission_mode"] = None
    if terminal.get_permission_mode() == "auto_approval" and getattr(terminal, "pending_execution_mode", None) == "direct":
        terminal.pending_execution_mode = None
        updates["pending_execution_mode"] = None
    if updates:
        terminal._persist_runtime_mode_metadata(updates)


def validate_limits(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("目标限制必须为对象")
    result = {}
    for key, maximum in (("max_turns", 10000), ("max_tokens", 1000000000)):
        limit = value.get(key)
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)
                                  or not 1 <= limit <= maximum):
            raise ValueError(f"{key} 必须为空或 1 到 {maximum} 的整数")
        result[key] = limit
    return result
