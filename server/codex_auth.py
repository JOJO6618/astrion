"""Codex 账号管理 API：连接状态、OAuth 登录流程、登出。

opencode 模式（独立凭证）：凭证唯一事实源是 astrion 自己的
``codex_auth.json``，与 Codex CLI 的 ``~/.codex/auth.json`` 完全解耦——
- 「登出」= 删除 astrion 自己的凭证文件（真登出），CLI 登录态不受影响；
- 登录走独立 OAuth 会话（独立 refresh token 族），刷新只回写自己的文件。

权限模型（2026-09-24）：本蓝图全部端点仅管理员可用（登录 + 管理员双重校验）。
docker 多用户模式下由管理员统一登录一个 Codex 账号，订阅凭证全局共享；
模型列表对全员可见（模型暴露只看凭证文件存在性，见 codex/models.py），
普通用户可直接选用 codex/ 模型，消耗的是管理员账号的订阅额度。
"""

from __future__ import annotations

import threading

from flask import Blueprint, jsonify

from server.auth_helpers import admin_api_required, api_login_required

from utils.api_client.codex.auth import get_auth_manager
from utils.api_client.codex.models import get_models_manager
from utils.api_client.codex.oauth import (
    cancel_login_flow,
    poll_login_flow,
    start_device_login_flow,
    start_login_flow,
)

codex_auth_bp = Blueprint("codex_auth", __name__)


@codex_auth_bp.route("/api/codex/status", methods=["GET"])
@api_login_required
@admin_api_required
def codex_status():
    """连接状态 + 模型缓存摘要（个人空间 Codex 区块轮询）。

    connected = astrion 自己的凭证存在且含有效 token 对（登出即删文件，无需其他标记）。
    """
    auth_status = get_auth_manager().status()
    models_summary = get_models_manager().status_summary()
    login_state = poll_login_flow()
    return jsonify(
        {
            "success": True,
            **auth_status,
            "models": models_summary,
            "login_flow": login_state,
        }
    )


@codex_auth_bp.route("/api/codex/login/start", methods=["POST"])
@api_login_required
@admin_api_required
def codex_login_start():
    """启动 OAuth 登录：起 1455 监听，返回授权 URL（前端负责打开浏览器）。"""
    auth = get_auth_manager()
    result = start_login_flow(auth)
    if result.get("status") == "failed":
        return jsonify({"success": False, "error": result.get("error")}), 500
    return jsonify({"success": True, **result})


@codex_auth_bp.route("/api/codex/login/poll", methods=["GET"])
@api_login_required
@admin_api_required
def codex_login_poll():
    """轮询登录进度；完成时后台刷新模型列表。"""
    state = poll_login_flow()
    if state.get("status") == "completed":
        models = get_models_manager()
        threading.Thread(
            target=models.refresh_sync, daemon=True, name="codex-models-refresh"
        ).start()
    return jsonify({"success": True, **state})


@codex_auth_bp.route("/api/codex/login/device/start", methods=["POST"])
@api_login_required
@admin_api_required
def codex_login_device_start():
    """启动设备码登录（无头场景，管理员任选）：立即返回 pending（phase=starting）。

    设备码请求/授权轮询/换 token 全部在后端后台线程执行；user_code 与
    verification_uri 由前端轮询 /api/codex/login/poll 异步补齐，前端手动打开
    官方授权页（无 localhost 回调，docker 部署可用）。
    前提：ChatGPT 账号设置 → 安全里已开启 device code authentication。
    """
    auth = get_auth_manager()
    result = start_device_login_flow(auth)
    # 失败不再同步返回（后台线程异步置 failed，经 poll 呈现）；保留分支作防御
    if result.get("status") == "failed":
        return jsonify({"success": False, "error": result.get("error")}), 500
    return jsonify({"success": True, **result})


@codex_auth_bp.route("/api/codex/login/cancel", methods=["POST"])
@api_login_required
@admin_api_required
def codex_login_cancel():
    return jsonify({"success": True, **cancel_login_flow()})


@codex_auth_bp.route("/api/codex/logout", methods=["POST"])
@api_login_required
@admin_api_required
def codex_logout():
    """登出：删除 astrion 自己的凭证文件（真登出；Codex CLI 登录态不受影响）。"""
    get_auth_manager().logout()
    return jsonify({"success": True})


