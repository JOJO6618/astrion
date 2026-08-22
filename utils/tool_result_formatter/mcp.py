from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from utils.tool_result_formatter.common import (
    _format_failure, _preview_text, _summarize_output_block, _summarize_todo_tasks
)

def _looks_like_mcp_payload(function_name: str, result_data: Any) -> bool:
    if isinstance(function_name, str) and function_name.startswith("mcp__"):
        return True
    if not isinstance(result_data, dict):
        return False
    if result_data.get("tool_alias") or result_data.get("server_id"):
        return True
    content = result_data.get("content")
    if isinstance(content, list) and any(isinstance(item, dict) and item.get("type") for item in content):
        return True
    return False

def _format_mcp_resource_reference(item: Dict[str, Any]) -> str:
    uri = str(item.get("uri") or item.get("url") or "").strip()
    title = str(item.get("title") or item.get("name") or "").strip()
    if uri and title:
        return f"[MCP 资源] {title} -> {uri}"
    if uri:
        return f"[MCP 资源] {uri}"
    if title:
        return f"[MCP 资源] {title}"
    return ""

def _sanitize_mcp_content_item(item: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = dict(item)
    ctype = str(sanitized.get("type") or "").strip().lower()
    if ctype in {"image", "audio"}:
        data = str(sanitized.pop("data", "") or "")
        if data:
            sanitized["data_omitted"] = True
            sanitized["data_length"] = len(data)
    if ctype == "resource":
        resource = sanitized.get("resource")
        if isinstance(resource, dict) and "blob" in resource:
            blob_data = str(resource.get("blob") or "")
            resource_copy = dict(resource)
            resource_copy.pop("blob", None)
            if blob_data:
                resource_copy["blob_omitted"] = True
                resource_copy["blob_length"] = len(blob_data)
            sanitized["resource"] = resource_copy
    return sanitized

def _is_default_mcp_message(message: str) -> bool:
    return message.strip() == "MCP 工具调用完成"

def extract_mcp_content_for_context(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """提取 MCP content，返回可读文本 + 可落盘媒体列表 + 去二进制后的 payload。"""
    if not isinstance(result_data, dict):
        return {
            "text": "",
            "media_items": [],
            "sanitized_payload": result_data,
        }

    content_items = result_data.get("content")
    if not isinstance(content_items, list):
        raw_result = result_data.get("raw_result")
        if isinstance(raw_result, dict) and isinstance(raw_result.get("content"), list):
            content_items = raw_result.get("content")
        else:
            content_items = []

    text_lines: List[str] = []
    media_lines: List[str] = []
    media_items: List[Dict[str, Any]] = []
    sanitized_content: List[Dict[str, Any]] = []

    for index, item in enumerate(content_items, start=1):
        if not isinstance(item, dict):
            continue
        ctype = str(item.get("type") or "").strip().lower()
        if ctype == "text":
            text_value = str(item.get("text") or "").strip()
            if text_value:
                text_lines.append(text_value)
            sanitized_content.append(_sanitize_mcp_content_item(item))
            continue

        if ctype in {"image", "audio"}:
            kind = "image" if ctype == "image" else "audio"
            mime_type = str(item.get("mimeType") or item.get("mime_type") or "").strip().lower()
            if not mime_type:
                mime_type = "image/png" if kind == "image" else "audio/wav"
            data_base64 = str(item.get("data") or "")
            if data_base64:
                media_items.append(
                    {
                        "index": index,
                        "item_type": ctype,
                        "kind": kind,
                        "mime_type": mime_type,
                        "data_base64": data_base64,
                        "label": item.get("label") or f"{kind}_{index}",
                        "name": item.get("name"),
                        "title": item.get("title"),
                        "uri": item.get("uri"),
                        "url": item.get("url"),
                    }
                )
                media_lines.append(
                    f"[MCP {kind} #{index}] {mime_type}（{len(data_base64)} base64 chars）"
                )
            else:
                media_lines.append(f"[MCP {kind} #{index}] 未包含数据")
            sanitized_content.append(_sanitize_mcp_content_item(item))
            continue

        if ctype in {"resource_link", "resourcelink"}:
            line = _format_mcp_resource_reference(item)
            if line:
                text_lines.append(line)
            sanitized_content.append(_sanitize_mcp_content_item(item))
            continue

        if ctype == "resource":
            resource = item.get("resource")
            resource_line = _format_mcp_resource_reference(item)
            if isinstance(resource, dict):
                resource_line = _format_mcp_resource_reference(resource) or resource_line
                resource_text = str(resource.get("text") or "").strip()
                if resource_text:
                    text_lines.append(resource_text)
                elif resource_line:
                    text_lines.append(resource_line)
            elif resource_line:
                text_lines.append(resource_line)
            sanitized_content.append(_sanitize_mcp_content_item(item))
            continue

        fallback_line = _format_mcp_resource_reference(item)
        if fallback_line:
            text_lines.append(fallback_line)
        else:
            text_lines.append(f"[MCP content:{ctype or 'unknown'}]")
        sanitized_content.append(_sanitize_mcp_content_item(item))

    message = str(result_data.get("message") or "").strip()
    if message and not _is_default_mcp_message(message):
        if message not in text_lines:
            text_lines.insert(0, message)

    if media_lines:
        text_lines.extend(media_lines)

    if not text_lines:
        structured = result_data.get("structured_content")
        if structured is None:
            structured = result_data.get("structuredContent")
        if structured is not None:
            try:
                text_lines.append(json.dumps(structured, ensure_ascii=False))
            except Exception:
                text_lines.append(str(structured))

    if not text_lines and message and not _is_default_mcp_message(message):
        text_lines.append(message)

    sanitized_payload: Dict[str, Any] = dict(result_data)
    if isinstance(result_data.get("content"), list):
        sanitized_payload["content"] = sanitized_content
    raw_result = result_data.get("raw_result")
    if isinstance(raw_result, dict):
        raw_sanitized = dict(raw_result)
        raw_content = raw_result.get("content")
        if isinstance(raw_content, list):
            raw_sanitized["content"] = [
                _sanitize_mcp_content_item(item) if isinstance(item, dict) else item
                for item in raw_content
            ]
        sanitized_payload["raw_result"] = raw_sanitized

    return {
        "text": "\n".join(line for line in text_lines if line),
        "media_items": media_items,
        "sanitized_payload": sanitized_payload,
    }
