# ========== api_client.py ==========
# utils/api_client.py - OpenAI-compatible API 客户端（支持Web模式）

import httpx
import json
import asyncio
import base64
import mimetypes
import os
from typing import List, Dict, Optional, AsyncGenerator, Any
from pathlib import Path
from datetime import datetime
from pathlib import Path
from typing import Tuple
try:
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )

from utils.log_rotation import append_line, prune_dir



from utils.api_client.utils import _api_dump_enabled

class APIClientFormattingMixin:
    def _format_read_file_result(self, data: Dict) -> str:
        """根据读取模式格式化 read_file 工具结果。"""
        if not isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False)
        if not data.get("success"):
            return json.dumps(data, ensure_ascii=False)
        
        read_type = data.get("type", "read")
        truncated_note = "（内容已截断）" if data.get("truncated") else ""
        path = data.get("path", "未知路径")
        max_chars = data.get("max_chars")
        max_note = f"(max_chars={max_chars})" if max_chars else ""
        
        if read_type == "read":
            line_start = data.get("line_start")
            line_end = data.get("line_end")
            char_count = data.get("char_count", len(data.get("content", "") or ""))
            header = f"读取 {path} 行 {line_start}~{line_end}，返回 {char_count} 字符 {max_note}{truncated_note}".strip()
            content = data.get("content", "")
            return f"{header}\n```\n{content}\n```"
        
        if read_type == "search":
            query = data.get("query", "")
            actual = data.get("actual_matches", 0)
            returned = data.get("returned_matches", 0)
            case_hint = "区分大小写" if data.get("case_sensitive") else "不区分大小写"
            header = (
                f"在 {path} 中搜索 \"{query}\"，返回 {returned}/{actual} 条结果（{case_hint}）"
                f" {max_note}{truncated_note}"
            ).strip()
            match_texts = []
            for idx, match in enumerate(data.get("matches", []), 1):
                match_note = "（片段截断）" if match.get("truncated") else ""
                hits = match.get("hits") or []
                hit_text = ", ".join(str(h) for h in hits) if hits else "无"
                label = match.get("id") or f"match_{idx}"
                snippet = match.get("snippet", "")
                match_texts.append(
                    f"[{label}] 行 {match.get('line_start')}~{match.get('line_end')} 命中行: {hit_text}{match_note}\n```\n{snippet}\n```"
                )
            if not match_texts:
                match_texts.append("未找到匹配内容。")
            return "\n".join([header] + match_texts)
