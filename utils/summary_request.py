"""Shared streaming summary requests; callers own context and history commits."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Optional

from config.paths import LOGS_DIR
from utils.log_rotation import append_line


class SummaryRequestError(RuntimeError):
    """An upstream API error, rather than an empty successful summary."""


def _safe_detail(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie)"
        r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;<>]+)",
        r"\1[redacted]", text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", text)
    return text[:1500]


def _api_error(error: Any) -> SummaryRequestError:
    if not isinstance(error, dict):
        return SummaryRequestError(_safe_detail(error) or "Summary API request failed")
    status = error.get("status_code")
    kind = error.get("error_type") or error.get("type")
    message = error.get("error_message") or error.get("message") or error.get("error_text")
    if isinstance(message, str):
        try:
            parsed = json.loads(message)
            nested = parsed.get("error", parsed) if isinstance(parsed, dict) else None
            if isinstance(nested, dict):
                kind = kind or nested.get("type") or nested.get("code")
                message = nested.get("message") or message
        except (ValueError, TypeError):
            pass
    parts = ["Summary API request failed"]
    if status is not None:
        parts.append(f"HTTP {status}")
    if kind:
        parts.append(_safe_detail(kind))
    if message:
        parts.append(_safe_detail(message))
    return SummaryRequestError(": ".join(parts))


async def request_summary(
    client: Any,
    messages: list[dict],
    tools: list[dict],
    *,
    check_cancelled: Optional[Callable[[], None]] = None,
    on_chunk: Optional[Callable[[dict], None]] = None,
    empty_message: str = "The model returned an empty summary",
) -> str:
    """Use an already configured client and always close its response stream.

    Keep tools/context decisions with the caller. This function never executes
    returned tool calls or mutates conversation history, and does not retry.
    """
    stream = None
    try:
        if check_cancelled:
            check_cancelled()
        stream = client.chat(messages, tools=tools, stream=True)
        pieces = []
        async for chunk in stream:
            if check_cancelled:
                check_cancelled()
            if not isinstance(chunk, dict):
                continue
            if on_chunk:
                on_chunk(chunk)
            if chunk.get("error"):
                raise _api_error(chunk["error"])
            for choice in chunk.get("choices") or []:
                if choice.get("index", 0) != 0:
                    continue
                content = (choice.get("delta") or {}).get("content")
                if isinstance(content, str):
                    pieces.append(content)
        result = "".join(pieces).strip()
        if not result:
            raise SummaryRequestError(empty_message)
        return result
    except Exception as exc:
        append_line(Path(LOGS_DIR) / "summary_requests.log", json.dumps({
            "time": time.time(), "event": "summary_request_failed",
            "model_key": getattr(client, "model_key", None),
            "error_class": type(exc).__name__, "error": _safe_detail(exc),
        }, ensure_ascii=False))
        raise
    finally:
        if stream is not None:
            await stream.aclose()
