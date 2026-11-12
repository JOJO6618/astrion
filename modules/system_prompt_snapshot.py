"""Shared metadata and transport contract for the main Agent system snapshot."""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT_KEY = "frozen_system_prompt"
SYSTEM_PROMPT_MARKER = "_frozen_system_prompt"

# Keep the historical order. Never infer it from metadata's dictionary order.
LEGACY_PROMPT_KEYS = (
    "frozen_main_system_prompt",
    "frozen_permission_prompt",
    "frozen_execution_prompt",
    "frozen_work_mode_prompt",
    "frozen_recent_conversations_prompt",
    "frozen_personalization_prompt",
    "frozen_workspace_prompt",
    "frozen_agents_md_prompt",
    "frozen_claude_md_prompt",
    "frozen_skills_prompt",
    "frozen_tool_loading_prompt",
    "frozen_preview_panel_prompt",
    "frozen_native_browser_prompt",
    "frozen_inline_ui_prompt",
    "frozen_memory_prompt",
    "frozen_workspace_memory_prompt",
    "frozen_custom_system_prompt",
    "frozen_disabled_tools_prompt",
)

# This paragraph was not stored separately by the old builder. Preserve its text
# and position when migrating old snapshots, rather than applying new templates.
LEGACY_COLLABORATION_RULES = (
    "子智能体按name命名并保留上下文，所有任务后台执行，结束一轮后最后回复会以user消息通知。"
    "完成后默认保留idle；本轮完成、暂时无任务或准备最终回复都不是终结理由，不要例行清理或预防性释放名额。"
    "仅在用户明确要求、经核实实例发生无法恢复且无法继续使用的错误，或严重违反明确指令且纠正后仍继续偏离时，才可终结；"
    "实际达到实例上限、已有实例无法复用且确需新建时，也可终结不再需要的空闲实例。"
    "发现正在或即将执行未经授权的破坏性操作时，立即stop_sub_agent，不必等待纠正；无法可靠继续协作时再terminate_sub_agent。"
    "普通报错、可恢复故障、理解偏差或意见分歧不构成终结理由。停止和终结不能撤销已发生的操作。"
    "后续优先复用已有实例，向原name发送消息，不重复创建同名实例。无需角色模板、finish工具或sleep等待。"
    "主子、子子提问可正常通信，及时处理问题。目录写入档位只约束内置工具，MCP沿用外置插件权限。"
)


def has_frozen_system(messages: Any) -> bool:
    """Transport marker is local-only; it must never be sent as an API field."""
    return bool(
        isinstance(messages, list)
        and messages
        and isinstance(messages[0], dict)
        and messages[0].get("role") == "system"
        and messages[0].get(SYSTEM_PROMPT_MARKER) is True
    )


def legacy_system_text(metadata: dict) -> str:
    """Merge only saved legacy segments, without regenerating mutable data."""
    if not any(isinstance(metadata.get(key), str) and metadata[key]
               for key in LEGACY_PROMPT_KEYS):
        return ""
    parts = []
    for key in LEGACY_PROMPT_KEYS:
        if key == "frozen_disabled_tools_prompt":
            parts.append(LEGACY_COLLABORATION_RULES)
        text = metadata.get(key)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n\n".join(parts)


def reset_system_prompt_updates() -> dict:
    """Compression may rebuild the whole system, never individual live segments."""
    return {
        SYSTEM_PROMPT_KEY: None,
        **{key: None for key in LEGACY_PROMPT_KEYS},
    }


def preserve_system_snapshot(current: dict, restored: dict) -> None:
    """History restoration cannot replace the target conversation's system."""
    keys = (
        SYSTEM_PROMPT_KEY, *LEGACY_PROMPT_KEYS,
        "workspace_memory_enabled", "workspace_memory_snapshot",
        "workspace_memory_write_version",
    )
    for key in keys:
        if key in current:
            restored[key] = current[key]
        else:
            restored.pop(key, None)
