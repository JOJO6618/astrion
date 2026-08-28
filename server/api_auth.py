"""Bearer Token 认证：用于 API v1。

策略：
- 从 Authorization: Bearer <token> 读取明文 token。
- 计算 SHA256，与 `api_user_manager` 里的 token_sha256 匹配。
- 验证通过后，把 username 写入 Flask session 与 `g.api_username`，并标记 `is_api_user=True`。
- 该装饰器跳过 CSRF（在 server/security.requires_csrf_protection 中已放行 Bearer）。
"""
from __future__ import annotations
import functools
from flask import request, jsonify, session, g

from . import state
from modules.i18n import tr


def _extract_bearer_token() -> str:
    auth_header = request.headers.get("Authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def api_token_required(view_func):
    @functools.wraps(view_func)
    def wrapped(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({"success": False, "error": tr("auth.missing_bearer_token")}), 401

        record = state.api_user_manager.get_user_by_token(token)
        if not record:
            return jsonify({"success": False, "error": tr("auth.invalid_token")}), 401

        try:
            state.api_user_manager.bump_usage(record.username, request.path)
        except Exception:
            pass

        # 写入 session 以复用现有上下文/工作区逻辑
        session["username"] = record.username
        session["role"] = "api"
        session["is_api_user"] = True
        g.api_username = record.username
        return view_func(*args, **kwargs)

    return wrapped


__all__ = ["api_token_required"]
