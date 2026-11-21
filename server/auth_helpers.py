"""认证与角色相关基础函数，供各模块复用。"""
from __future__ import annotations
from functools import wraps
from typing import Optional, Any, Dict
from flask import g, session, redirect, jsonify

from modules import admin_policy_manager
from . import state
from modules.i18n import tr


def is_logged_in() -> bool:
    if getattr(g, "host_bearer_authenticated", False):
        return True
    from .host_password import browser_session_valid
    if not browser_session_valid():
        return False
    if session.get('username') != 'host' or not session.get('host_mode') or session.get('is_api_user'):
        return False
    nonce = session.get('login_nonce')
    return bool(nonce and nonce in state.active_login_nonces.get('host', ()))


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect('/login')
        return view_func(*args, **kwargs)
    return wrapped


def api_login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return jsonify({"error": tr("auth.session_expired")}), 401
        return view_func(*args, **kwargs)
    return wrapped


def get_current_username() -> Optional[str]:
    return session.get('username')


def get_current_user_role() -> str:
    return session.get('role') or 'user'


def is_admin_user() -> bool:
    role = get_current_user_role()
    return isinstance(role, str) and role.lower() == 'admin'


def resolve_admin_policy() -> Dict[str, Any]:
    """获取当前 Host 会话生效的管理员策略。"""
    return admin_policy_manager.get_effective_policy(get_current_username(), get_current_user_role(), None)


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_admin_user():
            return redirect('/new')
        return view_func(*args, **kwargs)
    return wrapped


def admin_api_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_admin_user():
            return jsonify({"success": False, "error": tr("auth.admin_required")}), 403
        return view_func(*args, **kwargs)
    return wrapped

__all__ = [
    "is_logged_in",
    "login_required",
    "api_login_required",
    "get_current_username",
    "get_current_user_role",
    "is_admin_user",
    "resolve_admin_policy",
    "admin_required",
    "admin_api_required",
]
