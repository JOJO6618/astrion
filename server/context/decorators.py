"""HTTP 适配层装饰器与 socket 连接资源解析。"""
from __future__ import annotations

from functools import wraps
from typing import Optional

from flask import request, jsonify

from server import state
from server.auth_helpers import get_current_username
from server.context.identity import NoWorkspaceError
from server.context.resources import get_user_resources


def with_terminal(func=None, *, allow_no_workspace: bool = False):
    """注入用户专属终端和工作区。

    请求携带 conversation_id（query 参数或 JSON body）时返回该对话的对话级 terminal，
    否则返回工作区级服务 terminal。

    支持两种用法：@with_terminal 与 @with_terminal(allow_no_workspace=True)。
    allow_no_workspace=True 时，「尚未创建工作区」不再短路返回 no_workspace，
    而是以 terminal=None, workspace=None 调用视图——仅适用于个性化配置等
    与具体工作区无逻辑依赖的端点，视图内部必须自行判空。
    """
    def decorator(view_func):
        @wraps(view_func)
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
                if allow_no_workspace:
                    kwargs.update({
                        'terminal': None,
                        'workspace': None,
                        'username': username
                    })
                    return view_func(*args, **kwargs)
                # 「尚未创建工作区」是新装应用的正常初始空态而非服务故障，
                # 必须用 200 + 业务码表达：5xx 会被浏览器自动记为红色 console 错误，
                # 前端各空闲轮询器（status/git-summary/terminals 等）会持续刷屏。
                # 附带 success=False，使按 payload.success 判定的调用点自然走静默分支。
                return jsonify({"error": str(exc), "code": "no_workspace", "success": False}), 200
            except RuntimeError as exc:
                return jsonify({"error": str(exc), "code": "resource_busy"}), 503
            if not terminal or not workspace:
                return jsonify({"error": "System not initialized"}), 503
            kwargs.update({
                'terminal': terminal,
                'workspace': workspace,
                'username': username
            })
            return view_func(*args, **kwargs)
        return wrapper
    if func is not None:
        return decorator(func)
    return decorator


def get_terminal_for_sid(sid: str, conversation_id: Optional[str] = None):
    username = state.connection_users.get(sid)
    if not username:
        return None, None, None
    try:
        terminal, workspace = get_user_resources(username, conversation_id=conversation_id)
    except RuntimeError:
        return username, None, None
    return username, terminal, workspace