@codex_auth_bp.route("/api/codex/models/refresh", methods=["POST"])
@api_login_required
@admin_api_required
def codex_models_refresh():
    """手动刷新模型列表（在线拉取，ETag 条件请求）。"""
    result = get_models_manager().refresh_sync()
    return jsonify({"success": True, **result})


def _wham_request(method: str, url: str, json_body: dict | None = None):
    """wham/* 端点统一转发：token（临期自动刷新回写）+ 标准头 + 代理。

    返回 (httpx.Response, None) 或 (None, (flask_response, status_code)) 错误二元组。
    """
    import httpx

    from utils.api_client.codex.settings import resolve_proxy

    auth = get_auth_manager()
    try:
        access = auth.get_access_token_sync()
    except Exception as exc:
        return None, (jsonify({"success": False, "error": f"refresh_failed: {exc}"}), 502)
    if not access:
        return None, (jsonify({"success": False, "error": "no_credentials"}), 401)
    headers = {
        "Authorization": f"Bearer {access}",
        "originator": "codex_cli_rs",
        "accept": "application/json",
    }
    account_id = auth.get_account_id()
    if account_id:
        headers["chatgpt-account-id"] = account_id
    try:
        with httpx.Client(proxy=resolve_proxy(), timeout=30) as client:
            resp = client.request(method, url, headers=headers, json=json_body)
    except Exception as exc:
        return None, (jsonify({"success": False, "error": str(exc)}), 502)
    if resp.status_code != 200:
        return None, (jsonify({"success": False, "error": f"http_{resp.status_code}"}), 502)
    return resp, None


@codex_auth_bp.route("/api/codex/usage", methods=["GET"])
@api_login_required
@admin_api_required
def codex_usage():
    """订阅用量查询（转发 chatgpt.com wham/usage，CLI /status 的数据源）。"""
    from utils.api_client.codex.settings import USAGE_URL

    resp, err = _wham_request("GET", USAGE_URL)
    if err:
        return err
    return jsonify({"success": True, "usage": resp.json()})


@codex_auth_bp.route("/api/codex/reset-credits", methods=["GET"])
@api_login_required
@admin_api_required
def codex_reset_credits():
    """列出储存的限额重置额度（available/redeemed 同数组，status 区分，含获得/使用/过期时间）。"""
    from utils.api_client.codex.settings import RESET_CREDITS_URL

    resp, err = _wham_request("GET", RESET_CREDITS_URL)
    if err:
        return err
    return jsonify({"success": True, **resp.json()})


@codex_auth_bp.route("/api/codex/reset-credits/consume", methods=["POST"])
@api_login_required
@admin_api_required
def codex_reset_credits_consume():
    """兑换一个重置额度（不可逆）。body: {credit_id}；redeem_request_id 后端生成 uuid4（幂等键）。"""
    import uuid

    from flask import request

    from utils.api_client.codex.settings import RESET_CREDITS_CONSUME_URL

    data = request.get_json(silent=True) or {}
    credit_id = str(data.get("credit_id") or "").strip()
    if not credit_id:
        return jsonify({"success": False, "error": "missing credit_id"}), 400
    payload = {"credit_id": credit_id, "redeem_request_id": str(uuid.uuid4())}
    resp, err = _wham_request("POST", RESET_CREDITS_CONSUME_URL, json_body=payload)
    if err:
        return err
    return jsonify({"success": True, **resp.json()})


@codex_auth_bp.route("/api/codex/settings", methods=["GET", "POST"])
@api_login_required
@admin_api_required
def codex_settings():
    """codex 设置（proxy / client_version）。

    用户网络访问 chatgpt.com 必须走代理；proxy 为空表示跟随环境变量。
    """
    from flask import request

    from utils.api_client.codex.settings import (
        get_client_version,
        resolve_proxy,
        set_config_value,
    )

    if request.method == "GET":
        return jsonify(
            {
                "success": True,
                "proxy": resolve_proxy(),
                "client_version": get_client_version(),
            }
        )
    data = request.get_json(silent=True) or {}
    if "proxy" in data:
        raw = str(data.get("proxy") or "").strip()
        set_config_value("proxy", raw or None)
    if "client_version" in data:
        raw = str(data.get("client_version") or "").strip()
        set_config_value("client_version", raw or None)
    return jsonify({"success": True, "proxy": resolve_proxy()})


__all__ = ["codex_auth_bp"]
