"""对话级深压缩阈值：创建时快照，之后只读对话文件里的记录。

背景：原先每轮都从个人配置读取 `deep_compress_trigger_tokens`（Chat 读
`chat_deep_compress_trigger_tokens`），于是用户中途改全局设置会立刻改变所有
正在进行的对话的压缩时机。改为对话级快照后：

- 创建对话文件时读一次个人配置，写入 metadata 快照；
- 之后触发判断、上下文分母、前端圆环一律以对话记录为准；
- 用户在圆环卡片里改过则标记为 custom；
- 老对话缺字段时按「missing」处理，读取回退到当前配置，由前端在空闲时补写快照。

Agent 与 Chat 共用同一组字段，初值分别来自各自的个人设置。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

MIN_TOKENS = 1_000
MAX_TOKENS = 2_000_000

LIMIT_KEY = "compress_limit_tokens"
SOURCE_KEY = "compress_limit_source"

SOURCE_SNAPSHOT = "snapshot"
SOURCE_CUSTOM = "custom"
SOURCE_MISSING = "missing"


def parse_tokens(value: Any) -> Optional[int]:
    """把输入规范成合法阈值；非法（含 bool、越界、非整数）返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        return None
    if tokens < MIN_TOKENS or tokens > MAX_TOKENS:
        return None
    return tokens


def limit_tokens(metadata: Optional[Mapping[str, Any]]) -> Optional[int]:
    if not isinstance(metadata, Mapping):
        return None
    return parse_tokens(metadata.get(LIMIT_KEY))


def source(metadata: Optional[Mapping[str, Any]]) -> str:
    """对话记录的来源：custom / snapshot / missing（缺字段）。"""
    if limit_tokens(metadata) is None:
        return SOURCE_MISSING
    if isinstance(metadata, Mapping) and metadata.get(SOURCE_KEY) == SOURCE_CUSTOM:
        return SOURCE_CUSTOM
    return SOURCE_SNAPSHOT


def resolved(metadata: Optional[Mapping[str, Any]], configured: Any) -> int:
    """对话记录优先，缺失时回退个人配置（老对话保持旧行为）。"""
    recorded = limit_tokens(metadata)
    if recorded is not None:
        return recorded
    fallback = parse_tokens(configured)
    if fallback is not None:
        return fallback
    return 0


def resolved_for(context_manager: Any, conversation_id: Optional[str],
                 configured: Any) -> int:
    """只在查询的是当前对话时使用对话记录，避免把别的对话的阈值用到本轮。"""
    if context_manager is None:
        return resolved(None, configured)
    current = getattr(context_manager, "current_conversation_id", None)
    if conversation_id and current and str(conversation_id) != str(current):
        return resolved(None, configured)
    return resolved(getattr(context_manager, "conversation_metadata", None), configured)


def snapshot_update(tokens: Any) -> dict:
    value = parse_tokens(tokens)
    if value is None:
        raise ValueError("compress limit snapshot requires a valid token count")
    return {LIMIT_KEY: value, SOURCE_KEY: SOURCE_SNAPSHOT}


def custom_update(tokens: Any) -> dict:
    value = parse_tokens(tokens)
    if value is None:
        raise ValueError(f"compress limit must be an integer from {MIN_TOKENS} to {MAX_TOKENS}")
    return {LIMIT_KEY: value, SOURCE_KEY: SOURCE_CUSTOM}


def configured_for(data_dir: Any, kind: str = "agent") -> Optional[int]:
    """从个人配置取当前默认阈值（Agent / Chat 各自的字段）。"""
    try:
        from modules.personalization_manager import (
            load_personalization_config, resolve_context_compression_settings,
        )

        prefs = load_personalization_config(data_dir)
        if kind == "chat":
            return parse_tokens(prefs.get("chat_deep_compress_trigger_tokens"))
        return parse_tokens(resolve_context_compression_settings(prefs)["deep_trigger_tokens"])
    except Exception:
        return None
