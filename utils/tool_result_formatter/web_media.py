from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from utils.tool_result_formatter.common import (
    _format_failure, _preview_text, _summarize_output_block, _summarize_todo_tasks
)

from modules.i18n import tr

def _format_create_skill(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("create_skill", result_data)
    lines = [
        f"已归档 skill：{result_data.get('skill_name') or '未命名'}",
    ]
    return "\n".join(lines)

def _format_list_workflows(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("list_workflows", result_data)
    # message 已是格式化清单（无参形态）或 WORKFLOW.md 原文（name 形态），完整透传
    return str(result_data.get("message") or "")

def _format_save_workflow(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("save_workflow", result_data)
    return str(result_data.get("summary") or f"已归档工作流：{result_data.get('workflow_name') or '未命名'}")

def _format_extract_webpage(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("extract_webpage", result_data)
    url = result_data.get("url") or "目标网页"
    content = result_data.get("content") or ""
    length = len(content)
    truncated_flag = result_data.get("truncated") or False
    citation_id = result_data.get("citation_id")
    citation_note = f"（来源 ID: {citation_id}）" if citation_id else ""
    header = f"提取完成：{url}{citation_note}，长度 {length} 字符。"
    if not content:
        return f"{header} 内容为空。"
    # 为模型保留完整正文，避免 800 字预览导致上下文缺失
    note_parts = []
    if truncated_flag:
        note_parts.append("原始内容已被上游截断")
    note = f"（{'；'.join(note_parts)}）" if note_parts else ""
    return "\n".join([f"{header}{note}", "```", content, "```"])

def _format_vlm_analyze(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("vlm_analyze", result_data)
    content = result_data.get("content") or ""
    length = len(content)
    preview, truncated = _preview_text(content, 800)
    note = "（截断预览）" if truncated else "（未截断）"
    header = f"VLM 解析完成，长度 {length} 字符{note}"
    if not content:
        return f"{header}；未返回可识别文本。"
    return "\n".join([header, "```", preview, "```"])

def _format_ocr_image(result_data: Dict[str, Any]) -> str:
    return _format_vlm_analyze(result_data)

def _format_trigger_easter_egg(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("trigger_easter_egg", result_data)
    effect = (result_data.get("effect") or "").lower()
    duration = result_data.get("duration_seconds") or result_data.get("duration")
    if effect == "flood":
        return "大水即将淹没屏幕！预计持续 45 秒，不过这期间你和用户还可以继续对话。"
    if effect == "snake":
        dur_text = f"{duration} 秒" if duration else "约 200 秒"
        return f"发光贪吃蛇来访，约 {dur_text} 后或吃满 20 个苹果离场；动画不挡操作。"
    message = result_data.get("message")
    if message:
        return message
    return f"已触发彩蛋：{effect or '未知效果'}。"

def _format_manage_personalization(result_data: Dict[str, Any]) -> str:
    action = result_data.get("action") or "read"
    if not result_data.get("success"):
        validation_errors = result_data.get("validation_errors") or []
        base = _format_failure("manage_personalization", result_data)
        if validation_errors:
            return base + "\n校验失败: " + "；".join(str(item) for item in validation_errors)
        return base

    if action == "update" or result_data.get("updated_field"):
        field = result_data.get("updated_field") or "未知字段"
        old_value = result_data.get("old_value")
        new_value = result_data.get("updated_value")
        parts = [f"个性化字段已更新: {field}"]
        if old_value is not None:
            parts.append(f"旧值: {old_value}")
        if new_value is not None:
            parts.append(f"新值: {new_value}")
        if result_data.get("theme_changed") and result_data.get("new_theme"):
            parts.append(f"主题已切换为: {result_data.get('new_theme')}")
        message = result_data.get("message")
        if message:
            parts.append(str(message))
        return "\n".join(parts)

    config = result_data.get("data") or {}
    if not isinstance(config, dict):
        return result_data.get("message") or tr("fmt_web_media.personalization_read_ok")

    lines = ["当前个性化配置:"]
    for key, value in config.items():
        lines.append(f"- {key}: {value}")
    message = result_data.get("message")
    if message:
        lines.append(str(message))
    return "\n".join(lines)
