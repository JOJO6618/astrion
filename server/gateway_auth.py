"""Gateway 本机客户端通道认证（host Bearer token，2026-09-08 新增）。

定位：协议 docs/runtime_protocol.md §6「认证与授权按通道裁剪」的 host 通道
实现——让 CLI/GUI 等本机客户端无需 Web 会话流程（Cookie/CSRF/host-login）
即可接入 Gateway 公共协议端点。

安全模型与 /host-login 完全对齐（host 模式定位为本机单人使用）：
- 仅 TERMINAL_SANDBOX_MODE=host（且非 LINUX_SAFETY）生效；
- 仅允许回环地址（127.0.0.1/::1/localhost）直连调用；
- token 为本机生成的随机串，存于运行态数据目录
  （<DATA_DIR>/host_api_token，权限 0600），本机客户端读取后经
  `Authorization: Bearer <token>` 携带。
"""
from __future__ import annotations

import functools
import hmac
import os
import secrets
from pathlib import Path
from typing import Optional

from flask import current_app, jsonify, request, session

from config import DATA_DIR, TERMINAL_SANDBOX_MODE
from config.terminal import LINUX_SAFETY
from modules.host_workspace_manager import resolve_host_workspace
from modules.i18n import tr
from server.auth_helpers import is_logged_in

_LOOPBACK_ADDRS = {"127.0.0.1", "::1", "localhost"}
_TOKEN_FILENAME = "host_api_token"


def _host_channel_enabled() -> bool:
    return (TERMINAL_SANDBOX_MODE or "").lower() == "host" and not LINUX_SAFETY


def host_api_token_path() -> Path:
    return Path(DATA_DIR).expanduser() / _TOKEN_FILENAME


def get_or_create_host_api_token() -> Optional[str]:
    """读取（不存在则生成）host 通道 token；非 host 模式返回 None。

    生成使用临时文件 + os.replace 原子替换 + 0600 权限。本地单进程
    场景下并发首次生成概率极低；万一竞争产生两个 token，后写覆盖先写，
    持旧 token 的一方 401 后重读文件即可自愈。
    """
    if not _host_channel_enabled():
        return None
    path = host_api_token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(token + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        return None
    return token


def _extract_bearer_token() -> str:
    auth_header = request.headers.get("Authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def _verify_host_bearer(token: str) -> bool:
    """校验 host Bearer token：host 模式 + 回环地址 + token 匹配。"""
    if not token or not _host_channel_enabled():
        return False
    remote_addr = (request.remote_addr or "").strip()
    if remote_addr not in _LOOPBACK_ADDRS:
        return False
    expected = get_or_create_host_api_token()
    if not expected:
        return False
    return hmac.compare_digest(token, expected)


def _inject_host_identity() -> None:
    """注入与 /host-login 等价的 host 会话身份（复用现有上下文装配逻辑）。

    session 字段与 server/auth.py 的 host_login 保持一致（含 login_nonce），
    保证路由内及下游 is_logged_in()/principal 构造等既有逻辑无差异。

    工作区绑定：CLI 等本机客户端以启动目录为工作区，经
    `X-Astrion-Workspace-Id` 头声明；未声明或 id 不存在时 resolve 回退默认
    工作区（与 web host-login 语义一致）。host 本机单人模型下客户端声明
    不构成越权（工作区均属同一 host 用户）。
    """
    from server.auth import _issue_login_nonce

    requested_workspace_id = (request.headers.get("X-Astrion-Workspace-Id") or "").strip() or None
    _, host_workspace = resolve_host_workspace(requested_workspace_id)
    session["logged_in"] = True
    session["username"] = "host"
    session["role"] = "admin"
    session["host_mode"] = True
    if host_workspace:
        workspace_id = host_workspace.get("workspace_id") or "default"
        session["host_workspace_id"] = workspace_id
        session["workspace_id"] = workspace_id
    default_thinking = current_app.config.get("DEFAULT_THINKING_MODE", False)
    session["thinking_mode"] = default_thinking
    session["run_mode"] = current_app.config.get(
        "DEFAULT_RUN_MODE", "deep" if default_thinking else "fast"
    )
    session.permanent = True
    _issue_login_nonce("host")


def api_login_or_host_token_required(view_func):
    """双通道认证装饰器：host Bearer token 或 Web session 登录。

    - 携带 Bearer token 且匹配 host 通道 → 注入 host 身份并放行
    - 否则回退 Web session 检查（is_logged_in）
    Bearer 请求天然跳过 CSRF（server/security.py 已放行 Authorization 头）。
    """

    @functools.wraps(view_func)
    def wrapped(*args, **kwargs):
        token = _extract_bearer_token()
        if token and _verify_host_bearer(token):
            _inject_host_identity()
            return view_func(*args, **kwargs)
        if not is_logged_in():
            return jsonify({"success": False, "error": tr("auth.session_expired")}), 401
        return view_func(*args, **kwargs)

    return wrapped


__all__ = [
    "api_login_or_host_token_required",
    "get_or_create_host_api_token",
    "host_api_token_path",
]
