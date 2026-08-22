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


MAX_RECENT_UPLOADS = 10


class MessageMixin:
    """ContextManager message mixin 能力 mixin。"""

    def _build_recent_uploads_block(self, max_items: int = MAX_RECENT_UPLOADS) -> str:
        """扫描工作区 .astrion/user_upload 目录，按修改时间倒序生成最近上传文件列表。"""
        try:
            project_path = Path(getattr(self, "project_path", ".") or ".").expanduser().resolve()
            uploads_dir = project_path / ".astrion" / "user_upload"
            if not uploads_dir.is_dir():
                return ""
            files = []
            for item in uploads_dir.iterdir():
                if not item.is_file():
                    continue
                try:
                    stat = item.stat()
                    files.append((item, stat.st_mtime, stat.st_size))
                except Exception:
                    continue
            if not files:
                return ""
            files.sort(key=lambda x: x[1], reverse=True)
            lines = ["- **最近上传的文件**："]
            for item, mtime, size in files[:max_items]:
                try:
                    rel_path = item.relative_to(project_path)
                    rel_path_str = str(rel_path).replace("\\", "/")
                except Exception:
                    rel_path_str = str(item.name)
                try:
                    dt = datetime.fromtimestamp(mtime)
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    time_str = ""
                size_str = ""
                if size >= 1024 * 1024:
                    size_str = f"{size / 1024 / 1024:.1f} MB"
                elif size >= 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size} B"
                meta = f"{size_str}, {time_str}".strip(", ")
                lines.append(f"  - `{rel_path_str}` ({meta})")
            return "\n\n" + "\n".join(lines)
        except Exception:
            return ""

    def add_conversation(
        self,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict]] = None,
        tool_call_id: Optional[str] = None,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        reasoning_content: Optional[str] = None,
        images: Optional[List[Any]] = None,
        videos: Optional[List[Any]] = None,
        media_refs: Optional[List[Dict[str, Any]]] = None,
    ):
        """添加对话记录（改进版：集成自动保存 + 智能token统计）"""
        timestamp = datetime.now().isoformat()
        message_id = self._generate_message_id()
        if role == "assistant":
            message = {
                "role": role,
                "reasoning_content": reasoning_content if reasoning_content is not None else "",
                "content": content or "",
                "timestamp": timestamp,
                "message_id": message_id,
            }
        else:
            message = {
                "role": role,
                "content": content,
                "timestamp": timestamp,
                "message_id": message_id,
            }
        
        if metadata:
            message["metadata"] = dict(metadata)
        if images:
            message["images"] = images
            self.has_images = True
        if videos:
            message["videos"] = videos
            self.has_videos = True

        prepared_candidates = self._prepare_media_candidates(images=images, videos=videos, media_refs=media_refs)
        stored_media_refs: List[Dict[str, Any]] = []
        for candidate in prepared_candidates:
            stored_ref = self._store_media_item_from_candidate(candidate)
            if stored_ref:
                stored_media_refs.append(stored_ref)
        stored_media_refs = self._dedupe_media_refs(stored_media_refs)
        if stored_media_refs:
            message["media_refs"] = stored_media_refs
            message.setdefault("metadata", {})
            message["metadata"]["media_refs"] = stored_media_refs
            if any(str(item.get("kind") or "").strip().lower() == "image" for item in stored_media_refs):
                self.has_images = True
            if any(str(item.get("kind") or "").strip().lower() == "video" for item in stored_media_refs):
                self.has_videos = True
            try:
                self.media_store.link_message_media(
                    self.current_conversation_id,
                    message_id,
                    stored_media_refs,
                )
            except Exception:
                pass
        
        # 记录当前助手回复所用模型，便于回放时查看
        if role == "assistant":
            message.setdefault("metadata", {})
            if "model_key" not in message["metadata"]:
                model_key = getattr(self.main_terminal, "model_key", None) if self.main_terminal else None
                if model_key:
                    message["metadata"]["model_key"] = model_key

        # 如果是assistant消息且有工具调用，保存完整格式
        if role == "assistant" and tool_calls:
            # 确保工具调用格式完整
            formatted_tool_calls = []
            for tc in tool_calls:
                # 如果是简化格式，补全它
                if "function" in tc and not tc.get("id"):
                    formatted_tc = {
                        "id": f"call_{datetime.now().timestamp()}_{tc['function'].get('name', 'unknown')}",
                        "type": "function",
                        "function": tc["function"]
                    }
                else:
                    formatted_tc = tc
                formatted_tool_calls.append(formatted_tc)
            message["tool_calls"] = formatted_tool_calls
        
        # 如果是tool消息，保存必要信息
        if role == "tool":
            if tool_call_id:
                message["tool_call_id"] = tool_call_id
            if name:
                message["name"] = name
        
        self.conversation_history.append(message)
        
        # 自动保存
        self.auto_save_conversation()
        
        print(f"[Debug] 添加{role}消息后广播token更新")
        self.safe_broadcast_token_update()
        return message

    def add_tool_result(self, tool_call_id: str, function_name: str, result: str):
        """添加工具调用结果（保留方法以兼容）"""
        self.add_conversation(
            role="tool",
            content=result,
            tool_call_id=tool_call_id,
            name=function_name
        )

    def build_main_context(self, memory_content: str) -> Dict:
        """构建主终端上下文"""
        snapshot = self._ensure_project_snapshot()
        stats = snapshot.get("statistics") or {}
        total_files = stats.get("total_files", 0)
        total_size_bytes = stats.get("total_size", 0)
        
        context = {
            "project_info": {
                "path": str(self.project_path),
                "file_tree": snapshot.get("file_tree", ""),
                "file_annotations": self.file_annotations,
                "statistics": {
                    "total_files": total_files,
                    "total_size": f"{total_size_bytes / 1024 / 1024:.2f}MB"
                }
            },
            "memory": memory_content,
            "conversation": self.conversation_history,
            "todo_list": self.get_todo_snapshot()
        }
        
        return context

    def build_task_context(
        self,
        task_info: Dict,
        main_memory: str,
        task_memory: str,
        execution_results: List[Dict] = None
    ) -> Dict:
        """构建子任务上下文"""
        snapshot = self._ensure_project_snapshot()
        stats = snapshot.get("statistics") or {}
        total_files = stats.get("total_files", 0)
        total_size_bytes = stats.get("total_size", 0)
        
        context = {
            "task_info": task_info,
            "project_info": {
                "path": str(self.project_path),
                "file_tree": snapshot.get("file_tree", ""),
                "file_annotations": self.file_annotations,
                "statistics": {
                    "total_files": total_files,
                    "total_size": f"{total_size_bytes / 1024 / 1024:.2f}MB"
                }
            },
            "memory": {
                "main_memory": main_memory,
                "task_memory": task_memory
            },
            "temp_files": self.temp_files,
            "execution_results": execution_results or [],
            "conversation": {
                "main": self.conversation_history[-10:],  # 最近10条主对话
                "sub": []  # 子任务对话
            }
        }
        
        return context

    def _build_workspace_system_message(self, context: Dict) -> Optional[str]:
        """构建独立的工作区系统消息，根据运行模式动态展示环境与资源信息。"""
        template = self.load_prompt("workspace_system")
        if not template:
            template = (
                "## 工作区信息\n"
                "- **项目名称**：{project_name}\n"
                "- **运行环境**：{runtime_environment}\n"
                "- **资源限制**：{resource_limit}\n"
                "- **当前时间**：{current_time}\n"
                "- **项目结构**：\n\n{file_tree}\n\n"
                "{recent_uploads}"
                "- **长期记忆**：{memory}"
            )

        is_host = self._is_host_mode_without_safety()
        runtime_environment = (
            self._build_host_runtime_environment()
            if is_host
            else f"隔离容器中（挂载目录 {self.container_mount_path or '/workspace'}），宿主机路径已隐藏"
        )
        resource_limit = (
            "宿主机模式无限制"
            if is_host
            else f"CPU {self.container_cpu_limit} 核，内存 {self.container_memory_limit}，磁盘配额 {self.project_storage_limit}"
        )

        now = datetime.now()
        current_time_text = f"{now.year}年{now.month}月{now.day}日 {now.hour}点（24小时制）"
        project_name = (
            str(getattr(self, "workspace_label", "") or "").strip()
            or str(getattr(self, "project_label", "") or "").strip()
            or self.project_path.name
        )

        recent_uploads = self._build_recent_uploads_block()

        content = template.format(
            project_name=project_name,
            runtime_environment=runtime_environment,
            resource_limit=resource_limit,
            container_path=self.container_mount_path or "/workspace",
            container_cpus=self.container_cpu_limit,
            container_memory=self.container_memory_limit,
            project_storage=self.project_storage_limit,
            current_time=current_time_text,
            file_tree=(
                "（以下为工作区根目录的部分文件和文件夹）\n" + context["project_info"]["file_tree"]
                if context["project_info"].get("file_tree")
                else ""
            ),
            recent_uploads=recent_uploads,
        )

        return content

    def check_context_size(self) -> Dict:
        """检查上下文大小"""
        sizes = {
            "temp_files": sum(len(content) for content in self.temp_files.values()),
            "conversation": sum(len(json.dumps(msg, ensure_ascii=False)) for msg in self.conversation_history),
            "total": 0
        }
        sizes["total"] = sum(sizes.values())

        return {
            "sizes": sizes,
            "is_overflow": sizes["total"] > MAX_CONTEXT_SIZE,
            "usage_percent": (sizes["total"] / MAX_CONTEXT_SIZE) * 100
        }
