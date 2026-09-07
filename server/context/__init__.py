# server/context/__init__.py - 兼容入口（原 server/context.py 拆分为子包）
# 所有历史导入路径 `from server.context import X` 不变。
from server.context.identity import NoWorkspaceError, RuntimeIdentity, _resolve_user_role
from server.context.broadcast import (
    make_terminal_callback,
    attach_user_broadcast,
    _wrap_callback_with_conversation_id,
)
from server.context.personalization import _apply_workspace_personalization_preferences
from server.context.usage import get_or_create_usage_tracker, emit_user_quota_update
from server.context.upload import get_gui_manager, get_upload_guard, build_upload_error_response
from server.context.conversation import ensure_conversation_loaded, apply_conversation_overrides
from server.context.resources import (
    get_user_resources,
    _make_terminal_key,
    _touch_terminal_activity,
    _set_terminal_workspace_label,
    _ensure_workspace_skills_synced,
)
from server.context.decorators import with_terminal, get_terminal_for_sid
from server.context.reaper import (
    reset_system_state,
    reap_idle_conversation_terminals,
    start_conversation_terminal_reaper,
)

__all__ = [
    "NoWorkspaceError",
    "RuntimeIdentity",
    "get_user_resources",
    "with_terminal",
    "get_terminal_for_sid",
    "get_gui_manager",
    "get_upload_guard",
    "build_upload_error_response",
    "ensure_conversation_loaded",
    "apply_conversation_overrides",
    "reset_system_state",
    "get_or_create_usage_tracker",
    "emit_user_quota_update",
    "make_terminal_callback",
    "attach_user_broadcast",
    "reap_idle_conversation_terminals",
    "start_conversation_terminal_reaper",
]
