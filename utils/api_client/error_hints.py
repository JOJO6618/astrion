"""API 错误人话提示 helper。

识别「地区封锁 / 网络拦截」类上游错误，把误导性原始文案
（如 ocgo 网关包装的 "Upstream response was not valid JSON"）
替换为用户可理解、可行动的提示。

典型命中场景：
- OpenAI 结构化拒绝：error.code = unsupported_country_region_territory
- ocgo 网关包装：上游（xAI 等）对受限 IP 返回 Cloudflare HTML 拦截页，
  网关解析 JSON 失败，包装为 server_error: Upstream response was not valid JSON
- 直连官方 API 被 Cloudflare 拦：error_text 为 HTML 拦截页（非 JSON）
"""

from typing import Any, Dict, Optional

from modules.i18n import tr

# 确定信号：OpenAI 结构化地区封锁
_DEFINITE_MARKERS = (
    "unsupported_country_region_territory",
    "Country, region, or territory not supported",
)

# 强疑似信号：网关包装的上游非 JSON 响应（国内 IP 场景基本是拦截页，
# 但也可能是上游临时故障，文案需留余地）
_SUSPECTED_MESSAGE_MARKERS = (
    "Upstream response was not valid JSON",
)

# Cloudflare HTML 拦截页特征（error_text 非 JSON 时的识别）
_CLOUDFLARE_MARKERS = (
    "error code: 10",      # 1010/1020 等拦截页
    "Attention Required!",  # Cloudflare challenge 页标题
    "Sorry, you have been blocked",
    "cf-error-code",
)


def _short_detail(text: Optional[str], limit: int = 120) -> str:
    """原始错误截断（去换行），附在提示后保留排障线索。"""
    if not text:
        return ""
    squashed = " ".join(str(text).split())
    return squashed[:limit]


def region_block_hint(
    status_code: Optional[int],
    error_text: Optional[str],
    err: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """识别地区封锁/拦截类错误，命中返回人话提示，未命中返回 None。

    Args:
        status_code: HTTP 状态码（可为 None）。
        error_text: 上游原始响应体（可为非 JSON，如 HTML 拦截页）。
        err: 从 error_text 解析出的 error 子对象（解析失败传 None）。
    """
    try:
        err = err if isinstance(err, dict) else {}
        haystack = " ".join(
            str(err.get(k) or "") for k in ("type", "code", "message")
        )

        # 1) 确定：OpenAI 结构化地区封锁
        if any(m in haystack for m in _DEFINITE_MARKERS):
            return tr(
                "api_client.region_blocked",
                detail=_short_detail(err.get("message") or error_text),
            )

        # 2) 强疑似：网关包装的上游无效响应
        message = str(err.get("message") or "")
        if any(m in message for m in _SUSPECTED_MESSAGE_MARKERS):
            return tr(
                "api_client.region_blocked_suspected",
                detail=_short_detail(message),
            )

        # 3) 强疑似：Cloudflare HTML 拦截页（直连官方 API 场景）
        text = (error_text or "").strip()
        if text:
            head = text[:200].lower()
            html_like = head.startswith(("<!doctype", "<html"))
            if any(m in text for m in _CLOUDFLARE_MARKERS) or (
                status_code == 403 and html_like
            ):
                return tr(
                    "api_client.region_blocked_suspected",
                    detail=_short_detail(text),
                )
    except Exception:
        return None
    return None
