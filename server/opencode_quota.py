"""OpenCode Go 订阅额度查询 API（2026-11-16）。

前端只传提供商连接 id，key 由后端按连接记录读取，永不下发。
权限：host（本机单人）模式不限身份；docker 多用户部署只允许管理员，
与 ChatGPT 订阅额度保持同一口径。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from config import IS_HOST_MODE
from modules.opencode_quota import fetch_usage
from server.auth_helpers import api_login_required, is_admin_user

opencode_quota_bp = Blueprint("opencode_quota", __name__)


@opencode_quota_bp.route("/api/subscription/opencode-usage", methods=["GET"])
@api_login_required
def opencode_usage():
    """查询指定 OpenCode Go 提供商连接的订阅额度（30 秒缓存，``refresh=1`` 强制）。"""
    if not IS_HOST_MODE and not is_admin_user():
        return jsonify({"success": False, "error": "admin_required"}), 403
    provider_id = (request.args.get("provider_id") or "").strip()
    force = request.args.get("refresh") == "1"
    return jsonify(fetch_usage(provider_id, force=force))
