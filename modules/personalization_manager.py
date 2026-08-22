"""Utilities for managing per-user personalization settings."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Union

try:
    from config.limits import REASONING_EFFORT_LEVELS
except ImportError:
    REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

from core.tool_config import TOOL_CATEGORIES
from config.model_profiles import get_default_model_key, get_registered_model_keys

ALLOWED_RUN_MODES = {"fast", "thinking"}
ALLOWED_PERMISSION_MODES = {"readonly", "approval", "auto_approval", "unrestricted"}
ALLOWED_WORK_MODES = {"plan", "ask", "execute"}
ALLOWED_THEMES = {"classic", "light", "dark"}
ALLOWED_COMMUNICATION_STYLES = {"default", "human_like", "auto"}
ALLOWED_CONVERSATION_CONTINUITY = {"low", "medium", "high"}
ALLOWED_GOAL_REVIEW_MODES = {"readonly", "active"}
ALLOWED_GOAL_END_CONDITIONS = {"max_turns", "max_tokens"}
ALLOWED_BLOCK_DISPLAY_MODES = {"traditional", "stacked", "minimal"}
GOAL_MAX_TURNS_MIN = 1
GOAL_MAX_TURNS_MAX = 100
GOAL_MAX_TURNS_DEFAULT = 5
GOAL_MAX_TOKENS_MIN = 1_000
GOAL_MAX_TOKENS_MAX = 100_000_000

PERSONALIZATION_FILENAME = "personalization.json"
MAX_SHORT_FIELD_LENGTH = 20
MAX_CONSIDERATION_TEXT_LENGTH = 2000
MAX_CONSIDERATION_ITEMS = 10
TONE_PRESETS = ["健谈", "幽默", "直言不讳", "鼓励性", "诗意", "企业商务", "打破常规", "同理心"]
RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN = 1
RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX = 30
RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT = 10
PROJECT_MEMORY_INJECT_LIMIT_MIN = 5
PROJECT_MEMORY_INJECT_LIMIT_DEFAULT = 20
PROJECT_MEMORY_INJECT_LIMIT_MAX = 100_000  # 防御性上限；None 表示无上限
DEFAULT_SHALLOW_COMPRESS_TRIGGER_TOKENS = 80_000
DEFAULT_SHALLOW_COMPRESS_KEEP_RECENT_TOOLS = 15
DEFAULT_SHALLOW_COMPRESS_MAX_REPLACE_PER_ROUND = 10
DEFAULT_SHALLOW_COMPRESS_TRIGGER_TOOL_CALLS_INTERVAL = 10
DEFAULT_DEEP_COMPRESS_TRIGGER_TOKENS = 150_000
MIN_COMPRESSION_TRIGGER_TOKENS = 1_000
MAX_COMPRESSION_TRIGGER_TOKENS = 2_000_000
MIN_SHALLOW_KEEP_RECENT_TOOLS = 0
MAX_SHALLOW_KEEP_RECENT_TOOLS = 500
MIN_SHALLOW_MAX_REPLACE_PER_ROUND = 1
MAX_SHALLOW_MAX_REPLACE_PER_ROUND = 200
MIN_SHALLOW_TRIGGER_TOOL_CALLS_INTERVAL = 1
MAX_SHALLOW_TRIGGER_TOOL_CALLS_INTERVAL = 200
MIN_SHALLOW_KEEP_USER_TURN_TOOLS = 0
MAX_SHALLOW_KEEP_USER_TURN_TOOLS = 50
DEFAULT_SHALLOW_KEEP_USER_TURN_TOOLS = 3

# 三个审核智能体的固定键：自动审批 / 目标审核 / 工作流审核
REVIEW_AGENT_KEYS = ("auto_approval", "goal_review", "workflow_review")

DEFAULT_PERSONALIZATION_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "communication_style": "default",  # default / human_like / auto
    "conversation_continuity": "medium",  # high / medium / low
    "self_identify": "",
    "user_name": "",
    "use_custom_names": False,
    "profession": "",
    "tone": "",
    "considerations": [],
    "disabled_tool_categories": [],
    "enabled_skills": None,
    "skills_catalog_snapshot": None,
    "default_run_mode": "thinking",
    "default_reasoning_effort": None,  # 默认推理强度：None=默认（不传参）
    "default_permission_mode": "unrestricted",
    "default_work_mode": "plan",  # 默认运行模式：plan / ask / execute
    "auto_generate_title": True,
    "recent_conversations_prompt_enabled": False,
    "recent_conversations_prompt_limit": RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT,
    "project_memory_inject_limit": PROJECT_MEMORY_INJECT_LIMIT_DEFAULT,  # 项目记忆索引最大注入条数：None-无上限 / >=5-该值
    "tool_intent_enabled": True,
    "skill_hints_enabled": False,  # Skill 提示系统开关（默认关闭）
    "skill_strict_terminal_enabled": False,  # 强约束：terminal 系列工具需先阅读 terminal-guide
    "skill_strict_sub_agent_enabled": False,  # 强约束：子智能体系列工具需先阅读 sub-agent-guide
    "skill_strict_run_command_foreground_enabled": False,  # 强约束：run_command 前台模式需先阅读 run-command-guide
    "skill_strict_run_command_background_enabled": False,  # 强约束：run_command 后台模式需先阅读 run-command-guide
    "default_model": None,
    "image_compression": "original",  # original / 1080p / 720p / 540p
    "auto_shallow_compress_enabled": False,
    "auto_deep_compress_enabled": True,
    "shallow_compress_trigger_tokens": None,
    "shallow_compress_keep_recent_tools": None,
    "shallow_compress_max_replace_per_round": None,
    "shallow_compress_trigger_tool_calls_interval": None,
    "shallow_compress_keep_user_turn_tools": None,  # 保留最近N次用户输入后的工具不压缩（默认3）
    "deep_compress_trigger_tokens": None,
    "deep_compress_form": "file",  # 深压缩形式：file-生成文件 / inject-直接注入文件全文
    "silent_tool_disable": True,  # 禁用工具时不向模型插入提示（默认开启）
    "hide_tool_approval_panel": True,  # 自动审核模式下不自动打开工具审核面板（默认开启）
    "enhanced_tool_display": True,  # 增强工具显示
    "compact_message_display": "full",  # 简略消息显示：full-完整原始内容 / brief-一行概要
    "block_display_mode": "stacked",  # 堆叠块显示模式：traditional-传统列表 / stacked-堆叠动画 / minimal-极简模式
    "show_status_avatar": True,  # 是否显示助手状态形象
    "stacked_hide_borders": False,  # 堆叠块隐藏边线
    "minimal_expand_height_limited": True,  # 极简模式展开摘要行时限制最大高度
    "show_git_status_bar": True,  # 是否显示输入栏上方 Git 状态栏
    "versioning_enabled_by_default": True,  # 新对话是否默认开启版本控制
    "versioning_backup_mode": "shallow",  # 文件备份方式：shallow-浅备份（只备份AI编辑的文件）/ full-完全备份（整个工作区）
    "versioning_restore_mode": "overwrite",  # 版本回溯模式固定为 overwrite
    "agents_md_auto_inject": False,  # AGENTS.md 自动注入开关
    "new_chat_button_behavior": "route",  # 新建对话按钮行为：route-跳转空白新对话页 / blank-立即创建空对话
    "default_hide_workspace": False,  # 默认隐藏工作区
    "hide_quick_dock": False,  # 隐藏快捷窗口（对话区右侧的待办/子智能体/后台指令/文件窗口列）
    "quick_dock_auto_expand": True,  # 快捷窗口自动展开：True-有内容时自动展开 / False-只能手动点击按钮展开
    "file_preview_auto_wrap": False,  # 文件预览窗口自动换行：True-按面板宽度换行显示 / False-长行横向滚动
    "edit_summary_live_display": False,  # 编辑摘要卡片显示时机：False-一次工作完成后才显示（默认） / True-工作运行期间实时显示
    "modify_history_enabled": True,  # 修改留痕：True-任务完成时把本轮净修改落盘到工作区 .astrion/modify_history/（默认开启） / False-不落盘也不注入 prompt
    "group_sidebar_by_workspace": False,  # 侧边栏按工作区/项目分组显示对话
    "sidebar_pinned_workspaces": [],  # 分组侧边栏中永久置顶的工作区ID列表
    "sidebar_workspace_order": [],  # 分组侧边栏中非置顶工作区的显示顺序
    "theme": "classic",  # 主题配色: classic-经典/light-明亮/dark-暗黑
    # 目标模式（Goal Mode）
    "goal_review_mode": "readonly",  # readonly-仅读对话判断 / active-允许审核智能体跑只读命令取证
    "goal_end_conditions": ["max_turns"],  # 结束方式，可多选：max_turns / max_tokens
    "goal_max_turns": GOAL_MAX_TURNS_DEFAULT,  # 最多自动续命轮数
    "goal_max_tokens": None,  # 累计(输入+输出)token 上限；None 表示不启用
    # 传统模式子智能体设置（个人空间-子智能体管理；与多智能体无关）
    "sub_agent_compress_threshold_tokens": 150000,  # 子智能体上下文压缩阈值，最小 10000
    "sub_agent_max_turns": None,  # 子智能体最大执行轮次：None-默认 50 / 0-无上限 / 正整数-该值
    # 审核智能体统一配置（个人空间-审核智能体页）：模型名留空则用子智能体模型库 default_model
    "review_agents": {
        "auto_approval": {"model": "", "thinking": False, "timeout_seconds": 60, "max_rounds": 3, "max_command_timeout": 20},
        "goal_review": {"model": "", "thinking": False, "timeout_seconds": 60, "max_rounds": 3, "max_command_timeout": 60},
        "workflow_review": {"model": "", "thinking": False, "timeout_seconds": 120, "max_rounds": 6, "max_command_timeout": 60},
    },
}

__all__ = [
    "PERSONALIZATION_FILENAME",
    "DEFAULT_PERSONALIZATION_CONFIG",
    "TONE_PRESETS",
    "MAX_CONSIDERATION_ITEMS",
    "RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN",
    "RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX",
    "RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT",
    "PROJECT_MEMORY_INJECT_LIMIT_MIN",
    "PROJECT_MEMORY_INJECT_LIMIT_DEFAULT",
    "resolve_project_memory_inject_limit",
    "load_personalization_config",
    "save_personalization_config",
    "ensure_personalization_config",
    "build_personalization_prompt",
    "sanitize_personalization_payload",
    "resolve_context_compression_settings",
    "validate_context_compression_settings",
    "ALLOWED_PERMISSION_MODES",
]


PathLike = Union[str, Path]


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _to_path(base: PathLike) -> Path:
    base_path = Path(base).expanduser()
    if base_path.is_dir():
        return base_path / PERSONALIZATION_FILENAME
    return base_path


def ensure_personalization_config(base_dir: PathLike) -> Dict[str, Any]:
    """Ensure the personalization file exists and return its content."""
    path = _to_path(base_dir)
    _ensure_parent(path)
    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_PERSONALIZATION_CONFIG, f, ensure_ascii=False, indent=2)
        return deepcopy(DEFAULT_PERSONALIZATION_CONFIG)
    return load_personalization_config(base_dir)


def load_personalization_config(base_dir: PathLike) -> Dict[str, Any]:
    """Load personalization config; fall back to defaults on errors."""
    path = _to_path(base_dir)
    _ensure_parent(path)
    if not path.exists():
        return ensure_personalization_config(base_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
            sanitized = sanitize_personalization_payload(raw)
            # 若发现缺失字段（如默认模型）或数据被规范化，主动写回文件，避免下一次读取仍为旧格式
            if sanitized != raw:
                with open(path, "w", encoding="utf-8") as wf:
                    json.dump(sanitized, wf, ensure_ascii=False, indent=2)
            return sanitized
    except (json.JSONDecodeError, OSError):
        # 重置为默认配置，避免错误阻塞
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_PERSONALIZATION_CONFIG, f, ensure_ascii=False, indent=2)
        return deepcopy(DEFAULT_PERSONALIZATION_CONFIG)


def _sanitize_string_list(value: Any, max_length: int = 100) -> list:
    """Sanitize a list of non-empty strings, deduplicating and limiting length."""
    if not isinstance(value, list):
        return []
    cleaned: list = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
        if len(cleaned) >= max_length:
            break
    return cleaned


def sanitize_personalization_payload(
    payload: Optional[Dict[str, Any]],
    fallback: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Normalize payload structure and clamp field lengths."""
    base = deepcopy(DEFAULT_PERSONALIZATION_CONFIG)
    if fallback:
        base.update(fallback)
    data = payload or {}
    allowed_tool_categories = set(TOOL_CATEGORIES.keys())
    allowed_models = set(get_registered_model_keys(visible_only=True))
    allowed_image_modes = {"original", "1080p", "720p", "540p"}

    def _resolve_short_field(key: str) -> str:
        if key in data:
            return _sanitize_short_field(data.get(key))
        return _sanitize_short_field(base.get(key))

    base["enabled"] = bool(data.get("enabled", base["enabled"]))
    # 交流风格: default / human_like / auto
    _comm_style = data.get("communication_style", base.get("communication_style", "default"))
    base["communication_style"] = _comm_style if _comm_style in ALLOWED_COMMUNICATION_STYLES else "default"
    _conversation_continuity = data.get(
        "conversation_continuity",
        base.get("conversation_continuity", "medium"),
    )
    base["conversation_continuity"] = (
        _conversation_continuity
        if _conversation_continuity in ALLOWED_CONVERSATION_CONTINUITY
        else "medium"
    )
    base["auto_generate_title"] = bool(data.get("auto_generate_title", base["auto_generate_title"]))
    base["recent_conversations_prompt_enabled"] = bool(
        data.get("recent_conversations_prompt_enabled", base.get("recent_conversations_prompt_enabled", False))
    )
    base["recent_conversations_prompt_limit"] = (
        _sanitize_optional_int(
            data.get("recent_conversations_prompt_limit", base.get("recent_conversations_prompt_limit")),
            min_value=RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
            max_value=RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
        )
        or RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT
    )
    base["self_identify"] = _resolve_short_field("self_identify")
    base["user_name"] = _resolve_short_field("user_name")
    base["profession"] = _resolve_short_field("profession")
    base["tone"] = _resolve_short_field("tone")
    if "considerations" in data:
        base["considerations"] = _sanitize_considerations(data.get("considerations"))
    else:
        base["considerations"] = _sanitize_considerations(base.get("considerations", []))

    # 工具意图提示开关
    if "tool_intent_enabled" in data:
        base["tool_intent_enabled"] = bool(data.get("tool_intent_enabled"))
    else:
        base["tool_intent_enabled"] = bool(base.get("tool_intent_enabled"))

    # Skill 提示系统开关
    if "skill_hints_enabled" in data:
        base["skill_hints_enabled"] = bool(data.get("skill_hints_enabled"))
    else:
        base["skill_hints_enabled"] = bool(base.get("skill_hints_enabled"))

    # Skill 强约束开关（仅针对已启用的 skill 生效）
    if "skill_strict_terminal_enabled" in data:
        base["skill_strict_terminal_enabled"] = bool(data.get("skill_strict_terminal_enabled"))
    else:
        base["skill_strict_terminal_enabled"] = bool(base.get("skill_strict_terminal_enabled", False))

    if "skill_strict_sub_agent_enabled" in data:
        base["skill_strict_sub_agent_enabled"] = bool(data.get("skill_strict_sub_agent_enabled"))
    else:
        base["skill_strict_sub_agent_enabled"] = bool(base.get("skill_strict_sub_agent_enabled", False))

    if "skill_strict_run_command_foreground_enabled" in data:
        base["skill_strict_run_command_foreground_enabled"] = bool(data.get("skill_strict_run_command_foreground_enabled"))
    else:
        base["skill_strict_run_command_foreground_enabled"] = bool(
            base.get("skill_strict_run_command_foreground_enabled", False)
        )

    if "skill_strict_run_command_background_enabled" in data:
        base["skill_strict_run_command_background_enabled"] = bool(data.get("skill_strict_run_command_background_enabled"))
    else:
        base["skill_strict_run_command_background_enabled"] = bool(
            base.get("skill_strict_run_command_background_enabled", False)
        )

    if "disabled_tool_categories" in data:
        base["disabled_tool_categories"] = _sanitize_tool_categories(data.get("disabled_tool_categories"), allowed_tool_categories)
    else:
        base["disabled_tool_categories"] = _sanitize_tool_categories(base.get("disabled_tool_categories"), allowed_tool_categories)

    if "enabled_skills" in data:
        base["enabled_skills"] = _sanitize_skills(data.get("enabled_skills"))
    else:
        base["enabled_skills"] = _sanitize_skills(base.get("enabled_skills"))

    if "skills_catalog_snapshot" in data:
        base["skills_catalog_snapshot"] = _sanitize_skills(data.get("skills_catalog_snapshot"))
    else:
        base["skills_catalog_snapshot"] = _sanitize_skills(base.get("skills_catalog_snapshot"))

    if "default_run_mode" in data:
        base["default_run_mode"] = _sanitize_run_mode(data.get("default_run_mode"))
    else:
        base["default_run_mode"] = _sanitize_run_mode(base.get("default_run_mode"))

    if "default_reasoning_effort" in data:
        base["default_reasoning_effort"] = _sanitize_reasoning_effort(data.get("default_reasoning_effort"))
    else:
        base["default_reasoning_effort"] = _sanitize_reasoning_effort(base.get("default_reasoning_effort"))

    permission_mode = data.get("default_permission_mode", base.get("default_permission_mode"))
    if isinstance(permission_mode, str) and permission_mode in ALLOWED_PERMISSION_MODES:
        base["default_permission_mode"] = permission_mode
    elif base.get("default_permission_mode") not in ALLOWED_PERMISSION_MODES:
        base["default_permission_mode"] = "unrestricted"

    work_mode = data.get("default_work_mode", base.get("default_work_mode"))
    if isinstance(work_mode, str) and work_mode.strip().lower() in ALLOWED_WORK_MODES:
        base["default_work_mode"] = work_mode.strip().lower()
    elif base.get("default_work_mode") not in ALLOWED_WORK_MODES:
        base["default_work_mode"] = "plan"

    base["versioning_restore_mode"] = "overwrite"

    # 默认模型
    chosen_model = data.get("default_model", base.get("default_model"))
    if isinstance(chosen_model, str) and chosen_model in allowed_models:
        base["default_model"] = chosen_model
    elif allowed_models and base.get("default_model") not in allowed_models:
        try:
            base["default_model"] = get_default_model_key(visible_only=True)
        except Exception:
            base["default_model"] = None

    # 图片压缩模式
    img_mode = data.get("image_compression", base.get("image_compression"))
    if isinstance(img_mode, str) and img_mode in allowed_image_modes:
        base["image_compression"] = img_mode
    elif base.get("image_compression") not in allowed_image_modes:
        base["image_compression"] = "original"

    if "auto_shallow_compress_enabled" in data:
        base["auto_shallow_compress_enabled"] = bool(data.get("auto_shallow_compress_enabled"))
    else:
        base["auto_shallow_compress_enabled"] = bool(base.get("auto_shallow_compress_enabled", False))

    if "auto_deep_compress_enabled" in data:
        base["auto_deep_compress_enabled"] = bool(data.get("auto_deep_compress_enabled"))
    else:
        base["auto_deep_compress_enabled"] = bool(base.get("auto_deep_compress_enabled", False))

    if "shallow_compress_trigger_tokens" in data:
        base["shallow_compress_trigger_tokens"] = _sanitize_optional_int(
            data.get("shallow_compress_trigger_tokens"),
            min_value=MIN_COMPRESSION_TRIGGER_TOKENS,
            max_value=MAX_COMPRESSION_TRIGGER_TOKENS,
        )
    else:
        base["shallow_compress_trigger_tokens"] = _sanitize_optional_int(
            base.get("shallow_compress_trigger_tokens"),
            min_value=MIN_COMPRESSION_TRIGGER_TOKENS,
            max_value=MAX_COMPRESSION_TRIGGER_TOKENS,
        )

    if "shallow_compress_keep_recent_tools" in data:
        base["shallow_compress_keep_recent_tools"] = _sanitize_optional_int(
            data.get("shallow_compress_keep_recent_tools"),
            min_value=MIN_SHALLOW_KEEP_RECENT_TOOLS,
            max_value=MAX_SHALLOW_KEEP_RECENT_TOOLS,
        )
    else:
        base["shallow_compress_keep_recent_tools"] = _sanitize_optional_int(
            base.get("shallow_compress_keep_recent_tools"),
            min_value=MIN_SHALLOW_KEEP_RECENT_TOOLS,
            max_value=MAX_SHALLOW_KEEP_RECENT_TOOLS,
        )

    if "shallow_compress_keep_user_turn_tools" in data:
        base["shallow_compress_keep_user_turn_tools"] = _sanitize_optional_int(
            data.get("shallow_compress_keep_user_turn_tools"),
            min_value=MIN_SHALLOW_KEEP_USER_TURN_TOOLS,
            max_value=MAX_SHALLOW_KEEP_USER_TURN_TOOLS,
        )
    else:
        base["shallow_compress_keep_user_turn_tools"] = _sanitize_optional_int(
            base.get("shallow_compress_keep_user_turn_tools"),
            min_value=MIN_SHALLOW_KEEP_USER_TURN_TOOLS,
            max_value=MAX_SHALLOW_KEEP_USER_TURN_TOOLS,
        )

    if "shallow_compress_max_replace_per_round" in data:
        base["shallow_compress_max_replace_per_round"] = _sanitize_optional_int(
            data.get("shallow_compress_max_replace_per_round"),
            min_value=MIN_SHALLOW_MAX_REPLACE_PER_ROUND,
            max_value=MAX_SHALLOW_MAX_REPLACE_PER_ROUND,
        )
    else:
        base["shallow_compress_max_replace_per_round"] = _sanitize_optional_int(
            base.get("shallow_compress_max_replace_per_round"),
            min_value=MIN_SHALLOW_MAX_REPLACE_PER_ROUND,
            max_value=MAX_SHALLOW_MAX_REPLACE_PER_ROUND,
        )

    if "deep_compress_trigger_tokens" in data:
        base["deep_compress_trigger_tokens"] = _sanitize_optional_int(
            data.get("deep_compress_trigger_tokens"),
            min_value=MIN_COMPRESSION_TRIGGER_TOKENS,
            max_value=MAX_COMPRESSION_TRIGGER_TOKENS,
        )
    else:
        base["deep_compress_trigger_tokens"] = _sanitize_optional_int(
            base.get("deep_compress_trigger_tokens"),
            min_value=MIN_COMPRESSION_TRIGGER_TOKENS,
            max_value=MAX_COMPRESSION_TRIGGER_TOKENS,
        )

    # 深压缩形式：file（生成文件，引导语提示位置）/ inject（直接注入文件全文）
    deep_form = data.get("deep_compress_form", base.get("deep_compress_form"))
    if isinstance(deep_form, str) and deep_form.strip().lower() in ("file", "inject"):
        base["deep_compress_form"] = deep_form.strip().lower()
    else:
        base["deep_compress_form"] = "file"

    if "shallow_compress_trigger_tool_calls_interval" in data:
        base["shallow_compress_trigger_tool_calls_interval"] = _sanitize_optional_int(
            data.get("shallow_compress_trigger_tool_calls_interval"),
            min_value=MIN_SHALLOW_TRIGGER_TOOL_CALLS_INTERVAL,
            max_value=MAX_SHALLOW_TRIGGER_TOOL_CALLS_INTERVAL,
        )
    else:
        base["shallow_compress_trigger_tool_calls_interval"] = _sanitize_optional_int(
            base.get("shallow_compress_trigger_tool_calls_interval"),
            min_value=MIN_SHALLOW_TRIGGER_TOOL_CALLS_INTERVAL,
            max_value=MAX_SHALLOW_TRIGGER_TOOL_CALLS_INTERVAL,
        )

    # 静默禁用工具提示
    if "silent_tool_disable" in data:
        base["silent_tool_disable"] = bool(data.get("silent_tool_disable"))
    else:
        base["silent_tool_disable"] = bool(base.get("silent_tool_disable"))

    # 隐藏工具审核面板（自动审核模式下不自动展开）
    if "hide_tool_approval_panel" in data:
        base["hide_tool_approval_panel"] = bool(data.get("hide_tool_approval_panel"))
    else:
        base["hide_tool_approval_panel"] = bool(base.get("hide_tool_approval_panel", True))

    # 增强工具显示
    if "enhanced_tool_display" in data:
        base["enhanced_tool_display"] = bool(data.get("enhanced_tool_display"))
    else:
        base["enhanced_tool_display"] = bool(base.get("enhanced_tool_display", True))

    # 简略消息显示：full（完整原始内容）/ brief（一行概要）
    compact_msg = data.get("compact_message_display", base.get("compact_message_display"))
    if isinstance(compact_msg, str) and compact_msg.strip().lower() in ("full", "brief"):
        base["compact_message_display"] = compact_msg.strip().lower()
    else:
        base["compact_message_display"] = "full"

    # 堆叠块显示模式：traditional（传统列表）/ stacked（堆叠动画）/ minimal（极简模式）
    block_display_mode = data.get("block_display_mode", base.get("block_display_mode"))
    if isinstance(block_display_mode, str) and block_display_mode.strip().lower() in ALLOWED_BLOCK_DISPLAY_MODES:
        base["block_display_mode"] = block_display_mode.strip().lower()
    else:
        base["block_display_mode"] = "stacked"

    # 堆叠块隐藏边线
    if "stacked_hide_borders" in data:
        base["stacked_hide_borders"] = bool(data.get("stacked_hide_borders"))
    else:
        base["stacked_hide_borders"] = bool(base.get("stacked_hide_borders", False))

    # 极简模式展开高度限制
    if "minimal_expand_height_limited" in data:
        base["minimal_expand_height_limited"] = bool(data.get("minimal_expand_height_limited"))
    else:
        base["minimal_expand_height_limited"] = bool(base.get("minimal_expand_height_limited", True))

    # 助手状态形象显示开关
    if "show_status_avatar" in data:
        base["show_status_avatar"] = bool(data.get("show_status_avatar"))
    else:
        base["show_status_avatar"] = bool(base.get("show_status_avatar", True))

    # Git 状态栏显示开关
    if "show_git_status_bar" in data:
        base["show_git_status_bar"] = bool(data.get("show_git_status_bar"))
    else:
        base["show_git_status_bar"] = bool(base.get("show_git_status_bar", True))

    # 新对话默认开启版本控制
    if "versioning_enabled_by_default" in data:
        base["versioning_enabled_by_default"] = bool(data.get("versioning_enabled_by_default"))
    else:
        base["versioning_enabled_by_default"] = bool(base.get("versioning_enabled_by_default", True))

    # 文件备份方式
    if "versioning_backup_mode" in data:
        mode = str(data.get("versioning_backup_mode") or "shallow").strip().lower()
        base["versioning_backup_mode"] = "full" if mode == "full" else "shallow"
    else:
        base["versioning_backup_mode"] = "shallow" if base.get("versioning_backup_mode") not in ("shallow", "full") else base["versioning_backup_mode"]

    # 使用自定义称呼
    if "use_custom_names" in data:
        base["use_custom_names"] = bool(data.get("use_custom_names"))
    else:
        base["use_custom_names"] = bool(base.get("use_custom_names"))

    # AGENTS.md 自动注入开关
    if "agents_md_auto_inject" in data:
        base["agents_md_auto_inject"] = bool(data.get("agents_md_auto_inject"))
    else:
        base["agents_md_auto_inject"] = bool(base.get("agents_md_auto_inject", False))

    # 新建对话按钮行为：route-跳转空白新对话页 / blank-立即创建空对话
    new_chat_behavior = data.get("new_chat_button_behavior", base.get("new_chat_button_behavior"))
    if isinstance(new_chat_behavior, str) and new_chat_behavior.strip().lower() in ("route", "blank"):
        base["new_chat_button_behavior"] = new_chat_behavior.strip().lower()
    else:
        base["new_chat_button_behavior"] = "route"

    # 默认隐藏工作区
    if "default_hide_workspace" in data:
        base["default_hide_workspace"] = bool(data.get("default_hide_workspace"))
    else:
        base["default_hide_workspace"] = bool(base.get("default_hide_workspace", False))

    # 隐藏快捷窗口
    if "hide_quick_dock" in data:
        base["hide_quick_dock"] = bool(data.get("hide_quick_dock"))
    else:
        base["hide_quick_dock"] = bool(base.get("hide_quick_dock", False))

    # 快捷窗口自动展开
    if "quick_dock_auto_expand" in data:
        base["quick_dock_auto_expand"] = bool(data.get("quick_dock_auto_expand"))
    else:
        base["quick_dock_auto_expand"] = bool(base.get("quick_dock_auto_expand", True))

    # 文件预览窗口自动换行
    if "file_preview_auto_wrap" in data:
        base["file_preview_auto_wrap"] = bool(data.get("file_preview_auto_wrap"))
    else:
        base["file_preview_auto_wrap"] = bool(base.get("file_preview_auto_wrap", False))

    # 编辑摘要卡片显示时机（默认关闭 = 工作完成后才显示）
    if "edit_summary_live_display" in data:
        base["edit_summary_live_display"] = bool(data.get("edit_summary_live_display"))
    else:
        base["edit_summary_live_display"] = bool(base.get("edit_summary_live_display", False))

    # 修改留痕（默认开启 = 任务完成时落盘 diff 并注入 prompt）
    if "modify_history_enabled" in data:
        base["modify_history_enabled"] = bool(data.get("modify_history_enabled"))
    else:
        base["modify_history_enabled"] = bool(base.get("modify_history_enabled", True))

    # 侧边栏按工作区/项目分组显示对话
    if "group_sidebar_by_workspace" in data:
        base["group_sidebar_by_workspace"] = bool(data.get("group_sidebar_by_workspace"))
    else:
        base["group_sidebar_by_workspace"] = bool(base.get("group_sidebar_by_workspace", False))

    # 分组侧边栏置顶工作区ID列表
    if "sidebar_pinned_workspaces" in data:
        base["sidebar_pinned_workspaces"] = _sanitize_string_list(
            data.get("sidebar_pinned_workspaces"), max_length=100
        )
    else:
        base["sidebar_pinned_workspaces"] = _sanitize_string_list(
            base.get("sidebar_pinned_workspaces"), max_length=100
        )

    # 分组侧边栏工作区显示顺序
    if "sidebar_workspace_order" in data:
        base["sidebar_workspace_order"] = _sanitize_string_list(
            data.get("sidebar_workspace_order"), max_length=200
        )
    else:
        base["sidebar_workspace_order"] = _sanitize_string_list(
            base.get("sidebar_workspace_order"), max_length=200
        )

    # 主题配色
    theme_value = data.get("theme", base.get("theme"))
    if isinstance(theme_value, str) and theme_value in ALLOWED_THEMES:
        base["theme"] = theme_value
    elif base.get("theme") not in ALLOWED_THEMES:
        base["theme"] = "classic"

    # 目标模式：审核模式
    goal_review_mode = data.get("goal_review_mode", base.get("goal_review_mode"))
    if isinstance(goal_review_mode, str) and goal_review_mode in ALLOWED_GOAL_REVIEW_MODES:
        base["goal_review_mode"] = goal_review_mode
    elif base.get("goal_review_mode") not in ALLOWED_GOAL_REVIEW_MODES:
        base["goal_review_mode"] = "readonly"

    # 目标模式：结束方式（多选）
    if "goal_end_conditions" in data:
        base["goal_end_conditions"] = _sanitize_goal_end_conditions(data.get("goal_end_conditions"))
    else:
        base["goal_end_conditions"] = _sanitize_goal_end_conditions(base.get("goal_end_conditions"))

    # 目标模式：最大轮数
    if "goal_max_turns" in data:
        base["goal_max_turns"] = _sanitize_optional_int(
            data.get("goal_max_turns"), min_value=GOAL_MAX_TURNS_MIN, max_value=GOAL_MAX_TURNS_MAX
        ) or GOAL_MAX_TURNS_DEFAULT
    else:
        base["goal_max_turns"] = _sanitize_optional_int(
            base.get("goal_max_turns"), min_value=GOAL_MAX_TURNS_MIN, max_value=GOAL_MAX_TURNS_MAX
        ) or GOAL_MAX_TURNS_DEFAULT

    # 目标模式：累计 token 上限（可空）
    if "goal_max_tokens" in data:
        base["goal_max_tokens"] = _sanitize_optional_int(
            data.get("goal_max_tokens"), min_value=GOAL_MAX_TOKENS_MIN, max_value=GOAL_MAX_TOKENS_MAX
        )
    else:
        base["goal_max_tokens"] = _sanitize_optional_int(
            base.get("goal_max_tokens"), min_value=GOAL_MAX_TOKENS_MIN, max_value=GOAL_MAX_TOKENS_MAX
        )

    # 传统模式子智能体：上下文压缩阈值（最小 10000，与 PUT /api/multiagent/settings 校验一致）
    if "sub_agent_compress_threshold_tokens" in data:
        base["sub_agent_compress_threshold_tokens"] = (
            _sanitize_optional_int(
                data.get("sub_agent_compress_threshold_tokens"),
                min_value=10000,
                max_value=10_000_000,
            )
            or 150000
        )
    else:
        base["sub_agent_compress_threshold_tokens"] = (
            _sanitize_optional_int(
                base.get("sub_agent_compress_threshold_tokens"),
                min_value=10000,
                max_value=10_000_000,
            )
            or 150000
        )

    # 传统模式子智能体：最大执行轮次（None-默认 50 / 0-无上限 / 正整数-该值）
    if "sub_agent_max_turns" in data:
        base["sub_agent_max_turns"] = _sanitize_sub_agent_max_turns(data.get("sub_agent_max_turns"))
    else:
        base["sub_agent_max_turns"] = _sanitize_sub_agent_max_turns(base.get("sub_agent_max_turns"))

    # 项目记忆索引最大注入条数（None-无上限 / >=5-该值，默认 20）
    if "project_memory_inject_limit" in data:
        base["project_memory_inject_limit"] = _sanitize_project_memory_inject_limit(data.get("project_memory_inject_limit"))
    else:
        base["project_memory_inject_limit"] = _sanitize_project_memory_inject_limit(base.get("project_memory_inject_limit"))

    # 审核智能体统一配置（模型名 / 思考模式 / 超时与轮次参数）
    if "review_agents" in data:
        base["review_agents"] = _sanitize_review_agents(data.get("review_agents"))
    else:
        base["review_agents"] = _sanitize_review_agents(base.get("review_agents"))

    return base


