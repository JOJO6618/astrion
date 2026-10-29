"""Codex 账号管理 API：多账号状态、OAuth 登录流程、登出、用量与重置。

opencode 模式（独立凭证）：凭证唯一事实源是 astrion 自己的凭证文件，与 Codex CLI
的 ``~/.codex/auth.json`` 完全解耦——
- 「登出」= 删除该账号自己的凭证文件（真登出），CLI 登录态不受影响；
- 登录走独立 OAuth 会话（独立 refresh token 族），刷新只回写自己的文件。

多账号（2026-10-29，用户要求）：
- 每个账号一个连接 id + 独立凭证/模型缓存；``/api/codex/accounts`` 列出全部账号
  （id/短哈希/显示名/连接状态）；所有查询/操作用 ``account_id`` 参数指定账号；
- 不传 ``account_id`` 时使用「主账号」= 旧凭证迁移账号（legacy），否则最早添加的
  账号；被删除后自然顺延到下一个；
- 重复登录同一 ChatGPT 账号 = 更新原账号凭证（不新建、不改变连接 id/显示哈希）；
- 同一时刻只允许一个登录流程：已有 pending 流程时新请求返回 409
  ``login_in_progress``（绝不取消/覆盖别人的登录）。

权限模型（2026-09-24 保持）：本蓝图全部端点仅管理员可用（登录 + 管理员双重校验）。
docker 多用户模式下由管理员统一登录账号，订阅凭证全局共享；模型列表对全员可见
（模型暴露只看凭证文件存在性，见 codex/models.py），普通用户可直接选用模型，
消耗的是对应账号的订阅额度。
"""

from __future__ import annotations

import threading

from flask import Blueprint, jsonify, request

from server.auth_helpers import admin_api_required, api_login_required

from utils.api_client.codex.accounts import get_codex_account_store
from utils.api_client.codex.auth import CodexAuthError
from utils.api_client.codex.models import get_models_manager
from utils.api_client.codex.oauth import (
    cancel_login_flow,
    poll_login_flow,
    start_device_login_flow,
    start_login_flow,
)

codex_auth_bp = Blueprint("codex_auth", __name__)


def _requested_account_id() -> str:
    """账号选择器：query ``?account_id=`` 优先，其次 JSON body（GET 允许无 body）。

    空串 = 主账号（由注册表解析）。
    """
    raw = request.args.get("account_id")
    if raw is None:
        data = request.get_json(silent=True) or {}
        raw = data.get("account_id") if isinstance(data, dict) else None
    return str(raw or "").strip()


def _resolve_account(account_id: str):
    """返回 (resolved_id, None) 或 (None, (flask_response, status_code))。"""
    try:
        store = get_codex_account_store()
        return store.resolve_account_id(account_id or None), None
    except KeyError:
        return None, (
            jsonify({"success": False, "error": "account_not_found"}),
            404,
        )
    except Exception as exc:  # 注册表损坏等：明确报错，不静默回落到别的账号
        return None, (
            jsonify({"success": False, "error": f"account_registry_error: {exc}"}),
            500,
        )


@codex_auth_bp.route("/api/codex/accounts", methods=["GET"])
@api_login_required
@admin_api_required
def codex_accounts():
    """已登录的 ChatGPT 订阅账号列表（Codex 页账号选择器数据源）。

    仅返回公开字段（连接 id / 稳定短哈希 / 显示名 / 连接状态 / 时间），
    绝不返回 token 或 account_id 原文。
    """
    store = get_codex_account_store()
    accounts = store.list_accounts()
    primary = store.primary_account_id()
    for item in accounts:
        item["is_primary"] = item["id"] == primary
        # 凭证暂缺时缓存可能是旧数据：不给前端展示“有 N 个模型”的假象
        item["models"] = (
            {"model_count": 0, "total_models": 0}
            if item.get("credential_missing")
            else _account_models_summary(item["id"])
        )
    return jsonify({"success": True, "accounts": accounts, "primary_account_id": primary})


def _account_models_summary(account_id: str) -> dict:
    """单账号模型缓存摘要（失败返回空摘要，不影响账号列表）。"""
    try:
        return get_models_manager(account_id).status_summary()
    except Exception:
        return {"model_count": 0, "total_models": 0}


