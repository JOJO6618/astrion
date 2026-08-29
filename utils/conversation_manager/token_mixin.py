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


class TokenMixin:
    """ConversationManager token mixin 能力 mixin。"""

    def update_token_statistics(
        self,
        conversation_id: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        current_context_tokens: Optional[int] = None,
        cached_input_tokens: int = 0,
        cache_cold_start_pending: bool = False,
    ) -> bool:
        """
        更新对话的Token统计
        
        Args:
            conversation_id: 对话ID
            input_tokens: 输入Token数量
            output_tokens: 输出Token数量
            total_tokens: 本次请求的总Token数量（prompt+completion）
            current_context_tokens: 当前上下文长度（用于压缩阈值判断）
            cached_input_tokens: 本次请求命中缓存的输入Token数量
            cache_cold_start_pending: 置位「压缩后待判定」标记（深度压缩后调用时传入）
        
        Returns:
            bool: 更新是否成功
        """
        try:
            conversation_data = self.load_conversation(conversation_id)
            if not conversation_data:
                print(f"⚠️ 无法找到对话 {conversation_id}，跳过Token统计")
                return False
            
            # 确保Token统计结构存在
            if "token_statistics" not in conversation_data:
                conversation_data["token_statistics"] = self._initialize_token_statistics()
            
            # 更新统计数据
            token_stats = conversation_data["token_statistics"]

            # ── 冷启动豁免：首轮/压缩后首轮若未命中缓存，其输入属于“建立缓存”成本，
            # 豁免出命中率分母（cache_exempt_input_tokens）；若命中则说明缓存延续，正常处理。
            # 判断需在累加之前进行（首轮判定依赖累加前的 total_input_tokens 为 0）。
            if input_tokens > 0:
                is_first_call = token_stats.get("total_input_tokens", 0) == 0
                cold_start_pending = bool(token_stats.get("cache_cold_start_pending", False))
                if is_first_call or cold_start_pending:
                    if cached_input_tokens <= 0:
                        token_stats["cache_exempt_input_tokens"] = (
                            token_stats.get("cache_exempt_input_tokens", 0) + int(input_tokens)
                        )
                    # 压缩后首轮消费标记（首轮不涉及该标记，置 False 无副作用）
                    token_stats["cache_cold_start_pending"] = False

            token_stats["total_input_tokens"] = token_stats.get("total_input_tokens", 0) + input_tokens
            token_stats["total_output_tokens"] = token_stats.get("total_output_tokens", 0) + output_tokens
            token_stats["total_tokens"] = token_stats.get("total_tokens", 0) + total_tokens
            token_stats["total_cached_input_tokens"] = token_stats.get("total_cached_input_tokens", 0) + max(0, int(cached_input_tokens or 0))
            # 置位压缩后待判定标记（深度压缩重置统计时传入）
            if cache_cold_start_pending:
                token_stats["cache_cold_start_pending"] = True
            if current_context_tokens is None:
                # 兼容旧调用：未显式传入时，默认以输入 token 作为当前上下文长度
                current_context_tokens = input_tokens
            token_stats["current_context_tokens"] = max(0, int(current_context_tokens or 0))
            token_stats["updated_at"] = datetime.now().isoformat()
            
            # 保存更新
            self._save_conversation_file(conversation_id, conversation_data)
            
            print(f"📊 Token统计已更新: +{input_tokens}输入, +{output_tokens}输出 "
                  f"(总计: {token_stats['total_input_tokens']}输入, {token_stats['total_output_tokens']}输出)")
            
            return True
        except Exception as e:
            print(f"⌘ 更新Token统计失败 {conversation_id}: {e}")
            return False

    def get_token_statistics(self, conversation_id: str) -> Optional[Dict]:
        """
        获取对话的Token统计
        
        Args:
            conversation_id: 对话ID
        
        Returns:
            Dict: Token统计数据
        """
        try:
            conversation_data = self.load_conversation(conversation_id)
            if not conversation_data:
                return None
            
            validated = self._validate_token_statistics(conversation_data)
            token_stats = validated.get("token_statistics", {})
            
            result = {
                "total_input_tokens": token_stats.get("total_input_tokens", 0),
                "total_output_tokens": token_stats.get("total_output_tokens", 0),
                "total_tokens": token_stats.get("total_tokens", 0),
                "total_cached_input_tokens": token_stats.get("total_cached_input_tokens", 0),
                "cache_exempt_input_tokens": token_stats.get("cache_exempt_input_tokens", 0),
                "current_context_tokens": token_stats.get("current_context_tokens", 0),
                "updated_at": token_stats.get("updated_at"),
                "conversation_id": conversation_id
            }
            
            return result
        except Exception as e:
            print(f"⌘ 获取Token统计失败 {conversation_id}: {e}")
            return None

    def get_current_context_tokens(self, conversation_id: str) -> int:
        """获取最近一次请求的上下文token"""
        stats = self.get_token_statistics(conversation_id)
        if not stats:
            return 0
        return stats.get("current_context_tokens", 0)
