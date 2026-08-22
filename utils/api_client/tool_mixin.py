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

class APIClientToolMixin:
    def _validate_json_string(self, json_str: str) -> tuple:
        """
        验证JSON字符串的完整性
        
        Returns:
            (is_valid: bool, error_message: str, parsed_data: dict or None)
        """
        if not json_str or not json_str.strip():
            return True, "", {}
        
        # 检查基本的JSON结构标记
        stripped = json_str.strip()
        if not stripped.startswith('{') or not stripped.endswith('}'):
            return False, "JSON字符串格式不完整（缺少开始或结束大括号）", None
        
        # 检查引号配对
        in_string = False
        escape_next = False
        quote_count = 0
        
        for char in stripped:
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                continue
                
            if char == '"':
                quote_count += 1
                in_string = not in_string
        
        if in_string:
            return False, "JSON字符串中存在未闭合的引号", None
        
        # 尝试解析JSON
        try:
            parsed_data = json.loads(stripped)
            return True, "", parsed_data
        except json.JSONDecodeError as e:
            return False, f"JSON解析错误: {str(e)}", None

    def _safe_tool_arguments_parse(self, arguments_str: str, tool_name: str) -> tuple:
        """
        安全地解析工具参数，保持失败即时返回
        
        Returns:
            (success: bool, arguments: dict, error_message: str)
        """
        if not arguments_str or not arguments_str.strip():
            return True, {}, ""
        
        # 长度检查
        max_length = 999999999  # 50KB限制
        if len(arguments_str) > max_length:
            return False, {}, f"参数过长({len(arguments_str)}字符)，超过{max_length}字符限制"
        
        # 尝试直接解析JSON
        try:
            parsed_data = json.loads(arguments_str)
            return True, parsed_data, ""
        except json.JSONDecodeError as e:
            preview_length = 200
            stripped = arguments_str.strip()
            preview = stripped[:preview_length] + "..." if len(stripped) > preview_length else stripped
            return False, {}, f"JSON解析失败: {str(e)}\n参数预览: {preview}"

    def _extract_reasoning_delta(self, delta: Dict[str, Any]) -> str:
        """统一提取思考内容，兼容 reasoning_content / reasoning_details。"""
        if not isinstance(delta, dict):
            return ""
        if "reasoning_content" in delta:
            return delta.get("reasoning_content") or ""
        details = delta.get("reasoning_details")
        if isinstance(details, list):
            parts: List[str] = []
            for item in details:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(text)
            if parts:
                return "".join(parts)
        return ""
