"""将工具执行结果转换为对话上下文可用的纯文本摘要。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def _format_failure(tag: str, result_data: Dict[str, Any]) -> str:
    error = result_data.get("error") or result_data.get("message") or "未知错误"
    suggestion = result_data.get("suggestion")
    details = result_data.get("details")
    parts = [f"⚠️ {tag} 失败: {error}"]
    if suggestion:
        parts.append(f"建议：{suggestion}")
    elif isinstance(details, str) and details:
        parts.append(f"详情：{details}")
    elif isinstance(details, dict):
        detail_msg = details.get("message") or details.get("error")
        if detail_msg:
            parts.append(f"详情：{detail_msg}")
    return "；".join(parts)

def _summarize_output_block(output: Optional[str], truncated: Optional[bool]) -> str:
    if not output:
        return "无可见输出"
    lines = output.splitlines()
    line_count = len(lines)
    char_count = len(output)
    meta = f"输出 {line_count} 行 / {char_count} 字符"
    if truncated:
        meta += "（已截断）"
    return f"{meta}\n```\n{output}\n```"

def _preview_text(text: str, limit: int) -> Tuple[str, bool]:
    """返回截断预览及是否截断标记。"""
    if text is None:
        return "", False
    if len(text) <= limit:
        return text, False
    return text[:limit], True

def _summarize_todo_tasks(todo: Optional[Dict[str, Any]]) -> str:
    if not isinstance(todo, dict):
        return ""
    tasks = todo.get("tasks") or []
    parts = []
    for task in tasks:
        status_icon = "✅" if task.get("status") == "done" else "⬜️"
        parts.append(f"{status_icon} task{task.get('index')}: {task.get('title')}")
    return "；".join(parts)
