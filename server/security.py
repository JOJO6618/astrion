"""安全相关工具：限流、CSRF、Socket Token、工具结果压缩等。"""
from __future__ import annotations
import hmac
import os
import secrets
import time
from typing import Dict, Any, Optional, Tuple
from flask import request, session, jsonify
from functools import wraps

from . import state


def _trusted_proxies() -> frozenset:
    """可信反向代理地址集合（仅这些来源的 X-Forwarded-For 才被采信）。

    默认不信任任何代理：服务直连时 XFF 可被客户端任意伪造，限流/锁定全部失效。
    部署在 nginx/CF 等反代之后时，用环境变量 ASTRION_TRUSTED_PROXIES 配置，
    逗号分隔，如 "127.0.0.1,::1"。
    """
    raw = os.environ.get("ASTRION_TRUSTED_PROXIES", "")
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


_TRUSTED_PROXIES = _trusted_proxies()

# 内存限流桶/失败计数表的硬性上限，防止伪造来源导致内存无限增长（反向 DoS）。
_RATE_LIMIT_MAX_KEYS = 20000
_FAILURE_TRACKER_MAX_KEYS = 20000


# 便捷别名
def get_client_ip() -> str:
    """获取客户端IP。仅当直接对端是可信代理时才采信 X-Forwarded-For。"""
    remote = request.remote_addr or "unknown"
    if remote in _TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip() or remote
    return remote


def resolve_identifier(scope: str = "ip", identifier: Optional[str] = None, kwargs: Optional[Dict[str, Any]] = None) -> str:
    if identifier:
        return identifier
    if scope == "user":
        if kwargs:
            username = kwargs.get('username')
            if username:
                return username
        from .auth import get_current_username  # 局部导入避免循环
        username = get_current_username()
        if username:
            return username
    return get_client_ip()


def check_rate_limit(action: str, limit: int, window_seconds: int, identifier: Optional[str]) -> Tuple[bool, int]:
    """简单滑动窗口限频。"""
    bucket_key = f"{action}:{identifier or 'anonymous'}"
    bucket = state.RATE_LIMIT_BUCKETS[bucket_key]
    now = time.time()
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = window_seconds - int(now - bucket[0])
        return True, max(retry_after, 1)
    bucket.append(now)
    _gc_rate_limit_buckets(now)
    return False, 0


def _gc_rate_limit_buckets(now: float) -> None:
    """限流桶回收：超过硬上限时清掉已空置/全部过期的桶，防伪造来源刷爆内存。"""
    buckets = state.RATE_LIMIT_BUCKETS
    if len(buckets) <= _RATE_LIMIT_MAX_KEYS:
        return
    for key in list(buckets.keys()):
        bucket = buckets.get(key)
        if not bucket:
            buckets.pop(key, None)
    if len(buckets) > _RATE_LIMIT_MAX_KEYS:
        # 极端情况下（全部桶都活跃）直接整体清空，宁可误伤正常限流状态也不撑爆内存
        buckets.clear()


def gc_failure_trackers() -> None:
    """失败计数表回收：超过硬上限时清掉已解除锁定的条目。"""
    trackers = state.FAILURE_TRACKERS
    if len(trackers) <= _FAILURE_TRACKER_MAX_KEYS:
        return
    now = time.time()
    for key in list(trackers.keys()):
        entry = trackers.get(key) or {}
        if not entry.get("blocked_until") or entry["blocked_until"] <= now:
            trackers.pop(key, None)
    if len(trackers) > _FAILURE_TRACKER_MAX_KEYS:
        trackers.clear()


def rate_limited(action: str, limit: int, window_seconds: int, scope: str = "ip", error_message: Optional[str] = None):
    """装饰器：为路由增加速率限制。"""
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            identifier = resolve_identifier(scope, kwargs=kwargs)
            limited, retry_after = check_rate_limit(action, limit, window_seconds, identifier)
            if limited:
                message = error_message or "请求过于频繁，请稍后再试。"
                return jsonify({
                    "success": False,
                    "error": message,
                    "retry_after": retry_after
                }), 429
            return func(*args, **kwargs)
        return wrapped
    return decorator


def register_failure(action: str, limit: int, lock_seconds: int, scope: str = "ip", identifier: Optional[str] = None, kwargs: Optional[Dict[str, Any]] = None) -> int:
    """记录失败次数，超过阈值后触发锁定。"""
    gc_failure_trackers()
    ident = resolve_identifier(scope, identifier, kwargs)
    key = f"{action}:{ident}"
    now = time.time()
    entry = state.FAILURE_TRACKERS.setdefault(key, {"count": 0, "blocked_until": 0})
    blocked_until = entry.get("blocked_until", 0)
    if blocked_until and blocked_until > now:
        return int(blocked_until - now)
    entry["count"] = entry.get("count", 0) + 1
    if entry["count"] >= limit:
        entry["count"] = 0
        entry["blocked_until"] = now + lock_seconds
        return lock_seconds
    return 0


def is_action_blocked(action: str, scope: str = "ip", identifier: Optional[str] = None, kwargs: Optional[Dict[str, Any]] = None) -> Tuple[bool, int]:
    ident = resolve_identifier(scope, identifier, kwargs)
    key = f"{action}:{ident}"
    entry = state.FAILURE_TRACKERS.get(key)
    if not entry:
        return False, 0
    now = time.time()
    blocked_until = entry.get("blocked_until", 0)
    if blocked_until and blocked_until > now:
        return True, int(blocked_until - now)
    return False, 0


