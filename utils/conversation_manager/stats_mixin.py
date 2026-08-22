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


class StatsMixin:
    """ConversationManager stats mixin 能力 mixin。"""

    def get_statistics(self) -> Dict:
        """
        获取对话统计信息
        
        Returns:
            Dict: 统计信息
        """
        try:
            index = self._load_index()
            # 统计必须基于全量对话，避免启动阶段“仅预热最近20条索引”导致总数偏小。
            # 这里按需触发一次全量重建；后续统计可复用已保存的完整索引。
            try:
                total_files = len(self._iter_conversation_files(sort_by_mtime=False))
            except Exception:
                total_files = 0
            if total_files and len(index) < total_files:
                rebuilt_full = self._rebuild_index_from_files(max_count=None)
                if rebuilt_full:
                    self._save_index(rebuilt_full)
                    index = rebuilt_full
            
            total_conversations = len(index)
            total_messages = sum(meta.get("total_messages", 0) for meta in index.values())
            total_tools = sum(meta.get("total_tools", 0) for meta in index.values())
            
            # 按状态分类
            status_count = {}
            for metadata in index.values():
                status = metadata.get("status", "active")
                status_count[status] = status_count.get(status, 0) + 1
            
            # 按思考模式分类
            thinking_mode_count = {
                "thinking": sum(1 for meta in index.values() if meta.get("thinking_mode")),
                "fast": sum(1 for meta in index.values() if not meta.get("thinking_mode"))
            }
            
            # 新增：Token统计汇总
            total_input_tokens = 0
            total_output_tokens = 0
            token_stats_count = 0
            total_user_messages = 0

            for conv_id in index.keys():
                conversation_data = self.load_conversation(conv_id)
                if not conversation_data:
                    continue
                validated = self._validate_token_statistics(conversation_data)
                token_stats = validated.get("token_statistics", {}) or {}
                total_input_tokens += token_stats.get("total_input_tokens", 0)
                total_output_tokens += token_stats.get("total_output_tokens", 0)
                token_stats_count += 1
                messages = conversation_data.get("messages") or []
                total_user_messages += sum(1 for msg in messages if msg.get("role") == "user")
            
            return {
                "total_conversations": total_conversations,
                "total_messages": total_messages,
                "total_user_messages": total_user_messages,
                "total_tools": total_tools,
                "status_distribution": status_count,
                "thinking_mode_distribution": thinking_mode_count,
                "token_statistics": {
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens,
                    "conversations_with_stats": token_stats_count
                }
            }
        except Exception as e:
            print(f"⌘ 获取统计信息失败: {e}")
            return {}

    def get_current_conversation_id(self) -> Optional[str]:
        """获取当前对话ID"""
        return self.current_conversation_id

    def set_current_conversation_id(self, conversation_id: str):
        """设置当前对话ID"""
        self.current_conversation_id = conversation_id
