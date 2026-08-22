"""认证与角色相关基础函数，供各模块复用。"""
from __future__ import annotations
from functools import wraps
from typing import Optional, Any, Dict
from flask import session, redirect, jsonify

from modules import admin_policy_manager
from .utils_common import debug_log
from . import state


def is_logged_in() -> bool:
    username = (session.get('username') or '').strip().lower()
    if not username:
        return False
    nonce = session.get('login_nonce')
    if not nonce:
        return False
    pool = state.active_login_nonces.get(username) or set()
    return nonce in pool


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
            return jsonify({"error": "Unauthorized"}), 401
        return view_func(*args, **kwargs)
    return wrapped


def get_current_username() -> Optional[str]:
    return session.get('username')


def get_current_user_record():
    username = get_current_username()
    if not username:
        return None
    return state.user_manager.get_user(username)


def get_current_user_role(record=None) -> str:
    role = session.get('role')
    if role:
        return role
    if record is None:
        record = get_current_user_record()
    return (record.role if record and record.role else 'user')


def is_admin_user(record=None) -> bool:
    role = get_current_user_role(record)
    return isinstance(role, str) and role.lower() == 'admin'


def resolve_admin_policy(record=None) -> Dict[str, Any]:
    """获取当前用户生效的管理员策略。"""
    if record is None:
        record = get_current_user_record()
    username = record.username if record else None
    role = get_current_user_role(record)
    invite_code = getattr(record, "invite_code", None)
    try:
        return admin_policy_manager.get_effective_policy(username, role, invite_code)
    except Exception as exc:
        debug_log(f"[admin_policy] 加载失败: {exc}")
        return admin_policy_manager.get_effective_policy(username, role, invite_code)


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        # host 模式下可能不存在 users.json 记录（如 username=host），
        # 但 session.role 已是 admin，此时仍应允许访问管理页。
        if not is_admin_user():
            return redirect('/new')
        return view_func(*args, **kwargs)
    return wrapped


def admin_api_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_admin_user():
            return jsonify({"success": False, "error": "需要管理员权限"}), 403
        return view_func(*args, **kwargs)
    return wrapped

__all__ = [
    "is_logged_in",
    "login_required",
    "api_login_required",
    "get_current_username",
    "get_current_user_record",
    "get_current_user_role",
    "is_admin_user",
    "resolve_admin_policy",
    "admin_required",
    "admin_api_required",
]
