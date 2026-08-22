"""通用工具：文本处理、日志写入等（由原 web_server.py 拆分）。"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from config import LOGS_DIR
from utils.log_rotation import append_line as _append_rotating

# 文件名安全处理

def _sanitize_filename_component(text: str) -> str:
    safe = (text or "untitled").strip()
    safe = __import__('re').sub(r'[\\/:*?"<>|]+', '_', safe)
    return safe or "untitled"


def sanitize_filename_preserve_unicode(filename: str) -> str:
    """在保留中文等字符的同时，移除危险字符和路径成分（原 server/files.py，随 /file-manager 下线迁入）。"""
    import re
    if not filename:
        return ""
    cleaned = filename.strip().replace("\x00", "")
    if not cleaned:
        return ""
    cleaned = cleaned.replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r'[<>:"\\|?*\n\r\t]', "_", cleaned)
    cleaned = cleaned.strip(". ")
    if not cleaned:
        return ""
    return cleaned[:255]


def build_review_lines(messages, limit=None):
    """
    将对话消息序列拍平成简化文本。
    保留 user / assistant / system 以及 assistant 内的 tool 调用与 tool 消息。
    limit 为正整数时，最多返回该数量的行（用于预览）。
    """
    lines: List[str] = []

    def append_line(text: str):
        lines.append(text.rstrip())

    def extract_text(content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text") or "")
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        if isinstance(content, dict):
            return content.get("text") or ""
        return ""

    def append_tool_call(name, args):
        try:
            args_text = json.dumps(args, ensure_ascii=False)
        except Exception:
            args_text = str(args)
        append_line(f"tool_call：{name} {args_text}")

    for msg in messages or []:
        role = msg.get("role")
        base_content_raw = msg.get("content") if isinstance(msg.get("content"), (str, list, dict)) else msg.get("text") or ""
        base_content = extract_text(base_content_raw)

        if role in ("user", "assistant", "system"):
            append_line(f"{role}：{base_content}")

        if role == "tool":
            append_line(f"tool：{extract_text(base_content_raw)}")

        if role == "assistant":
            actions = msg.get("actions") or []
            for action in actions:
                if action.get("type") != "tool":
                    continue
                tool = action.get("tool") or {}
                name = tool.get("name") or "tool"
                args = tool.get("arguments")
                if args is None:
                    args = tool.get("argumentSnapshot")
                try:
                    args_text = json.dumps(args, ensure_ascii=False)
                except Exception:
                    args_text = str(args)
                append_line(f"tool_call：{name} {args_text}")

                tool_content = tool.get("content")
                if tool_content is None:
                    if isinstance(tool.get("result"), str):
                        tool_content = tool.get("result")
                    elif tool.get("result") is not None:
                        try:
                            tool_content = json.dumps(tool.get("result"), ensure_ascii=False)
                        except Exception:
                            tool_content = str(tool.get("result"))
                    elif tool.get("message"):
                        tool_content = tool.get("message")
                    else:
                        tool_content = ""
                append_line(f"tool：{tool_content}")

                if isinstance(limit, int) and limit > 0 and len(lines) >= limit:
                    return lines[:limit]

            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or "tool"
                args_raw = fn.get("arguments")
                try:
                    args_obj = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except Exception:
                    args_obj = args_raw
                append_tool_call(name, args_obj)
                if isinstance(limit, int) and limit > 0 and len(lines) >= limit:
                    return lines[:limit]

            if isinstance(base_content_raw, list):
                for item in base_content_raw:
                    if isinstance(item, dict) and item.get("type") == "tool_call":
                        fn = item.get("function") or {}
                        name = fn.get("name") or "tool"
                        args_raw = fn.get("arguments")
                        try:
                            args_obj = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        except Exception:
                            args_obj = args_raw
                        append_tool_call(name, args_obj)
                        if isinstance(limit, int) and limit > 0 and len(lines) >= limit:
                            return lines[:limit]

        if isinstance(limit, int) and limit > 0 and len(lines) >= limit:
            return lines[:limit]

    return lines if limit is None else lines[:limit]


# 日志输出
_ORIGINAL_PRINT = print
ENABLE_VERBOSE_CONSOLE = True


def brief_log(message: str):
    """始终输出的简要日志（模型输出/工具调用等关键事件）"""
    try:
        _ORIGINAL_PRINT(message)
    except Exception:
        pass


DEBUG_LOG_FILE = Path(LOGS_DIR).expanduser().resolve() / "debug_stream.log"
CHUNK_BACKEND_LOG_FILE = Path(LOGS_DIR).expanduser().resolve() / "chunk_backend.log"
CHUNK_FRONTEND_LOG_FILE = Path(LOGS_DIR).expanduser().resolve() / "chunk_frontend.log"
STREAMING_DEBUG_LOG_FILE = Path(LOGS_DIR).expanduser().resolve() / "streaming_debug.log"
CONN_DIAG_LOG_FILE = Path(LOGS_DIR).expanduser().resolve() / "conn_diag.log"


def _write_log(file_path: Path, message: str) -> None:
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    _append_rotating(file_path, f"[{timestamp}] {message}")


def debug_log(message):
    """写入调试日志"""
    _write_log(DEBUG_LOG_FILE, message)


def log_conn_diag(message: str):
    """连接诊断日志：统一关键词并直接落盘到独立文件。"""
    _write_log(CONN_DIAG_LOG_FILE, f"[CONN_DIAG] {message}")


def log_backend_chunk(conversation_id: str, iteration: int, chunk_index: int, elapsed: float, char_len: int, content_preview: str):
    preview = content_preview.replace('\n', '\\n')
    _write_log(
        CHUNK_BACKEND_LOG_FILE,
        f"conv={conversation_id or 'unknown'} iter={iteration} chunk={chunk_index} elapsed={elapsed:.3f}s len={char_len} preview={preview}"
    )


def log_frontend_chunk(conversation_id: str, chunk_index: int, elapsed: float, char_len: int, client_ts: float):
    _write_log(
        CHUNK_FRONTEND_LOG_FILE,
        f"conv={conversation_id or 'unknown'} chunk={chunk_index} elapsed={elapsed:.3f}s len={char_len} client_ts={client_ts}"
    )


def log_streaming_debug_entry(data: Dict[str, Any]):
    try:
        serialized = json.dumps(data, ensure_ascii=False)
    except Exception:
        serialized = str(data)
    _write_log(STREAMING_DEBUG_LOG_FILE, serialized)

__all__ = [
    "_sanitize_filename_component",
    "sanitize_filename_preserve_unicode",
    "build_review_lines",
    "brief_log",
    "debug_log",
    "log_backend_chunk",
    "log_frontend_chunk",
    "log_streaming_debug_entry",
    "DEBUG_LOG_FILE",
    "CHUNK_BACKEND_LOG_FILE",
    "CHUNK_FRONTEND_LOG_FILE",
    "STREAMING_DEBUG_LOG_FILE",
    "CONN_DIAG_LOG_FILE",
    "log_conn_diag",
]