@codex_auth_bp.route("/api/codex/status", methods=["GET"])
@api_login_required
@admin_api_required
def codex_status():
    """账号连接状态 + 模型缓存摘要 + 登录流程状态。

    ``account_id`` 缺省 = 主账号；响应始终回带 ``account_id`` 与
    ``account_hash``，前端据此丢弃晚到的旧账号响应。
    """
    account_id, err = _resolve_account(_requested_account_id())
    if err:
        store = get_codex_account_store()
        if not _requested_account_id() and not store.list_accounts():
            # 一个账号都没登录：返回空状态（与旧行为一致，前端展示未连接）
            return jsonify(
                {
                    "success": True,
                    "connected": False,
                    "account_id": None,
                    "account_hash": None,
                    "account_name": None,
                    "is_primary": True,
                    "models": {"model_count": 0, "total_models": 0},
                    "login_flow": poll_login_flow(),
                    "error": "no_codex_account",
                }
            )
        return err
    store = get_codex_account_store()
    account = store.get_account(account_id)
    auth = store.get_auth_manager(account_id)
    auth_status = auth.status()
    models_summary = get_models_manager(account_id).status_summary()
    login_state = poll_login_flow()
    return jsonify(
        {
            "success": True,
            **auth_status,
            "account_id": account_id,
            "account_hash": account.get("hash"),
            "account_name": account.get("display_name"),
            "is_primary": account_id == store.primary_account_id(),
            "models": models_summary,
            "login_flow": login_state,
        }
    )


@codex_auth_bp.route("/api/codex/login/start", methods=["POST"])
@api_login_required
@admin_api_required
def codex_login_start():
    """启动 OAuth 登录：起 1455 监听，返回授权 URL（前端负责打开浏览器）。

    登录目标账号在授权完成后按账号身份决定（同账号更新、新账号新建）；
    已有进行中的登录流程时明确拒绝（409 login_in_progress）。
    """
    store = get_codex_account_store()
    result = start_login_flow(store)
    if result.get("error") == "login_in_progress":
        return jsonify({"success": False, **result}), 409
    if result.get("status") == "failed":
        return jsonify({"success": False, "error": result.get("error")}), 500
    return jsonify({"success": True, **result})


@codex_auth_bp.route("/api/codex/login/poll", methods=["GET"])
@api_login_required
@admin_api_required
def codex_login_poll():
    """轮询登录进度；完成时后台刷新「本次登录落到的那一个账号」的模型列表。"""
    state = poll_login_flow()
    if state.get("status") == "completed":
        target = str(state.get("connection_id") or "").strip()
        if target:
            models = get_models_manager(target)
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
    store = get_codex_account_store()
    result = start_device_login_flow(store)
    if result.get("error") == "login_in_progress":
        return jsonify({"success": False, **result}), 409
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
    """登出指定账号：删除该账号凭证文件 + 索引条目（真登出）。

    body/query 的 ``account_id`` 缺省 = 主账号；删除后返回新的主账号。
    """
    account_id, err = _resolve_account(_requested_account_id())
    if err:
        return err
    store = get_codex_account_store()
    try:
        removed = store.logout(account_id)
    except OSError as exc:
        # 凭证文件删除失败（权限/占用）：保持账号登记与凭证原样，返回结构化错误，
        # 不把“没登出”包装成成功，也不静默删除索引。
        return (
            jsonify(
                {
                    "success": False,
                    "error": "logout_failed",
                    "account_id": account_id,
                    "detail": str(exc),
                }
            ),
            500,
        )
    # 内存中的模型缓存也同步丢弃（磁盘缓存已由 store 清理，best-effort）
    from utils.api_client.codex.models import reset_models_manager

    reset_models_manager(account_id)
    return jsonify({"success": True, "account_id": account_id, **removed})


