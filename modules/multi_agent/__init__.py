"""多智能体模式核心实现。

由 conversations metadata.multi_agent_mode = true 触发启用，
不另起隔离链路，直接复用现有 MainTerminal、SubAgentManager、SubAgentTask
但通过对话级开关注入多智能体版工具集、prompt、消息路由。

关键组件：
- RoleConfig：角色 Markdown Frontmatter 解析与归档
- MultiAgentState：一个多智能体会话的运行态状态机
- 消息格式构造：format_multi_agent_message
- 工具定义：master 侧、sub_agent 侧扩展
- prompt 构造：build_multi_agent_master_prompt / build_multi_agent_sub_agent_prompt
"""
from __future__ import annotations

from .state import MultiAgentState, format_multi_agent_message
from .role_store import (
    RoleConfig,
    load_preset_role,
    list_roles,
    save_custom_role,
    build_role_system_prompt,
)
from .prompts import (
    build_multi_agent_master_prompt,
    build_multi_agent_sub_agent_prompt,
    MULTI_AGENT_MASTER_PROMPT_BODY,
    MULTI_AGENT_SUB_AGENT_PROMPT_BODY,
)
from .tools import (
    MULTI_AGENT_MASTER_TOOLS,
    MULTI_AGENT_SUB_AGENT_TOOLS,
    build_master_tools_for_conversation,
    build_sub_agent_tools_for_role,
)

__all__ = [
    "MultiAgentState",
    "format_multi_agent_message",
    "RoleConfig",
    "load_preset_role",
    "list_roles",
    "save_custom_role",
    "build_role_system_prompt",
    "build_multi_agent_master_prompt",
    "build_multi_agent_sub_agent_prompt",
    "MULTI_AGENT_MASTER_PROMPT_BODY",
    "MULTI_AGENT_SUB_AGENT_PROMPT_BODY",
    "MULTI_AGENT_MASTER_TOOLS",
    "MULTI_AGENT_SUB_AGENT_TOOLS",
    "build_master_tools_for_conversation",
    "build_sub_agent_tools_for_role",
]