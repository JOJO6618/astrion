"""Responses 正文／函数调用的接收计时，与摘要和参数的展示缓冲分离。"""
from __future__ import annotations

import time
from typing import Any, Callable

from utils.token_usage import normalize_usage_payload


class ResponsesOutputTimer:
    """合并原始输出事件的时间区间，避免并行工具重复累计同一段时间。"""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._item_keys: dict[str, str] = {}
        self._active: set[tuple[str, str]] = set()
        self._started_at: float | None = None
        self._elapsed = 0.0
        self._completed = False

    def _item_key(self, data: dict[str, Any]) -> str:
        item = data.get("item") or {}
        item_id = str(data.get("item_id") or item.get("id") or "")
        index = data.get("output_index")
        if item_id in self._item_keys:
            return self._item_keys[item_id]
        key = f"index:{index}" if index is not None else f"id:{item_id}"
        if item_id:
            self._item_keys[item_id] = key
        return key

    def _start(self, key: tuple[str, str], now: float) -> None:
        if not self._active:
            self._started_at = now
        self._active.add(key)

    def _close(self, keys: set[tuple[str, str]], now: float) -> None:
        self._active.difference_update(keys)
        if not self._active and self._started_at is not None:
            self._elapsed += max(0.0, now - self._started_at)
            self._started_at = None

    def observe(self, data: dict[str, Any]) -> None:
        """在协议转换的任何 yield／缓冲前调用；不把推理事件算作输出。"""
        event = str(data.get("type") or "")
        now = self._clock()
        key = self._item_key(data)
        item = data.get("item") or {}
        text_key = (key, f"text:{data.get('content_index', 0)}")
        tool_key = (key, "function")

        if event == "response.output_item.added":
            if item.get("type") == "function_call" and item.get("name"):
                # 工具名已是实际输出，参数仍在协议转换层缓冲。
                self._start(tool_key, now)
            elif item.get("type") == "reasoning":
                self._close(set(self._active), now)
        elif event in ("response.output_text.delta", "response.refusal.delta"):
            if data.get("delta"):
                self._start(text_key, now)
        elif event == "response.function_call_arguments.delta":
            if data.get("delta"):
                self._start(tool_key, now)
        elif event in ("response.output_text.done", "response.refusal.done"):
            self._close({text_key}, now)
        elif event == "response.function_call_arguments.done":
            self._close({tool_key}, now)
        elif event == "response.output_item.done":
            self._close({part for part in self._active if part[0] == key}, now)
        elif event.startswith("response.reasoning"):
            self._close(set(self._active), now)
        elif event == "response.completed":
            # 兼容未单独发送 done 的网关；只收尾已看到真实输出的区间。
            self._close(set(self._active), now)
            self._completed = True

    def summary(self) -> dict[str, int]:
        duration_ms = round(self._elapsed * 1000)
        if not self._completed or duration_ms <= 0:
            return {}
        return {"duration_ms": duration_ms}


def build_responses_speed_sample(
    usage: dict[str, Any], timing: dict[str, Any],
) -> dict[str, int]:
    """缺推理明细／有效计时则跳过，不将未知推理 token 默认为零。"""
    normalized = normalize_usage_payload(usage) or {}
    output_tokens = normalized.get("completion_tokens", 0)
    reasoning_tokens = normalized.get("reasoning_tokens")
    duration_ms = timing.get("duration_ms")
    if (
        reasoning_tokens is None
        or reasoning_tokens > output_tokens
        or not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms <= 0
        or output_tokens <= reasoning_tokens
    ):
        return {}
    return {
        "output_tokens": output_tokens - reasoning_tokens,
        "duration_ms": duration_ms,
    }
