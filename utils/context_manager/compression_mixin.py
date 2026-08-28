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

from modules.i18n import tr

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


class CompressionMixin:
    """ContextManager compression mixin 能力 mixin。"""

    def is_compression_in_progress(self) -> bool:
        return bool(self._get_meta_flag("compression_in_progress", False))

    def set_compression_state(
        self,
        *,
        in_progress: bool,
        mode: Optional[str] = None,
        stage: Optional[str] = None,
        error: Optional[str] = None,
        resume_payload: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None,
    ):
        updates: Dict[str, Any] = {
            "compression_in_progress": bool(in_progress),
            "compression_mode": mode if in_progress else None,
            "compression_stage": stage if in_progress else None,
            "compression_error": error if in_progress else None,
            "compression_resume_payload": resume_payload if in_progress else None,
            "compression_job_id": job_id if in_progress else None,
            # 记录发起压缩的进程 pid：进程重启后该标记必然残留（压缩随进程死亡），
            # status 读取侧据此做懒清理（见 deep_compression.heal_stale_compression_flag）。
            "compression_pid": os.getpid() if in_progress else None,
        }
        for k, v in updates.items():
            self.conversation_metadata[k] = v
        if self.current_conversation_id:
            try:
                target_manager = (
                    self._get_conversation_manager_for_id(self.current_conversation_id)
                    if hasattr(self, "_get_conversation_manager_for_id")
                    else self.conversation_manager
                )
                target_manager.update_conversation_metadata(self.current_conversation_id, updates)
            except Exception as exc:
                print(f"[ContextCompression] 更新压缩状态失败: {exc}")

    def on_tool_call_finished(
        self,
        tool_name: Optional[str] = None,
        *,
        enable_shallow: bool = True,
        shallow_trigger_tokens: int = 80_000,
        deep_trigger_tokens: int = 150_000,
        shallow_batch_size: int = 10,
        shallow_keep_recent_tools: int = 15,
        shallow_trigger_tool_calls_interval: int = 10,
        shallow_keep_user_turn_tools: int = 3,
    ) -> int:
        """每次工具调用完成后触发：更新计数并执行自动浅压缩。返回本轮浅压缩条数。"""
        if not self.current_conversation_id:
            return 0
        # 兼容历史对话：tool_call_count 可能从 0 开始，但历史里已存在大量 tool 结果。
        # 这里优先对齐到“当前历史中的 tool 消息数量”，避免长期无法触发浅压缩。
        meta_tool_count = int(self._get_meta_flag("tool_call_count", 0) or 0)
        history_tool_count = 0
        try:
            history_tool_count = sum(
                1 for msg in (self.conversation_history or [])
                if isinstance(msg, dict) and msg.get("role") == "tool"
            )
        except Exception:
            history_tool_count = 0
        tool_count = max(meta_tool_count + 1, history_tool_count)
        self._set_meta_flag("tool_call_count", tool_count, save=False)

        current_tokens = self.get_current_context_tokens(self.current_conversation_id)
        shallow_trigger_tokens = max(1, int(80_000 if shallow_trigger_tokens is None else shallow_trigger_tokens))
        deep_trigger_tokens = max(
            shallow_trigger_tokens + 1,
            int(150_000 if deep_trigger_tokens is None else deep_trigger_tokens),
        )
        shallow_batch_size = max(1, int(10 if shallow_batch_size is None else shallow_batch_size))
        shallow_keep_recent_tools = max(0, int(15 if shallow_keep_recent_tools is None else shallow_keep_recent_tools))
        shallow_keep_user_turn_tools = max(0, int(3 if shallow_keep_user_turn_tools is None else shallow_keep_user_turn_tools))
        shallow_trigger_tool_calls_interval = max(
            1,
            int(10 if shallow_trigger_tool_calls_interval is None else shallow_trigger_tool_calls_interval),
        )
        was_long_conversation = bool(self._get_meta_flag("is_long_conversation", False))
        if current_tokens > shallow_trigger_tokens and not was_long_conversation:
            self._set_meta_flag("is_long_conversation", True, save=False)
        just_marked_long = (not was_long_conversation) and bool(self._get_meta_flag("is_long_conversation", False))
        if current_tokens > deep_trigger_tokens and not self._get_meta_flag("is_ultra_long_conversation", False):
            self._set_meta_flag("is_ultra_long_conversation", True, save=False)

        # 每 10 次工具调用触发一次浅压缩（当 long 已标记）
        last_checkpoint = int(self._get_meta_flag("last_shallow_compress_tool_count", 0) or 0)
        should_try_shallow = (
            bool(enable_shallow)
            and bool(self._get_meta_flag("is_long_conversation", False))
            and (
                just_marked_long
                or (last_checkpoint <= 0 and tool_count > 0)
                or (tool_count - last_checkpoint >= shallow_trigger_tool_calls_interval)
            )
        )
        changed = False
        compressed_count = 0
        if should_try_shallow:
            compressed = self._run_auto_shallow_compression(
                batch_size=shallow_batch_size,
                keep_recent_tools=shallow_keep_recent_tools,
                keep_user_turn_tools=shallow_keep_user_turn_tools,
            )
            self._set_meta_flag("last_shallow_compress_tool_count", tool_count, save=False)
            compressed_count = max(0, int(compressed or 0))
            changed = compressed > 0

        if self.current_conversation_id:
            updates = {
                "tool_call_count": tool_count,
                "is_long_conversation": bool(self._get_meta_flag("is_long_conversation", False)),
                "is_ultra_long_conversation": bool(self._get_meta_flag("is_ultra_long_conversation", False)),
                "last_shallow_compress_tool_count": int(self._get_meta_flag("last_shallow_compress_tool_count", 0) or 0),
            }
            try:
                self.conversation_manager.update_conversation_metadata(self.current_conversation_id, updates)
            except Exception as exc:
                print(f"[ContextCompression] 写入计数失败: {exc}")
        if changed:
            self.auto_save_conversation(force=True)
        return compressed_count

    def _run_auto_shallow_compression(self, batch_size: int = 10, keep_recent_tools: int = 15, keep_user_turn_tools: int = 3) -> int:
        """浅压缩：仅打标记，不修改原文。返回本轮标记条数。
        
        Args:
            batch_size: 每轮最大压缩数量
            keep_recent_tools: 保留最近的N条工具消息不压缩
            keep_user_turn_tools: 保留最近N次用户输入后的工具消息不压缩
        """
        history = self.conversation_history or []
        if not history:
            return 0

        tool_indices = [idx for idx, msg in enumerate(history) if msg.get("role") == "tool"]
        protected_indices = set(tool_indices[-max(0, keep_recent_tools):]) if keep_recent_tools > 0 else set()
        
        # 新增机制：保留最近keep_user_turn_tools次用户输入后的工具消息
        if keep_user_turn_tools > 0:
            # 从后往前找真正的用户输入（排除系统自动发送的）
            user_turn_indices = []
            for idx in range(len(history) - 1, -1, -1):
                msg = history[idx]
                if msg.get("role") == "user":
                    # 检查是否是系统自动发送的消息（向后兼容：检查metadata或前缀文本）
                    metadata = msg.get("metadata") or {}
                    content = msg.get("content") or ""
                    is_auto = metadata.get("is_auto_generated") or \
                              content.startswith("这是一句系统自动发送的user消息，用于通知你")
                    if not is_auto:
                        user_turn_indices.append(idx)
                        if len(user_turn_indices) >= keep_user_turn_tools:
                            break
            
            # 保护这些用户输入之后的所有工具消息（直到下一个user消息）
            for user_idx in user_turn_indices:
                for idx in range(user_idx + 1, len(history)):
                    msg = history[idx]
                    if msg.get("role") == "user":
                        break  # 遇到下一个user消息，停止
                    if msg.get("role") == "tool":
                        protected_indices.add(idx)

        candidates: List[int] = []
        for idx, msg in enumerate(history):
            if msg.get("role") != "tool":
                continue
            if idx in protected_indices:
                continue
            tool_name = (msg.get("name") or "").strip()
            if tool_name not in AUTO_SHALLOW_TOOL_WHITELIST:
                continue
            metadata = msg.get("metadata") or {}
            if metadata.get("auto_shallow_compacted"):
                continue
            candidates.append(idx)
            if len(candidates) >= max(1, batch_size):
                break

        if not candidates:
            return 0

        self._shallow_compact_round += 1
        now = datetime.now().isoformat()
        for idx in candidates:
            msg = history[idx]
            metadata = msg.get("metadata") or {}
            metadata["auto_shallow_compacted"] = True
            metadata["auto_shallow_compacted_at"] = now
            metadata["auto_shallow_compact_round"] = self._shallow_compact_round
            msg["metadata"] = metadata
            history[idx] = msg
        self.conversation_history = history
        return len(candidates)

    def compress_conversation(self, conversation_id: str) -> Dict:
        """
        压缩指定对话：保留用户/助手原文（不含 reasoning），提取工具意图/名称，
        生成一条 system 消息作为新对话的压缩版历史。
        """
        conversation_data = self.conversation_manager.load_conversation(conversation_id)
        if not conversation_data:
            return {
                "success": False,
                "error": tr("ctx_mgr.conversation_not_found", conversation_id=conversation_id)
            }

        original_messages = conversation_data.get("messages", []) or []
        if not original_messages:
            return {
                "success": False,
                "error": tr("ctx_mgr.nothing_to_compress")
            }

        header_text = (
            f"系统提示：根据压缩后的工作记录继续这个任务。"
            f"如果信息不足，提示用户使用对话回顾功能。源对话：{conversation_id}"
        )

        lines: List[str] = []
        tool_buffer: List[str] = []
        seen_tool_call_ids = set()

        def add_spacing():
            if lines and lines[-1] != "":
                lines.append("")

        def flush_tools():
            if not tool_buffer:
                return
            add_spacing()
            lines.append("tool：")
            lines.extend(f"- {entry}" for entry in tool_buffer)
            tool_buffer.clear()

        for message in original_messages:
            role = message.get("role")

            if role == "user":
                flush_tools()
                content = message.get("content") or ""
                add_spacing()
                lines.append(f"user：{content}")
                continue

            if role == "assistant":
                content = message.get("content") or ""
                has_visible_content = bool(str(content).strip())
                if has_visible_content:
                    flush_tools()
                    add_spacing()
                    lines.append(f"assistant：{content}")

                tool_calls = message.get("tool_calls") or []
                for tc in tool_calls:
                    tc_id = tc.get("id") or tc.get("tool_call_id")
                    if tc_id:
                        seen_tool_call_ids.add(tc_id)

                    func = tc.get("function") or {}
                    arguments = func.get("arguments")
                    args_obj = {}
                    if isinstance(arguments, str):
                        try:
                            args_obj = json.loads(arguments)
                        except Exception:
                            args_obj = {}
                    elif isinstance(arguments, dict):
                        args_obj = arguments

                    intent = args_obj.get("intent") if isinstance(args_obj, dict) else None
                    name = func.get("name") or tc.get("name") or "unknown_tool"
                    entry = intent.strip() if isinstance(intent, str) and intent.strip() else name
                    tool_buffer.append(entry)
                continue

            if role == "tool":
                tc_id = message.get("tool_call_id") or message.get("id")
                if tc_id and tc_id in seen_tool_call_ids:
                    # 已经通过 intent 记录，无需重复
                    continue
                name = message.get("name") or "unknown_tool"
                tool_buffer.append(name)
                continue

            # 其他角色（如 system）原样保留
            flush_tools()
            content = message.get("content") or ""
            add_spacing()
            lines.append(f"{role}：{content}" if role else content)

        flush_tools()

        summary_text = header_text + "\n\n" + "\n".join(lines)

        system_message = {
            "role": "system",
            "content": summary_text,
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "compression": {
                    "source_conversation_id": conversation_id,
                    "types": ["intent_summary"],
                    "created_at": datetime.now().isoformat()
                }
            }
        }

        metadata = conversation_data.get("metadata", {})
        resolved_project_path = self._resolve_project_path_from_metadata(metadata)
        project_path = str(resolved_project_path)
        thinking_mode = metadata.get("thinking_mode", False)
        run_mode = metadata.get("run_mode") or ("thinking" if thinking_mode else "fast")
        model_key = metadata.get("model_key")
        has_images = metadata.get("has_images", False)
        original_title = conversation_data.get("title")

        compressed_conversation_id = self.conversation_manager.create_conversation(
            project_path=project_path,
            thinking_mode=thinking_mode,
            run_mode=run_mode,
            initial_messages=[system_message],
            model_key=model_key,
            has_images=has_images,
            metadata_overrides={
                "permission_mode": metadata.get("permission_mode", "unrestricted"),
            },
        )

        # 设置压缩后的对话标题
        if original_title:
            try:
                new_title = f"{original_title} 压缩后"
                self.conversation_manager.update_conversation_title(compressed_conversation_id, new_title)
            except Exception:
                pass

        return {
            "success": True,
            "compressed_conversation_id": compressed_conversation_id,
            "compressed_types": ["intent_summary"],
            "system_message": summary_text
        }
