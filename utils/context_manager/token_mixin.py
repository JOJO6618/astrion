# utils/context_manager.py - 上下文管理器（集成对话持久化和Token统计）

import os
import json
import base64
import logging
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

logger = logging.getLogger(__name__)

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


class TokenMixin:
    """ContextManager token mixin 能力 mixin。"""

    def _token_totals_path(self) -> Path:
        return self.data_dir / "token_totals.json"

    def _load_token_totals(self) -> Dict[str, Any]:
        path = self._token_totals_path()
        if not path.exists():
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "updated_at": None,
            }
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh) or {}
            return {
                "input_tokens": int(payload.get("input_tokens") or payload.get("total_input_tokens") or 0),
                "output_tokens": int(payload.get("output_tokens") or payload.get("total_output_tokens") or 0),
                "total_tokens": int(payload.get("total_tokens") or 0),
                "cached_input_tokens": int(payload.get("cached_input_tokens") or payload.get("total_cached_input_tokens") or 0),
                "updated_at": payload.get("updated_at"),
            }
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"[TokenStats] 读取累计Token失败: {exc}")
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "updated_at": None,
            }

    def _save_token_totals(self, data: Dict[str, Any]):
        path = self._token_totals_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def _increment_workspace_token_totals(self, input_tokens: int, output_tokens: int, total_tokens: int, cached_input_tokens: int = 0):
        if input_tokens <= 0 and output_tokens <= 0 and total_tokens <= 0:
            return
        snapshot = self._load_token_totals()
        snapshot["input_tokens"] = snapshot.get("input_tokens", 0) + max(0, int(input_tokens))
        snapshot["output_tokens"] = snapshot.get("output_tokens", 0) + max(0, int(output_tokens))
        snapshot["total_tokens"] = snapshot.get("total_tokens", 0) + max(0, int(total_tokens))
        snapshot["cached_input_tokens"] = snapshot.get("cached_input_tokens", 0) + max(0, int(cached_input_tokens))
        snapshot["updated_at"] = datetime.now().isoformat()
        self._save_token_totals(snapshot)

    def reset_work_segment_speed_samples(self) -> None:
        """新工作段开始：清空本段 tokens/s 样本。压缩续接沿用同一段，不调用本方法。"""
        self._work_segment_speed_samples = []

    def record_work_segment_speed_sample(self, output_tokens: int, duration_ms: int) -> None:
        """记录单次主智能体 API 请求的「输出 token + 耗时」，供段结束时算加权平均。"""
        try:
            output_value = max(0, int(output_tokens))
            duration_value = int(duration_ms)
        except (TypeError, ValueError):
            return
        if output_value <= 0 or duration_value <= 0:
            return
        samples = getattr(self, "_work_segment_speed_samples", None)
        if not isinstance(samples, list):
            samples = []
            self._work_segment_speed_samples = samples
        samples.append({"output_tokens": output_value, "duration_ms": duration_value})

    def consume_work_segment_speed_summary(self) -> Optional[Dict[str, Any]]:
        """取出本段速度汇总并清空样本；样本为空或全无有效数据时返回 None。"""
        samples = getattr(self, "_work_segment_speed_samples", None)
        self._work_segment_speed_samples = []
        if not isinstance(samples, list) or not samples:
            return None
        total_output = sum(int(item.get("output_tokens") or 0) for item in samples)
        total_ms = sum(int(item.get("duration_ms") or 0) for item in samples)
        if total_output <= 0 or total_ms <= 0:
            return None
        return {
            "avg_tps": round(total_output / (total_ms / 1000.0), 1),
            "output_tokens": total_output,
            "duration_ms": total_ms,
            "requests": len(samples),
            "updated_at": datetime.now().isoformat(),
        }

    def apply_usage_statistics(self, usage: Dict[str, Any], *, price=None,
                               request_id=None, conversation_id=None,
                               duration_ms: Optional[int] = None,
                               ttft_ms: Optional[int] = None,
                               speed_sample: Optional[Dict[str, int]] = None) -> bool:
        """Apply full usage/cost; an explicit speed sample overrides only tokens/s."""
        target_id = conversation_id or self.current_conversation_id
        actor = None
        previous = {}
        if price is not None and request_id and target_id:
            from modules.api_pricing import calculate_cost
            from modules.conversation_costs import actor_statistics, record_request
            manager = getattr(self, '_get_conversation_manager_for_id', lambda _: self.conversation_manager)(target_id)
            previous = manager.get_token_statistics(target_id) or {}
            cost_entry = calculate_cost(usage, price)
            # 生成耗时与首包等待随账本复用存储（tokens/s 的明细来源，
            # 历史请求缺这些字段按未记录处理）
            for _key, _value in (('duration_ms', duration_ms), ('ttft_ms', ttft_ms)):
                if _value is None:
                    continue
                try:
                    cost_entry[_key] = max(0, int(_value))
                except (TypeError, ValueError):
                    pass
            if speed_sample is not None:
                # Responses 使用独立非思考样本；空字典明确表示无法准确采样。
                cost_entry['speed_sample'] = dict(speed_sample)
            committed = record_request(
                self.data_dir, target_id, request_id, 'main', '', 'main', cost_entry,
                historical_unpriced=bool(previous.get('total_input_tokens')),
                baseline={'input_tokens': previous.get('total_input_tokens', 0),
                          'output_tokens': previous.get('total_output_tokens', 0),
                          'total_tokens': previous.get('total_tokens', 0),
                          'cached_input_tokens': previous.get('total_cached_input_tokens', 0)},
            )
            actor = actor_statistics(self.data_dir, target_id, 'main')
            if not committed and actor['total_tokens'] == previous.get('total_tokens', 0):
                return True

        normalized_usage = normalize_usage_payload(usage) or {}
        prompt_tokens = int(normalized_usage.get("prompt_tokens") or 0)
        completion_tokens = int(normalized_usage.get("completion_tokens") or 0)
        total_tokens = int(normalized_usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        # 当前上下文长度优先取专用字段；缺失时回退到 prompt_tokens
        current_context_tokens = int(normalized_usage.get("current_context_tokens") or prompt_tokens)
        # 本次请求命中缓存的输入 token（全厂商字段已在 normalize 中归一化）
        cached_input_tokens = int(normalized_usage.get("cached_input_tokens") or 0)
        if actor:
            # Repair a previous ledger-only commit after interruption without
            # counting a repeated request twice in the conversation token totals.
            prompt_tokens = max(0, actor['input_tokens'] - previous.get('total_input_tokens', 0))
            completion_tokens = max(0, actor['output_tokens'] - previous.get('total_output_tokens', 0))
            total_tokens = max(0, actor['total_tokens'] - previous.get('total_tokens', 0))
            cached_input_tokens = max(0, actor['cached_input_tokens'] - previous.get('total_cached_input_tokens', 0))

        try:
            self._increment_workspace_token_totals(prompt_tokens, completion_tokens, total_tokens, cached_input_tokens)
        except Exception as exc:
            print(f"[TokenStats] 无法写入累计Token: {exc}")

        # None 沿用 Chat 全输出口径；显式样本仅影响速度，不扣减计费用量。
        # Responses 空样本跳过，不能回退到包含思考的 output／通用耗时。
        sample = speed_sample if speed_sample is not None else {
            'output_tokens': completion_tokens, 'duration_ms': duration_ms,
        }
        if sample.get('duration_ms'):
            try:
                self.record_work_segment_speed_sample(
                    sample.get('output_tokens', 0), sample['duration_ms'],
                )
            except Exception as exc:
                logger.warning(f"[TokenStats] 记录速度样本失败: {exc}")

        if not target_id:
            print("⚠️ 没有当前对话ID，跳过usage统计更新")
            return False
        
        try:
            # 路由到正确的 conversation_manager（与 get_conversation_token_statistics 一致）
            # 多智能体对话 ID 存储在 multi_agent_conversation_manager 中，
            # 直接使用 self.conversation_manager 会找不到对话导致统计写入失败
            target_manager = getattr(
                self, "_get_conversation_manager_for_id", lambda _: self.conversation_manager
            )(target_id)
            success = target_manager.update_token_statistics(
                target_id,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                current_context_tokens=current_context_tokens,
                cached_input_tokens=cached_input_tokens,
            )

            if success:
                self.safe_broadcast_token_update()

            return success
        except Exception as e:
            print(f"更新usage统计失败: {e}")
            return False

    def record_unavailable_usage(self, *, price, request_id, conversation_id=None):
        """Keep successful calls lacking usage explicit without resetting context."""
        from modules.api_pricing import calculate_cost
        from modules.conversation_costs import record_request
        target_id = conversation_id or self.current_conversation_id
        if not target_id:
            return
        manager = getattr(self, '_get_conversation_manager_for_id', lambda _: self.conversation_manager)(target_id)
        previous = manager.get_token_statistics(target_id) or {}
        record_request(
            self.data_dir, target_id, request_id, 'main', '', 'main', calculate_cost(None, price),
            historical_unpriced=bool(previous.get('total_input_tokens')),
            baseline={'input_tokens': previous.get('total_input_tokens', 0),
                      'output_tokens': previous.get('total_output_tokens', 0),
                      'total_tokens': previous.get('total_tokens', 0),
                      'cached_input_tokens': previous.get('total_cached_input_tokens', 0)},
        )
        self.safe_broadcast_token_update()

    def get_conversation_token_statistics(self, conversation_id: str = None) -> Optional[Dict]:
        """
        获取指定对话的token统计
        
        Args:
            conversation_id: 对话ID，默认为当前对话
        
        Returns:
            Dict: Token统计信息
        """
        target_id = conversation_id or self.current_conversation_id
        if not target_id:
            return None

        # 路由到正确的 conversation_manager（支持多智能体会话独立存储）
        target_manager = getattr(
            self, "_get_conversation_manager_for_id", lambda _: self.conversation_manager
        )(target_id)
        stats = target_manager.get_token_statistics(target_id)
        if stats is not None:
            from modules.conversation_costs import summarize
            from modules.exchange_rates import get_rate
            terminal = getattr(self, 'main_terminal', None)
            sub_manager = getattr(terminal, 'sub_agent_manager', None)
            tasks = getattr(sub_manager, 'tasks', {}).copy().values()
            children = [task for task in tasks if task.get('conversation_id') == target_id]
            stats['costs'] = summarize(self.data_dir, target_id,
                                      main_input=stats.get('total_input_tokens', 0),
                                      sub_agents=children)
            stats['costs']['exchange_rate'] = get_rate()
        return stats

    def get_current_context_tokens(self, conversation_id: str = None) -> int:
        """
        获取最近一次请求的上下文token数量
        """
        stats = self.get_conversation_token_statistics(conversation_id)
        if not stats:
            return 0
        return stats.get("current_context_tokens", 0)

    def safe_broadcast_token_update(self, extra: Optional[Dict[str, Any]] = None):
        """安全的token更新广播（只广播累计统计，不重新计算）

        extra：附加字段（如工作段 tokens/s）合并进同一事件，不新增事件类型。
        """
        try:
            # 检查是否有回调函数
            if not hasattr(self, '_web_terminal_callback'):
                return

            if not self._web_terminal_callback:
                return

            if not self.current_conversation_id:
                return

            # 只获取已有的累计token统计，不重新计算
            cumulative_stats = self.get_conversation_token_statistics()
            
            # 准备广播数据
            broadcast_data = {
                'conversation_id': self.current_conversation_id,
                'cumulative_input_tokens': cumulative_stats.get("total_input_tokens", 0) if cumulative_stats else 0,
                'cumulative_output_tokens': cumulative_stats.get("total_output_tokens", 0) if cumulative_stats else 0,
                'cumulative_total_tokens': cumulative_stats.get("total_tokens", 0) if cumulative_stats else 0,
                'cumulative_cached_input_tokens': cumulative_stats.get("total_cached_input_tokens", 0) if cumulative_stats else 0,
                'cache_exempt_input_tokens': cumulative_stats.get("cache_exempt_input_tokens", 0) if cumulative_stats else 0,
                'current_context_tokens': cumulative_stats.get("current_context_tokens", 0) if cumulative_stats else 0,
                'costs': cumulative_stats.get('costs') if cumulative_stats else None,
                'updated_at': datetime.now().isoformat()
            }

            # 广播到前端（extra 携带的附加字段并入同一事件）
            if isinstance(extra, dict):
                broadcast_data.update(extra)
            self._web_terminal_callback('token_update', broadcast_data)

        except Exception as e:
            logger.warning(f"[TokenStats] 广播token更新失败: {e}")

    def broadcast_work_segment_speed(self, message_id: Optional[str],
                                     summary: Optional[Dict[str, Any]]) -> None:
        """工作段结束：把本段 tokens/s 汇总随 token 更新事件下发，供前端即时渲染。

        任务完成时前端不会重载历史（只本地标记 work_timer），因此速度行必须靠事件送达；
        消息 id 指向铺点 user 消息，前端据此把 token_speed 写回本地消息 metadata。
        """
        if not summary or not message_id:
            return
        try:
            self.safe_broadcast_token_update(extra={
                'token_speed': {'message_id': message_id, **summary},
            })
        except Exception as exc:
            logger.warning(f"[TokenStats] 广播段速度失败: {exc}")
