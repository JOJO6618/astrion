"""Compact, lossless text for the isolated approval reviewer."""
from __future__ import annotations

import json
from typing import Any


def text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(row.get("text") or "") for row in value
            if isinstance(row, dict) and row.get("type") in {"text", "input_text"}
        )
    return "" if value is None else str(value)


def metadata(message: dict) -> dict:
    value = message.get("metadata")
    return value if isinstance(value, dict) else {}


def user_text(message: dict) -> str | None:
    """Classify producers, not the user role or the injection transport flag."""
    if message.get("role") != "user":
        return None
    meta = metadata(message)
    source = str(meta.get("message_source") or meta.get("source") or "").lower()
    if source == "guidance":
        return str(meta.get("runtime_guidance_original") or text_content(message.get("content")))
    if source in {"user", "presend"}:
        if meta.get("is_auto_generated") or meta.get("multi_agent_message"):
            return None
        return text_content(message.get("content"))
    if source or any(meta.get(key) for key in (
        "is_auto_generated", "runtime_injected", "hidden", "multi_agent_message",
        "conversation_migration", "network_revision",
    )) or meta.get("visibility") == "hidden":
        return None
    # Current Chat messages, as well as older genuine inputs, omit message_source.
    content = text_content(message.get("content"))
    if content.startswith(("[系统通知|", "[Chat 联网设置更新]")):
        return None
    return content


def attachment_text(message: dict) -> str:
    meta = metadata(message)
    paths = list(meta.get("files") or message.get("files") or [])
    for item in [*(message.get("images") or []), *(message.get("videos") or [])]:
        path = item.get("path") if isinstance(item, dict) else item
        if isinstance(path, str) and path and not path.startswith("data:"):
            paths.append(path)
    for row in message.get("media_refs") or meta.get("media_refs") or []:
        if isinstance(row, dict):
            path = row.get("path") or row.get("relative_path") or row.get("file_path")
            if path:
                paths.append(path)
    return "\n".join(dict.fromkeys(str(path) for path in paths if path))


def value_text(value: Any, indent: int = 0) -> str:
    """Use indentation rather than JSON keys/brackets; never truncate semantics."""
    prefix = " " * indent
    if isinstance(value, dict):
        return "\n".join(
            f"{prefix}{key}:\n{value_text(item, indent + 2)}"
            if isinstance(item, (dict, list)) else f"{prefix}{key}: {scalar_text(item)}"
            for key, item in value.items()
        ) or f"{prefix}（空）"
    if isinstance(value, list):
        return "\n".join(f"{prefix}-\n{value_text(item, indent + 2)}" for item in value) or f"{prefix}（空列表）"
    return prefix + scalar_text(value)


def scalar_text(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def parse_arguments(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {"原始参数": value}
    except (TypeError, ValueError):
        return {"原始参数": value}


def tool_payload(message: dict) -> dict | None:
    payload = metadata(message).get("tool_payload")
    if isinstance(payload, dict):
        return payload
    try:
        parsed = json.loads(text_content(message.get("content")))
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def result_text(message: dict) -> str:
    payload = tool_payload(message)
    return value_text(payload) if payload is not None else text_content(message.get("content"))


def status_text(message: dict) -> str:
    payload = tool_payload(message) or {}
    state = payload.get("status") or metadata(message).get("status")
    if state:
        return str(state)
    if payload.get("success") is False:
        return "失败／未执行，以结果为准"
    if payload.get("success") is True:
        return "已返回结果，以结果为准"
    return "已有结果，执行状态见正文"