def _sanitize_sub_agent_max_turns(value: Any) -> Optional[int]:
    """清洗子智能体最大轮次：None/''/非法值/负数 → None（未设置，下游默认 50）；0 → 无上限；正整数 → 该值。"""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return min(parsed, 100_000)  # 防御性上限；需要更大时用 0 表示无上限


def _sanitize_review_agents(value: Any) -> Dict[str, Dict[str, Any]]:
    """清洗审核智能体统一配置：结构不对时回落默认值，数值参数钳到合理区间。"""
    defaults = DEFAULT_PERSONALIZATION_CONFIG["review_agents"]
    src = value if isinstance(value, dict) else {}
    out: Dict[str, Dict[str, Any]] = {}

    def _clamp_int(raw: Any, fallback: int, lo: int, hi: int) -> int:
        if isinstance(raw, bool):
            return fallback
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return fallback
        return max(lo, min(parsed, hi))

    for key in REVIEW_AGENT_KEYS:
        d = defaults[key]
        item = src.get(key) if isinstance(src.get(key), dict) else {}
        out[key] = {
            "model": str(item.get("model") or "").strip()[:100],
            "thinking": bool(item.get("thinking", d["thinking"])),
            "timeout_seconds": _clamp_int(item.get("timeout_seconds"), d["timeout_seconds"], 5, 3600),
            "max_rounds": _clamp_int(item.get("max_rounds"), d["max_rounds"], 1, 50),
            "max_command_timeout": _clamp_int(item.get("max_command_timeout"), d["max_command_timeout"], 1, 600),
        }
    return out


