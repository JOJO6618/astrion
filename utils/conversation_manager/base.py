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


class ConversationManagerBase:
    """ConversationManager 基础类，包含初始化与目录管理。"""

    def __init__(self, base_dir: Optional[str] = None, project_path: Optional[str] = None):
        self.base_dir = Path(base_dir).expanduser().resolve() if base_dir else Path(DATA_DIR).resolve()
        self.conversations_root = self.base_dir / "conversations"
        self._io_lock = threading.RLock()
        self.current_conversation_id: Optional[str] = None
        self.workspace_root = Path(__file__).resolve().parents[1]
        self.host_workspaces = self._load_host_workspaces()
        self.current_project_path = self._normalize_path(project_path) if project_path else None
        self.current_workspace_id = self._resolve_workspace_id(self.current_project_path)
        # 当前管理器只暴露“当前工作区”的对话目录；未匹配到工作区时保留根目录作为兜底写入位置。
        self.conversations_dir = self._conversation_dir_for_workspace(self.current_workspace_id)
        self.index_file = self.conversations_dir / "index.json"
        self._ensure_directories()
        self._index_verified = False
        # 首次加载索引仅重建最近 20 条，降低启动开销；后续按需扩展
        self._load_index(ensure_integrity=True, max_rebuild=20)

    def _normalize_path(self, value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        try:
            return Path(str(value)).expanduser().resolve()
        except Exception:
            return None

    def _ensure_directories(self):
        """确保必要的目录存在，并迁移可归类的旧版根目录对话。"""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_conversation_files()
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

        # 如果当前工作区索引文件不存在，创建空索引
        if not self.index_file.exists():
            self._save_index({})
