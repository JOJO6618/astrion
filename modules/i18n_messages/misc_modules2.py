"""Backend i18n message pack: modules 组 misc 用户可见消息（第二批）。

覆盖：modules/versioning_manager.py、modules/shallow_versioning.py、
modules/upload_security.py、modules/sub_agent/{creation,task}.py、
modules/easter_egg_manager.py、modules/personalization_manager.py、
modules/custom_tool_registry.py、modules/webpage_extractor.py、
modules/multi_agent/prompts.py、modules/sub_agent/prompts.py。
纯数据模块 — 禁止任何 import；由 modules/i18n.py import 时自动聚合。
zh-CN 文案逐字复制自源码；en-US 为简洁的英文翻译（sentence case）。
"""

MESSAGES = {
    # ── versioning_manager.py ──
    "versioning_err.missing_conversation_id": {
        "zh-CN": "缺少 conversation_id",
        "en-US": "Missing conversation_id",
    },
    "versioning_err.git_not_found": {
        "zh-CN": "未检测到 git 可执行文件",
        "en-US": "Git executable not found",
    },
    "versioning_err.git_timeout": {
        "zh-CN": "git 执行超时: {cmd}",
        "en-US": "Git execution timed out: {cmd}",
    },
    "versioning_err.git_cmd_failed": {
        "zh-CN": "git 命令失败: {cmd}",
        "en-US": "Git command failed: {cmd}",
    },
    "versioning_err.initial_checkpoint_failed": {
        "zh-CN": "创建初始版本点失败：未获取到 commit",
        "en-US": "Failed to create the initial checkpoint: no commit obtained",
    },
    "versioning_err.snapshot_failed": {
        "zh-CN": "创建版本快照失败：未获取到 tree hash",
        "en-US": "Failed to create the version snapshot: no tree hash obtained",
    },
    "versioning_err.missing_tree_hash": {
        "zh-CN": "缺少 tree hash",
        "en-US": "Missing tree hash",
    },
    "versioning_err.tree_not_found": {
        "zh-CN": "目标 tree 不存在",
        "en-US": "Target tree does not exist",
    },

    # ── shallow_versioning.py ──
    "shallow_ver.missing_conversation_id": {
        "zh-CN": "缺少 conversation_id",
        "en-US": "Missing conversation_id",
    },
    "shallow_ver.snapshot_not_found": {
        "zh-CN": "未找到消息 {message_id} 对应的快照",
        "en-US": "No snapshot found for message {message_id}",
    },

    # ── upload_security.py ──
    "upload_sec.scanner_not_found": {
        "zh-CN": "未找到 ClamAV 扫描器，请检查配置",
        "en-US": "ClamAV scanner not found; please check the configuration",
    },
    "upload_sec.scanner_unavailable": {
        "zh-CN": "ClamAV 扫描器不可用",
        "en-US": "ClamAV scanner unavailable",
    },

    # ── sub_agent/creation.py ──
    "sub_agent_creation.deliverables_dir_required": {
        "zh-CN": "交付目录不能为空，必须指定",
        "en-US": "Deliverables directory cannot be empty",
    },
    "sub_agent_creation.deliverables_dir_outside": {
        "zh-CN": "交付目录必须位于项目目录内",
        "en-US": "Deliverables directory must be inside the project directory",
    },
    "sub_agent_creation.deliverables_dir_not_new": {
        "zh-CN": "交付目录必须为不存在的新目录",
        "en-US": "Deliverables directory must be a new, non-existing directory",
    },

    # ── sub_agent/task.py ──
    "sub_agent_task2.no_model_config": {
        "zh-CN": "未找到可用子智能体模型配置: {path}",
        "en-US": "No usable sub-agent model configuration found: {path}",
    },
    "sub_agent_task2.api_call_failed": {
        "zh-CN": "API 调用失败: {error}",
        "en-US": "API call failed: {error}",
    },
    "sub_agent_task2.api_call_exception": {
        "zh-CN": "API 调用异常: {error}",
        "en-US": "API call exception: {error}",
    },

    # ── easter_egg_manager.py ──
    "easter_egg.flood_message": {
        "zh-CN": "淡蓝色水面从底部缓缓上涨，并带有柔和波纹。",
        "en-US": "A light blue water surface slowly rises from the bottom with gentle ripples.",
    },
    "easter_egg.snake_message": {
        "zh-CN": "发光的丝带贪吃蛇追逐苹果，吃满 20 个后会一路远行离开屏幕。",
        "en-US": "A glowing ribbon snake chases apples and leaves the screen after eating 20.",
    },

    # ── personalization_manager.py ──
    "personalization.deep_trigger_gt_shallow": {
        "zh-CN": "深压缩触发上下文必须大于浅压缩触发上下文",
        "en-US": "Deep compression trigger context must be greater than shallow compression trigger context",
    },

    # ── custom_tool_registry.py ──
    "custom_tool_reg.id_required": {
        "zh-CN": "id 必填",
        "en-US": "id is required",
    },
    "custom_tool_reg.id_invalid": {
        "zh-CN": "工具 ID 不合法：需以字母开头，可包含字母、数字、下划线、短横线",
        "en-US": "Invalid tool ID: must start with a letter and may contain letters, digits, underscores, and hyphens",
    },

    # ── webpage_extractor.py ──
    "webpage.unknown_error": {
        "zh-CN": "未知错误",
        "en-US": "Unknown error",
    },

    # ── multi_agent/prompts.py ──
    "ma_prompts.template_missing": {
        "zh-CN": "多智能体 prompt 模板缺失: {path}",
        "en-US": "Multi-agent prompt template missing: {path}",
    },

    # ── sub_agent/prompts.py ──
    "sa_prompts.template_missing": {
        "zh-CN": "子智能体 prompt 模板缺失: {path}",
        "en-US": "Sub-agent prompt template missing: {path}",
    },
}