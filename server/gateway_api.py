"""Gateway 公共协议路由（2026-09-08 新增）。

定位：docs/runtime_protocol.md 的 session.* 能力传输暴露——
为 CLI/GUI 等非 Web 客户端提供不依赖 Web 专属会话路由的公共入口，
Web 端既有 /api/conversations 路由保持不动。

路由（全部支持双通道认证：Web session 或 host Bearer token）：
- GET  /api/runtime/sessions                  → session.list
- POST /api/runtime/sessions                  → session.create（显式）
- GET  /api/runtime/sessions/<cid>/history    → session.history

「加载会话用于展示」= session.history；「让 agent 在某会话中执行」=
run.start 携带 conversation_id。不单独提供 session.load 命令
（Web 的 load 语义是切换 terminal 内存态，属 Web 适配层视图概念）。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from modules.i18n import tr
from server.gateway_auth import api_login_or_host_token_required
from server.runtime.context import principal_from_session_snapshot
from server.runtime.service import runtime_service

gateway_bp = Blueprint("gateway", __name__)


def _resolve_workspace_id(explicit: str = "") -> str:
    """查询/创建未显式指定工作区时，回落当前会话工作区。"""
    return str(explicit or session.get("workspace_id") or "default")


@gateway_bp.route("/api/runtime/sessions", methods=["GET"])
@api_login_or_host_token_required
def list_runtime_sessions():
    """session.list：会话列表（工作区/数量/多智能体筛选）。"""
    workspace_id = _resolve_workspace_id(request.args.get("workspace_id", ""))
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    ma_param = request.args.get("multi_agent_mode", None)
    multi_agent_mode = None if ma_param is None else ma_param in ("1", "true", "True")
    username = str(session.get("username") or "")
    principal = principal_from_session_snapshot(dict(session), workspace_id, username)
    try:
        result = runtime_service.list_sessions(
            username,
            workspace_id,
            principal,
            limit=limit,
            offset=offset,
            multi_agent_mode=multi_agent_mode,
        )
        return jsonify({"success": True, **(result or {})})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 503


@gateway_bp.route("/api/runtime/sessions", methods=["POST"])
@api_login_or_host_token_required
def create_runtime_session():
    """session.create：显式创建会话，返回 conversation_id。"""
    data = request.get_json(silent=True) or {}
    workspace_id = _resolve_workspace_id(str(data.get("workspace_id") or ""))
    username = str(session.get("username") or "")
    principal = principal_from_session_snapshot(dict(session), workspace_id, username)
    try:
        result = runtime_service.create_session(
            username,
            workspace_id,
            principal,
            run_mode=data.get("run_mode"),
            thinking_mode=data.get("thinking_mode"),
            model_key=data.get("model_key"),
            multi_agent_mode=bool(data.get("multi_agent_mode")),
        )
        return jsonify({"success": True, **result})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 503


@gateway_bp.route("/api/runtime/sessions/<conversation_id>/history", methods=["GET"])
@api_login_or_host_token_required
def get_runtime_session_history(conversation_id: str):
    """session.history：会话历史（磁盘权威快照）。"""
    workspace_id = _resolve_workspace_id(request.args.get("workspace_id", ""))
    username = str(session.get("username") or "")
    principal = principal_from_session_snapshot(dict(session), workspace_id, username)
    try:
        result = runtime_service.get_session_history(
            username, workspace_id, conversation_id, principal
        )
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 503
    if result is None:
        return jsonify({"success": False, "error": tr("ctx_mgr.conversation_not_found", conversation_id=conversation_id)}), 404
    return jsonify({"success": True, "conversation": result})


@gateway_bp.route("/api/runtime/sessions/<conversation_id>/token-stats", methods=["GET"])
@api_login_or_host_token_required
def get_runtime_session_token_stats(conversation_id: str):
    """session.token_stats：会话 token 统计（累计输入/输出/缓存/当前上下文）。"""
    workspace_id = _resolve_workspace_id(request.args.get("workspace_id", ""))
    username = str(session.get("username") or "")
    principal = principal_from_session_snapshot(dict(session), workspace_id, username)
    try:
        stats = runtime_service.get_session_token_stats(
            username, workspace_id, conversation_id, principal
        )
        return jsonify({"success": True, "stats": stats})
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 503


__all__ = ["gateway_bp"]
