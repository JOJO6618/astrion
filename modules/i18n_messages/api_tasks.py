"""API tasks message pack.

User-visible messages produced by server/tasks/api.py / models.py / skills.py
(REST responses and the error dicts they pass through to the frontend).
Pure data only — do not import anything here; merged into modules.i18n at import time.
Keys are prefixed with ``tasks.``.
"""

MESSAGES = {
    # ── api.py：running-status / create_task 校验 ──
    "tasks.missing_conversation_id": {
        "zh-CN": "缺少 conversation_id",
        "en-US": "Missing conversation_id",
    },
    "tasks.message_empty": {
        "zh-CN": "消息不能为空",
        "en-US": "Message cannot be empty",
    },
    "tasks.message_too_long": {
        "zh-CN": "消息内容过长",
        "en-US": "Message is too long",
    },
    "tasks.workspace_unavailable": {
        "zh-CN": "工作区不可用",
        "en-US": "Workspace is unavailable",
    },
    "tasks.read_skill_failed": {
        "zh-CN": "读取 skill 失败",
        "en-US": "Failed to load skill",
    },
    "tasks.task_not_found": {
        "zh-CN": "任务不存在",
        "en-US": "Task not found",
    },
    "tasks.task_status_not_allowed": {
        "zh-CN": "任务状态不允许",
        "en-US": "Task status does not allow this action",
    },
    "tasks.guidance_content_empty": {
        "zh-CN": "引导内容不能为空",
        "en-US": "Guidance content cannot be empty",
    },
    "tasks.message_not_found": {
        "zh-CN": "消息不存在",
        "en-US": "Message not found",
    },
    "tasks.delete_failed": {
        "zh-CN": "删除失败",
        "en-US": "Failed to delete",
    },

    # ── api.py：运行时引导/排队接口的兜底文案 ──
    "tasks.guidance_enqueue_failed": {
        "zh-CN": "追加引导失败",
        "en-US": "Failed to enqueue guidance",
    },
    "tasks.message_enqueue_failed": {
        "zh-CN": "追加消息失败",
        "en-US": "Failed to enqueue the message",
    },
    "tasks.guidance_failed": {
        "zh-CN": "引导失败",
        "en-US": "Guidance failed",
    },

    # ── models.py：运行队列 / 引导队列操作返回的 error dict ──
    "tasks.task_not_running_append_message": {
        "zh-CN": "任务已结束，无法追加消息",
        "en-US": "Task has ended; cannot append a message",
    },
    "tasks.queue_full_max": {
        "zh-CN": "堆积消息已满（最多 {limit} 条）",
        "en-US": "Queued messages are full (max {limit})",
    },
    "tasks.invalid_message_id": {
        "zh-CN": "消息ID无效",
        "en-US": "Invalid message ID",
    },
    "tasks.task_not_running_guide": {
        "zh-CN": "任务已结束，无法引导",
        "en-US": "Task has ended; cannot guide",
    },
    "tasks.guidance_queue_full_max": {
        "zh-CN": "引导队列已满（最多 {guidance_limit} 条）",
        "en-US": "Guidance queue is full (max {guidance_limit})",
    },
    "tasks.message_content_empty": {
        "zh-CN": "消息内容为空",
        "en-US": "Message content is empty",
    },
    "tasks.task_not_running_append_guidance": {
        "zh-CN": "任务已结束，无法追加引导",
        "en-US": "Task has ended; cannot append guidance",
    },

    # ── server/tasks/models.py 补充（代理 24 中断遗留） ──
    "tasks.invalid_run_mode": {
        "zh-CN": "run_mode 只支持 fast/thinking/deep",
        "en-US": "run_mode only supports fast/thinking/deep",
    },
    "tasks.task_already_running": {
        "zh-CN": "当前对话已有运行中的任务，请稍后再试。",
        "en-US": "This conversation already has a running task. Please try again later.",
    },
    "tasks.system_not_initialized": {
        "zh-CN": "系统未初始化",
        "en-US": "System not initialized",
    },
    "tasks.conversation_load_failed": {
        "zh-CN": "对话加载失败: {error}",
        "en-US": "Failed to load the conversation: {error}",
    },

    # ── server/tasks/skills.py ──
    "tasks.skill_dir_unavailable": {
        "zh-CN": "无法定位工作区 skills 目录: {error}",
        "en-US": "Cannot locate the workspace skills directory: {error}",
    },
    "tasks.skill_path_resolve_failed": {
        "zh-CN": "skill 路径解析失败: {error}",
        "en-US": "Failed to resolve the skill path: {error}",
    },
    "tasks.skill_path_not_skillmd": {
        "zh-CN": "skill 路径必须指向 SKILL.md: {name}",
        "en-US": "The skill path must point to SKILL.md: {name}",
    },
    "tasks.skill_path_outside": {
        "zh-CN": "skill 路径必须位于当前工作区 .astrion/skills/ 内",
        "en-US": "The skill path must be inside the current workspace's .astrion/skills/",
    },
    "tasks.skill_not_found": {
        "zh-CN": "skill 文件不存在",
        "en-US": "Skill file not found",
    },
    "tasks.skill_encoding_error": {
        "zh-CN": "skill 文件编码错误，无法读取: {name}",
        "en-US": "Skill file encoding error; cannot read: {name}",
    },
    "tasks.skill_read_failed": {
        "zh-CN": "读取 skill 文件失败: {error}",
        "en-US": "Failed to read the skill file: {error}",
    },
}