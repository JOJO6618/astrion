"""Goal-mode policy shared by tool definition, execution and child resources."""
from __future__ import annotations

from modules.goal_state_manager import GoalStateManager

INTERACTIVE_TOOLS = frozenset({"ask_user", "submit_plan"})
# 与 tool_approval_manager.resolve_approval_requirements 的 auto_full_access 条件保持一致：
# 只有这两个权限模式下，单次完全访问才可能由审核智能体单独裁决。
AUTO_FULL_ACCESS_MODES = frozenset({"auto_approval", "unrestricted"})
# Workflow reviews and agent-to-agent questions are automatic internal operations.
DENIED_MESSAGE = "目标模式不允许等待人工交互或申请完全访问权限。请在当前权限内继续；无法推进时说明具体阻塞原因。"


def terminal_data_dir(terminal):
    cm = getattr(terminal, "context_manager", None)
    return getattr(terminal, "data_dir", None) or getattr(cm, "data_dir", None)


def goal_for_terminal(terminal):
    cm = getattr(terminal, "context_manager", None)
    cid = getattr(cm, "current_conversation_id", None)
    data_dir = terminal_data_dir(terminal)
    if not cid or not data_dir:
        return None
    manager = GoalStateManager(data_dir, cid)
    return manager if manager.is_active() else None


def auto_review_covers_full_access(terminal) -> bool:
    """单次完全访问是否只需审核智能体裁决（没有人工闸）。

    条件与创建审批时的 auto_full_access 一致：权限模式为 auto_approval/unrestricted，
    且用户开启了「允许自动审核通过单次完全访问请求」。只有该组合下目标模式才允许
    发起申请，避免停在等待人工的审批上，也避免自动审核通过后仍须人工确认。
    任何读取失败都按未开启处理（保守拒绝）。
    """
    try:
        if terminal.get_permission_mode() not in AUTO_FULL_ACCESS_MODES:
            return False
    except Exception:
        return False
    data_dir = terminal_data_dir(terminal)
    if not data_dir:
        return False
    try:
        from modules.personalization_manager import load_personalization_config
        config = load_personalization_config(data_dir) or {}
    except Exception:
        return False
    return bool(config.get("allow_auto_approve_full_access", False))


def goal_blocks_background(data_dir, conversation_id) -> bool:
    """Paused/blocked goals retain notifications until an explicit continuation."""
    if not data_dir or not conversation_id:
        return False
    return GoalStateManager(data_dir, conversation_id).state["status"] in {"paused", "blocked"}


def interaction_denied(terminal, name: str, arguments=None) -> bool:
    args = arguments or {}
    wants_full_access = bool(args.get("request_full_access"))
    if name not in INTERACTIVE_TOOLS and not wants_full_access:
        return False
    if goal_for_terminal(terminal) is None:
        return False
    # 审核智能体可单独批准单次完全访问时，不属于等待人工的交互，允许申请。
    if wants_full_access and auto_review_covers_full_access(terminal):
        return False
    return True


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
    # 自动审核已支持完全访问，保留用户显式排队的执行环境选择。
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