@codex_auth_bp.route("/api/codex/models/refresh", methods=["POST"])
@api_login_required
@admin_api_required
def codex_models_refresh():
    """手动刷新指定账号的模型列表（在线拉取，ETag 条件请求，账号缓存隔离）。

    契约对齐 /api/providers/<id>/refresh：始终携带 models_count（当前模型
    原始总数），供「提供商」分区统一展示「已刷新，共 N 个模型」。
    """
    account_id, err = _resolve_account(_requested_account_id())
    if err:
        return err
    manager = get_models_manager(account_id)
    result = manager.refresh_sync()
    count = result.get("count")
    if not isinstance(count, int):
        # 304 / 降级路径 refresh_sync 不带 count：回读当前缓存原始总数
        count = int(manager.status_summary().get("total_models") or 0)
    return jsonify(
        {"success": True, "account_id": account_id, "models_count": count, **result}
    )


def _wham_request(account_id: str, method: str, url: str, json_body: dict | None = None):
    """wham/* 端点统一转发：目标账号 token（临期自动刷新回写）+ 标准头 + 代理。

    返回 (httpx.Response, None) 或 (None, (flask_response, status_code)) 错误二元组。
    """
    import httpx

    from utils.api_client.codex.settings import resolve_proxy

    store = get_codex_account_store()
    auth = store.get_auth_manager(account_id)
    try:
        access = auth.get_access_token_sync()
    except CodexAuthError as exc:
        return None, (jsonify({"success": False, "error": f"refresh_failed: {exc}"}), 502)
    except Exception as exc:
        return None, (jsonify({"success": False, "error": f"refresh_failed: {exc}"}), 502)
    if not access:
        return None, (jsonify({"success": False, "error": "no_credentials"}), 401)
    headers = {
        "Authorization": f"Bearer {access}",
        "originator": "codex_cli_rs",
        "accept": "application/json",
    }
    account_id_header = auth.get_account_id()
    if account_id_header:
        headers["chatgpt-account-id"] = account_id_header
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

    account_id, err = _resolve_account(_requested_account_id())
    if err:
        return err
    resp, wham_err = _wham_request(account_id, "GET", USAGE_URL)
    if wham_err:
        return wham_err
    return jsonify(
        {"success": True, "account_id": account_id, "usage": resp.json()}
    )


@codex_auth_bp.route("/api/codex/reset-credits", methods=["GET"])
@api_login_required
@admin_api_required
def codex_reset_credits():
    """列出指定账号储存的限额重置额度（available/redeemed 同数组，status 区分）。"""
    from utils.api_client.codex.settings import RESET_CREDITS_URL

    account_id, err = _resolve_account(_requested_account_id())
    if err:
        return err
    resp, wham_err = _wham_request(account_id, "GET", RESET_CREDITS_URL)
    if wham_err:
        return wham_err
    return jsonify({"success": True, "account_id": account_id, **resp.json()})


@codex_auth_bp.route("/api/codex/reset-credits/consume", methods=["POST"])
@api_login_required
@admin_api_required
def codex_reset_credits_consume():
    """兑换指定账号的一个重置额度（不可逆）。

    body: {credit_id, account_id?}；redeem_request_id 后端生成 uuid4（幂等键）。
    account_id 必须在请求体中显式携带（前端捕获当时选中的账号），避免
    用户切换选择器后把兑换打到另一个账号。
    """
    import uuid

    from utils.api_client.codex.settings import RESET_CREDITS_CONSUME_URL

    account_id, err = _resolve_account(_requested_account_id())
    if err:
        return err
    data = request.get_json(silent=True) or {}
    credit_id = str(data.get("credit_id") or "").strip()
    if not credit_id:
        return jsonify({"success": False, "error": "missing credit_id"}), 400
    payload = {"credit_id": credit_id, "redeem_request_id": str(uuid.uuid4())}
    resp, wham_err = _wham_request(
        account_id, "POST", RESET_CREDITS_CONSUME_URL, json_body=payload
    )
    if wham_err:
        return wham_err
    return jsonify({"success": True, "account_id": account_id, **resp.json()})


@codex_auth_bp.route("/api/codex/settings", methods=["GET", "POST"])
@api_login_required
@admin_api_required
def codex_settings():
    """codex 设置（全局 proxy / client_version，不随账号变化）。

    用户网络访问 chatgpt.com 必须走代理；proxy 为空表示跟随环境变量。
    """
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
