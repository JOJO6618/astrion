"""Backend i18n message pack: modules 组 misc 用户可见消息。

覆盖：modules/skills_manager.py、modules/todo_manager.py、
modules/custom_tool_executor.py、modules/ocr_client.py、
modules/webpage_extractor.py、modules/mcp_client_manager/{manager,http_client}.py、
modules/easter_egg_manager.py、modules/versioning_manager.py。
纯数据模块 — 禁止任何 import；由 modules/i18n.py import 时自动聚合。
zh-CN 文案逐字复制自源码；en-US 为简洁的英文翻译（sentence case）。
"""

MESSAGES = {
    # ── skills_manager.py ──
    "skills.source_dir_not_dir": {
        "zh-CN": "source_dir 不是目录",
        "en-US": "source_dir is not a directory",
    },
    "skills.skill_id_invalid": {
        "zh-CN": "skill 文件夹名称不合法",
        "en-US": "Invalid skill folder name",
    },
    "skills.missing_skill_md": {
        "zh-CN": "缺少 SKILL.md",
        "en-US": "SKILL.md is missing",
    },
    "skills.skill_md_read_failed": {
        "zh-CN": "读取 SKILL.md 失败: {error}",
        "en-US": "Failed to read SKILL.md: {error}",
    },
    "skills.missing_name": {
        "zh-CN": "缺少 name:",
        "en-US": "Missing name:",
    },
    "skills.missing_description": {
        "zh-CN": "缺少 description:",
        "en-US": "Missing description:",
    },
    "skills.target_path_invalid": {
        "zh-CN": "目标路径不合法",
        "en-US": "Invalid target path",
    },
    "skills.target_exists": {
        "zh-CN": "目标 skill 已存在",
        "en-US": "Target skill already exists",
    },
    "skills.move_failed": {
        "zh-CN": "移动 skill 失败: {error}",
        "en-US": "Failed to move skill: {error}",
    },
    "skills.skills_dir_not_in_project": {
        "zh-CN": "skills 目录不在项目路径内",
        "en-US": "Skills directory is outside the project path",
    },

    # ── todo_manager.py ──
    "todo.overview_empty": {
        "zh-CN": "任务概述不能为空。",
        "en-US": "Task overview cannot be empty.",
    },
    "todo.overview_too_long": {
        "zh-CN": "任务概述过长（当前 {count} 字），请精简至 {max_length} 字以内。",
        "en-US": "Task overview is too long ({count} chars). Please keep it within {max_length} chars.",
    },
    "todo.no_tasks": {
        "zh-CN": "需要至少提供一个任务。",
        "en-US": "Please provide at least one task.",
    },
    "todo.too_many_tasks": {
        "zh-CN": "任务数量过多，最多允许 {max_count} 个任务。",
        "en-US": "Too many tasks; at most {max_count} tasks are allowed.",
    },
    "todo.task_too_long": {
        "zh-CN": "任务「{title}」过长，请控制在 {max_length} 字以内。",
        "en-US": "Task \"{title}\" is too long. Please keep it within {max_length} chars.",
    },
    "todo.created": {
        "zh-CN": "待办列表已创建（覆盖之前的列表）。",
        "en-US": "Todo list created (overwrites a previous list).",
    },
    "todo.no_todo_list": {
        "zh-CN": "当前没有待办列表，请先创建。",
        "en-US": "No todo list yet; please create one first.",
    },
    "todo.list_closed": {
        "zh-CN": "待办列表已结束，无法继续修改。",
        "en-US": "The todo list is finished and cannot be modified.",
    },
    "todo.invalid_indices_type": {
        "zh-CN": "task_index 或 task_indices 必须是数字或数字数组。",
        "en-US": "task_index or task_indices must be a number or an array of numbers.",
    },
    "todo.no_indices": {
        "zh-CN": "请提供至少一个任务序号。",
        "en-US": "Please provide at least one task index.",
    },
    "todo.index_out_of_range": {
        "zh-CN": "任务序号超出范围（1-{valid_max}）：{invalid}",
        "en-US": "Task index out of range (1-{valid_max}): {invalid}",
    },
    "todo.all_done": {
        "zh-CN": "所有任务已完成。",
        "en-US": "All tasks completed.",
    },
    "todo.task_done": {
        "zh-CN": "任务 {index}完成。",
        "en-US": "Task {index} marked as done.",
    },
    "todo.task_undone": {
        "zh-CN": "任务 {index}取消完成。",
        "en-US": "Task {index} unmarked as done.",
    },
    "todo.tasks_all_done": {
        "zh-CN": "任务 {indices}全部完成。",
        "en-US": "Tasks {indices} all completed.",
    },
    "todo.tasks_all_undone": {
        "zh-CN": "任务 {indices}已取消完成。",
        "en-US": "Tasks {indices} unmarked as done.",
    },

    # ── custom_tool_executor.py ──
    "custom_tool.not_found": {
        "zh-CN": "未找到自定义工具: {tool_id}",
        "en-US": "Custom tool not found: {tool_id}",
    },
    "custom_tool.unsupported_type": {
        "zh-CN": "当前仅支持 python 执行类型",
        "en-US": "Only the python execution type is supported",
    },
    "custom_tool.missing_code_template": {
        "zh-CN": "自定义工具缺少 code_template",
        "en-US": "Custom tool is missing code_template",
    },
    "custom_tool.missing_params": {
        "zh-CN": "缺少必填参数: {error}",
        "en-US": "Missing required parameter: {error}",
    },
    "custom_tool.render_failed": {
        "zh-CN": "模板渲染失败: {error}",
        "en-US": "Template rendering failed: {error}",
    },
    "custom_tool.executed": {
        "zh-CN": "已执行自定义工具",
        "en-US": "Custom tool executed",
    },

    # ── ocr_client.py ──
    "ocr.file_not_exists": {
        "zh-CN": "文件不存在",
        "en-US": "File does not exist",
    },
    "ocr.not_a_file": {
        "zh-CN": "不是文件",
        "en-US": "Not a file",
    },
    "ocr.prompt_empty": {
        "zh-CN": "prompt 不能为空",
        "en-US": "prompt cannot be empty",
    },
    "ocr.config_missing": {
        "zh-CN": "VLM 配置缺失，请设置 OCR_API_BASE_URL / OCR_API_KEY / OCR_MODEL_ID",
        "en-US": "VLM config missing; set OCR_API_BASE_URL / OCR_API_KEY / OCR_MODEL_ID",
    },
    "ocr.client_init_failed": {
        "zh-CN": "VLM 客户端初始化失败",
        "en-US": "VLM client initialization failed",
    },
    "ocr.read_failed": {
        "zh-CN": "读取文件失败: {error}",
        "en-US": "Failed to read file: {error}",
    },
    "ocr.file_empty": {
        "zh-CN": "文件为空，无法识别",
        "en-US": "File is empty and cannot be analyzed",
    },
    "ocr.image_too_large": {
        "zh-CN": "图片过大({size}字节)，上限为{max_size}字节",
        "en-US": "Image too large ({size} bytes); limit is {max_size} bytes",
    },
    "ocr.unknown_image_type": {
        "zh-CN": "无法确定图片类型，已按 JPEG 处理",
        "en-US": "Cannot determine the image type; processing as JPEG",
    },
    "ocr.vlm_call_failed": {
        "zh-CN": "VLM 调用失败: {error}",
        "en-US": "VLM call failed: {error}",
    },

    # ── webpage_extractor.py ──
    "webpage.api_key_missing": {
        "zh-CN": "Tavily API密钥未配置",
        "en-US": "Tavily API key is not configured",
    },
    "webpage.api_request_failed": {
        "zh-CN": "API请求失败: HTTP {status_code}",
        "en-US": "API request failed: HTTP {status_code}",
    },
    "webpage.timeout": {
        "zh-CN": "请求超时，网页响应过慢",
        "en-US": "Request timed out; the web page responded too slowly",
    },
    "webpage.network_error": {
        "zh-CN": "网络请求错误: {error}",
        "en-US": "Network request error: {error}",
    },
    "webpage.extract_error": {
        "zh-CN": "提取异常: {error}",
        "en-US": "Extraction error: {error}",
    },
    "webpage.format_failed": {
        "zh-CN": "❌ 提取失败: {error}",
        "en-US": "❌ Extraction failed: {error}",
    },
    "webpage.no_content": {
        "zh-CN": "❌ 未能提取到任何内容",
        "en-US": "❌ No content could be extracted",
    },

    # ── mcp_client_manager（manager.py + http_client.py） ──
    "mcp.server_not_found": {
        "zh-CN": "未找到 MCP 服务: {server_id}",
        "en-US": "MCP server not found: {server_id}",
    },
    "mcp.tool_mapping_not_found": {
        "zh-CN": "未找到 MCP 工具映射: {tool_alias}",
        "en-US": "MCP tool mapping not found: {tool_alias}",
    },
    "mcp.server_disabled": {
        "zh-CN": "MCP 服务已禁用: {server_id}",
        "en-US": "MCP server is disabled: {server_id}",
    },
    "mcp.call_failed": {
        "zh-CN": "{method} 调用失败: {error}",
        "en-US": "{method} call failed: {error}",
    },

    # ── easter_egg_manager.py ──
    "easter_egg.missing_effect": {
        "zh-CN": "缺少 effect 参数",
        "en-US": "Missing effect parameter",
    },
    "easter_egg.unknown_effect": {
        "zh-CN": "未知彩蛋: {effect}",
        "en-US": "Unknown easter egg: {effect}",
    },

    # ── versioning_manager.py ──
    "versioning.initial_state": {
        "zh-CN": "版本管理开启（初始状态）",
        "en-US": "Versioning enabled (initial state)",
    },
}