"""API 请求错误的分类与重试口径（主智能体流式循环与审核智能体共用）。

分类只看「错误本身的确定性信息」，不猜测意图：
- 额度/计费类错误单独归为 quota_exhausted，重试不会恢复；
- 认证、配置类错误归为 authentication / configuration，重试无意义；
- 429 / 408 / 409 / 425 / 5xx、超时、连接中断、断流归为 retryable；
- 其余未知错误保持 unknown_error，交给调用方按可重试与否的保守策略处理。

主智能体的重试循环在 server/chat_flow_stream_loop.py（带前端 error/stream_reset
事件与可停止等待）；审核智能体的重试循环在 modules/review_model_caller.py
（后台无 UI，固定间隔）。两边共用这里的判定，避免口径漂移。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "RETRYABLE",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_DELAY_SECONDS",
    "classify_error",
    "is_retryable_error",
]

RETRYABLE = "retryable"
# 与主智能体流式循环保持一致：最多 5 次尝试（1 次原始 + 4 次重试）、固定 10s 间隔。
RETRY_MAX_ATTEMPTS = 5
RETRY_DELAY_SECONDS = 10

_QUOTA_WORDS = (
    "insufficient_quota", "usage_limit", "usage limit", "out_of_usage", "out of usage",
    "outof usage", "outofusage", "credit balance", "insufficient balance", "quota_exceeded",
    "quota exceeded", "billing_hard_limit", "额度不足", "额度已用", "余额不足", "用量上限",
)
_AUTH_WORDS = ("authentication", "invalid_api_key", "unauthorized", "http 401", "http 403")
_CONFIG_WORDS = ("invalid_request", "context_overflow", "no_model", "not configured", "profile incomplete")
_RATE_WORDS = ("rate_limit", "rate limit", "http 429")
_NETWORK_WORDS = (
    "timeout", "timed out", "connection", "connecterror", "readerror", "writeerror",
    "protocolerror", "network", "disconnect", "断流", "超时", "连接", "网络",
)


def classify_error(error: Any) -> str:
    """Provider code/type first; message fallback for providers without codes."""
    data = error if isinstance(error, dict) else {"message": str(error)}
    code = " ".join(str(data.get(k) or "").lower() for k in ("error_code", "error_type", "code", "type"))
    text = code + " " + " ".join(
        str(data.get(k) or "").lower() for k in ("message", "error_message", "error_text")
    )
    if any(word in text for word in _QUOTA_WORDS):
        return "quota_exhausted"
    status = data.get("status_code")
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    if status in (401, 403) or any(word in text for word in _AUTH_WORDS):
        return "authentication"
    if status in (400, 404, 405, 413, 422) or any(word in text for word in _CONFIG_WORDS):
        return "configuration"
    if status == 429 or any(word in text for word in _RATE_WORDS):
        return RETRYABLE
    if status in (408, 409, 425) or (isinstance(status, int) and status >= 500):
        return RETRYABLE
    if any(word in text for word in _NETWORK_WORDS):
        return RETRYABLE
    # Unknown failures must remain visible and manually resumable, not spin forever.
    return "unknown_error"


def is_retryable_error(error: Any) -> bool:
    return classify_error(error) == RETRYABLE
