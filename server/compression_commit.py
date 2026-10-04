"""压缩只提交摘要对应的历史前缀，消息、引导语和元信息一次落盘。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any
import uuid


def check_compression_cancelled() -> None:
    """检查当前任务的取消信号，其他对话的停止不影响本次压缩。"""
    from server.state import stop_flags

    try:
        task = asyncio.current_task()
    except RuntimeError:
        return
    if task is None:
        return
    # cancelling() 是 Python 3.11 新增；3.9 仍由停止标记和 await 取消生效。
    cancelling = getattr(task, "cancelling", None)
    if (callable(cancelling) and cancelling()) or any(
        entry.get("task") is task and entry.get("stop")
        for entry in list(stop_flags.values()) if isinstance(entry, dict)
    ):
        raise asyncio.CancelledError()


def _same_message(current: Any, original: Any) -> bool:
    if not isinstance(current, dict) or not isinstance(original, dict):
        return current == original
    fields = ("role", "content", "tool_calls", "tool_call_id", "message_id",
              "images", "videos", "media_refs", "reasoning_content")
    return all(current.get(key) == original.get(key) for key in fields)


def commit_compression(
    cm: Any,
    manager: Any,
    conversation_id: str,
    snapshot: list,
    updates: dict,
    *,
    round_index: int,
    now: str,
    guide_message: str = "",
) -> int:
    """同步提交区没有 await：取消发生在提交前或完整提交之后。"""
    check_compression_cancelled()
    with manager._io_lock:
        data = manager.load_conversation(conversation_id)
        if not data:
            raise RuntimeError("compression conversation disappeared")
        messages = deepcopy(data.get("messages") or [])
        # 历史被编辑/回溯时不提交过期摘要；允许快照之后追加新消息。
        if len(messages) < len(snapshot) or any(
            not _same_message(current, original)
            for current, original in zip(messages, snapshot)
        ):
            raise RuntimeError("compression history changed while generating summary")
        marked = 0
        for message in messages[:len(snapshot)]:
            if not isinstance(message, dict):
                continue
            metadata = message.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                message["metadata"] = metadata
            if metadata.get("deep_compacted"):
                continue
            metadata.update(
                deep_compacted=True,
                deep_compacted_round=round_index,
                deep_compacted_at=now,
            )
            marked += 1
        if guide_message:
            messages.append({
                "role": "user",
                "content": guide_message,
                "timestamp": now,
                "message_id": str(uuid.uuid4()),
                "metadata": {"message_source": "compression_handoff"},
            })
        data["messages"] = messages
        metadata = data.setdefault("metadata", {})
        metadata.update(updates)
        metadata["total_messages"] = len(messages)
        stats = data.setdefault("token_statistics", {})
        stats.update(current_context_tokens=0, cache_cold_start_pending=True, updated_at=now)
        data["updated_at"] = now
        check_compression_cancelled()
        # 使用会抛出错误的原子写，禁止吞掉失败后继续更新内存。
        manager._atomic_write_json(
            manager._get_conversation_file_path(conversation_id),
            manager._validate_token_statistics(data),
        )
        cm.conversation_history = deepcopy(messages)
        cm.conversation_metadata = deepcopy(metadata)
        manager._update_index(conversation_id, data)
    return marked