def _sanitize_project_memory_inject_limit(value: Any) -> Optional[int]:
    """清洗项目记忆索引最大注入条数：None/''/0/负数 → None（无上限）；非法值 → 默认 20；正整数钳到 [5, 100000]。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return PROJECT_MEMORY_INJECT_LIMIT_DEFAULT
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return PROJECT_MEMORY_INJECT_LIMIT_DEFAULT
    if parsed <= 0:
        return None
    return max(PROJECT_MEMORY_INJECT_LIMIT_MIN, min(parsed, PROJECT_MEMORY_INJECT_LIMIT_MAX))


def resolve_project_memory_inject_limit(config: Any) -> Optional[int]:
    """从个性化配置取项目记忆注入上限：None-无上限；缺键/非法 → 默认 20。"""
    if not isinstance(config, dict):
        return PROJECT_MEMORY_INJECT_LIMIT_DEFAULT
    if "project_memory_inject_limit" not in config:
        return PROJECT_MEMORY_INJECT_LIMIT_DEFAULT
    return _sanitize_project_memory_inject_limit(config.get("project_memory_inject_limit"))


def _sanitize_goal_end_conditions(value: Any) -> list:
    """清洗目标模式结束方式列表，保证至少包含 max_turns。"""
    cleaned: list = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item in ALLOWED_GOAL_END_CONDITIONS and item not in cleaned:
                cleaned.append(item)
    if "max_turns" not in cleaned:
        cleaned.insert(0, "max_turns")
    return cleaned


def _sanitize_skills(value: Any) -> Optional[list]:
    """Sanitize enabled skills list / 清洗启用技能列表。"""
    if value is None:
        return None
    if not isinstance(value, list):
        return []
    cleaned: list = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            continue
        skill_id = item.strip()
        if not skill_id or skill_id in seen:
            continue
        cleaned.append(skill_id)
        seen.add(skill_id)
    return cleaned


def save_personalization_config(base_dir: PathLike, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist sanitized personalization config and return it."""
    existing = load_personalization_config(base_dir)
    config = sanitize_personalization_payload(payload, fallback=existing)
    validate_context_compression_settings(config)
    path = _to_path(base_dir)
    _ensure_parent(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config


def _sanitize_optional_int(value: Any, *, min_value: int, max_value: int) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < min_value:
        return min_value
    if parsed > max_value:
        return max_value
    return parsed


def resolve_context_compression_settings(config: Optional[Dict[str, Any]]) -> Dict[str, int]:
    data = config or {}
    shallow_trigger = _sanitize_optional_int(
        data.get("shallow_compress_trigger_tokens"),
        min_value=MIN_COMPRESSION_TRIGGER_TOKENS,
        max_value=MAX_COMPRESSION_TRIGGER_TOKENS,
    ) or DEFAULT_SHALLOW_COMPRESS_TRIGGER_TOKENS
    shallow_keep_recent = _sanitize_optional_int(
        data.get("shallow_compress_keep_recent_tools"),
        min_value=MIN_SHALLOW_KEEP_RECENT_TOOLS,
        max_value=MAX_SHALLOW_KEEP_RECENT_TOOLS,
    )
    if shallow_keep_recent is None:
        shallow_keep_recent = DEFAULT_SHALLOW_COMPRESS_KEEP_RECENT_TOOLS
    shallow_keep_user_turn = _sanitize_optional_int(
        data.get("shallow_compress_keep_user_turn_tools"),
        min_value=MIN_SHALLOW_KEEP_USER_TURN_TOOLS,
        max_value=MAX_SHALLOW_KEEP_USER_TURN_TOOLS,
    )
    if shallow_keep_user_turn is None:
        shallow_keep_user_turn = DEFAULT_SHALLOW_KEEP_USER_TURN_TOOLS
    shallow_max_replace = _sanitize_optional_int(
        data.get("shallow_compress_max_replace_per_round"),
        min_value=MIN_SHALLOW_MAX_REPLACE_PER_ROUND,
        max_value=MAX_SHALLOW_MAX_REPLACE_PER_ROUND,
    ) or DEFAULT_SHALLOW_COMPRESS_MAX_REPLACE_PER_ROUND
    shallow_trigger_interval = _sanitize_optional_int(
        data.get("shallow_compress_trigger_tool_calls_interval"),
        min_value=MIN_SHALLOW_TRIGGER_TOOL_CALLS_INTERVAL,
        max_value=MAX_SHALLOW_TRIGGER_TOOL_CALLS_INTERVAL,
    ) or DEFAULT_SHALLOW_COMPRESS_TRIGGER_TOOL_CALLS_INTERVAL
    deep_trigger = _sanitize_optional_int(
        data.get("deep_compress_trigger_tokens"),
        min_value=MIN_COMPRESSION_TRIGGER_TOKENS,
        max_value=MAX_COMPRESSION_TRIGGER_TOKENS,
    ) or DEFAULT_DEEP_COMPRESS_TRIGGER_TOKENS
    if deep_trigger <= shallow_trigger:
        deep_trigger = shallow_trigger + 1
    return {
        "shallow_trigger_tokens": shallow_trigger,
        "shallow_keep_recent_tools": shallow_keep_recent,
        "shallow_keep_user_turn_tools": shallow_keep_user_turn,
        "shallow_max_replace_per_round": shallow_max_replace,
        "shallow_trigger_tool_calls_interval": shallow_trigger_interval,
        "deep_trigger_tokens": deep_trigger,
    }


def validate_context_compression_settings(config: Optional[Dict[str, Any]]) -> None:
    data = config or {}
    shallow_trigger = _sanitize_optional_int(
        data.get("shallow_compress_trigger_tokens"),
        min_value=MIN_COMPRESSION_TRIGGER_TOKENS,
        max_value=MAX_COMPRESSION_TRIGGER_TOKENS,
    ) or DEFAULT_SHALLOW_COMPRESS_TRIGGER_TOKENS
    deep_trigger = _sanitize_optional_int(
        data.get("deep_compress_trigger_tokens"),
        min_value=MIN_COMPRESSION_TRIGGER_TOKENS,
        max_value=MAX_COMPRESSION_TRIGGER_TOKENS,
    ) or DEFAULT_DEEP_COMPRESS_TRIGGER_TOKENS
    if deep_trigger <= shallow_trigger:
        raise ValueError("深压缩触发上下文必须大于浅压缩触发上下文")


def _load_human_like_prompt() -> str:
    """加载拟人化风格提示词文件。"""
    prompt = _load_prompt_file("human_like_style.txt")
    if prompt:
        return prompt
    # 默认提示词
    return (
        "不要「像AI一样说话」，而是尽可能的模仿真人的说话方式或是模仿用户输入的说话方式。\n"
        "尽可能不要用markdown格式，尤其是：\n"
        "- ###、##、#等一二三级标题\n"
        "- 等列表形式\n"
        "- 不必要的** **加粗\n"
        "- 刻意的分点分层次的回答\n"
        "用户如果不输入emoji，就不要使用emoji回答。"
    )


def _load_auto_style_prompt() -> str:
    """加载自动交流风格提示词文件。"""
    return _load_prompt_file("auto_style.txt")


def _load_prompt_file(filename: str) -> str:
    from config.paths import PROMPTS_DIR
    try:
        prompt_path = Path(PROMPTS_DIR) / filename
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def build_personalization_prompt(
    config: Optional[Dict[str, Any]],
    include_header: bool = True
) -> Optional[str]:
    """Generate the personalization prompt text based on config."""
    if not config or not config.get("enabled"):
        return None

    lines = []
    if include_header:
        lines.append("用户的个性化数据，请回答时务必参照这些信息")

    if config.get("self_identify"):
        lines.append(f"用户希望你自称：{config['self_identify']}")
    if config.get("user_name"):
        lines.append(f"用户希望你称呼为：{config['user_name']}")
    if config.get("profession"):
        lines.append(f"用户的职业是：{config['profession']}")
    if config.get("tone"):
        lines.append(f"用户希望你使用 {config['tone']} 的语气与TA交流")

    considerations: str = config.get("considerations") or ""
    if considerations:
        lines.append("用户希望你在回答问题时必须考虑的信息是：")
        lines.append(considerations)

    conversation_continuity = str(config.get("conversation_continuity") or "medium").strip().lower()
    if conversation_continuity == "high":
        lines.append("\n【对话连续性：高】")
        lines.append(
            "当前对话应积极延续用户长期背景。回答时优先参考用户过往对话、长期记忆和项目背景；"
            "遇到可能相关的问题，应主动搜索/回顾历史对话；发现值得长期保留的偏好、项目事实、"
            "稳定经验或用户明确要求记住的内容，应主动调用记忆工具记录。"
        )
    elif conversation_continuity == "low":
        lines.append("\n【对话连续性：低】")
        lines.append(
            "当前对话应尽量独立。不要主动回顾之前的对话，不要主动调用记忆或历史对话工具，"
            "不要主动参考用户历史记忆来回答；只有用户明确要求、当前任务必须依赖历史，"
            "或安全/连续性需要时，才可搜索历史或读取记忆。"
        )
    else:
        lines.append("\n【对话连续性：中】")
        lines.append(
            "当前对话优先，但可以在适当时候参考历史。若用户问题明显与过往偏好、项目背景或未完成事项有关，"
            "可以调用记忆或历史对话工具；不要为了普通问题主动翻历史。"
        )

    communication_style = str(config.get("communication_style") or "default").strip().lower()
    if communication_style == "auto":
        auto_style_prompt = _load_auto_style_prompt()
        if auto_style_prompt:
            lines.append("\n【交流风格：自动】")
            lines.append(auto_style_prompt)
    elif communication_style == "human_like":
        human_like_prompt = _load_human_like_prompt()
        if human_like_prompt:
            lines.append("\n【交流风格要求】")
            lines.append(human_like_prompt)

    if len(lines) == (1 if include_header else 0):
        # 没有任何有效内容时不注入
        return None
    return "\n".join(lines)


def _sanitize_short_field(value: Optional[str]) -> str:
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text[:MAX_SHORT_FIELD_LENGTH]


def _sanitize_considerations(value: Any) -> str:
    """Sanitize considerations text. Legacy array data is joined with newlines."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:MAX_CONSIDERATION_TEXT_LENGTH]
    if isinstance(value, list):
        cleaned = []
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            cleaned.append(text)
            if len(cleaned) >= MAX_CONSIDERATION_ITEMS:
                break
        return "\n".join(cleaned)[:MAX_CONSIDERATION_TEXT_LENGTH]
    return ""


def _sanitize_tool_categories(value: Any, allowed: set) -> list:
    if not isinstance(value, list):
        return []
    result = []
    dynamic_prefixes = ("mcp_server__",)
    for item in value:
        if not isinstance(item, str):
            continue
        candidate = item.strip()
        if not candidate:
            continue
        dynamic_ok = candidate == "custom" or any(candidate.startswith(prefix) for prefix in dynamic_prefixes)
        if candidate not in allowed and not dynamic_ok:
            continue
        if candidate not in result:
            result.append(candidate)
    return result


def _sanitize_run_mode(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate == "deep":  # 旧版标识符，映射为思考模式
            candidate = "thinking"
        if candidate in ALLOWED_RUN_MODES:
            return candidate
    return None


def _sanitize_reasoning_effort(value: Any) -> Optional[str]:
    """推理强度：合法档位原样返回，其余（含空）一律 None=默认不传参。"""
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if candidate in REASONING_EFFORT_LEVELS else None
