"""Host 管理页与管理员 API（策略 / 自定义工具）。

已移除随 Docker 多用户版移出的租户管理面：`/admin/monitor`、`/admin/api`、
`/api/admin/dashboard`、`/api/admin/invites`、`/api/admin/users/<username>/reset-password`、
`/api/admin/api-dashboard`、`/api/admin/api-users*`。

`/user_upload/<path>`、`/workspace/<path>`、`/static/<path>` 在本模块曾有与
`server.auth` 完全重名的注册；Host 以 `server.auth` 的可信工作区版本为唯一实现，
这里不再重复注册（Blueprint 注册顺序上原本也是 auth 先命中）。
"""
from __future__ import annotations
from pathlib import Path
import time
import logging

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash

from .auth_helpers import login_required, admin_required, admin_api_required, api_login_required, resolve_admin_policy
from .entry_assets import serve_entry_html
from .security import rate_limited
from . import state  # 使用动态 state，确保与入口实例保持一致
from .state import custom_tool_registry
from modules import admin_policy_manager
from config import ADMIN_SECONDARY_PASSWORD_HASH, ADMIN_SECONDARY_PASSWORD, ADMIN_SECONDARY_TTL_SECONDS
from modules.i18n import tr

# CSRF 豁免：二级密码相关接口不需要 CSRF
try:
    state.CSRF_EXEMPT_PATHS.update({
        "/api/admin/secondary/status",
        "/api/admin/secondary/verify",
    })
except Exception:
    pass

admin_bp = Blueprint('admin', __name__)


# ------------------ 二级密码校验 ------------------ #
def _is_secondary_verified() -> bool:
    ts = session.get("admin_secondary_verified_at")
    if not ts:
        return False
    try:
        return (time.time() - float(ts)) < ADMIN_SECONDARY_TTL_SECONDS
    except Exception:
        return False


def _secondary_required():
    if _is_secondary_verified():
        return None
    return jsonify({"success": False, "error": tr("admin.secondary_required_error"), "code": "secondary_required"}), 403

@admin_bp.route('/admin/policy')
@login_required
@admin_required
def admin_policy_page():
    """管理员策略配置页面"""
    return serve_entry_html('admin_policy/index.html')

@admin_bp.route('/admin/custom-tools')
@login_required
@admin_required
def admin_custom_tools_page():
    """自定义工具管理页面"""
    return serve_entry_html('custom_tools/index.html')


@admin_bp.route('/api/admin/secondary/status', methods=['GET'])
@login_required
@admin_required
def admin_secondary_status():
    # configured：是否已设置二级密码（hash 或明文）。未配置时前端应显示设置引导，
    # 而不是密码输入框（未配置时 verify 恒 401，输入什么都进不去）。
    configured = bool(ADMIN_SECONDARY_PASSWORD_HASH or ADMIN_SECONDARY_PASSWORD)
    return jsonify({
        "success": True,
        "verified": _is_secondary_verified(),
        "configured": configured,
        "ttl": ADMIN_SECONDARY_TTL_SECONDS,
    })


@admin_bp.route('/api/admin/secondary/verify', methods=['POST'])
@login_required
@admin_required
@rate_limited("admin_secondary_verify", 5, 300, scope="user", host_exempt=False)
def admin_secondary_verify():
    payload = request.get_json() or {}
    password = str(payload.get("password") or "").strip()
    if not password:
        return jsonify({"success": False, "error": tr("admin.enter_secondary_password")}), 400
    try:
        if ADMIN_SECONDARY_PASSWORD_HASH:
            ok = check_password_hash(ADMIN_SECONDARY_PASSWORD_HASH, password)
        else:
            ok = password == (ADMIN_SECONDARY_PASSWORD or "")
    except Exception:
        ok = False
    if not ok:
        return jsonify({"success": False, "error": tr("admin.secondary_password_wrong")}), 401
    session["admin_secondary_verified_at"] = time.time()
    return jsonify({"success": True, "ttl": ADMIN_SECONDARY_TTL_SECONDS})


@admin_bp.route('/api/admin/policy', methods=['GET', 'POST'])
@api_login_required
@admin_api_required
def admin_policy_api():
    guard = _secondary_required()
    if guard:
        return guard
    if request.method == 'GET':
        try:
            data = admin_policy_manager.load_policy()
            defaults = admin_policy_manager.describe_defaults()
            return jsonify({"success": True, "data": data, "defaults": defaults})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500
    # POST 更新
    payload = request.get_json() or {}
    target_type = payload.get("target_type")
    target_value = payload.get("target_value") or ""
    config = payload.get("config") or {}
    try:
        saved = admin_policy_manager.save_scope_policy(target_type, target_value, config)
        return jsonify({"success": True, "data": saved})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/custom-tools', methods=['GET', 'POST', 'DELETE'])
@api_login_required
@admin_api_required
def admin_custom_tools_api():
    """自定义工具管理（仅全局管理员）。"""
    guard = _secondary_required()
    if guard:
        return guard
    try:
        if request.method == 'GET':
            return jsonify({"success": True, "data": custom_tool_registry.list_tools()})
        if request.method == 'POST':
            payload = request.get_json() or {}
            saved = custom_tool_registry.upsert_tool(payload)
            return jsonify({"success": True, "data": saved})
        # DELETE
        tool_id = request.args.get("id") or (request.get_json() or {}).get("id")
        if not tool_id:
            return jsonify({"success": False, "error": tr("admin.missing_id")}), 400
        removed = custom_tool_registry.delete_tool(tool_id)
        if removed:
            return jsonify({"success": True, "data": {"deleted": tool_id}})
        return jsonify({"success": False, "error": tr("admin.tool_not_found")}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logging.exception("custom-tools API error")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/custom-tools/file', methods=['GET', 'POST'])
@api_login_required
@admin_api_required
def admin_custom_tools_file_api():
    tool_id = request.args.get("id") or (request.get_json() or {}).get("id")
    name = request.args.get("name") or (request.get_json() or {}).get("name")
    guard = _secondary_required()
    if guard:
        return guard
    if not tool_id or not name:
        return jsonify({"success": False, "error": tr("admin.missing_id_or_name")}), 400
    tool_dir = Path(custom_tool_registry.root) / tool_id
    if not tool_dir.exists():
        return jsonify({"success": False, "error": tr("admin.tool_dir_missing")}), 404
    target = tool_dir / name

    if request.method == 'GET':
        if not target.exists():
            return jsonify({"success": False, "error": tr("admin.file_not_found")}), 404
        try:
            return target.read_text(encoding="utf-8")
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    # POST 保存文件
    payload = request.get_json() or {}
    content = payload.get("content")
    try:
        target.write_text(content or "", encoding="utf-8")
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/custom-tools/reload', methods=['POST'])
@api_login_required
@admin_api_required
def admin_custom_tools_reload_api():
    guard = _secondary_required()
    if guard:
        return guard
    try:
        custom_tool_registry.reload()
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/effective-policy', methods=['GET'])
@api_login_required
def effective_policy_api():
    policy = resolve_admin_policy()
    return jsonify({"success": True, "data": policy})
