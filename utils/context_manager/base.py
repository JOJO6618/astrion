# utils/context_manager.py - 上下文管理器（集成对话持久化和Token统计）

import os
import json
import base64
import mimetypes
import io
import uuid
import platform
import shutil
import subprocess
from copy import deepcopy
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime
try:
    from config import (
        MAX_CONTEXT_SIZE,
        DATA_DIR,
        PROMPTS_DIR,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
    from config.model_profiles import (
        get_model_prompt_replacements,
        get_registered_model_keys,
        model_supports_image,
        model_supports_video,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        MAX_CONTEXT_SIZE,
        DATA_DIR,
        PROMPTS_DIR,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
    from config.model_profiles import (
        get_model_prompt_replacements,
        get_registered_model_keys,
        model_supports_image,
        model_supports_video,
    )
from utils.conversation_manager import ConversationManager
from utils.host_workspace_debug import write_host_workspace_debug
from utils.media_store import MediaStore
from utils.token_usage import normalize_usage_payload

try:
    from modules.multi_agent.role_store import DEFAULT_MUTIAGENTS_DIR
except Exception:
    DEFAULT_MUTIAGENTS_DIR = None

AUTO_SHALLOW_PLACEHOLDER = "过早的工具结果已经被自动压缩"
AUTO_SHALLOW_TOOL_WHITELIST = {
    "write_file",
    "read_file",
    "edit_file",
    "terminal_input",
    "terminal_snapshot",
    "web_search",
    "extract_webpage",
    "run_command",
    "view_image",
    "view_video",
}


class ContextManagerBase:
    """ContextManager 基础类，包含初始化与跨模块公共工具方法。"""

    def __init__(self, project_path: str, data_dir: Optional[str] = None):
        self.project_path = Path(project_path).resolve()
        self.initial_project_path = self.project_path
        self.container_mount_path = TERMINAL_SANDBOX_MOUNT_PATH or "/workspace"
        self.container_cpu_limit = TERMINAL_SANDBOX_CPUS or "未限制"
        self.container_memory_limit = TERMINAL_SANDBOX_MEMORY or "未限制"
        self.project_storage_limit = f"{PROJECT_MAX_STORAGE_MB}MB" if PROJECT_MAX_STORAGE_MB else "未限制"
        self.workspace_root = Path(__file__).resolve().parents[1]
        self.data_dir = Path(data_dir).expanduser().resolve() if data_dir else Path(DATA_DIR).resolve()
        self.temp_files = {}  # 临时加载的文件内容
        self.file_annotations = {}  # 文件备注
        self.conversation_history = []  # 当前对话历史（内存中）
        self.todo_list: Optional[Dict[str, Any]] = None
        self.has_images: bool = False
        self.has_videos: bool = False
        self.image_compression_mode: str = "original"
        # 对话元数据与项目快照缓存
        self.conversation_metadata: Dict[str, Any] = {}
        self.project_snapshot: Optional[Dict[str, Any]] = None
        self._host_runtime_cache: Optional[Dict[str, str]] = None
        self._shallow_compact_round: int = 0
        
        # 新增：对话持久化管理器
        self.conversation_manager = ConversationManager(base_dir=self.data_dir, project_path=str(self.project_path))
        # 多智能体对话独立存储到 mutiagents/conversations
        ma_base_dir = DEFAULT_MUTIAGENTS_DIR if DEFAULT_MUTIAGENTS_DIR else (self.data_dir.parent / "mutiagents")
        self.multi_agent_conversation_manager = ConversationManager(
            base_dir=str(ma_base_dir),
            project_path=str(self.project_path),
        )
        self.media_store = MediaStore(self.data_dir)
        self.current_conversation_id: Optional[str] = None
        self.auto_save_enabled = True
        self.main_terminal = None  # 由宿主终端在初始化后回填，用于工具定义访问
        
        # 用于接收Web终端的回调函数
        self._web_terminal_callback = None

        self.load_annotations()

    def _is_host_mode_without_safety(self) -> bool:
        """是否处于宿主机模式且未启用安全保护。"""
        return (TERMINAL_SANDBOX_MODE or "").lower() == "host" and not LINUX_SAFETY

    def set_web_terminal_callback(self, callback):
        """设置Web终端回调函数，用于广播事件"""
        self._web_terminal_callback = callback

    def _get_meta_flag(self, key: str, default: Any = None) -> Any:
        return (self.conversation_metadata or {}).get(key, default)

    def _set_meta_flag(self, key: str, value: Any, save: bool = True):
        self.conversation_metadata[key] = value
        if save and self.current_conversation_id:
            try:
                # 路由到正确的 conversation_manager
                target_manager = getattr(self, "_get_conversation_manager_for_id", lambda _: self.conversation_manager)(self.current_conversation_id)
                target_manager.update_conversation_metadata(
                    self.current_conversation_id,
                    {key: value}
                )
            except Exception as exc:
                print(f"[ContextCompression] 保存 metadata 失败 {key}: {exc}")

    def _generate_message_id(self) -> str:
        """生成消息唯一 ID（用于 media_store 映射）。"""
        return f"msg_{uuid.uuid4().hex}"

    def _resolve_project_path_from_metadata(self, metadata: Dict[str, Any]) -> Path:
        """
        根据对话元数据解析项目路径，优先使用相对路径以提升可移植性
        """
        candidates = []

        relative_path = metadata.get("project_relative_path")
        if isinstance(relative_path, str) and relative_path.strip():
            rel_path_obj = Path(relative_path.strip())
            if rel_path_obj.is_absolute():
                candidates.append(rel_path_obj)
            else:
                candidates.append((self.workspace_root / rel_path_obj).resolve())

        stored_path = metadata.get("project_path")
        if isinstance(stored_path, str) and stored_path.strip():
            try:
                candidates.append(Path(stored_path.strip()).expanduser())
            except Exception:
                pass

        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                continue

        # 最终回退到启动时指定的路径
        return self.initial_project_path
