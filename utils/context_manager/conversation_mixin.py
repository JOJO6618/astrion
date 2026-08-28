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


class ConversationMixin:
    """ContextManager conversation mixin 能力 mixin。"""

    def _get_conversation_manager_for_multi_agent_mode(self, multi_agent_mode: bool = False):
        """根据是否为多智能体会话返回对应的 ConversationManager。"""
        if multi_agent_mode:
            return getattr(self, "multi_agent_conversation_manager", None) or self.conversation_manager
        return self.conversation_manager

    def _get_conversation_manager_for_id(self, conversation_id: str):
        """根据对话ID判断它属于哪个 ConversationManager。

        优先检查多智能体管理器（如果存在），再回退到普通管理器。
        """
        ma_manager = getattr(self, "multi_agent_conversation_manager", None)
        if not ma_manager:
            return self.conversation_manager
        # 先查多智能体索引/文件
        try:
            ma_path = ma_manager._get_conversation_file_path(conversation_id)
            if ma_path.exists():
                return ma_manager
        except Exception:
            pass
        try:
            regular_path = self.conversation_manager._get_conversation_file_path(conversation_id)
            if regular_path.exists():
                return self.conversation_manager
        except Exception:
            pass
        # 都不存在时：若ID在常规索引中则走常规，否则默认走多智能体（兼容新建对话后立刻保存）
        try:
            regular_index = self.conversation_manager._load_index()
            if conversation_id in regular_index:
                return self.conversation_manager
        except Exception:
            pass
        return ma_manager

    def start_new_conversation(
        self,
        project_path: str = None,
        thinking_mode: bool = False,
        run_mode: Optional[str] = None,
        metadata_overrides: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        开始新对话
        
        Args:
            project_path: 项目路径，默认使用当前项目路径
            thinking_mode: 思考模式
        
        Returns:
            str: 新对话ID
        """
        if project_path is None:
            project_path = str(self.project_path)
        
        # 保存当前对话（如果有的话）
        if self.current_conversation_id and self.conversation_history:
            self.save_current_conversation()

        # 同步 skills（每次新对话覆盖镜像）
        try:
            from modules.personalization_manager import load_personalization_config
            from modules.skills_manager import infer_private_skills_dir, sync_workspace_skills
            personalization_config = getattr(self, "custom_personalization_config", None) or load_personalization_config(self.data_dir)
            enabled_skills = None
            if isinstance(personalization_config, dict):
                enabled_skills = personalization_config.get("enabled_skills")
            sync_workspace_skills(
                self.project_path,
                enabled_skills,
                private_dir=infer_private_skills_dir(self.data_dir),
            )
        except Exception as exc:
            print(f"[Skills] 同步失败: {exc}")
        
        # 创建新对话：多智能体对话使用独立的 conversation_manager
        is_multi_agent = bool((metadata_overrides or {}).get("multi_agent_mode"))
        target_manager = self._get_conversation_manager_for_multi_agent_mode(is_multi_agent)
        conversation_id = target_manager.create_conversation(
            project_path=project_path,
            thinking_mode=thinking_mode,
            run_mode=run_mode or ("thinking" if thinking_mode else "fast"),
            initial_messages=[],
            model_key=getattr(self.main_terminal, "model_key", None),
            has_images=False,
            has_videos=False,
            metadata_overrides=metadata_overrides,
        )
        
        # 重置当前状态
        self.current_conversation_id = conversation_id
        self.conversation_history = []
        self.todo_list = None
        self.has_images = False
        self.has_videos = False
        self.conversation_metadata = {}
        self.project_snapshot = None
        
        print(f"📝 开始新对话: {conversation_id}")
        return conversation_id

    def load_conversation_by_id(self, conversation_id: str, attach_history: bool = True) -> bool:
        """
        加载指定对话
        
        Args:
            conversation_id: 对话ID
            attach_history: 是否把消息历史/元数据挂载到内存。工作区级服务实例应传 False
                （仅恢复焦点 current_conversation_id 与运行模式）：对话历史的权威载体是
                磁盘 + 对话级 terminal，服务实例挂载历史只会成为 merge-on-save 的污染源
                （版本回溯被旧内存“救回”覆盖即此机制事故）。
        
        Returns:
            bool: 加载是否成功
        """
        # 先保存当前对话（加载目标就是当前对话时跳过：接下来会以磁盘为准重载，
        # 若本实例内存落后于磁盘，保存反而把旧历史回写覆盖新消息——双实例并存时
        # 这是已实锤的回退源头；其余路径的落后写入由 save_conversation 守卫拦截）
        current_norm = (self.current_conversation_id or "").removeprefix("conv_")
        target_norm = (conversation_id or "").removeprefix("conv_")
        if (
            self.current_conversation_id
            and self.conversation_history
            and current_norm != target_norm
        ):
            self.save_current_conversation()
        
        # 加载指定对话：先按ID路由到对应 manager
        target_manager = self._get_conversation_manager_for_id(conversation_id)
        conversation_data = target_manager.load_conversation(conversation_id)
        if not conversation_data:
            # 兜底：如果目标 manager 没有，再尝试另一个
            fallback_manager = (
                self.conversation_manager
                if target_manager is self.multi_agent_conversation_manager
                else self.multi_agent_conversation_manager
            )
            try:
                conversation_data = fallback_manager.load_conversation(conversation_id)
            except Exception:
                conversation_data = None
        if not conversation_data:
            print(f"⌘ 对话 {conversation_id} 不存在")
            return False
        
        # 更新当前状态
        self.current_conversation_id = conversation_id
        if attach_history:
            self.conversation_history = conversation_data.get("messages", [])
            todo_data = conversation_data.get("todo_list")
            self.todo_list = deepcopy(todo_data) if todo_data else None
            self.conversation_metadata = deepcopy(conversation_data.get("metadata", {}) or {})
        else:
            # 服务实例：仅焦点，不挂载消息/元数据（保持空内存，merge 语义下任何保存都不会回写消息）
            self.conversation_history = []
            self.todo_list = None
            self.conversation_metadata = {}
        # 恢复项目文件树快照（如已存在）
        meta = deepcopy(conversation_data.get("metadata", {}) or {})
        if meta.get("project_file_tree"):
            self.project_snapshot = {
                "file_tree": meta.get("project_file_tree"),
                "statistics": meta.get("project_statistics"),
                "snapshot_at": meta.get("project_snapshot_at")
            }
        else:
            self.project_snapshot = None
        
        # 更新项目路径（如果对话中有的话）
        metadata = conversation_data.get("metadata", {})
        resolved_project_path = self._resolve_project_path_from_metadata(metadata)

        stored_path = metadata.get("project_path")
        stored_path_obj = None
        if isinstance(stored_path, str) and stored_path.strip():
            try:
                stored_path_obj = Path(stored_path.strip()).expanduser().resolve()
            except Exception:
                stored_path_obj = None

        if stored_path_obj and stored_path_obj != resolved_project_path:
            print(f"⚠️ 对话记录中的项目路径不可用，已回退至: {resolved_project_path}")

        self.project_path = resolved_project_path
        write_host_workspace_debug(
            "context_manager.load_conversation_by_id.set_project_path",
            conversation_id=conversation_id,
            metadata_project_path=str(stored_path_obj) if stored_path_obj else None,
            resolved_project_path=str(resolved_project_path),
        )
        
        run_mode = metadata.get("run_mode")
        permission_mode = metadata.get("permission_mode")
        model_key = metadata.get("model_key")
        self.has_images = metadata.get("has_images", False)
        self.has_videos = metadata.get("has_videos", False)
        if attach_history and (not self.has_images or not self.has_videos):
            for msg in self.conversation_history:
                if not isinstance(msg, dict):
                    continue
                images = msg.get("images") or []
                videos = msg.get("videos") or []
                if images:
                    self.has_images = True
                if videos:
                    self.has_videos = True
                for ref in (msg.get("media_refs") or []):
                    kind = str((ref or {}).get("kind") or "").strip().lower()
                    if kind == "image":
                        self.has_images = True
                    elif kind == "video":
                        self.has_videos = True
                if self.has_images and self.has_videos:
                    break
        if self.main_terminal:
            try:
                if model_key:
                    self.main_terminal.set_model(model_key)
            except Exception:
                fallback_key = None
                for candidate in get_registered_model_keys(visible_only=True):
                    if self.has_images and not model_supports_image(candidate):
                        continue
                    if self.has_videos and not model_supports_video(candidate):
                        continue
                    fallback_key = candidate
                    break
                if fallback_key:
                    try:
                        self.main_terminal.set_model(fallback_key)
                        self.conversation_metadata["model_key"] = fallback_key
                    except Exception:
                        pass
            try:
                if run_mode:
                    self.main_terminal.set_run_mode(run_mode)
                elif metadata.get("thinking_mode"):
                    self.main_terminal.set_run_mode("thinking")
                else:
                    self.main_terminal.set_run_mode("fast")
            except Exception:
                pass
            try:
                fallback_mode = getattr(self.main_terminal, "default_permission_mode", "unrestricted")
                self.main_terminal.set_permission_mode(permission_mode or fallback_mode, persist=False)
            except Exception:
                pass
        
        print(f"📖 加载对话: {conversation_id} - {conversation_data.get('title', '未知标题')}")
        print(f"📊 包含 {len(self.conversation_history)} 条消息")
        
        return True

    def _is_service_instance_no_history(self) -> bool:
        """是否为「不持有对话历史」的工作区级服务实例（由 WebTerminal 初始化时打标）。

        服务实例不挂载/不回写消息历史：历史权威在磁盘与对话级 terminal。
        """
        return bool(getattr(self, "_service_instance_no_history", False))

    def save_current_conversation(self) -> bool:
        """
        保存当前对话
        
        Returns:
            bool: 保存是否成功
        """
        if self._is_service_instance_no_history():
            # 服务实例不持有历史，保存无语义；直接跳过，防止任何路径把空/陈旧内存写回
            return False
        if not self.current_conversation_id:
            print("⚠️ 没有当前对话ID，无法保存")
            return False
        
        if not self.auto_save_enabled:
            return False
        
        try:
            run_mode = getattr(self.main_terminal, "run_mode", None) if hasattr(self, "main_terminal") else None
            target_manager = self._get_conversation_manager_for_id(self.current_conversation_id)
            success = target_manager.save_conversation(
                conversation_id=self.current_conversation_id,
                messages=self.conversation_history,
                project_path=str(self.project_path),
                todo_list=self.todo_list,
                thinking_mode=getattr(self.main_terminal, "thinking_mode", None) if hasattr(self, "main_terminal") else None,
                run_mode=run_mode,
                model_key=getattr(self.main_terminal, "model_key", None) if hasattr(self, "main_terminal") else None,
                has_images=self.has_images,
                has_videos=self.has_videos
            )
            
            if success:
                print(f"💾 对话已自动保存: {self.current_conversation_id}")
            else:
                print(f"⌘ 对话保存失败: {self.current_conversation_id}")
            
            return success
        except Exception as e:
            print(f"⌘ 保存对话异常: {e}")
            return False

    def auto_save_conversation(self, force: bool = False):
        """自动保存对话（静默模式，减少日志输出）"""
        if self._is_service_instance_no_history():
            return
        if not self.auto_save_enabled or not self.current_conversation_id:
            return
        if not force and not self.conversation_history:
            return
        try:
            run_mode = getattr(self.main_terminal, "run_mode", None) if hasattr(self, "main_terminal") else None
            model_key = getattr(self.main_terminal, "model_key", None) if hasattr(self, "main_terminal") else None
            target_manager = self._get_conversation_manager_for_id(self.current_conversation_id)
            target_manager.save_conversation(
                conversation_id=self.current_conversation_id,
                messages=self.conversation_history,
                project_path=str(self.project_path),
                todo_list=self.todo_list,
                thinking_mode=getattr(self.main_terminal, "thinking_mode", None) if hasattr(self, "main_terminal") else None,
                run_mode=run_mode,
                model_key=model_key,
                has_images=self.has_images,
                has_videos=self.has_videos
            )
            # 静默保存，不输出日志
        except Exception as e:
            print(f"⌘ 自动保存异常: {e}")

    def get_conversation_list(self, limit: int = 50, offset: int = 0, non_empty: bool = False, multi_agent_mode: Optional[bool] = None) -> Dict:
        """获取对话列表。

        多智能体对话存储在独立目录，需要合并普通管理器和多智能体管理器的结果。
        """
        # 多智能体模式只查多智能体管理器；常规模式只查普通管理器；None 时合并
        if multi_agent_mode is True:
            ma_manager = getattr(self, "multi_agent_conversation_manager", None)
            if ma_manager:
                return ma_manager.get_conversation_list(limit=limit, offset=offset, non_empty=non_empty, multi_agent_mode=multi_agent_mode)
            return self.conversation_manager.get_conversation_list(limit=limit, offset=offset, non_empty=non_empty, multi_agent_mode=multi_agent_mode)
        if multi_agent_mode is False:
            return self.conversation_manager.get_conversation_list(limit=limit, offset=offset, non_empty=non_empty, multi_agent_mode=multi_agent_mode)

        # None：合并两个管理器的结果
        regular = self.conversation_manager.get_conversation_list(limit=limit, offset=offset, non_empty=non_empty, multi_agent_mode=False)
        ma_manager = getattr(self, "multi_agent_conversation_manager", None)
        if not ma_manager:
            return regular
        ma = ma_manager.get_conversation_list(limit=limit, offset=offset, non_empty=non_empty, multi_agent_mode=True)

        merged_conversations = list(regular.get("conversations", [])) + list(ma.get("conversations", []))
        merged_conversations.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        total = (regular.get("total") or 0) + (ma.get("total") or 0)
        result = merged_conversations[offset:offset + limit]
        return {
            "conversations": result,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total
        }

    def delete_conversation_by_id(self, conversation_id: str) -> bool:
        """删除指定对话"""
        # 如果是当前对话，清理状态
        if self.current_conversation_id == conversation_id:
            self.current_conversation_id = None
            self.conversation_history = []
            self.todo_list = None
        elif self.current_conversation_id and self.conversation_history:
            try:
                target_manager = self._get_conversation_manager_for_id(self.current_conversation_id)
                conversation_data = target_manager.load_conversation(self.current_conversation_id)
                if not conversation_data:
                    self.current_conversation_id = None
                    self.conversation_history = []
                    self.todo_list = None
                else:
                    todo_data = conversation_data.get("todo_list")
                    self.todo_list = deepcopy(todo_data) if todo_data else None
            except Exception as exc:
                print(f"⌘ 刷新待办列表失败: {exc}")
                self.todo_list = None

        # 先定位对话所在 manager，再删除；两个目录都尝试兜底
        target_manager = self._get_conversation_manager_for_id(conversation_id)
        try:
            if target_manager.load_conversation(conversation_id):
                return target_manager.delete_conversation(conversation_id)
        except Exception:
            pass
        fallback_manager = (
            self.conversation_manager
            if target_manager is self.multi_agent_conversation_manager
            else self.multi_agent_conversation_manager
        )
        try:
            if fallback_manager.load_conversation(conversation_id):
                return fallback_manager.delete_conversation(conversation_id)
        except Exception:
            pass
        return False

    def search_conversations(self, query: str, limit: int = 20, multi_agent_mode: Optional[bool] = None) -> List[Dict]:
        """搜索对话"""
        return self.conversation_manager.search_conversations(query, limit, multi_agent_mode=multi_agent_mode)

    def get_conversation_statistics(self) -> Dict:
        """获取对话统计"""
        return self.conversation_manager.get_statistics()

    def duplicate_conversation(self, conversation_id: str) -> Dict:
        """复制对话，生成新的对话副本"""
        conversation_data = self.conversation_manager.load_conversation(conversation_id)
        if not conversation_data:
            return {
                "success": False,
                "error": tr("ctx_mgr.conversation_not_found", conversation_id=conversation_id)
            }

        original_messages = deepcopy(conversation_data.get("messages", []) or [])
        original_title = conversation_data.get("title")

        metadata = conversation_data.get("metadata", {})
        resolved_project_path = self._resolve_project_path_from_metadata(metadata)
        project_path = str(resolved_project_path)
        thinking_mode = metadata.get("thinking_mode", False)
        run_mode = metadata.get("run_mode") or ("thinking" if thinking_mode else "fast")
        model_key = metadata.get("model_key")
        has_images = metadata.get("has_images", False)

        duplicate_conversation_id = self.conversation_manager.create_conversation(
            project_path=project_path,
            thinking_mode=thinking_mode,
            run_mode=run_mode,
            initial_messages=original_messages,
            model_key=model_key,
            has_images=has_images,
            metadata_overrides={
                "permission_mode": metadata.get("permission_mode", "unrestricted"),
            },
        )

        token_stats = conversation_data.get("token_statistics")
        if token_stats:
            new_data = self.conversation_manager.load_conversation(duplicate_conversation_id)
            if new_data:
                new_data["token_statistics"] = deepcopy(token_stats)
                new_metadata = new_data.get("metadata", {})
                new_metadata["total_messages"] = metadata.get("total_messages", len(original_messages))
                new_metadata["total_tools"] = metadata.get("total_tools", 0)
                new_metadata["status"] = metadata.get("status", "active")
                new_data["metadata"] = new_metadata
                new_data["updated_at"] = datetime.now().isoformat()
                self.conversation_manager._save_conversation_file(duplicate_conversation_id, new_data)
                self.conversation_manager._update_index(duplicate_conversation_id, new_data)

        # 设置复制后的对话标题
        if original_title:
            try:
                new_title = tr("ctx_mgr.duplicate_title_suffix", original_title=original_title)
                self.conversation_manager.update_conversation_title(duplicate_conversation_id, new_title)
            except Exception:
                pass

        return {
            "success": True,
            "duplicate_conversation_id": duplicate_conversation_id
        }

    def save_conversation(self):
        """保存对话历史（废弃，使用新的持久化系统）"""
        print("⚠️ save_conversation() 已废弃，使用新的持久化系统")
        return self.save_current_conversation()

    def load_conversation(self):
        """加载对话历史（废弃，使用新的持久化系统）"""
        print("⚠️ load_conversation() 已废弃，使用 load_conversation_by_id()")
        # 兼容性：尝试加载最近的对话
        conversations = self.get_conversation_list(limit=1)
        if conversations["conversations"]:
            latest_conv = conversations["conversations"][0]
            return self.load_conversation_by_id(latest_conv["id"])
        return False
