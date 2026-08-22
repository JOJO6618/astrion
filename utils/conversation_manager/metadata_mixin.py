# utils/conversation_manager.py - 对话持久化管理器（集成Token统计）

import json
import os
import time
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
try:
    from config import DATA_DIR, HOST_WORKSPACES_FILE
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import DATA_DIR, HOST_WORKSPACES_FILE

@dataclass
class ConversationMetadata:
    """对话元数据"""
    id: str
    title: str
    created_at: str
    updated_at: str
    project_path: Optional[str]
    project_relative_path: Optional[str]
    thinking_mode: bool
    total_messages: int
    total_tools: int
    run_mode: str = "fast"
    model_key: Optional[str] = None
    has_images: bool = False
    has_videos: bool = False
    status: str = "active"  # active, archived, error


class MetadataMixin:
    """ConversationManager metadata mixin 能力 mixin。"""

    def _generate_conversation_id(self) -> str:
        """生成唯一的对话ID"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 添加毫秒确保唯一性
        ms = int(time.time() * 1000) % 1000
        return f"conv_{timestamp}_{ms:03d}"

    def _get_conversation_file_path(self, conversation_id: str) -> Path:
        """获取对话文件路径"""
        return self.conversations_dir / f"{conversation_id}.json"

    def _extract_title_from_messages(self, messages: List[Dict]) -> str:
        """从消息中提取标题"""
        # 找到第一个用户消息作为标题
        for msg in messages:
            if msg.get("role") == "user":
                metadata = msg.get("metadata") or {}
                source = str(metadata.get("source") or metadata.get("message_source") or "").strip().lower()
                if source == "skill" or metadata.get("hidden") is True:
                    continue
                content = msg.get("content", "").strip()
                if content:
                    # 取前50个字符作为标题
                    title = content[:50]
                    if len(content) > 50:
                        title += "..."
                    return title
        return "新对话"

    def _extract_text_content(self, content: Any) -> str:
        """从字符串/多模态列表/字典中提取纯文本。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    elif isinstance(item.get("text"), str):
                        parts.append(item.get("text") or "")
            return "".join(parts)
        if isinstance(content, dict):
            return str(content.get("text") or "")
        return ""

    def _extract_first_user_message(self, messages: List[Dict], max_chars: int = 100) -> str:
        """提取首条用户消息纯文本，用于最近对话/搜索展示。"""
        for msg in messages or []:
            if msg.get("role") != "user":
                continue
            text = " ".join(self._extract_text_content(msg.get("content")).split())
            if not text:
                continue
            if len(text) > max_chars:
                return text[:max_chars] + "..."
            return text
        return ""

    def _count_tools_in_messages(self, messages: List[Dict]) -> int:
        """统计消息中的工具调用数量（仅统计 assistant.tool_calls）。"""
        tool_count = 0
        for msg in messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                tool_calls = msg.get("tool_calls", [])
                tool_count += len(tool_calls) if isinstance(tool_calls, list) else 0
        return tool_count

    def _prepare_project_path_metadata(self, project_path: Optional[str]) -> Dict[str, Optional[str]]:
        """
        将项目路径规范化为绝对/相对形式，便于在不同机器间迁移
        """
        normalized = {
            "project_path": None,
            "project_relative_path": None
        }

        if not project_path:
            return normalized

        try:
            absolute_path = Path(project_path).expanduser().resolve()
            normalized["project_path"] = str(absolute_path)

            try:
                relative_path = absolute_path.relative_to(self.workspace_root)
                normalized["project_relative_path"] = relative_path.as_posix()
            except ValueError:
                normalized["project_relative_path"] = None
        except Exception:
            # 回退为原始字符串，至少不会阻止对话保存
            normalized["project_path"] = str(project_path)
            normalized["project_relative_path"] = None

        return normalized

    def _initialize_token_statistics(self) -> Dict:
        """初始化Token统计结构"""
        now = datetime.now().isoformat()
        return {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "current_context_tokens": 0,
            "updated_at": now
        }

    def _validate_token_statistics(self, data: Dict) -> Dict:
        """验证并修复Token统计数据"""
        token_stats = data.get("token_statistics", {})
        
        # 确保必要字段存在
        defaults = self._initialize_token_statistics()
        for key, default_value in defaults.items():
            if key not in token_stats:
                token_stats[key] = default_value
        
        # 确保数值类型正确
        try:
            token_stats["total_input_tokens"] = int(token_stats.get("total_input_tokens", 0))
            token_stats["total_output_tokens"] = int(token_stats.get("total_output_tokens", 0))
            token_stats["total_tokens"] = int(token_stats.get("total_tokens", 0))
            token_stats["current_context_tokens"] = int(token_stats.get("current_context_tokens", 0))
        except (ValueError, TypeError):
            print("⚠️ Token统计数据损坏，重置为0")
            token_stats = defaults
        
        data["token_statistics"] = token_stats
        return data
