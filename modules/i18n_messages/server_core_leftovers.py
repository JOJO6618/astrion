"""Message pack: server_core_leftovers (agent_32 迁移的 server/core 遗留中文)。

覆盖域：
- files.         server/files.py 的 jsonify error（文件浏览/管理、path、项目路径）
- chat_flow.     server/chat_flow.py 主任务门闸拒绝消息
- stream_loop.   server/chat_flow_stream_loop.py 工具准备中消息
- web_terminal.  core/web_terminal.py 工具执行状态广播（sender message；
                 与 modules/i18n_messages/web_terminal.py 的 key 不重叠）
- main_terminal. core/main_terminal.py 的 raise ValueError
- tools_policy.  core/main_terminal_parts/tools_policy.py 的 raise ValueError
- commands_raise. core/main_terminal_parts/commands.py 斜杠命令报错 raise
                 （与 commands.py 既有结果文本 key 前缀 commands. 区分）
- api_v1_raise.  server/api_v1.py 路由层 raise（被 except 包成 API 错误返回；
                 与 api_v1.py 既有业务错误 key 前缀 api_v1. 区分）

说明：
- 复用既有 key：tool_loop.cancelled_by_user / tool_loop.deep_compression_failed
  （位于 modules/i18n_messages/chat_flow.py，本文件不重复定义）。
- 纯数据模块：禁止 import modules.i18n；由 modules/i18n.py import 时自动聚合。
- 插值用 str.format 命名参数：tr("<prefix>.<key>", name=value)。
"""

