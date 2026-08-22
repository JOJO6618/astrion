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


class PathMixin:
    """ConversationManager path mixin 能力 mixin。"""

    def _load_host_workspaces(self) -> List[Dict[str, str]]:
        """读取宿主机工作区配置，用于把对话归入 workspace 子目录。"""
        try:
            path = Path(HOST_WORKSPACES_FILE).expanduser().resolve()
            if not path.exists():
                return []
            raw = json.loads(path.read_text(encoding="utf-8"))
            workspaces = raw.get("workspaces") if isinstance(raw, dict) else None
            if not isinstance(workspaces, list):
                return []
            result: List[Dict[str, str]] = []
            for item in workspaces:
                if not isinstance(item, dict):
                    continue
                workspace_id = str(item.get("workspace_id") or "").strip()
                workspace_path = self._normalize_path(item.get("path"))
                if workspace_id and workspace_path:
                    result.append({"workspace_id": workspace_id, "path": str(workspace_path)})
            return result
        except Exception as exc:
            print(f"⚠️ 读取宿主机工作区配置失败: {exc}")
            return []

    @staticmethod
    def _path_is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _resolve_workspace_id(self, project_path: Optional[Path]) -> Optional[str]:
        """按 host_workspaces.json 用最长路径前缀匹配 workspace_id。"""
        if not project_path:
            return None
        matches = []
        for item in self.host_workspaces:
            workspace_path = self._normalize_path(item.get("path"))
            if not workspace_path:
                continue
            if project_path == workspace_path or self._path_is_relative_to(project_path, workspace_path):
                matches.append((len(str(workspace_path)), item.get("workspace_id")))
        if not matches:
            # 未匹配任何工作区时兜底使用 "default"（web 模式 / 空配置均适用）
            return "default"
        matches.sort(reverse=True)
        return str(matches[0][1])

    def _conversation_dir_for_workspace(self, workspace_id: Optional[str]) -> Path:
        return self.conversations_root / workspace_id if workspace_id else self.conversations_root
