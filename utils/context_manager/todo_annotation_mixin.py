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


class TodoAnnotationMixin:
    """ContextManager todo annotation mixin 能力 mixin。"""

    def load_annotations(self):
        """加载文件备注"""
        annotations_file = self.data_dir / "file_annotations.json"
        if annotations_file.exists():
            try:
                with open(annotations_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content.strip():
                        self.file_annotations = json.loads(content)
                    else:
                        self.file_annotations = {}
            except (json.JSONDecodeError, KeyError):
                print(f"⚠️ [警告] 文件备注格式错误，将重新初始化")
                self.file_annotations = {}
                self.save_annotations()

    def save_annotations(self):
        """保存文件备注"""
        annotations_file = self.data_dir / "file_annotations.json"
        with open(annotations_file, 'w', encoding='utf-8') as f:
            json.dump(self.file_annotations, f, ensure_ascii=False, indent=2)

    def update_annotation(self, file_path: str, annotation: str):
        """更新文件备注"""
        self.file_annotations[file_path] = annotation
        self.save_annotations()

    def get_todo_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.todo_list:
            return None
        snapshot = deepcopy(self.todo_list)
        snapshot["all_done"] = all(
            task.get("status") == "done" for task in snapshot.get("tasks", [])
        )
        snapshot["instruction"] = self._build_todo_instruction(snapshot)
        return snapshot

    def _build_todo_instruction(self, todo: Dict[str, Any]) -> str:
        status = todo.get("status", "active")
        all_done = all(task.get("status") == "done" for task in todo.get("tasks", []))
        if status == "closed":
            return "任务已结束，请在总结中说明未完成的事项。"
        if status == "completed" or all_done:
            return "所有任务已完成，可以结束任务并向用户汇报"
        return "请在确认完成某项任务后再勾选，然后继续下一步"

    def set_todo_list(self, todo_data: Optional[Dict[str, Any]]):
        if todo_data is not None:
            self.todo_list = deepcopy(todo_data)
        else:
            self.todo_list = None
        self.auto_save_conversation(force=True)
        self.broadcast_todo_update()

    def broadcast_todo_update(self):
        if not self._web_terminal_callback:
            return
        try:
            self._web_terminal_callback('todo_updated', {
                "conversation_id": self.current_conversation_id,
                "todo_list": self.get_todo_snapshot()
            })
        except Exception as e:
            print(f"[Debug] 广播todo更新失败: {e}")

    def render_todo_system_message(self) -> Optional[str]:
        snapshot = self.get_todo_snapshot()
        if not snapshot:
            return None

        lines = ["=== TODO_LIST ===", f"任务概述：{snapshot.get('overview', '')}"]
        for task in snapshot.get("tasks", []):
            status_icon = "✅已完成" if task.get("status") == "done" else "❌未完成"
            lines.append(f"task{task.get('index')}：{task.get('title')} [{status_icon}]")
        lines.append(snapshot.get("instruction", "请在确认完成某项任务后再勾选，然后继续下一步"))
        return "\n".join(lines)
