from __future__ import annotations
from pathlib import Path
import time
import mimetypes
import logging
import json
from flask import Blueprint, jsonify, send_from_directory, request, current_app, redirect, abort, session
from werkzeug.security import check_password_hash

from .auth_helpers import login_required, admin_required, admin_api_required, api_login_required, get_current_user_record, resolve_admin_policy
from .context import with_terminal
from .security import rate_limited
from .utils_common import debug_log
from . import state  # 使用动态 state，确保与入口实例保持一致
from .state import (
    custom_tool_registry,
    mcp_server_registry,
    mcp_client_manager,
    user_manager,
    container_manager,
    PROJECT_MAX_STORAGE_MB,
)
from .conversation import (
    build_admin_dashboard_snapshot as _build_dashboard_rich,
    compute_workspace_storage,
    collect_user_token_statistics,
    collect_upload_events,
    summarize_upload_events,
)
from .conversation_stats import summarize_invite_codes
from modules import admin_policy_manager
from collections import Counter
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

@admin_bp.route('/admin/monitor')
@login_required
@admin_required
def admin_monitor_page():
    """管理员监控页面入口"""
    return send_from_directory(str(Path(current_app.static_folder)/"admin_dashboard"), 'index.html')


@admin_bp.route('/monitor')
@login_required
@admin_required
def admin_monitor_page_alias():
    """兼容旧入口：/monitor -> /admin/monitor"""
    return redirect('/admin/monitor')

@admin_bp.route('/admin/policy')
@login_required
@admin_required
def admin_policy_page():
    """管理员策略配置页面"""
    return send_from_directory(Path(current_app.static_folder) / 'admin_policy', 'index.html')

@admin_bp.route('/admin/custom-tools')
@login_required
@admin_required
def admin_custom_tools_page():
    """自定义工具管理页面"""
    return send_from_directory(str(Path(current_app.static_folder)/"custom_tools"), 'index.html')


@admin_bp.route('/admin/api')
@login_required
@admin_required
def admin_api_page():
    """API 管理页面入口。"""
    return send_from_directory(str(Path(current_app.static_folder)/"admin_api"), 'index.html')


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


@admin_bp.route('/admin/assets/<path:filename>')
@login_required
@admin_required
def admin_asset_file(filename: str):
    return send_from_directory(str(Path(current_app.static_folder)/"admin_dashboard"), filename)


@admin_bp.route('/user_upload/<path:filename>')
@login_required
def serve_user_upload(filename: str):
    """
    直接向前端暴露当前登录用户的上传目录文件，用于 <show_image src="/user_upload/..."> 等场景。
    - 仅登录用户可访问
    - 路径穿越校验：目标必须位于用户自己的 uploads_dir 内
    """
    user = get_current_user_record()
    if not user:
        return redirect('/login')

    workspace = user_manager.ensure_user_workspace(user.username)
    uploads_dir = workspace.uploads_dir.resolve()

    target = (uploads_dir / filename).resolve()
    try:
        target.relative_to(uploads_dir)
    except ValueError:
        abort(403)

    if not target.exists() or not target.is_file():
        abort(404)

    return send_from_directory(str(uploads_dir), str(target.relative_to(uploads_dir)))


@admin_bp.route('/workspace/<path:filename>')
@login_required
def serve_workspace_file(filename: str):
    """
    暴露当前登录用户项目目录下的文件（主要用于图片展示）。
    - 仅登录用户可访问自己的项目文件
    - 路径穿越校验：目标必须位于用户自己的 project_path 内
    - 非图片直接拒绝，避免误暴露其他文件
    """
    user = get_current_user_record()
    if not user:
        return redirect('/login')

    workspace = user_manager.ensure_user_workspace(user.username)
    project_root = workspace.project_path.resolve()

    target = (project_root / filename).resolve()
    try:
        target.relative_to(project_root)
    except ValueError:
        abort(403)

    if not target.exists() or not target.is_file():
        abort(404)

    mime_type, _ = mimetypes.guess_type(str(target))
    if not mime_type or not mime_type.startswith("image/"):
        abort(415)

    return send_from_directory(str(target.parent), target.name)


@admin_bp.route('/static/<path:filename>')
def static_files(filename):
    """提供静态文件"""
    if filename.startswith('admin_dashboard'):
        abort(404)
    return send_from_directory('static', filename)