MESSAGES = {
    # ── server/files.py ──
    "files.browse_disabled_admin": {
        "zh-CN": "文件浏览已被管理员禁用",
        "en-US": "File browsing is disabled by the administrator",
    },
    "files.manage_disabled_admin": {
        "zh-CN": "文件管理已被管理员禁用",
        "en-US": "File management is disabled by the administrator",
    },
    "files.missing_path": {
        "zh-CN": "缺少 path",
        "en-US": "Missing path",
    },
    "files.missing_path_or_content": {
        "zh-CN": "缺少 path 或 content",
        "en-US": "Missing path or content",
    },
    "files.project_path_not_found": {
        "zh-CN": "项目路径不存在",
        "en-US": "Project path does not exist",
    },
    "files.invalid_project_path": {
        "zh-CN": "项目路径无效: {error}",
        "en-US": "Invalid project path: {error}",
    },
    "files.scan_project_failed": {
        "zh-CN": "扫描项目失败: {error}",
        "en-US": "Failed to scan the project: {error}",
    },

    # ── server/chat_flow.py ──
    "chat_flow.task_already_running": {
        "zh-CN": "当前对话已有任务在运行，请稍后再试。",
        "en-US": "A task is already running in this conversation. Please try again later.",
    },

    # ── server/chat_flow_stream_loop.py ──
    "stream_loop.preparing_tool": {
        "zh-CN": "准备调用 {tool}...",
        "en-US": "Preparing to call {tool}...",
    },

    # ── core/web_terminal.py（工具执行状态广播 message） ──
    # 注意：本组 key 与 modules/i18n_messages/web_terminal.py 的 key 不重叠。
    "web_terminal.tool_executing": {
        "zh-CN": "正在执行 {tool_name}...",
        "en-US": "Executing {tool_name}...",
    },
    "web_terminal.tool_failed_param_too_long": {
        "zh-CN": "{tool_name} 执行失败: 参数过长",
        "en-US": "{tool_name} failed: parameters are too long",
    },
    "web_terminal.tool_failed_param_format": {
        "zh-CN": "{tool_name} 执行失败: 参数格式错误",
        "en-US": "{tool_name} failed: parameter format error",
    },
    "web_terminal.tool_failed_general": {
        "zh-CN": "{tool_name} 执行失败: {error_msg}",
        "en-US": "{tool_name} failed: {error_msg}",
    },
    "web_terminal.tool_succeeded": {
        "zh-CN": "{tool_name} 执行成功",
        "en-US": "{tool_name} executed successfully",
    },
    "web_terminal.tool_result_not_json": {
        "zh-CN": "{tool_name} 返回了非JSON格式结果",
        "en-US": "{tool_name} returned a non-JSON result",
    },

    # ── core/main_terminal.py ──
    "main_terminal.invalid_network_permission": {
        "zh-CN": "无效网络权限，仅支持 restricted / full / none",
        "en-US": "Invalid network permission; only restricted / full / none are supported",
    },
    "main_terminal.invalid_permission_mode": {
        "zh-CN": "无效权限模式，仅支持 readonly / approval / auto_approval / unrestricted",
        "en-US": "Invalid permission mode; only readonly / approval / auto_approval / unrestricted are supported",
    },
    "main_terminal.invalid_execution_environment": {
        "zh-CN": "无效执行环境，仅支持 sandbox / direct",
        "en-US": "Invalid execution environment; only sandbox / direct are supported",
    },
    "main_terminal.plan_mode_locks_sandbox": {
        "zh-CN": "计划模式下执行环境锁定为沙箱，请先切换运行模式",
        "en-US": "Execution environment is locked to sandbox in plan mode; switch work mode first",
    },
    "main_terminal.restricted_mode_locks_sandbox": {
        "zh-CN": "只读/批准/自动审核模式下执行环境锁定为沙箱，切换到无限制模式后可选完全访问",
        "en-US": "Execution environment is locked to sandbox in readonly/approval/auto-approval mode; switch to unrestricted for direct access",
    },

    # ── core/main_terminal_parts/tools_policy.py ──
    "tools_policy.invalid_work_mode": {
        "zh-CN": "无效运行模式，仅支持 plan / ask / execute",
        "en-US": "Invalid work mode; only plan / ask / execute are supported",
    },
    "tools_policy.invalid_permission_mode": {
        "zh-CN": "无效权限模式，仅支持 readonly / approval / auto_approval / unrestricted",
        "en-US": "Invalid permission mode; only readonly / approval / auto_approval / unrestricted are supported",
    },
    "tools_policy.plan_mode_locks_readonly": {
        "zh-CN": "计划模式下权限模式锁定为只读，请先切换运行模式",
        "en-US": "Permission mode is locked to readonly in plan mode; switch work mode first",
    },
    "tools_policy.unknown_tool_category": {
        "zh-CN": "未知的工具类别: {category}",
        "en-US": "Unknown tool category: {category}",
    },
    "tools_policy.category_admin_locked": {
        "zh-CN": "该类别被管理员强制为启用/禁用，无法修改",
        "en-US": "This category is forced enabled/disabled by the administrator and cannot be modified",
    },

    # ── core/main_terminal_parts/commands.py（斜杠命令报错，展示给用户；
    #    前缀 commands_raise. 与既有结果文本 commands. 区分） ──
    "commands_raise.unsupported_mode": {
        "zh-CN": "不支持的模式: {mode}",
        "en-US": "Unsupported mode: {mode}",
    },
    "commands_raise.thinking_only_model": {
        "zh-CN": "当前模型仅支持思考模式",
        "en-US": "This model only supports thinking mode",
    },
    "commands_raise.fast_only_model": {
        "zh-CN": "当前模型仅支持快速模式",
        "en-US": "This model only supports fast mode",
    },
    "commands_raise.unsupported_reasoning_effort": {
        "zh-CN": "不支持的推理强度: {effort}",
        "en-US": "Unsupported reasoning effort: {effort}",
    },
    "commands_raise.model_no_image": {
        "zh-CN": "当前对话包含图片，目标模型不支持图片输入",
        "en-US": "The conversation contains images, but the target model does not support image input",
    },
    "commands_raise.model_no_video": {
        "zh-CN": "当前对话包含视频，目标模型不支持视频输入",
        "en-US": "The conversation contains videos, but the target model does not support video input",
    },

    # ── server/api_v1.py（路由层 raise，被 except 包成 API 错误返回；
    #    前缀 api_v1_raise. 与既有业务错误 api_v1. 区分） ──
    "api_v1_raise.invalid_path": {
        "zh-CN": "非法路径",
        "en-US": "Invalid path",
    },
    "api_v1_raise.workspace_id_empty": {
        "zh-CN": "workspace_id 不能为空",
        "en-US": "workspace_id must not be empty",
    },
}