def clear_failures(action: str, scope: str = "ip", identifier: Optional[str] = None, kwargs: Optional[Dict[str, Any]] = None):
    ident = resolve_identifier(scope, identifier, kwargs)
    key = f"{action}:{ident}"
    state.FAILURE_TRACKERS.pop(key, None)


def get_csrf_token(force_new: bool = False) -> str:
    token = session.get(state.CSRF_SESSION_KEY)
    if force_new or not token:
        token = secrets.token_urlsafe(32)
        session[state.CSRF_SESSION_KEY] = token
    return token


def requires_csrf_protection(path: str) -> bool:
    # Bearer Token 请求走无状态认证，跳过 CSRF
    auth_header = (request.headers.get("Authorization") or "").lower()
    if auth_header.startswith("bearer "):
        return False
    # API v1 统一跳过 CSRF；若未携带 Authorization，将由鉴权层返回 401
    if path.startswith("/api/v1/"):
        return False
    if path in state.CSRF_EXEMPT_PATHS:
        return False
    if path in state.CSRF_PROTECTED_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in state.CSRF_PROTECTED_PREFIXES)


def validate_csrf_request() -> bool:
    expected = session.get(state.CSRF_SESSION_KEY)
    provided = request.headers.get(state.CSRF_HEADER_NAME) or request.form.get("csrf_token")
    if not expected or not provided:
        return False
    try:
        return hmac.compare_digest(str(provided), str(expected))
    except Exception:
        return False


def prune_socket_tokens(now: Optional[float] = None):
    current = now or time.time()
    for token, meta in list(state.pending_socket_tokens.items()):
        if meta.get("expires_at", 0) <= current:
            state.pending_socket_tokens.pop(token, None)


def consume_socket_token(token_value: Optional[str], username: Optional[str]) -> bool:
    if not token_value or not username:
        return False
    prune_socket_tokens()
    token_meta = state.pending_socket_tokens.pop(token_value, None)
    if not token_meta:
        return False
    if token_meta.get("username") != username:
        return False
    if token_meta.get("expires_at", 0) <= time.time():
        return False
    fingerprint = token_meta.get("fingerprint") or ""
    request_fp = (request.headers.get("User-Agent") or "")[:128]
    if fingerprint and request_fp and not hmac.compare_digest(fingerprint, request_fp):
        return False
    return True


def format_tool_result_notice(tool_name: str, tool_call_id: Optional[str], content: str) -> str:
    """将工具执行结果转为系统消息文本，方便在对话中回传。"""
    header = f"[工具结果] {tool_name}"
    if tool_call_id:
        header += f" (tool_call_id={tool_call_id})"
    body = (content or "").strip()
    if not body:
        body = "（无附加输出）"
    return f"{header}\n{body}"


def compact_web_search_result(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """提取 web_search 结果中前端展示所需的关键字段，避免持久化时丢失列表。"""
    if not isinstance(result_data, dict):
        return {"success": False, "error": "invalid search result"}

    compact: Dict[str, Any] = {
        "success": bool(result_data.get("success")),
        "summary": result_data.get("summary"),
        "query": result_data.get("query"),
        "filters": result_data.get("filters") or {},
        "total_results": result_data.get("total_results", 0)
    }

    items: list[Dict[str, Any]] = []
    for item in result_data.get("results") or []:
        if not isinstance(item, dict):
            continue
        items.append({
            "index": item.get("index"),
            "title": item.get("title") or item.get("name"),
            "url": item.get("url")
        })

    compact["results"] = items

    if not compact.get("success") and result_data.get("error"):
        compact["error"] = result_data.get("error")

    return compact

def attach_security_hooks(app):
    """注册 CSRF 校验与通用安全响应头。"""
    @app.before_request
    def _block_admin_static_for_non_admin():
        # Flask 内置 static 路由会优先于蓝图里的 /static/<path> 拦截，
        # 因此 admin_dashboard 静态资源必须在 before_request 层面拦截。
        path = request.path or ""
        if path.startswith("/static/admin_dashboard"):
            from .auth_helpers import get_current_user_record  # 局部导入避免循环
            user = get_current_user_record()
            if not user or getattr(user, "role", None) != "admin":
                from flask import abort
                abort(404)

    @app.before_request
    def _enforce_csrf_token():
        method = (request.method or "GET").upper()
        if method in state.CSRF_SAFE_METHODS:
            return
        if not requires_csrf_protection(request.path):
            return
        if validate_csrf_request():
            return
        return jsonify({"success": False, "error": "CSRF validation failed"}), 403

    @app.after_request
    def _apply_security_headers(response):
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if response.mimetype == "application/json":
            response.headers.setdefault("Cache-Control", "no-store")
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

__all__ = [
    "get_client_ip",
    "resolve_identifier",
    "check_rate_limit",
    "rate_limited",
    "register_failure",
    "is_action_blocked",
    "clear_failures",
    "get_csrf_token",
    "requires_csrf_protection",
    "validate_csrf_request",
    "prune_socket_tokens",
    "consume_socket_token",
    "format_tool_result_notice",
    "compact_web_search_result",
    "attach_security_hooks",
]
__all__ = [
    "get_client_ip",
    "resolve_identifier",
    "check_rate_limit",
    "rate_limited",
    "register_failure",
    "is_action_blocked",
    "clear_failures",
    "get_csrf_token",
    "requires_csrf_protection",
    "validate_csrf_request",
    "prune_socket_tokens",
    "consume_socket_token",
    "format_tool_result_notice",
    "compact_web_search_result",
]