@admin_bp.route('/api/admin/dashboard')
@api_login_required
@admin_api_required
def admin_dashboard_snapshot_api():
    guard = _secondary_required()
    if guard:
        return guard
    try:
        # 若当前管理员没有容器句柄，主动确保容器存在，避免面板始终显示“宿主机模式”
        # 注意：容器 key 必须带工作区后缀（{username}::{workspace_id}），
        # 否则会创建出旧式命名的 agent-term-{username} 孤儿容器。
        try:
            record = get_current_user_record()
            uname = record.username if record else None
            if uname:
                handles = state.container_manager.list_containers()
                default_ws_id = "default"
                container_key = f"{uname}::{default_ws_id}"
                if container_key not in handles:
                    workspace = state.user_manager.ensure_user_workspace(uname, default_ws_id)
                    state.container_manager.ensure_container(
                        uname,
                        str(workspace.project_path),
                        container_key=container_key,
                        preferred_mode="docker",
                    )
        except Exception as ensure_exc:
            logging.getLogger(__name__).warning("ensure_container for admin failed: %s", ensure_exc)

        snapshot = build_admin_dashboard_snapshot()
        # 双通道输出：标准日志 + 调试文件
        try:
            logging.getLogger(__name__).info(
                "[admin_dashboard] snapshot=%s",
                json.dumps(snapshot, ensure_ascii=False)[:2000],
            )
        except Exception:
            pass
        try:
            debug_log(f"[admin_dashboard] {json.dumps(snapshot, ensure_ascii=False)[:2000]}")
        except Exception:
            pass
        return jsonify({"success": True, "data": snapshot})
    except Exception as exc:
        logging.exception("Failed to build admin dashboard")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/invites', methods=['GET', 'POST', 'PUT', 'DELETE'])
@api_login_required
@admin_api_required
def admin_invites_api():
    """邀请码管理：查询 / 新增 / 更新 / 删除。"""
    guard = _secondary_required()
    if guard:
        return guard

    try:
        if request.method == 'GET':
            codes = user_manager.list_invite_codes()
            return jsonify({
                "success": True,
                "data": {
                    "summary": summarize_invite_codes(codes),
                    "codes": codes,
                },
            })

        payload = request.get_json(silent=True) or {}
        code = (payload.get("code") or request.args.get("code") or "").strip()

        if request.method in {'POST', 'PUT'}:
            has_remaining_key = "remaining" in payload
            remaining = payload.get("remaining") if has_remaining_key else None
            # 未传 remaining 时默认 1，便于快速新增邀请码
            if not has_remaining_key:
                remaining = 1
            saved = user_manager.upsert_invite_code(code, remaining)
            return jsonify({"success": True, "data": saved})

        # DELETE
        deleted = user_manager.delete_invite_code(code)
        if not deleted:
            return jsonify({"success": False, "error": tr("admin.invite_code_not_found")}), 404
        return jsonify({"success": True, "data": {"deleted": code}})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logging.exception("invites API error")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/users/<username>/reset-password', methods=['POST'])
@api_login_required
@admin_api_required
def admin_reset_user_password(username: str):
    """管理员重置用户密码。"""
    guard = _secondary_required()
    if guard:
        return guard
    if not username:
        return jsonify({"success": False, "error": tr("admin.missing_username")}), 400
    payload = request.get_json(silent=True) or {}
    new_password = (payload.get("password") or "").strip()
    if not new_password:
        return jsonify({"success": False, "error": tr("admin.new_password_required")}), 400
    try:
        record = user_manager.reset_password(username, new_password)
        return jsonify({
            "success": True,
            "data": {"username": record.username, "message": tr("admin.password_reset")}
        })
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logging.exception("reset-password API error")
        return jsonify({"success": False, "error": str(exc)}), 500


