# server/context/__init__.py - 兼容入口（原 server/context.py 拆分为子包）
# 所有历史导入路径 `from server.context import X` 不变。
#
# 2026-09-08（G4 拆解）：改为 PEP 562 模块级 __getattr__ 懒加载——
# 符号首次被引用时才加载对应子模块，避免任务核心层（server.tasks）
# import 单个符号就连带拉起全部子模块（含 Web 适配层的 flask 依赖链）。
from importlib import import_module as _import_module

_SYMBOL_MODULE = {
    # identity
    "NoWorkspaceError": "server.context.identity",
    "RuntimeIdentity": "server.context.identity",
    "_resolve_user_role": "server.context.identity",
    # broadcast
    # personalization
    "_apply_workspace_personalization_preferences": "server.context.personalization",
    # usage
    "get_or_create_usage_tracker": "server.context.usage",
    # upload
    "get_gui_manager": "server.context.upload",
    "get_upload_guard": "server.context.upload",
    "build_upload_error_response": "server.context.upload",
    # conversation
    "ensure_conversation_loaded": "server.context.conversation",
    "apply_conversation_overrides": "server.context.conversation",
    # resources
    "get_user_resources": "server.context.resources",
    "make_terminal_callback": "server.context.resources",
    "_make_terminal_key": "server.context.resources",
    "_touch_terminal_activity": "server.context.resources",
    "_set_terminal_workspace_label": "server.context.resources",
    "_ensure_workspace_skills_synced": "server.context.resources",
    # decorators
    "with_terminal": "server.context.decorators",
    "get_terminal_for_sid": "server.context.decorators",
    # reaper
    "reset_system_state": "server.context.reaper",
    "reap_idle_conversation_terminals": "server.context.reaper",
    "start_conversation_terminal_reaper": "server.context.reaper",
}

__all__ = [
    "NoWorkspaceError",
    "RuntimeIdentity",
    "get_user_resources",
    "make_terminal_callback",
    "with_terminal",
    "get_terminal_for_sid",
    "get_gui_manager",
    "get_upload_guard",
    "build_upload_error_response",
    "ensure_conversation_loaded",
    "apply_conversation_overrides",
    "reset_system_state",
    "get_or_create_usage_tracker",
    "reap_idle_conversation_terminals",
    "start_conversation_terminal_reaper",
]


def __getattr__(name):
    module_name = _SYMBOL_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_import_module(module_name), name)
    globals()[name] = value  # 缓存，后续访问直接命中
    return value


def __dir__():
    return sorted(set(list(globals()) + list(_SYMBOL_MODULE)))
