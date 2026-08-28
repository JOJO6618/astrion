"""API multi-agent message pack.

User-visible messages produced by server/multi_agent.py (REST responses).
Pure data only — do not import anything here; merged into modules.i18n at import time.
Keys are prefixed with ``multi_agent_api.`` (the core table already owns
``multi_agent.*`` — no key below duplicates it).
"""

MESSAGES = {
    # ── /api/multiagent/rebuild-index ──
    "multi_agent_api.not_logged_in": {
        "zh-CN": "未登录",
        "en-US": "Not logged in",
    },
    "multi_agent_api.workspace_not_ready": {
        "zh-CN": "工作区未就绪",
        "en-US": "Workspace is not ready",
    },
    "multi_agent_api.context_manager_not_initialized": {
        "zh-CN": "上下文管理器未初始化",
        "en-US": "Context manager is not initialized",
    },
    "multi_agent_api.ma_conversation_manager_not_initialized": {
        "zh-CN": "多智能体对话管理器未初始化",
        "en-US": "Multi-agent conversation manager is not initialized",
    },

    # ── POST /api/multiagent/roles（创建自定义角色） ──
    "multi_agent_api.role_fields_required": {
        "zh-CN": "role_id/name/body_prompt 必填",
        "en-US": "role_id/name/body_prompt are required",
    },
    "multi_agent_api.cannot_override_preset_role": {
        "zh-CN": "不能覆盖预设角色 {role_id}",
        "en-US": "Cannot override preset role {role_id}",
    },
    "multi_agent_api.role_already_exists": {
        "zh-CN": "角色 {role_id} 已存在",
        "en-US": "Role {role_id} already exists",
    },

    # ── PUT /api/multiagent/roles/<role_id>（更新自定义角色） ──
    "multi_agent_api.role_not_found": {
        "zh-CN": "角色 {role_id} 不存在",
        "en-US": "Role {role_id} not found",
    },

    # ── DELETE /api/multiagent/roles/<role_id>（删除自定义角色） ──
    "multi_agent_api.cannot_delete_preset_role": {
        "zh-CN": "不能删除预设角色 {role_id}",
        "en-US": "Cannot delete preset role {role_id}",
    },
    "multi_agent_api.role_not_found_or_delete_failed": {
        "zh-CN": "角色 {role_id} 不存在或无法删除",
        "en-US": "Role {role_id} not found or could not be deleted",
    },

    # ── PUT /api/multiagent/settings（更新多智能体设置） ──
    "multi_agent_api.compress_threshold_too_small": {
        "zh-CN": "压缩阈值不能小于 10000",
        "en-US": "Compression threshold cannot be lower than 10000",
    },
    "multi_agent_api.max_turns_must_be_integer": {
        "zh-CN": "最大轮次必须是整数",
        "en-US": "Max turns must be an integer",
    },
    "multi_agent_api.max_turns_cannot_be_negative": {
        "zh-CN": "最大轮次不能为负数（0 表示无上限）",
        "en-US": "Max turns cannot be negative (0 means unlimited)",
    },

    # ── POST /api/multiagent/conversations（创建多智能体对话） ──
    "multi_agent_api.conversation_manager_not_initialized": {
        "zh-CN": "对话管理器未初始化",
        "en-US": "Conversation manager is not initialized",
    },

    # ── GET /api/multiagent/active_sub_agents ──
    "multi_agent_api.missing_conversation_id_param": {
        "zh-CN": "缺少 conversation_id 参数",
        "en-US": "Missing conversation_id parameter",
    },
    "multi_agent_api.sub_agent_manager_not_ready": {
        "zh-CN": "子智能体管理器未就绪",
        "en-US": "Sub-agent manager is not ready",
    },
}