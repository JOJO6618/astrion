"""Backend i18n message pack: mcp_client_manager + utils 组残留用户可见消息（第二批）。

覆盖：modules/mcp_client_manager/{manager,http_client,stdio_client}.py、
utils/media_store.py、utils/api_client/{chat_mixin,profile_mixin}.py、
utils/tool_result_formatter/{terminal,web_media,file,agent_context}.py、
utils/context_manager/project_mixin.py。
纯数据模块 — 禁止任何 import；由 modules/i18n.py import 时自动聚合。
zh-CN 文案逐字复制自源码；en-US 为简洁的英文翻译（sentence case）。
"""

MESSAGES = {
    # ── mcp_client_manager/manager.py（MCPClientError → 前端工具块错误） ──
    "mcp_mgr.server_missing_id": {
        "zh-CN": "MCP 服务缺少 id",
        "en-US": "MCP server is missing an id",
    },
    "mcp_mgr.client_exec_failed": {
        "zh-CN": "MCP 客户端执行失败",
        "en-US": "MCP client execution failed",
    },
    "mcp_mgr.unsupported_transport": {
        "zh-CN": "不支持的 MCP transport: {transport}",
        "en-US": "Unsupported MCP transport: {transport}",
    },

    # ── mcp_client_manager/http_client.py（streamable HTTP 客户端错误） ──
    "mcp_http.writeback_failed": {
        "zh-CN": "HTTP MCP 回写失败: {error}",
        "en-US": "HTTP MCP write-back failed: {error}",
    },
    "mcp_http.writeback_status_error": {
        "zh-CN": "HTTP MCP 回写状态异常: {status} {detail}",
        "en-US": "HTTP MCP write-back status error: {status} {detail}",
    },
    "mcp_http.missing_url": {
        "zh-CN": "streamable_http 服务缺少 url",
        "en-US": "streamable_http server is missing a url",
    },
    "mcp_http.request_failed": {
        "zh-CN": "HTTP MCP 请求失败: {error}",
        "en-US": "HTTP MCP request failed: {error}",
    },
    "mcp_http.status_error": {
        "zh-CN": "HTTP MCP 状态异常: {status} {detail}",
        "en-US": "HTTP MCP status error: {status} {detail}",
    },
    "mcp_http.unparsable_response": {
        "zh-CN": "HTTP MCP 未返回可解析响应（method={method}）",
        "en-US": "HTTP MCP returned no parseable response (method={method})",
    },

    # ── mcp_client_manager/stdio_client.py（stdio 客户端错误） ──
    "mcp_stdio.missing_command": {
        "zh-CN": "stdio 服务缺少 command",
        "en-US": "stdio server is missing a command",
    },
    "mcp_stdio.docker_missing_container_name": {
        "zh-CN": "docker 模式下 stdio MCP 缺少 container_name",
        "en-US": "Docker-mode stdio MCP is missing container_name",
    },
    "mcp_stdio.launch_failed": {
        "zh-CN": "启动 stdio MCP 服务失败: {error}",
        "en-US": "Failed to launch stdio MCP server: {error}",
    },
    "mcp_stdio.process_exited": {
        "zh-CN": "stdio 进程已退出（code={code}）{detail}",
        "en-US": "stdio process exited (code={code}) {detail}",
    },
    "mcp_stdio.process_not_started": {
        "zh-CN": "stdio 进程未启动",
        "en-US": "stdio process has not started",
    },
    "mcp_stdio.write_failed": {
        "zh-CN": "写入 stdio 消息失败: {error}",
        "en-US": "Failed to write stdio message: {error}",
    },
    "mcp_stdio.read_timeout": {
        "zh-CN": "读取 stdio MCP 响应超时",
        "en-US": "Timed out reading stdio MCP response",
    },
    "mcp_stdio.call_failed": {
        "zh-CN": "{method} 调用失败: {error}",
        "en-US": "{method} call failed: {error}",
    },

    # ── utils/media_store.py（ValueError 文案） ──
    "media_store.base64_empty": {
        "zh-CN": "base64 数据为空",
        "en-US": "Base64 data is empty",
    },
    "media_store.invalid_data_url": {
        "zh-CN": "非法 data URL",
        "en-US": "Invalid data URL",
    },
    "media_store.base64_only": {
        "zh-CN": "仅支持 base64 data URL",
        "en-US": "Only base64 data URLs are supported",
    },
    "media_store.index_not_object": {
        "zh-CN": "index.json 不是对象",
        "en-US": "index.json is not an object",
    },
    "media_store.binary_empty": {
        "zh-CN": "媒体二进制为空",
        "en-US": "Media binary data is empty",
    },

    # ── utils/api_client/chat_mixin.py（工具参数过长建议文案） ──
    "api_client2.chunk_suggestion": {
        "zh-CN": "参数过长，建议分块处理或使用更简洁的内容",
        "en-US": "Arguments too long; chunk the input or use more concise content",
    },

    # ── utils/api_client/profile_mixin.py ──
    "api_profile.invalid_model_config": {
        "zh-CN": "无效的模型配置",
        "en-US": "Invalid model configuration",
    },

    # ── tool_result_formatter/terminal.py（fallback 文案） ──
    "fmt_terminal2.waited_sub_agents": {
        "zh-CN": "已等待 {n} 个子智能体结束",
        "en-US": "Waited for {n} sub-agents to finish",
    },
    "fmt_terminal2.wait_done": {
        "zh-CN": "等待完成",
        "en-US": "Waiting to complete",
    },
    "fmt_terminal2.background_created": {
        "zh-CN": "后台命令已创建；以下为当前已捕获输出。",
        "en-US": "Background command created; output captured so far:",
    },
    "fmt_terminal2.execution_failed": {
        "zh-CN": "执行失败",
        "en-US": "Execution failed",
    },

    # ── tool_result_formatter/web_media.py ──
    "fmt_web_media.personalization_read_ok": {
        "zh-CN": "个性化配置读取成功",
        "en-US": "Personalization config loaded",
    },

    # ── tool_result_formatter/file.py（残留 fallback 文案） ──
    "fmt_file2.reason_not_specified": {
        "zh-CN": "未说明原因",
        "en-US": "No reason specified",
    },
    "fmt_file2.reason_unknown": {
        "zh-CN": "未知原因",
        "en-US": "Unknown reason",
    },
    "fmt_file2.error_template": {
        "zh-CN": "⚠️ 错误: {error}",
        "en-US": "⚠️ Error: {error}",
    },

    # ── tool_result_formatter/agent_context.py（fallback 文案） ──
    "fmt_agent2.task_completed": {
        "zh-CN": "子智能体任务已完成。",
        "en-US": "Sub-agent task completed.",
    },
    "fmt_agent2.task_failed": {
        "zh-CN": "子智能体任务失败",
        "en-US": "Sub-agent task failed",
    },
    "fmt_agent2.closed": {
        "zh-CN": "子智能体已关闭。",
        "en-US": "Sub-agent closed.",
    },
    "fmt_agent2.force_closed": {
        "zh-CN": "子智能体已被强制关闭。",
        "en-US": "Sub-agent was forcibly closed.",
    },
    "fmt_agent2.paused": {
        "zh-CN": "子智能体已暂停。",
        "en-US": "Sub-agent paused.",
    },

    # ── utils/context_manager/project_mixin.py ──
    "project_mixin.host_mode_tree_unavailable": {
        "zh-CN": "宿主机模式下文件树不可用",
        "en-US": "File tree unavailable in host mode",
    },
}