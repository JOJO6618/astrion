# utils/conversation_manager.py - 对话持久化管理器（集成Token统计）

import json
import os
import re
import time
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import time
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


class ListSearchMixin:
    """ConversationManager list search mixin 能力 mixin。"""

    def get_conversation_list(self, limit: int = 50, offset: int = 0, non_empty: bool = False, multi_agent_mode: Optional[bool] = None) -> Dict:
        """
        获取对话列表

        Args:
            limit: 限制数量
            offset: 偏移量
            non_empty: 仅返回有内容（total_messages > 0）的对话，分页基于过滤后的结果
            multi_agent_mode: 若为 True 仅返回多智能体模式对话；若为 False 仅返回常规对话；None 不过滤

        Returns:
            Dict: 包含对话列表和统计信息
        """
        t0 = time.perf_counter()
        if perf_log:
            perf_log("get_conversation_list enter", extra={"limit": limit, "offset": offset, "non_empty": non_empty, "multi_agent_mode": multi_agent_mode})
        try:
            def _filter_by_multi_agent(items):
                """按 multi_agent_mode 元数据过滤。"""
                if multi_agent_mode is None:
                    return items
                result = []
                for conv_id, meta in items:
                    conv_ma = bool(meta.get("multi_agent_mode", False))
                    if conv_ma == multi_agent_mode:
                        result.append((conv_id, meta))
                return result

            if non_empty:
                # 过滤模式：全量加载后剔除空对话，再在过滤结果上分页，
                # 保证 total / has_more 与"有内容对话"的真实数量一致。
                index = self._ensure_index_covering(limit=10000, offset=0)
                sorted_conversations = sorted(
                    index.items(),
                    key=lambda x: x[1].get("updated_at") or "",
                    reverse=True
                )
                sorted_conversations = [
                    item for item in sorted_conversations
                    if (item[1].get("total_messages", 0) or 0) > 0
                ]
                if multi_agent_mode is not None:
                    sorted_conversations = _filter_by_multi_agent(sorted_conversations)
                total = len(sorted_conversations)
                conversations = sorted_conversations[offset:offset + limit]
            else:
                # 总对话数按文件数统计，防止初始索引截断导致"没有更多"按钮消失
                total_files = len(self._iter_conversation_files(sort_by_mtime=False))

                index = self._ensure_index_covering(limit=limit, offset=offset)

                # 按更新时间倒序排列（处理 None 值）
                sorted_conversations = sorted(
                    index.items(),
                    key=lambda x: x[1].get("updated_at") or "",
                    reverse=True
                )

                if multi_agent_mode is not None:
                    sorted_conversations = _filter_by_multi_agent(sorted_conversations)

                # 分页
                total = max(len(sorted_conversations), total_files if multi_agent_mode is None else len(sorted_conversations))
                conversations = sorted_conversations[offset:offset+limit]

            # 格式化结果
            result = []
            for conv_id, metadata in conversations:
                result.append({
                    "id": conv_id,
                    "title": metadata.get("title", "未命名对话"),
                    "created_at": metadata.get("created_at"),
                    "updated_at": metadata.get("updated_at"),
                    "project_path": metadata.get("project_path"),
                    "project_relative_path": metadata.get("project_relative_path"),
                    "thinking_mode": metadata.get("thinking_mode", False),
                    "total_messages": metadata.get("total_messages", 0),
                    "total_tools": metadata.get("total_tools", 0),
                    "status": metadata.get("status", "active"),
                    "multi_agent_mode": bool(metadata.get("multi_agent_mode", False))
                })

            elapsed_ms = (time.perf_counter() - t0) * 1000
            if perf_log:
                perf_log("get_conversation_list done", elapsed_ms=elapsed_ms, extra={"result_count": len(result), "total": total})
            return {
                "conversations": result,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if perf_log:
                perf_log("get_conversation_list error", elapsed_ms=elapsed_ms, extra={"error": str(e)})
            print(f"⌘ 获取对话列表失败: {e}")
            return {
                "conversations": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
                "has_more": False
            }

    def get_recent_conversation_summaries(
        self,
        limit: int = 10,
        *,
        exclude_conversation_id: Optional[str] = None,
        first_message_max_chars: int = 100,
    ) -> List[Dict]:
        """返回当前工作区最近对话摘要：标题 + 首条用户消息。"""
        try:
            target_limit = max(1, int(limit))
            listing = self.get_conversation_list(limit=10000, offset=0)
            summaries: List[Dict] = []
            for item in listing.get("conversations") or []:
                conv_id = item.get("id")
                if not conv_id or conv_id == exclude_conversation_id:
                    continue
                data = self.load_conversation(conv_id) or {}
                first_user_message = self._extract_first_user_message(
                    data.get("messages") or [],
                    max_chars=max(1, int(first_message_max_chars or 100)),
                )
                if not first_user_message:
                    continue
                summaries.append({
                    "id": conv_id,
                    "title": item.get("title") or data.get("title") or "未命名对话",
                    "created_at": item.get("created_at") or data.get("created_at"),
                    "updated_at": item.get("updated_at") or data.get("updated_at"),
                    "total_messages": item.get("total_messages") or (data.get("metadata") or {}).get("total_messages", 0),
                    "total_tools": item.get("total_tools") or (data.get("metadata") or {}).get("total_tools", 0),
                    "first_user_message": first_user_message,
                })
                if len(summaries) >= target_limit:
                    break
            return summaries
        except Exception as e:
            print(f"⌘ 获取最近对话摘要失败: {e}")
            return []

    def search_conversation_summaries(
        self,
        query: str = "",
        *,
        keywords: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10,
        first_message_max_chars: int = 100,
        exclude_conversation_id: Optional[str] = None,
    ) -> List[Dict]:
        """在当前工作区内搜索标题 + 首条用户消息，可按创建日期过滤。"""
        try:
            raw_keywords = keywords if isinstance(keywords, list) else []
            normalized_keywords = [str(item).strip().lower() for item in raw_keywords if str(item or "").strip()]
            fallback_query = (query or "").strip().lower()
            if fallback_query:
                normalized_keywords.append(fallback_query)
            normalized_keywords = normalized_keywords[:3]
            start_key = (start_date or "").strip()
            end_key = (end_date or "").strip()
            listing = self.get_conversation_list(limit=10000, offset=0)
            results: List[Dict] = []
            for item in listing.get("conversations") or []:
                conv_id = item.get("id")
                if not conv_id or conv_id == exclude_conversation_id:
                    continue
                created_at = str(item.get("created_at") or "")
                date_key = created_at[:10]
                if start_key and date_key and date_key < start_key:
                    continue
                if end_key and date_key and date_key > end_key:
                    continue
                data = self.load_conversation(conv_id) or {}
                first_message = self._extract_first_user_message(
                    data.get("messages") or [],
                    max_chars=max(1, int(first_message_max_chars or 100)),
                )
                # 空对话（没有任何用户文本）不应出现在搜索/列表结果中，
                # 否则无关键词 list 最近对话时会出现大量“新对话”。
                if not first_message:
                    continue
                title = item.get("title") or data.get("title") or "未命名对话"
                haystack = f"{title} {first_message}".lower()
                if normalized_keywords and not any(keyword in haystack for keyword in normalized_keywords):
                    continue
                results.append({
                    "id": conv_id,
                    "title": title,
                    "first_user_message": first_message,
                    "created_at": item.get("created_at") or data.get("created_at"),
                    "updated_at": item.get("updated_at") or data.get("updated_at"),
                    "total_messages": item.get("total_messages") or (data.get("metadata") or {}).get("total_messages", 0),
                    "total_tools": item.get("total_tools") or (data.get("metadata") or {}).get("total_tools", 0),
                })
                if len(results) >= max(1, int(limit or 10)):
                    break
            return results
        except Exception as e:
            print(f"⌘ 搜索对话摘要失败: {e}")
            return []

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        删除对话
        
        Args:
            conversation_id: 对话ID
        
        Returns:
            bool: 删除是否成功
        """
        try:
            # 删除对话文件
            file_path = self._get_conversation_file_path(conversation_id)
            if file_path.exists():
                file_path.unlink()
            
            # 从索引中删除
            index = self._load_index()
            if conversation_id in index:
                del index[conversation_id]
                self._save_index(index)
            
            # 如果删除的是当前对话，清除当前对话ID
            if self.current_conversation_id == conversation_id:
                self.current_conversation_id = None
            
            print(f"🗑️ 已删除对话: {conversation_id}")
            return True
        except Exception as e:
            print(f"⌘ 删除对话失败 {conversation_id}: {e}")
            return False

    def archive_conversation(self, conversation_id: str) -> bool:
        """
        归档对话（标记为已归档，不删除）
        
        Args:
            conversation_id: 对话ID
        
        Returns:
            bool: 归档是否成功
        """
        try:
            # 更新对话状态
            conversation_data = self.load_conversation(conversation_id)
            if not conversation_data:
                return False
            
            conversation_data["metadata"]["status"] = "archived"
            conversation_data["updated_at"] = datetime.now().isoformat()
            
            # 保存更新
            self._save_conversation_file(conversation_id, conversation_data)
            self._update_index(conversation_id, conversation_data)
            
            print(f"📦 已归档对话: {conversation_id}")
            return True
        except Exception as e:
            print(f"⌘ 归档对话失败 {conversation_id}: {e}")
            return False

    def search_conversations(self, query: str, limit: int = 20, multi_agent_mode: Optional[bool] = None) -> List[Dict]:
        """
        搜索对话（标题 / 首条用户消息首句，与原前端客户端搜索口径一致）

        Args:
            query: 搜索关键词
            limit: 限制数量
            multi_agent_mode: True 仅多智能体对话；False 仅常规对话；None 不过滤

        Returns:
            List[Dict]: 匹配的对话列表
        """
        try:
            index = self._load_index()
            results = []

            query_lower = (query or "").lower()
            if not query_lower:
                return []

            for conv_id, metadata in index.items():
                if multi_agent_mode is not None:
                    conv_ma = bool(metadata.get("multi_agent_mode", False))
                    if conv_ma != multi_agent_mode:
                        continue

                entry = {
                    "id": conv_id,
                    "title": metadata.get("title"),
                    "created_at": metadata.get("created_at"),
                    "updated_at": metadata.get("updated_at"),
                    "project_path": metadata.get("project_path"),
                }

                # 搜索标题（权重最高）
                title = (metadata.get("title") or "").lower()
                if query_lower in title:
                    results.append((100, entry["updated_at"] or "", {**entry, "match_type": "title"}))
                    continue

                # 搜索首条用户消息首句
                first_sentence = self._get_first_user_sentence(conv_id, metadata)
                if first_sentence and query_lower in first_sentence:
                    results.append((80, entry["updated_at"] or "", {**entry, "match_type": "content"}))

            # 分数优先，同分按更新时间新到旧
            results.sort(key=lambda x: (x[0], x[1]), reverse=True)

            # 返回前N个结果
            return [item[2] for item in results[:limit]]
        except Exception as e:
            print(f"⌘ 搜索对话失败: {e}")
            return []

    def _get_first_user_sentence(self, conversation_id: str, metadata: Optional[Dict] = None) -> str:
        """读取对话首条用户消息并提取首句（小写）。

        实例级缓存（terminal 按工作区缓存，manager 实例跨请求存活），
        以 updated_at 作为失效依据，避免每次按键都重读对话文件。
        """
        try:
            cache = getattr(self, "_search_sentence_cache", None)
            if cache is None:
                cache = {}
                self._search_sentence_cache = cache
            updated_at = (metadata or {}).get("updated_at") or ""
            cached = cache.get(conversation_id)
            if cached and cached[0] == updated_at:
                return cached[1]

            sentence = ""
            conversation_data = self.load_conversation(conversation_id)
            if conversation_data:
                for msg in conversation_data.get("messages") or []:
                    if not isinstance(msg, dict) or msg.get("role") != "user":
                        continue
                    sentence = self._extract_first_sentence(self._extract_message_text(msg))
                    break

            sentence_lower = sentence.lower()
            cache[conversation_id] = (updated_at, sentence_lower)
            # 防止缓存无限增长
            if len(cache) > 2000:
                cache.clear()
            return sentence_lower
        except Exception:
            return ""

    @staticmethod
    def _extract_message_text(msg: Dict) -> str:
        """从消息中提取纯文本（与 build_review_lines 的 extract_text 口径一致）。"""
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text") or "")
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        if isinstance(content, dict):
            return content.get("text") or ""
        return str(msg.get("text") or "")

    @staticmethod
    def _extract_first_sentence(text: str) -> str:
        """与旧前端 extractFirstSentence 口径一致：取到第一个句末标点为止。"""
        if not text:
            return ""
        normalized = re.sub(r"\s+", " ", str(text)).strip()
        m = re.match(r"(.+?[。！？.!?])", normalized)
        return m.group(1) if m else normalized

    def cleanup_old_conversations(self, days: int = 30) -> int:
        """
        清理旧对话（可选功能）
        
        Args:
            days: 保留天数
        
        Returns:
            int: 清理的对话数量
        """
        try:
            from datetime import datetime, timedelta
            cutoff_date = datetime.now() - timedelta(days=days)
            cutoff_iso = cutoff_date.isoformat()
            
            index = self._load_index()
            to_delete = []
            
            for conv_id, metadata in index.items():
                updated_at = metadata.get("updated_at", "")
                if updated_at < cutoff_iso and metadata.get("status") != "archived":
                    to_delete.append(conv_id)
            
            deleted_count = 0
            for conv_id in to_delete:
                if self.delete_conversation(conv_id):
                    deleted_count += 1
            
            if deleted_count > 0:
                print(f"🧹 清理了 {deleted_count} 个旧对话")
            
            return deleted_count
        except Exception as e:
            print(f"⌘ 清理旧对话失败: {e}")
            return 0
