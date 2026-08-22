# utils/conversation_manager.py - 对话持久化管理器（集成Token统计）

import json
import os
import time
import tempfile
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

try:
    from utils.perf_log import perf_log
except Exception:
    perf_log = None


# save_conversation 专用哨兵：区分「不更新该字段」与「显式清除为 None」
_KEEP = object()

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


class CrudMixin:
    """ConversationManager crud mixin 能力 mixin。"""

    def create_conversation(
        self,
        project_path: str,
        thinking_mode: bool = False,
        run_mode: str = "fast",
        initial_messages: List[Dict] = None,
        model_key: Optional[str] = None,
        has_images: bool = False,
        has_videos: bool = False,
        metadata_overrides: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        创建新对话
        
        Args:
            project_path: 项目路径
            thinking_mode: 思考模式
            initial_messages: 初始消息列表
        
        Returns:
            conversation_id: 对话ID
        """
        t0 = time.perf_counter()
        if perf_log:
            perf_log("cm.create_conversation enter")
        conversation_id = self._generate_conversation_id()
        messages = initial_messages or []
        
        # 创建对话数据
        path_metadata = self._prepare_project_path_metadata(project_path)
        normalized_mode = run_mode if run_mode in {"fast", "thinking", "deep"} else ("thinking" if thinking_mode else "fast")
        metadata = {
            "project_path": path_metadata["project_path"],
            "project_relative_path": path_metadata["project_relative_path"],
            "thinking_mode": thinking_mode,
            "run_mode": normalized_mode,
            "model_key": model_key,
            "permission_mode": "unrestricted",
            "execution_mode": "sandbox",
            "pending_permission_mode": None,
            "pending_execution_mode": None,
            "frozen_permission_prompt": None,
            "frozen_execution_prompt": None,
            "has_images": has_images,
            "has_videos": has_videos,
            # 首次对话尚未生成文件树快照，待首次用户消息时填充
            "project_file_tree": None,
            "project_statistics": None,
            "project_snapshot_at": None,
            "total_messages": len(messages),
            "total_tools": self._count_tools_in_messages(messages),
            "status": "active",
            # 压缩相关字段
            "compression_count": 0,
            "is_long_conversation": False,
            "is_ultra_long_conversation": False,
            "tool_call_count": 0,
            "last_shallow_compress_tool_count": 0,
            "compression_in_progress": False,
            "compression_mode": None,
            "compression_stage": None,
            "compression_job_id": None,
            "compression_error": None,
            "compression_resume_payload": None,
            "deep_compression_records": [],
            "last_deep_compression_record": None,
            "skip_auto_title_generation": False,
        }
        if isinstance(metadata_overrides, dict):
            metadata.update(metadata_overrides)

        conversation_data = {
            "id": conversation_id,
            "title": self._extract_title_from_messages(messages),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "messages": messages,
            "todo_list": None,
            "metadata": metadata,
            "token_statistics": self._initialize_token_statistics()  # 新增
        }
        
        # 保存对话文件
        t1 = time.perf_counter()
        self._save_conversation_file(conversation_id, conversation_data)
        if perf_log:
            perf_log("cm.create_conversation save_file", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": conversation_id})

        # 更新索引
        self._update_index(conversation_id, conversation_data)
        if perf_log:
            perf_log("cm.create_conversation update_index", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": conversation_id})

        self.current_conversation_id = conversation_id
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if perf_log:
            perf_log("cm.create_conversation done", elapsed_ms=elapsed_ms, extra={"conv_id": conversation_id})
        print(f"📝 创建新对话: {conversation_id} - {conversation_data['title']}")

        return conversation_id

    def update_conversation_metadata(self, conversation_id: str, updates: Dict[str, Any]) -> bool:
        """合并更新对话 metadata。"""
        if not conversation_id or not isinstance(updates, dict):
            return False
        try:
            data = self.load_conversation(conversation_id)
            if not data:
                return False
            metadata = data.get("metadata", {}) or {}
            metadata.update(updates)
            data["metadata"] = metadata
            data["updated_at"] = datetime.now().isoformat()
            self._save_conversation_file(conversation_id, data)
            self._update_index(conversation_id, data)
            return True
        except Exception as exc:
            print(f"⚠️ 更新对话 metadata 失败 {conversation_id}: {exc}")
            return False

    def update_conversation_title(self, conversation_id: str, title: str) -> bool:
        """更新对话标题并刷新索引。"""
        if not conversation_id or not title:
            return False
        try:
            data = self.load_conversation(conversation_id)
            if not data:
                return False
            data["title"] = title
            data["updated_at"] = datetime.now().isoformat()
            meta = data.get("metadata", {}) or {}
            meta["title_locked"] = True
            data["metadata"] = meta
            self._save_conversation_file(conversation_id, data)
            self._update_index(conversation_id, data)
            if self.current_conversation_id == conversation_id:
                self.current_conversation_title = title
            return True
        except Exception as exc:
            print(f"⚠️ 更新对话标题失败 {conversation_id}: {exc}")
            return False

    def _save_conversation_file(self, conversation_id: str, data: Dict):
        """保存对话文件"""
        file_path = self._get_conversation_file_path(conversation_id)
        try:
            # 确保Token统计数据有效
            data = self._validate_token_statistics(data)
            with self._io_lock:
                self._atomic_write_json(file_path, data)
        except Exception as e:
            print(f"⌘ 保存对话文件失败 {conversation_id}: {e}")

    def _update_index(self, conversation_id: str, conversation_data: Dict):
        """更新对话索引"""
        try:
            # 读-改-写必须整体持锁：此前仅在 _save_index 内加锁，
            # 并发保存时后写者会用旧的读结果覆盖掉先写者的条目（丢失更新），
            # 进而触发“索引缺文件→全量重建”的连锁反应。_io_lock 为 RLock，可重入。
            with self._io_lock:
                index = self._load_index()

                # 创建元数据
                metadata = ConversationMetadata(
                    id=conversation_id,
                    title=conversation_data["title"],
                    created_at=conversation_data["created_at"],
                    updated_at=conversation_data["updated_at"],
                    project_path=conversation_data["metadata"]["project_path"],
                    project_relative_path=conversation_data["metadata"].get("project_relative_path"),
                    thinking_mode=conversation_data["metadata"]["thinking_mode"],
                    run_mode=conversation_data["metadata"].get("run_mode", "thinking" if conversation_data["metadata"]["thinking_mode"] else "fast"),
                    total_messages=conversation_data["metadata"]["total_messages"],
                    total_tools=conversation_data["metadata"]["total_tools"],
                    status=conversation_data["metadata"].get("status", "active")
                )

                # 添加到索引
                index[conversation_id] = {
                    "title": metadata.title,
                    "created_at": metadata.created_at,
                    "updated_at": metadata.updated_at,
                    "project_path": metadata.project_path,
                    "project_relative_path": metadata.project_relative_path,
                    "thinking_mode": metadata.thinking_mode,
                    "run_mode": metadata.run_mode,
                    "model_key": conversation_data["metadata"].get("model_key"),
                    "has_images": conversation_data["metadata"].get("has_images", False),
                    "has_videos": conversation_data["metadata"].get("has_videos", False),
                    "total_messages": metadata.total_messages,
                    "total_tools": metadata.total_tools,
                    "status": metadata.status,
                    "multi_agent_mode": bool(conversation_data["metadata"].get("multi_agent_mode", False))
                }

                self._save_index(index)
        except Exception as e:
            print(f"⌘ 更新对话索引失败: {e}")

    @staticmethod
    def _merge_messages_by_id(disk_messages: List[Dict], new_messages: List[Dict]):
        """按 message_id 合并磁盘与内存消息（方向 C：merge-on-save）。

        规则：
        - 快路径：内存 id 序列前缀包含磁盘 id 序列（正常追加）→ 直接返回内存版；
        - 慢路径：以磁盘为基，同 id 取内存版（浅压缩打标等修改生效），
          内存独有 id 消息按原序追加在后；无 message_id 的内存消息防御性跳过追加
          （存量消息 100% 有 id，新消息由 add_conversation 生成必有 id）。

        Returns:
            (merged, appended, healed):
            merged   合并后的消息列表
            appended 内存独有而被追加的消息数
            healed   磁盘有而内存缺失（被矫正保留）的消息数
        """
        disk_ids = [m.get("message_id") for m in disk_messages]
        new_ids = [m.get("message_id") for m in new_messages]
        # 快路径：前缀追加，与现状全量覆写语义一致
        if len(new_ids) >= len(disk_ids) and new_ids[:len(disk_ids)] == disk_ids:
            return list(new_messages), 0, 0

        new_by_id = {m.get("message_id"): m for m in new_messages if m.get("message_id")}
        merged: List[Dict] = []
        seen = set()
        healed = 0
        for dm in disk_messages:
            mid = dm.get("message_id")
            if mid and mid in new_by_id:
                merged.append(new_by_id[mid])
            else:
                merged.append(dm)
                if mid:
                    healed += 1
            if mid:
                seen.add(mid)
        appended = 0
        for m in new_messages:
            mid = m.get("message_id")
            if not mid:
                continue
            if mid not in seen:
                merged.append(m)
                seen.add(mid)
                appended += 1
        return merged, appended, healed

    def save_conversation(
        self, 
        conversation_id: str, 
        messages: List[Dict],
        project_path: str = None,
        thinking_mode: bool = None,
        run_mode: Optional[str] = None,
        reasoning_effort: Any = _KEEP,
        todo_list: Optional[Dict] = None,
        model_key: Optional[str] = None,
        has_images: Optional[bool] = None,
        has_videos: Optional[bool] = None,
        project_file_tree: Optional[str] = None,
        project_statistics: Optional[Dict] = None,
        project_snapshot_at: Optional[str] = None,
        allow_shrink: bool = False
    ) -> bool:
        """
        保存对话（更新现有对话）
        
        Args:
            conversation_id: 对话ID
            messages: 消息列表
            project_path: 项目路径
            thinking_mode: 思考模式
            allow_shrink: 允许消息数缩减（仅检查点恢复等合法缩减路径使用）
        
        Returns:
            bool: 保存是否成功
        """
        try:
            # 加载现有对话数据
            existing_data = self.load_conversation(conversation_id)
            if not existing_data:
                print(f"⚠️ 对话 {conversation_id} 不存在，无法更新")
                return False
            
            # 方向 C（merge-on-save）：正常保存不做全量覆写，按 message_id 合并，
            # 任何实例任何时机的写回都不会丢消息（旧内存回写从根免疫）。
            # 仅检查点恢复等 allow_shrink=True 路径保持覆写语义。
            old_messages = existing_data.get("messages")
            old_len = len(old_messages) if isinstance(old_messages, list) else 0
            new_len = len(messages) if isinstance(messages, list) else 0
            if not allow_shrink and isinstance(old_messages, list) and old_messages:
                merged, appended, healed = self._merge_messages_by_id(old_messages, messages or [])
                if healed or appended:
                    print(
                        f"🔀 [ConvSaveMerge] {conversation_id}: "
                        f"磁盘 {old_len} 条 / 内存 {new_len} 条 → 合并 {len(merged)} 条"
                        f"（矫正保留磁盘 {healed} 条，追加内存独有 {appended} 条）"
                    )
                messages = merged
                new_len = len(messages)
            # 守卫断言：merge 后恒不缩减（触发即 merge 实现 bug）；豁免路径跳过。
            if new_len < old_len and not allow_shrink:
                print(
                    f"🚨 [ConvSaveGuard] 拒绝保存 {conversation_id}: "
                    f"消息数 {old_len} -> {new_len} 缩减且未豁免（merge 断言失败，疑似 bug）"
                )
                return False

            # 更新数据
            existing_data["messages"] = messages
            existing_data["updated_at"] = datetime.now().isoformat()
            
            # 更新标题（如未锁定自动标题时，仍按首条消息回退）
            title_locked = existing_data.get("metadata", {}).get("title_locked", False)
            if not title_locked:
                new_title = self._extract_title_from_messages(messages)
                if new_title != "新对话":
                    existing_data["title"] = new_title
            
            # 更新元数据
            if project_path is not None:
                path_metadata = self._prepare_project_path_metadata(project_path)
                existing_data["metadata"]["project_path"] = path_metadata["project_path"]
                existing_data["metadata"]["project_relative_path"] = path_metadata["project_relative_path"]
            else:
                existing_data["metadata"].setdefault("project_relative_path", None)
            if thinking_mode is not None:
                existing_data["metadata"]["thinking_mode"] = thinking_mode
            if run_mode:
                normalized_mode = str(run_mode).lower()
                if normalized_mode == "deep":  # 旧版标识符映射
                    normalized_mode = "thinking"
                if normalized_mode not in {"fast", "thinking"}:
                    normalized_mode = "thinking" if existing_data["metadata"].get("thinking_mode") else "fast"
                existing_data["metadata"]["run_mode"] = normalized_mode
            elif "run_mode" not in existing_data["metadata"]:
                existing_data["metadata"]["run_mode"] = "thinking" if existing_data["metadata"].get("thinking_mode") else "fast"
            if reasoning_effort is not _KEEP:
                # 显式传入：None/空 = 清除为默认（不传参）；否则写入档位
                if reasoning_effort is None:
                    existing_data["metadata"]["reasoning_effort"] = None
                else:
                    normalized_effort = str(reasoning_effort).strip().lower()
                    existing_data["metadata"]["reasoning_effort"] = normalized_effort or None
            elif "reasoning_effort" not in existing_data["metadata"]:
                existing_data["metadata"]["reasoning_effort"] = None
            # 推断最新使用的模型（优先参数，其次倒序扫描助手消息）
            inferred_model = None
            if model_key is None:
                for msg in reversed(messages):
                    if msg.get("role") != "assistant":
                        continue
                    msg_meta = msg.get("metadata") or {}
                    mk = msg_meta.get("model_key")
                    if mk:
                        inferred_model = mk
                        break
            target_model = model_key if model_key is not None else inferred_model
            if target_model is not None:
                existing_data["metadata"]["model_key"] = target_model
            elif "model_key" not in existing_data["metadata"]:
                existing_data["metadata"]["model_key"] = None
            if has_images is not None:
                existing_data["metadata"]["has_images"] = bool(has_images)
            elif "has_images" not in existing_data["metadata"]:
                existing_data["metadata"]["has_images"] = False
            if has_videos is not None:
                existing_data["metadata"]["has_videos"] = bool(has_videos)
            elif "has_videos" not in existing_data["metadata"]:
                existing_data["metadata"]["has_videos"] = False
            # 文件树快照（如果有新值则更新，若已有则保持）
            if project_file_tree is not None:
                existing_data["metadata"]["project_file_tree"] = project_file_tree
            elif "project_file_tree" not in existing_data["metadata"]:
                existing_data["metadata"]["project_file_tree"] = None
            if project_statistics is not None:
                existing_data["metadata"]["project_statistics"] = project_statistics
            elif "project_statistics" not in existing_data["metadata"]:
                existing_data["metadata"]["project_statistics"] = None
            if project_snapshot_at is not None:
                existing_data["metadata"]["project_snapshot_at"] = project_snapshot_at
            elif "project_snapshot_at" not in existing_data["metadata"]:
                existing_data["metadata"]["project_snapshot_at"] = None

            existing_data["metadata"]["total_messages"] = len(messages)
            existing_data["metadata"]["total_tools"] = self._count_tools_in_messages(messages)

            # 更新待办列表
            existing_data["todo_list"] = todo_list

            # 确保Token统计结构存在（向后兼容）
            if "token_statistics" not in existing_data:
                existing_data["token_statistics"] = self._initialize_token_statistics()
            else:
                existing_data["token_statistics"]["updated_at"] = datetime.now().isoformat()

            # 保存文件
            self._save_conversation_file(conversation_id, existing_data)

            # 更新索引
            self._update_index(conversation_id, existing_data)

            return True
        except Exception as e:
            print(f"⌘ 保存对话失败 {conversation_id}: {e}")
            return False

    def mark_latest_user_work_completed(
        self,
        conversation_id: str,
        finished_at: Optional[str] = None,
        context_manager: Optional[Any] = None,
    ) -> bool:
        """将对话中最后一条 status 为 working 的用户消息的 work_timer 标记为完成。

        用于任务停止或正常结束后，刷新页面时不再恢复为"工作中"状态。

        Args:
            conversation_id: 要处理的对话 ID。
            finished_at: 完成时间 ISO 字符串，默认当前时间。
            context_manager: 可选的当前 ContextManager 实例；若提供，会同步更新
                其内存中的 conversation_history，避免后续 auto_save 把已持久化的
                完成状态覆盖回去。
        """
        if not conversation_id:
            return False
        try:
            data = self.load_conversation(conversation_id)
            if not data:
                return False
            messages = data.get("messages") or []
            completed = False
            now_iso = finished_at or datetime.now().isoformat()
            for msg in reversed(messages):
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                metadata = msg.get("metadata") or {}
                timer = metadata.get("work_timer")
                if not isinstance(timer, dict):
                    continue
                if timer.get("status") != "working":
                    continue
                started_at = timer.get("started_at") or msg.get("timestamp") or now_iso
                try:
                    start_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
                    duration_ms = max(0, int((end_dt - start_dt).total_seconds() * 1000))
                except Exception:
                    duration_ms = timer.get("duration_ms", 0) or 0
                timer["status"] = "completed"
                timer["started_at"] = started_at
                timer["finished_at"] = now_iso
                timer["duration_ms"] = duration_ms
                msg["metadata"] = metadata
                completed = True
                break
            if not completed:
                return False
            data["updated_at"] = datetime.now().isoformat()
            self._save_conversation_file(conversation_id, data)
            self._update_index(conversation_id, data)

            # 同步内存副本（如果调用方持有当前对话的内存引用）
            if (
                context_manager
                and getattr(context_manager, "current_conversation_id", None) == conversation_id
            ):
                try:
                    self._sync_work_timer_in_memory(context_manager, now_iso)
                except Exception:
                    pass
            return True
        except Exception as exc:
            print(f"⌘ 标记用户工作完成失败 {conversation_id}: {exc}")
            return False

    def _sync_work_timer_in_memory(self, context_manager: Any, finished_at: str) -> bool:
        """将当前 ContextManager 内存中最后一条 working 的 work_timer 标记为完成。"""
        history = getattr(context_manager, "conversation_history", None) or []
        if not history:
            return False

        for msg in reversed(history):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            metadata = msg.get("metadata") or {}
            timer = metadata.get("work_timer")
            if not isinstance(timer, dict):
                continue
            if timer.get("status") != "working":
                continue

            started_at = timer.get("started_at") or msg.get("timestamp") or finished_at
            try:
                start_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                duration_ms = max(0, int((end_dt - start_dt).total_seconds() * 1000))
            except Exception:
                duration_ms = timer.get("duration_ms", 0) or 0

            timer.update({
                "status": "completed",
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
            })
            msg["metadata"] = metadata
            return True

        return False

    def update_project_snapshot(
        self,
        conversation_id: str,
        project_file_tree: str,
        project_statistics: Optional[Dict],
        project_snapshot_at: Optional[str] = None
    ) -> bool:
        """
        单独更新对话的项目文件树快照，不修改消息内容。
        便于在首次用户消息时写入固定文件结构。
        """
        try:
            data = self.load_conversation(conversation_id)
            if not data:
                return False
            meta = data.get("metadata", {}) or {}
            meta["project_file_tree"] = project_file_tree
            meta["project_statistics"] = project_statistics
            meta["project_snapshot_at"] = project_snapshot_at
            data["metadata"] = meta
            self._save_conversation_file(conversation_id, data)
            self._update_index(conversation_id, data)
            return True
        except Exception as exc:
            print(f"⌘ 更新项目快照失败 {conversation_id}: {exc}")
            return False

    def load_conversation(self, conversation_id: str) -> Optional[Dict]:
        """
        加载对话数据
        
        Args:
            conversation_id: 对话ID
        
        Returns:
            Dict: 对话数据，如果不存在返回None
        """
        try:
            file_path = self._get_conversation_file_path(conversation_id)
            if not file_path.exists():
                return None
            
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return None
                
                data = json.loads(content)

                metadata = data.get("metadata", {})
                if "project_relative_path" not in metadata:
                    metadata["project_relative_path"] = None
                    self._save_conversation_file(conversation_id, data)
                    print(f"🔧 为对话 {conversation_id} 添加相对路径字段")

                # 向后兼容：确保Token统计结构存在
                if "token_statistics" not in data:
                    data["token_statistics"] = self._initialize_token_statistics()
                    # 自动保存修复后的数据
                    self._save_conversation_file(conversation_id, data)
                    print(f"🔧 为对话 {conversation_id} 添加Token统计结构")
                else:
                    # 验证现有Token统计数据
                    data = self._validate_token_statistics(data)
                
                if "run_mode" not in metadata:
                    metadata["run_mode"] = "thinking" if metadata.get("thinking_mode") else "fast"
                    self._save_conversation_file(conversation_id, data)
                    print(f"🔧 为对话 {conversation_id} 添加运行模式字段")
                
                # 确保项目快照字段存在（向后兼容）
                changed = False
                if "project_file_tree" not in metadata:
                    metadata["project_file_tree"] = None
                    changed = True
                if "project_statistics" not in metadata:
                    metadata["project_statistics"] = None
                    changed = True
                if "project_snapshot_at" not in metadata:
                    metadata["project_snapshot_at"] = None
                    changed = True
                if changed:
                    data["metadata"] = metadata
                    self._save_conversation_file(conversation_id, data)
                    print(f"🔧 为对话 {conversation_id} 补齐项目快照字段")

                # 兼容历史口径：旧版本 total_tools 可能把 role=tool 也计入，导致翻倍。
                expected_total_tools = self._count_tools_in_messages(data.get("messages") or [])
                if int(metadata.get("total_tools", 0) or 0) != expected_total_tools:
                    metadata["total_tools"] = expected_total_tools
                    data["metadata"] = metadata
                    self._save_conversation_file(conversation_id, data)
                    self._update_index(conversation_id, data)
                    print(f"🔧 修正对话 {conversation_id} 的 total_tools 统计为 {expected_total_tools}")
                
                # 回填缺失的模型字段：从最近的助手消息元数据推断
                if metadata.get("model_key") is None:
                    inferred_model = None
                    for msg in reversed(data.get("messages") or []):
                        if msg.get("role") != "assistant":
                            continue
                        mk = (msg.get("metadata") or {}).get("model_key")
                        if mk:
                            inferred_model = mk
                            break
                    if inferred_model is not None:
                        metadata["model_key"] = inferred_model
                        self._save_conversation_file(conversation_id, data)
                        print(f"🔧 为对话 {conversation_id} 回填模型字段: {inferred_model}")
                
                return data
        except (json.JSONDecodeError, Exception) as e:
            print(f"⌘ 加载对话失败 {conversation_id}: {e}")
            return None
