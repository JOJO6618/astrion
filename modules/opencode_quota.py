"""OpenCode Go 订阅额度查询（``/zen/go/v1/usage``，2026-11-16）。

上游端点只返回三个窗口的「已使用百分比 + 重置时间」，不提供金额、套餐名
（Go / Go Plus / Go Teams）与历史明细；因此这里也只对外暴露同一形状，
不做任何推算。

约束：
- 只接受 base_url 指向 ``opencode.ai`` 的提供商连接（Go 与 Zen 同域，
  但只有 ``/zen/go`` 路径对应 Go 订阅额度）；其他提供商绝不携带其 key 请求。
- key 只在本进程内使用，接口层永远不下发；缓存键含 key 指纹，轮换 key 或
  断开重连后自动失效，不会读到上一个 key 的额度。
- 30 秒缓存（与 ChatGPT 订阅额度同口径），失败不写入缓存。
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

OPENCODE_HOST = "opencode.ai"
GO_PATH_MARKER = "/zen/go"
USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
USAGE_TTL_SECONDS = 30
REQUEST_TIMEOUT = httpx.Timeout(10, connect=5, pool=5)

# 上游窗口键 → 前端展示用的稳定标识。
WINDOW_ORDER = ("rolling", "weekly", "monthly")

_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]


def is_opencode_go_base(base_url: Optional[str]) -> bool:
    """base_url 是否指向 OpenCode Go 网关（够用即止，不做通配匹配）。"""
    try:
        parsed = urlparse(str(base_url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if (parsed.hostname or "").lower() != OPENCODE_HOST:
        return False
    return GO_PATH_MARKER in (parsed.path or "")


def _to_windows(payload: Any) -> list:
    """把上游 ``{"usage": {"rolling": {...}}}`` 转成有序窗口数组。"""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return []
    windows = []
    for key in WINDOW_ORDER:
        window = usage.get(key)
        if not isinstance(window, dict):
            continue
        percent = window.get("percent")
        if not isinstance(percent, (int, float)) or isinstance(percent, bool):
            continue
        windows.append(
            {
                "key": key,
                "percent": max(0, min(100, int(round(float(percent))))),
                "reset_at": window.get("resetsAt") or window.get("resets_at") or "",
                "status": window.get("status") or "",
            }
        )
    return windows


def _load_provider(provider_id: str) -> Tuple[Optional[dict], Optional[str]]:
    from modules.provider_manager import get_provider_manager

    manager = get_provider_manager()
    if not provider_id:
        provider_id, record = _first_go_provider(manager)
        if not record:
            return None, "provider_not_found"
    else:
        record = manager.get_provider(provider_id)
    if not isinstance(record, dict):
        return None, "provider_not_found"
    if not record.get("enabled", True):
        return None, "provider_disabled"
    if not is_opencode_go_base(record.get("base_url")):
        return None, "not_opencode_provider"
    if not str(record.get("api_key") or "").strip():
        return None, "credential_missing"
    return record, None


def _first_go_provider(manager) -> Tuple[str, Optional[dict]]:
    """未指定 provider_id 时（固定选 opencode 但当前模型不属任何 Go 连接）。"""
    try:
        records = manager._load()["providers"]
    except Exception:
        return "", None
    for pid, record in records.items():
        if not isinstance(record, dict):
            continue
        if not record.get("enabled", True):
            continue
        if not is_opencode_go_base(record.get("base_url")):
            continue
        if not str(record.get("api_key") or "").strip():
            continue
        return pid, record
    return "", None


def fetch_usage(provider_id: str, *, force: bool = False) -> Dict[str, Any]:
    """查询指定提供商连接的 Go 订阅额度；失败返回 ``success=False`` 与错误码。"""
    record, error = _load_provider(provider_id)
    if error:
        return {"success": False, "error": error, "provider_id": provider_id}

    api_key = str(record.get("api_key") or "").strip()
    fingerprint = _fingerprint(api_key)
    now = time.monotonic()

    with _lock:
        entry = _cache.get(provider_id)
        if (
            not force
            and entry
            and entry.get("fingerprint") == fingerprint
            and now - float(entry.get("fetched_at") or 0) < USAGE_TTL_SECONDS
        ):
            return {**entry["payload"]}

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(
                USAGE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
    except httpx.TimeoutException:
        return {"success": False, "error": "timeout", "provider_id": provider_id}
    except Exception:
        return {"success": False, "error": "network_error", "provider_id": provider_id}

    if response.status_code in (401, 403):
        return {"success": False, "error": "invalid_credential", "provider_id": provider_id}
    if response.status_code == 404:
        # 端点缺失说明该 key 不属于 Go 订阅（或上游路由变更）。
        return {"success": False, "error": "not_subscribed", "provider_id": provider_id}
    if response.status_code != 200:
        return {
            "success": False,
            "error": f"http_{response.status_code}",
            "provider_id": provider_id,
        }

    try:
        payload = response.json()
    except ValueError:
        return {"success": False, "error": "invalid_response", "provider_id": provider_id}

    windows = _to_windows(payload)
    if not windows:
        return {"success": False, "error": "invalid_response", "provider_id": provider_id}

    result = {
        "success": True,
        "source": "opencode",
        "provider_id": provider_id,
        "label": record.get("name") or record.get("base_name") or "OpenCode Go",
        "windows": windows,
        "fetched_at": int(time.time()),
    }
    with _lock:
        _cache[provider_id] = {
            "fingerprint": fingerprint,
            "fetched_at": time.monotonic(),
            "payload": result,
        }
    return {**result}


def invalidate(provider_id: str) -> None:
    with _lock:
        _cache.pop(provider_id, None)


__all__ = ["fetch_usage", "is_opencode_go_base", "invalidate"]
