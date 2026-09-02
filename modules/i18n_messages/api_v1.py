"""Backend i18n message pack: server/api_v1.py user-visible error messages.

Pure data module — do not import anything here. Auto-discovered and merged
by modules/i18n.py at import time. zh-CN copy is verbatim from source;
en-US is concise product-level English (sentence case).
"""

MESSAGES = {
    # ── server/api_v1.py（key 前缀 api_v1.） ──
    "api_v1.workspace_id_invalid_chars": {
        "zh-CN": "workspace_id 只能包含字母/数字/._-，长度1-40",
        "en-US": "workspace_id may only contain letters/numbers/._- and be 1-40 characters long",
    },
    "api_v1.workspace_id_invalid": {
        "zh-CN": "workspace_id 不合法",
        "en-US": "Invalid workspace_id",
    },
    "api_v1.workspace_not_found": {
        "zh-CN": "workspace 不存在",
        "en-US": "Workspace not found",
    },
    "api_v1.workspace_has_running_tasks": {
        "zh-CN": "该工作区有运行中的任务，无法删除",
        "en-US": "Cannot delete the workspace: it has running tasks",
    },
    "api_v1.workspace_not_found_or_delete_failed": {
        "zh-CN": "workspace 不存在或删除失败",
        "en-US": "Workspace not found or deletion failed",
    },
    "api_v1.system_not_initialized": {
        "zh-CN": "系统未初始化",
        "en-US": "System not initialized",
    },
    "api_v1.create_conversation_failed": {
        "zh-CN": "创建对话失败",
        "en-US": "Failed to create conversation",
    },
    "api_v1.message_empty": {
        "zh-CN": "消息不能为空",
        "en-US": "Message cannot be empty",
    },
    "api_v1.conversation_load_failed": {
        "zh-CN": "对话加载失败: {error}",
        "en-US": "Failed to load conversation: {error}",
    },
    "api_v1.prompt_not_found": {
        "zh-CN": "prompt 不存在",
        "en-US": "Prompt not found",
    },
    "api_v1.personalization_not_found": {
        "zh-CN": "personalization 不存在",
        "en-US": "Personalization not found",
    },
    "api_v1.personalization_parse_failed": {
        "zh-CN": "personalization 解析失败",
        "en-US": "Failed to parse personalization",
    },
    "api_v1.custom_params_error": {
        "zh-CN": "自定义参数错误: {error}",
        "en-US": "Invalid custom parameters: {error}",
    },
    "api_v1.conversation_not_found": {
        "zh-CN": "对话不存在",
        "en-US": "Conversation not found",
    },
    "api_v1.task_not_found": {
        "zh-CN": "任务不存在",
        "en-US": "Task not found",
    },
    "api_v1.no_file_uploaded": {
        "zh-CN": "未找到文件",
        "en-US": "No file found",
    },
    "api_v1.invalid_filename": {
        "zh-CN": "非法文件名",
        "en-US": "Invalid filename",
    },
    "api_v1.save_file_failed": {
        "zh-CN": "保存文件失败: {error}",
        "en-US": "Failed to save file: {error}",
    },
    "api_v1.path_not_found": {
        "zh-CN": "路径不存在",
        "en-US": "Path not found",
    },
    "api_v1.path_missing": {
        "zh-CN": "缺少 path",
        "en-US": "Missing path",
    },
    "api_v1.file_does_not_exist": {
        "zh-CN": "文件不存在",
        "en-US": "File not found",
    },
    "api_v1.name_empty": {
        "zh-CN": "name 不能为空",
        "en-US": "Name cannot be empty",
    },
    "api_v1.parse_failed": {
        "zh-CN": "解析失败: {error}",
        "en-US": "Failed to parse: {error}",
    },
    "api_v1.content_empty": {
        "zh-CN": "content 不能为空",
        "en-US": "Content cannot be empty",
    },
    "api_v1.content_must_be_object": {
        "zh-CN": "content 必须是 JSON object",
        "en-US": "Content must be a JSON object",
    },
    "api_v1.save_failed": {
        "zh-CN": "保存失败: {error}",
        "en-US": "Failed to save: {error}",
    },
    "api_v1.invalid_resource_name": {
        "zh-CN": "名称只能包含字母、数字、下划线和连字符（最长64字符）",
        "en-US": "Name may only contain letters, digits, underscores and hyphens (max 64 chars)",
    },
    "api_v1.folder_download_removed": {
        "zh-CN": "文件夹打包下载功能已下线",
        "en-US": "Folder archive download has been removed",
    },
    "api_v1.message_too_long": {
        "zh-CN": "消息内容过长",
        "en-US": "Message is too long",
    },
}