def build_admin_dashboard_snapshot():
    """
    复用对话模块的完整统计逻辑，返回带用量/Token/存储/上传等数据的仪表盘快照。
    """
    try:
        return _build_dashboard_rich()
    except Exception as exc:
        # 兜底：若高级统计失败，仍返回最小信息，避免前端 500
        logging.getLogger(__name__).warning("rich dashboard snapshot failed: %s", exc)
        handle_map = state.container_manager.list_containers()
        invite_codes = state.user_manager.list_invite_codes()
        invite_summary = summarize_invite_codes(invite_codes)
        return {
            "generated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            "overview": {
                "totals": {
                    "users": len(state.user_manager.list_users() or {}),
                    "active_users": len(handle_map),
                    "containers_active": len(handle_map),
                    "containers_max": getattr(state.container_manager, "max_containers", len(handle_map)),
                    "available_container_slots": None,
                }
            },
            "containers": [
                {"username": u, "status": h} for u, h in handle_map.items()
            ],
            "invites": {
                "summary": invite_summary,
                "codes": invite_codes,
            },
        }


def build_api_admin_dashboard_snapshot():
    """构建 API 用户专用的监控快照。"""
    handle_map = container_manager.list_containers()
    api_users = state.api_user_manager.list_users()
    usage_map = state.api_user_manager.list_usage()
    items = []
    token_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    storage_total_bytes = 0
    quarantine_total_bytes = 0
    total_requests = 0

    def _find_handle(name: str):
        for key in handle_map.keys():
            if key.startswith(name):
                try:
                    return container_manager.get_container_status(key, include_stats=True)
                except Exception:
                    return None
        return None

    for username, record in api_users.items():
        try:
            ws = state.api_user_manager.ensure_workspace(username)
        except Exception:
            continue
        storage = compute_workspace_storage(ws)
        tokens = collect_user_token_statistics(ws)
        usage = usage_map.get(username) or {}
        storage_total_bytes += storage.get("total_bytes", 0) or 0
        quarantine_total_bytes += storage.get("quarantine_bytes", 0) or 0
        for k in token_totals:
            token_totals[k] += tokens.get(k, 0) or 0
        handle = _find_handle(username)
        total_requests += int(usage.get("total", 0) or 0)
        items.append({
            "username": username,
            "note": getattr(record, "note", "") if record else "",
            "created_at": getattr(record, "created_at", ""),
            "token_sha256": getattr(record, "token_sha256", ""),
            "tokens": tokens,
            "storage": storage,
            "usage": {
                "total_requests": int(usage.get("total", 0)),
                "endpoints": usage.get("endpoints") or {},
                "last_request_at": usage.get("last_request_at"),
            },
            "container": handle,
        })

    api_handles = {
        key: val for key, val in handle_map.items()
        if key.split("::")[0] in api_users
    }
    containers_active = len(api_handles)
    max_containers = getattr(container_manager, "max_containers", None) or len(handle_map)
    available_slots = None
    if max_containers:
        available_slots = max(0, max_containers - len(handle_map))

    upload_events = collect_upload_events()
    uploads_summary = summarize_upload_events(upload_events, quarantine_total_bytes)

    overview = {
        "generated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "totals": {
            "users": len(api_users),
            "containers_active": containers_active,
            "containers_max": max_containers,
            "available_container_slots": available_slots,
        },
        "token_totals": token_totals,
        "usage_totals": {
            "requests": total_requests
        },
        "storage": {
            "total_bytes": storage_total_bytes,
            "project_max_mb": PROJECT_MAX_STORAGE_MB,
        },
        "uploads": uploads_summary.get("stats") or {},
    }
    return {
        "generated_at": overview["generated_at"],
        "overview": overview,
        "users": items,
        "containers": api_handles,
        "uploads": uploads_summary,
    }

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


