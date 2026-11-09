"""macOS 内置浏览器 AI 工具：对话级开关与站点授权管理接口。"""
from __future__ import annotations

from flask import jsonify, request

from server.chat import chat_bp
from server.auth_helpers import api_login_required
from server.context import with_terminal
from server.context.data_decorators import with_workspace_data
from server.context.resources import find_runtime
from server.security import rate_limited
from modules.native_browser.availability import native_browser_supported
from modules.native_browser.state import BROWSER_ENABLED_META_KEY, BROWSER_PENDING_META_KEY
from modules.native_browser import site_permissions


def _conversation_running(username: str, conversation_id: str) -> bool:
    try:
        from server.tasks import task_manager
        return any(
            r.conversation_id == conversation_id and r.status in {"pending", "running", "cancel_requested"}
            for r in task_manager.list_tasks(username)
        )
    except Exception:
        return False


@chat_bp.route('/api/browser-tools', methods=['GET'])
@api_login_required
@with_workspace_data
def get_browser_tools(data_context, workspace, username: str):
    supported = native_browser_supported()
    conversation_id = str(request.args.get('conversation_id') or '').strip()
    enabled, pending = False, None
    if conversation_id:
        if not conversation_id.startswith('conv_'):
            conversation_id = f'conv_{conversation_id}'
        terminal = find_runtime(data_context.username, data_context.workspace, conversation_id)
        if terminal is not None and terminal.context_manager.current_conversation_id == conversation_id:
            meta = terminal.context_manager.conversation_metadata or {}
        else:
            data = data_context.manager_for(conversation_id).read_conversation(conversation_id)
            if data is None:
                return jsonify({"success": False, "error": "对话不存在"}), 404
            meta = data.get('metadata') or {}
        enabled = meta.get(BROWSER_ENABLED_META_KEY) is True
        pending = meta.get(BROWSER_PENDING_META_KEY) if meta.get(BROWSER_PENDING_META_KEY) in {"on", "off"} else None
    return jsonify({"success": True, "supported": supported, "enabled": enabled, "pending": pending})


@chat_bp.route('/api/browser-tools', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("browser_tools_switch", 30, 60, scope="user")
def update_browser_tools(terminal, workspace, username: str):
    if not native_browser_supported() or getattr(terminal, "conversation_kind", "agent") == "chat":
        return jsonify({"success": False, "error": "内置浏览器工具仅在 macOS 桌面版 Agent 对话中可用"}), 400
    data = request.get_json() or {}
    if not isinstance(data.get("enabled"), bool):
        return jsonify({"success": False, "error": "enabled 必须是布尔值"}), 400
    conversation_id = getattr(terminal.context_manager, "current_conversation_id", None)
    if not conversation_id:
        return jsonify({"success": False, "error": "对话尚未创建"}), 409
    target = "on" if data["enabled"] else "off"
    if _conversation_running(username, conversation_id):
        # 运行中：完整工具批次后生效并插入切换通知。
        terminal._persist_runtime_mode_metadata({BROWSER_PENDING_META_KEY: target})
        return jsonify({"success": True, "enabled": data["enabled"], "pending": target})
    # 空闲：直接生效；通知由基线漂移机制在下一条用户消息时补发。
    terminal._persist_runtime_mode_metadata({BROWSER_ENABLED_META_KEY: data["enabled"], BROWSER_PENDING_META_KEY: None})
    return jsonify({"success": True, "enabled": data["enabled"], "pending": None})


@chat_bp.route('/api/browser-site-permissions', methods=['GET'])
@api_login_required
@with_workspace_data
def list_browser_site_permissions(data_context, workspace, username: str):
    return jsonify({"success": True, "supported": native_browser_supported(),
                    "sites": site_permissions.list_sites(workspace.data_dir)})


@chat_bp.route('/api/browser-site-permissions', methods=['DELETE'])
@api_login_required
@with_workspace_data
@rate_limited("browser_site_permission_revoke", 60, 60, scope="user")
def revoke_browser_site_permission(data_context, workspace, username: str):
    data = request.get_json(silent=True) or {}
    site = str(data.get("site") or "").strip()
    if not site_permissions.valid_site(site):
        return jsonify({"success": False, "error": "站点无效"}), 400
    removed = site_permissions.revoke_site(workspace.data_dir, site)
    return jsonify({"success": True, "removed": removed, "sites": site_permissions.list_sites(workspace.data_dir)})
