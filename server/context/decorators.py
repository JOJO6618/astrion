"""HTTP 适配层装饰器与 socket 连接资源解析。"""
from __future__ import annotations

from functools import wraps
from typing import Optional

from flask import request, jsonify

from server import state
from server.auth_helpers import get_current_username
from server.context.identity import NoWorkspaceError
from server.context.resources import get_user_resources


def with_terminal(func):
    """注入用户专属终端和工作区。

    请求携带 conversation_id（query 参数或 JSON body）时返回该对话的对话级 terminal，
    否则返回工作区级服务 terminal。
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        username = get_current_username()
        conversation_id = None
        try:
            conversation_id = (request.args.get("conversation_id") or "").strip() or None
            if not conversation_id and request.is_json:
                body = request.get_json(silent=True) or {}
                if isinstance(body, dict):
                    conversation_id = (body.get("conversation_id") or "").strip() or None
        except Exception:
            conversation_id = None
        try:
            terminal, workspace = get_user_resources(username, conversation_id=conversation_id)
        except NoWorkspaceError as exc:
            return jsonify({"error": str(exc), "code": "no_workspace"}), 503
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "code": "resource_busy"}), 503
        if not terminal or not workspace:
            return jsonify({"error": "System not initialized"}), 503
        kwargs.update({
            'terminal': terminal,
            'workspace': workspace,
            'username': username
        })
        return func(*args, **kwargs)
    return wrapper


def get_terminal_for_sid(sid: str, conversation_id: Optional[str] = None):
    username = state.connection_users.get(sid)
    if not username:
        return None, None, None
    try:
        terminal, workspace = get_user_resources(username, conversation_id=conversation_id)
    except RuntimeError:
        return username, None, None
    return username, terminal, workspace
