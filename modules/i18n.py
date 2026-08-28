"""Backend i18n for user-visible messages.

Scope rule（与用户确认的边界）：只有「真正发送到前端显示」的消息走本模块
（toast 错误、系统通知包装、默认对话标题、子智能体/后台任务通知包装等）。
日志、内部异常、给模型的 prompt 注入文案不做多语言。

语言来源：personalization.json 的 ui_locale 字段（用户级共享，多工作区软链）。
读取侧零 IO：load/save personalization 时由 personalization_manager 调
sync_from_config() 把 ui_locale 推进进程级缓存；消息生成处直接 tr()。

注意：消息文本被前端正则/判等识别（如子智能体摘要前缀、上传错误），
改动任一文案必须同步前端匹配逻辑（见各处的 \"i18n-match\" 注释）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("zh-CN", "en-US")
DEFAULT_LOCALE = "zh-CN"

_LOCALE_LOCK = threading.RLock()
_current_locale = DEFAULT_LOCALE

# ---------------------------------------------------------------------------
# 文案表：key -> {locale: text}
# 插值用 str.format 命名参数：tr("key", name="...")。
# ---------------------------------------------------------------------------
_MESSAGES: Dict[str, Dict[str, str]] = {
    # ── 对话 ──
    "conversation.default_title": {
        "zh-CN": "新对话",
        "en-US": "New Chat",
    },

    # ── 子智能体完成通知包装（注入对话历史；前端 history.ts / ui/shared.ts /
    #    ChatArea.vue 有对应识别正则，改动格式必须同步前端） ──
    "sub_agent.summary_completed": {
        "zh-CN": "✅ 子智能体{agent_id} 任务摘要：{summary} 已完成。",
        "en-US": "✅ Sub-agent {agent_id} task summary: {summary} Completed.",
    },
    "sub_agent.summary_timeout": {
        "zh-CN": "⏱️ 子智能体{agent_id} 任务摘要：{summary} 超时未完成。",
        "en-US": "⏱️ Sub-agent {agent_id} task summary: {summary} Timed out.",
    },
    "sub_agent.summary_failed": {
        "zh-CN": "❌ 子智能体{agent_id} 任务摘要：{summary} 执行失败。",
        "en-US": "❌ Sub-agent {agent_id} task summary: {summary} Failed.",
    },
    "sub_agent.done_label": {
        # ChatArea.vue SUB_AGENT_DONE_LABEL_RE 识别此格式
        "zh-CN": "子智能体{agent_id} 任务完成",
        "en-US": "Sub-agent {agent_id} task done",
    },
    "sub_agent.timeout_terminated_note": {
        "zh-CN": "等待超时，子智能体已被终止。",
        "en-US": "Wait timed out; the sub-agent has been terminated.",
    },

    # ── 后台 run_command 完成通知包装（前端 history.ts / ui/shared.ts 有识别正则） ──
    "background_command.done_header": {
        "zh-CN": "[后台 run_command 完成]",
        "en-US": "[Background run_command finished]",
    },

    # ── 后台任务派发通知（chat_flow_task_main 注入对话的通知包装） ──
    "background_tasks.done_header": {
        "zh-CN": "后台指令已完成。",
        "en-US": "Background commands finished.",
    },
    "background_tasks.command_line": {
        "zh-CN": "命令：{command}",
        "en-US": "Command: {command}",
    },
    "background_tasks.return_code_line": {
        "zh-CN": "退出码：{code}",
        "en-US": "Exit code: {code}",
    },
    "background_tasks.output_section": {
        "zh-CN": "输出：",
        "en-US": "Output:",
    },

    # ── 上传安全（API 错误 → 前端 toast；stores/upload.ts 有识别正则） ──
    "upload.security_rejected": {
        "zh-CN": "安全审核未通过",
        "en-US": "Security check failed",
    },
    "upload.type_not_allowed": {
        "zh-CN": "文件类型不在允许列表中",
        "en-US": "File type is not in the allowed list",
    },
    "upload.size_exceeded": {
        "zh-CN": "文件大小 {size} 超过上限 {max} 字节",
        "en-US": "File size {size} exceeds the limit of {max} bytes",
    },

    # ── 工具调用（注入对话的工具错误；taskPolling/lifecycle.ts 有识别正则） ──
    "tool.param_parse_failed": {
        "zh-CN": "工具参数解析失败: {error}",
        "en-US": "Failed to parse tool arguments: {error}",
    },

    # ── 多智能体消息包装（前端 ChatArea.vue MULTI_AGENT_MESSAGE_RE 识别） ──
    "multi_agent.from_directed": {
        # 来自 {display_name} 向 {target} 的{type_label}
        "zh-CN": "来自 {display_name} 向 {target} 的{type_label}",
        "en-US": "From {display_name} to {target}: {type_label}",
    },
    "multi_agent.from_plain": {
        # 来自 {display_name} 的{type_label}
        "zh-CN": "来自 {display_name} 的{type_label}",
        "en-US": "From {display_name}: {type_label}",
    },
    "multi_agent.type_task": {
        "zh-CN": "任务发布",
        "en-US": "task dispatch",
    },
    "multi_agent.type_output": {
        "zh-CN": "任务进度输出",
        "en-US": "task progress",
    },
    "multi_agent.type_ask": {
        "zh-CN": "提问",
        "en-US": "question",
    },
    "multi_agent.type_answer": {
        "zh-CN": "回答",
        "en-US": "answer",
    },
    "multi_agent.type_message": {
        "zh-CN": "消息",
        "en-US": "message",
    },
}


def set_locale(locale: Any) -> None:
    """Set the process-level current locale (invalid values fall back to default)."""
    global _current_locale
    if not isinstance(locale, str):
        return
    normalized = locale.strip()
    if normalized not in SUPPORTED_LOCALES:
        return
    with _LOCALE_LOCK:
        _current_locale = normalized


def get_locale() -> str:
    """Return the process-level current locale."""
    with _LOCALE_LOCK:
        return _current_locale


def sync_from_config(config: Any) -> None:
    """Push personalization config's ui_locale into the process-level cache.

    Called by personalization_manager on every successful load/save so that
    all message-generation sites see a fresh locale without doing file IO.
    """
    if not isinstance(config, dict):
        return
    locale = config.get("ui_locale")
    if isinstance(locale, str) and locale in SUPPORTED_LOCALES:
        set_locale(locale)


def tr(key: str, **params: Any) -> str:
    """Translate a message key into the current locale with named params.

    Unknown keys fall back to the key itself (visible in UI, easy to spot in dev).
    """
    bundle = _MESSAGES.get(key)
    if bundle is None:
        return key
    locale = get_locale()
    text = bundle.get(locale) or bundle.get(DEFAULT_LOCALE) or key
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text
    return text


# ---------------------------------------------------------------------------
# 域消息包自动聚合
#
# 各域消息表放在 modules/i18n_messages/<domain>.py，每个文件暴露模块级
# `MESSAGES` dict（结构与 _MESSAGES 相同）。本模块 import 时自动合并，
# 调用方始终只用本模块的 tr()。新增域 = 往 modules/i18n_messages/ 丢一个
# 新文件，无需注册。消息包模块必须是纯数据（禁止 import modules.i18n）。
# ---------------------------------------------------------------------------


def _load_domain_messages() -> None:
    import importlib
    import pkgutil

    try:
        import modules.i18n_messages as _pack
    except ImportError:
        return
    for mod_info in pkgutil.iter_modules(_pack.__path__):
        try:
            mod = importlib.import_module(f"modules.i18n_messages.{mod_info.name}")
        except Exception as exc:
            logger.error(f"[i18n] failed to load message pack {mod_info.name}: {exc}")
            continue
        domain_messages = getattr(mod, "MESSAGES", None)
        if not isinstance(domain_messages, dict):
            continue
        for key, value in domain_messages.items():
            if key in _MESSAGES:
                logger.warning(
                    f"[i18n] duplicate message key overridden by pack {mod_info.name}: {key}"
                )
            _MESSAGES[key] = value


_load_domain_messages()
