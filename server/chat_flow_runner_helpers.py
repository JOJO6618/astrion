from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def extract_intent_from_partial(arg_str: str) -> Optional[str]:
    """从不完整的JSON字符串中粗略提取 intent 字段，容错用于流式阶段。"""
    if not arg_str or "intent" not in arg_str:
        return None
    match = re.search(r'"intent"\s*:\s*"([^"]{0,128})', arg_str, re.IGNORECASE | re.DOTALL)
    if match:
        raw = match.group(1)
        try:
            # 某些模型会对非 ASCII 字符输出 JSON Unicode 转义（如 \u4e2d\u6587），
            # 这里用 json.loads 安全解码，失败时回退原始值。
            decoded = json.loads('"{}"'.format(raw))
            if isinstance(decoded, str):
                return decoded
        except Exception:
            pass
        return raw
    return None


def resolve_monitor_path(args: Dict[str, Any], fallback: Optional[str] = None) -> Optional[str]:
    candidates = [
        args.get('path'),
        args.get('target_path'),
        args.get('file_path'),
        args.get('destination_path'),
        fallback,
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            trimmed = candidate.strip()
            if trimmed:
                return trimmed
    return None


def resolve_monitor_memory(entries: Any, entry_limit: int) -> Optional[List[str]]:
    if isinstance(entries, list):
        return [str(item) for item in entries][:entry_limit]
    return None


def capture_monitor_snapshot(file_manager, path: Optional[str], snapshot_char_limit: int, debug_logger) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        read_result = file_manager.read_file(path)
    except Exception as exc:
        debug_logger(f"[MonitorSnapshot] 读取文件失败: {path} ({exc})")
        return None
    if not isinstance(read_result, dict) or not read_result.get('success'):
        return None
    content = read_result.get('content')
    if not isinstance(content, str):
        content = ''
    if len(content) > snapshot_char_limit:
        content = content[:snapshot_char_limit]
    return {
        'path': read_result.get('path') or path,
        'content': content,
    }
