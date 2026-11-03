"""对话级自适应压缩阈值：把固定开销计入自动压缩触发点。

问题：上下文占用 = 固定开销（系统提示词 / 工具定义 / 技能与记忆等）+ 对话内容。
直接用“总占用”和配置的压缩阈值比较，固定开销会吃掉本该留给对话的额度：
设置 250k、固定开销 50k 时，实际只剩 200k 可用，压缩会提前触发。

做法：在对话第一次请求、以及每次深度压缩后的第一次请求完成时，把当次请求的
input tokens 记为基线（此时历史几乎为空，≈ 固定开销 + 首条输入），
此后触发点 = 基线 + 配置阈值。基线记录在对话文件的 metadata 里。

字段（都存放在 metadata）：
- context_baseline_tokens：固定开销基线
- context_baseline_model_key：测量时的模型（切换模型即失效）
- context_baseline_at：测量时间
- context_baseline_pending：待重测标记（新对话创建、深度压缩后置位）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping, Optional

BASELINE_KEY = "context_baseline_tokens"
BASELINE_MODEL_KEY = "context_baseline_model_key"
BASELINE_AT_KEY = "context_baseline_at"
PENDING_KEY = "context_baseline_pending"


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def baseline_tokens(metadata: Optional[Mapping[str, Any]]) -> int:
    """当前对话记录的固定开销基线；未测量时为 0。"""
    if not isinstance(metadata, Mapping):
        return 0
    return _as_int(metadata.get(BASELINE_KEY))


def effective_trigger(metadata: Optional[Mapping[str, Any]], configured_trigger: Any) -> int:
    """有效压缩触发点 = 配置阈值 + 固定开销基线。"""
    return _as_int(configured_trigger) + baseline_tokens(metadata)


def pending_metadata() -> Dict[str, Any]:
    """新对话创建 / 深度压缩完成后：标记基线待重测。

    保留旧的基线值直到重新测量，避免压缩后短时间内阈值跳回裸配置值。
    """
    return {PENDING_KEY: True}


def invalidated_metadata() -> Dict[str, Any]:
    """模型切换：固定开销随模型变化，清空基线并等待下次请求重测。"""
    return {
        BASELINE_KEY: None,
        BASELINE_MODEL_KEY: None,
        BASELINE_AT_KEY: None,
        PENDING_KEY: True,
    }


def record_baseline(
    metadata: Optional[Mapping[str, Any]],
    current_context_tokens: Any,
) -> Optional[Dict[str, Any]]:
    """待重测时返回要写入的元数据更新，否则返回 None。

    只在请求实际用掉 token（>0）时记录；模型从请求当时的对话 metadata 取。
    """
    if not isinstance(metadata, Mapping) or not metadata.get(PENDING_KEY):
        return None
    tokens = _as_int(current_context_tokens)
    if tokens <= 0:
        return None
    return {
        BASELINE_KEY: tokens,
        BASELINE_MODEL_KEY: metadata.get("model_key") or None,
        BASELINE_AT_KEY: datetime.now().isoformat(),
        PENDING_KEY: False,
    }


def effective_trigger_for(context_manager: Any, conversation_id: Optional[str],
                          configured_trigger: Any) -> int:
    """按对话取有效阈值；非当前对话或缺少元数据时回退配置值。"""
    configured = _as_int(configured_trigger)
    if context_manager is None:
        return configured
    current = getattr(context_manager, "current_conversation_id", None)
    if conversation_id and current and str(conversation_id) != str(current):
        return configured
    return effective_trigger(getattr(context_manager, "conversation_metadata", None), configured)