@admin_bp.route('/api/admin/mcp-servers', methods=['GET', 'POST', 'DELETE'])
@api_login_required
@admin_api_required
def admin_mcp_servers_api():
    """MCP 服务配置管理（仅全局管理员）。"""
    guard = _secondary_required()
    if guard:
        return guard
    try:
        if request.method == 'GET':
            mcp_server_registry.reload()
            return jsonify({"success": True, "data": mcp_server_registry.list_servers()})
        if request.method == 'POST':
            payload = request.get_json() or {}
            saved = mcp_server_registry.upsert_server(payload)
            return jsonify({"success": True, "data": saved})

        # DELETE
        server_id = request.args.get("id") or (request.get_json() or {}).get("id")
        if not server_id:
            return jsonify({"success": False, "error": tr("admin.missing_id")}), 400
        removed = mcp_server_registry.delete_server(server_id)
        if removed:
            return jsonify({"success": True, "data": {"deleted": server_id}})
        return jsonify({"success": False, "error": tr("admin.server_not_found")}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logging.exception("mcp-servers API error")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/mcp-servers/reload', methods=['POST'])
@api_login_required
@admin_api_required
def admin_mcp_servers_reload_api():
    guard = _secondary_required()
    if guard:
        return guard
    try:
        servers = mcp_server_registry.reload()
        return jsonify({"success": True, "data": {"count": len(servers)}})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/mcp-servers/sync', methods=['POST'])
@api_login_required
@admin_api_required
@with_terminal
def admin_mcp_servers_sync_api(terminal, workspace, username):
    """同步 MCP 服务工具缓存（tools/list）。"""
    guard = _secondary_required()
    if guard:
        return guard
    payload = request.get_json() or {}
    target_id = (payload.get("id") or "").strip() or None
    manager = getattr(terminal, "mcp_client_manager", None) or mcp_client_manager
    try:
        result = manager.sync_servers(server_id=target_id)
        return jsonify({"success": bool(result.get("success", True)), "data": result})
    except Exception as exc:
        logging.exception("mcp-servers sync API error")
        return jsonify({"success": False, "error": str(exc)}), 500


# ------------------ API 用户与监控 ------------------ #
@admin_bp.route('/api/admin/api-dashboard', methods=['GET'])
@api_login_required
@admin_api_required
def admin_api_dashboard():
    guard = _secondary_required()
    if guard:
        return guard
    try:
        snapshot = build_api_admin_dashboard_snapshot()
        return jsonify({"success": True, "data": snapshot})
    except Exception as exc:
        logging.exception("Failed to build api dashboard")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/api-users', methods=['GET', 'POST'])
@api_login_required
@admin_api_required
def admin_api_users():
    guard = _secondary_required()
    if guard:
        return guard
    if request.method == 'GET':
        users = state.api_user_manager.list_users()
        usage = state.api_user_manager.list_usage()
        items = []
        for username, record in users.items():
            meta = usage.get(username) or {}
            items.append({
                "username": username,
                "note": getattr(record, "note", ""),
                "created_at": getattr(record, "created_at", ""),
                "token_sha256": getattr(record, "token_sha256", ""),
                "last_request_at": meta.get("last_request_at"),
                "total_requests": meta.get("total", 0),
                "endpoints": meta.get("endpoints") or {},
            })
        return jsonify({"success": True, "data": items})

    payload = request.get_json() or {}
    username = (payload.get("username") or "").strip().lower()
    note = (payload.get("note") or "").strip()
    if not username:
        return jsonify({"success": False, "error": tr("admin.username_required")}), 400
    try:
        record, token = state.api_user_manager.create_user(username, note=note)
        return jsonify({
            "success": True,
            "data": {
                "username": record.username,
                "token": token,
                "created_at": record.created_at,
                "note": record.note,
            }
        })
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logging.exception("create api user failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/api-users/<username>', methods=['DELETE'])
@api_login_required
@admin_api_required
def admin_api_users_delete(username: str):
    guard = _secondary_required()
    if guard:
        return guard
    if not username:
        return jsonify({"success": False, "error": tr("admin.missing_username")}), 400
    try:
        ok = state.api_user_manager.delete_user(username)
        if not ok:
            return jsonify({"success": False, "error": tr("admin.user_not_found")}), 404
        return jsonify({"success": True})
    except Exception as exc:
        logging.exception("delete api user failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/admin/api-users/<username>/token', methods=['GET'])
@api_login_required
@admin_api_required
def admin_api_users_token(username: str):
    guard = _secondary_required()
    if guard:
        return guard
    try:
        # 尝试读取；若不存在或解密失败，自动重新签发
        token = None
        try:
            token = state.api_user_manager.get_plain_token(username)
        except Exception:
            pass
        if not token:
            _, token = state.api_user_manager.issue_token(username)
        return jsonify({"success": True, "data": {"token": token}})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    except Exception as exc:
        logging.exception("get api user token failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@admin_bp.route('/api/effective-policy', methods=['GET'])
@api_login_required
def effective_policy_api():
    record = get_current_user_record()
    policy = resolve_admin_policy(record)
    return jsonify({"success": True, "data": policy})
