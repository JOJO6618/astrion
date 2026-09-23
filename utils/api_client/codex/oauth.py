"""Codex OAuth 登录链路（浏览器回环回调 + Device Code 无头双流）。

浏览器流（host 同机场景，与 Codex CLI / opencode / openai-oauth 一致）：
1. 生成 PKCE verifier/challenge 与 state；
2. 后台线程起 HTTP 服务**同时监听 127.0.0.1:1455 与 [::1]:1455**——
   localhost 在 macOS/Windows 双栈解析，浏览器（Happy Eyeballs）优先 IPv6，
   只听 IPv4 时回调会被占着 [::1]:1455 的其他程序（如 opencode）截胡
   （2026-09-24 真实事故：回调落到 opencode 报 "Invalid state"）；
3. 前端打开系统浏览器到授权 URL；
4. 用户授权后浏览器回调 /auth/callback?code&state；
5. 校验 state，code + verifier 换 token，原子写入 astrion 独立凭证文件。

Device Code 流（docker/无头场景，OpenAI 官方端点，2026-09-24 落地；字段与行为
逐行对齐 codex-rs login/src/device_code_auth.rs + 实测）：
1. POST deviceauth/usercode（JSON 仅 client_id）拿 device_auth_id/user_code/interval；
2. 管理员在任意设备浏览器开固定官方验证页 auth.openai.com/codex/device 输码授权；
3. 按服务端 interval 轮询 deviceauth/token（JSON device_auth_id+user_code；
   pending = 403/404 或 400+deviceauth_authorization_pending），授权完成后响应
   同时下发 authorization_code 与 PKCE code_verifier（服务端生成，客户端不事先持有）；
4. 同一 token 端点交换（redirect_uri 换设备流专用 + 服务端 verifier），
   token 与浏览器流完全一致；总上限 15 分钟。
前提：ChatGPT 账号设置 → 安全里开启 device code authentication。
两流共用 _current_flow 互斥（同时只允许一个登录流程）与同一凭证落盘逻辑。
"""

from __future__ import annotations

import hashlib
import base64
import json
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

import httpx

from utils.api_client.codex.auth import (
    CodexAuthManager,
    account_id_from_id_token,
)
from utils.api_client.codex.settings import (
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_DEVICE_CODE_URL,
    OAUTH_DEVICE_REDIRECT_URI,
    OAUTH_DEVICE_TOKEN_URL,
    OAUTH_DEVICE_VERIFY_URL,
    OAUTH_LISTEN_PORT,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPE,
    OAUTH_TOKEN_URL,
    resolve_proxy,
)

FLOW_TIMEOUT_SECONDS = 300
# 设备码流程更长（用户需手动开页面输码）：与 codex-rs 官方一致 15 分钟
DEVICE_FLOW_TIMEOUT_SECONDS = 15 * 60
DEVICE_POLL_INTERVAL_SECONDS = 5.0

_CALLBACK_OK_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Codex 登录</title></head>
<body style="font-family:system-ui;text-align:center;padding-top:80px">
<h2>Codex 授权成功</h2>
<p>可以关闭此页面，回到 Astrion 继续。</p>
</body></html>"""

_CALLBACK_FAIL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Codex 登录</title></head>
<body style="font-family:system-ui;text-align:center;padding-top:80px">
<h2>授权失败</h2>
<p>{error}</p>
</body></html>"""


class _V6OnlyHTTPServer(HTTPServer):
    """IPv6 loopback 监听（显式 V6ONLY，避免与 IPv4 监听抢地址族）。"""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


class CodexOAuthFlow:
    """一次登录流程的状态机：pending → completed / failed / timeout / cancelled。"""

    def __init__(self, auth_manager: CodexAuthManager) -> None:
        self._auth = auth_manager
        self._status = "pending"
        self._error: Optional[str] = None
        self._account_id: Optional[str] = None
        self._servers: List[HTTPServer] = []
        self._done = threading.Event()
        self._started_at = time.time()

        self._verifier = secrets.token_urlsafe(64)[:64]
        digest = hashlib.sha256(self._verifier.encode()).digest()
        self._challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        self._state = secrets.token_urlsafe(24)
        self._code: Optional[str] = None

    # ------------------------------------------------------------- 生命周期

    @property
    def authorize_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "scope": OAUTH_SCOPE,
            "code_challenge": self._challenge,
            "code_challenge_method": "S256",
            "state": self._state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        }
        return f"{OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def start(self) -> None:
        """起本地监听线程（IPv4+IPv6 双栈，任一失败整体报错并释放）。"""
        flow = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib 命名
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/auth/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(parsed.query)
                error = (query.get("error") or [None])[0]
                code = (query.get("code") or [None])[0]
                state = (query.get("state") or [None])[0]
                if error:
                    flow._fail(f"授权页返回错误: {error}")
                    self._reply(_CALLBACK_FAIL_HTML.format(error=error))
                    return
                if not code or state != flow._state:
                    flow._fail("回调参数校验失败（state 不匹配或缺少 code）")
                    self._reply(_CALLBACK_FAIL_HTML.format(error="参数校验失败"))
                    return
                flow._code = code
                self._reply(_CALLBACK_OK_HTML)
                flow._done.set()

            def _reply(self, html: str) -> None:
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # 静默
                return

        servers: List[HTTPServer] = []
        try:
            servers.append(HTTPServer(("127.0.0.1", OAUTH_LISTEN_PORT), _Handler))
            servers.append(_V6OnlyHTTPServer(("::1", OAUTH_LISTEN_PORT), _Handler))
        except OSError as exc:
            for srv in servers:
                try:
                    srv.server_close()
                except Exception:
                    pass
            self._fail(
                f"无法监听 {OAUTH_LISTEN_PORT} 端口（{exc}）。"
                "回调端口被其他程序占用（常见：opencode / Codex CLI 正在运行），"
                "请关闭后重试。"
            )
            return
        self._servers = servers
        for idx, srv in enumerate(servers):
            threading.Thread(
                target=srv.serve_forever, daemon=True, name=f"codex-oauth-{idx}"
            ).start()

    def poll(self) -> Dict[str, Any]:
        """前端轮询入口：回调到达后同步完成换 token（幂等）。"""
        if self._status == "pending":
            if time.time() - self._started_at > FLOW_TIMEOUT_SECONDS:
                self._fail("登录超时（5 分钟未完成授权）")
            elif self._done.is_set() and self._code:
                self._exchange()
        return {
            "status": self._status,
            "error": self._error,
            "account_id": self._account_id,
            "flow_mode": "browser",
        }

    def cancel(self) -> None:
        if self._status == "pending":
            self._status = "cancelled"
        self._shutdown_server()

    # ------------------------------------------------------------- 内部步骤

    def _exchange(self) -> None:
        """授权码换 token 并落盘（仅在 poll 中执行一次）。"""
        assert self._code is not None
        error, account_id = _exchange_code_for_tokens(
            self._auth,
            code=self._code,
            verifier=self._verifier,
            redirect_uri=OAUTH_REDIRECT_URI,
        )
        if error:
            self._fail(error)
            return
        self._account_id = account_id
        self._status = "completed"
        self._shutdown_server()

    def _fail(self, message: str) -> None:
        self._status = "failed"
        self._error = message
        self._done.set()
        # 释放监听必须异步：_fail 可能在 handler 线程内被调用（state 不匹配 /
        # 授权错误分支），而 HTTPServer.shutdown() 会等待 serve_forever 退出——
        # 同线程调用即自等死锁（handler 正在 serve_forever 循环体内执行）。
        threading.Thread(
            target=self._shutdown_server, daemon=True, name="codex-oauth-close"
        ).start()

    def _shutdown_server(self) -> None:
        for srv in self._servers:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        self._servers = []


# ---------------------------------------------------------------------------
# 共享步骤：授权码交换 + 凭证落盘（浏览器流 / 设备码流共用）
# ---------------------------------------------------------------------------


def _persist_tokens(
    auth_manager: CodexAuthManager,
    access: str,
    refresh: str,
    id_token: str,
    account_id: Optional[str],
) -> None:
    """写入 astrion 独立凭证文件（结构与 CLI auth.json 对齐；不触碰 CLI 文件）。"""
    from datetime import datetime, timezone
    import json as _json
    import os
    from pathlib import Path

    path = Path(auth_manager.auth_path)
    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            existing = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    data: Dict[str, Any] = {
        "auth_mode": existing.get("auth_mode") or "chatgpt",
        "OPENAI_API_KEY": existing.get("OPENAI_API_KEY"),
        "tokens": {
            "id_token": id_token,
            "access_token": access,
            "refresh_token": refresh,
            "account_id": account_id,
        },
        "last_refresh": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
    }
    tmp = path.with_suffix(path.suffix + ".tmp-astrion")
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    auth_manager.reload()


def _exchange_code_for_tokens(
    auth_manager: CodexAuthManager,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> tuple[Optional[str], Optional[str]]:
    """授权码 + PKCE verifier 换 token 并原子落盘。返回 (error, account_id)。"""
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": OAUTH_CLIENT_ID,
            "code_verifier": verifier,
        }
    ).encode()
    try:
        with httpx.Client(proxy=resolve_proxy(), timeout=30) as client:
            resp = client.post(
                OAUTH_TOKEN_URL,
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as exc:
        return f"换取 token 网络失败: {exc}", None
    if resp.status_code != 200:
        return f"换取 token 失败 (HTTP {resp.status_code}): {resp.text[:300]}", None
    payload = resp.json()
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    id_token = payload.get("id_token") or ""
    if not access or not refresh:
        return "token 响应缺少 access_token/refresh_token", None
    account_id = account_id_from_id_token(id_token)
    try:
        _persist_tokens(auth_manager, access, refresh, id_token, account_id)
    except Exception as exc:
        return f"写入凭证失败: {exc}", None
    return None, account_id


class CodexDeviceFlow:
    """Device Code 登录流程（无头环境）：pending → completed / failed / timeout / cancelled。

    无 localhost 回调：管理员在任意设备浏览器开固定官方验证页输 user_code，
    本类按服务端 interval 轮询 deviceauth/token；授权码与 PKCE verifier 均由
    服务端在授权完成后随轮询响应下发（官方 codex-rs 同款，客户端不事先生成）。
    """

    def __init__(self, auth_manager: CodexAuthManager) -> None:
        self._auth = auth_manager
        self._status = "pending"
        self._error: Optional[str] = None
        self._account_id: Optional[str] = None
        self._started_at = time.time()

        self._device_auth_id: Optional[str] = None
        self.user_code: Optional[str] = None
        self.verification_uri: Optional[str] = None
        self._poll_interval = DEVICE_POLL_INTERVAL_SECONDS
        self._next_poll_at = 0.0

    # ------------------------------------------------------------- 生命周期

    def start(self) -> None:
        """请求设备码（JSON，官方口径仅 client_id）；失败直接进入 failed。"""
        try:
            with httpx.Client(proxy=resolve_proxy(), timeout=30) as client:
                resp = client.post(
                    OAUTH_DEVICE_CODE_URL,
                    json={"client_id": OAUTH_CLIENT_ID},
                    headers={"Accept": "application/json"},
                )
        except Exception as exc:
            self._fail(f"请求设备码网络失败: {exc}")
            return
        if resp.status_code != 200:
            hint = ""
            if resp.status_code == 404:
                hint = "（设备码登录未开启：请在 ChatGPT 设置 → 安全中开启 device code authentication）"
            self._fail(
                f"请求设备码失败 (HTTP {resp.status_code}): {resp.text[:300]}{hint}"
            )
            return
        try:
            payload = resp.json()
        except Exception:
            self._fail("设备码响应不是合法 JSON")
            return
        self._device_auth_id = payload.get("device_auth_id")
        self.user_code = payload.get("user_code") or payload.get("usercode")
        self.verification_uri = OAUTH_DEVICE_VERIFY_URL
        # interval 官方为字符串秒（如 "5"），解析失败回退默认 5s
        try:
            interval = float(str(payload.get("interval") or "").strip())
            if interval > 0:
                self._poll_interval = interval
        except (TypeError, ValueError):
            pass
        if not (self._device_auth_id and self.user_code):
            self._fail("设备码响应缺少必要字段（device_auth_id/user_code）")

    def poll(self) -> Dict[str, Any]:
        """前端轮询入口：按 interval 轮询授权状态，拿到授权码即同步换 token。"""
        if self._status == "pending":
            if time.time() - self._started_at > DEVICE_FLOW_TIMEOUT_SECONDS:
                self._fail("登录超时（15 分钟未完成授权）")
            elif self._device_auth_id and time.monotonic() >= self._next_poll_at:
                self._next_poll_at = time.monotonic() + self._poll_interval
                self._poll_once()
        return {
            "status": self._status,
            "error": self._error,
            "account_id": self._account_id,
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "flow_mode": "device",
        }

    def cancel(self) -> None:
        if self._status == "pending":
            self._status = "cancelled"

    # ------------------------------------------------------------- 内部步骤

    def _poll_once(self) -> None:
        """单次轮询授权状态；网络抖动静默重试，终态错误才 fail。"""
        try:
            with httpx.Client(proxy=resolve_proxy(), timeout=30) as client:
                resp = client.post(
                    OAUTH_DEVICE_TOKEN_URL,
                    json={
                        "device_auth_id": self._device_auth_id,
                        "user_code": self.user_code,
                    },
                    headers={"Accept": "application/json"},
                )
        except Exception:
            return
        if resp.status_code == 200:
            try:
                payload = resp.json()
            except Exception:
                return
            if not isinstance(payload, dict):
                return
            authorization_code = payload.get("authorization_code")
            code_verifier = payload.get("code_verifier")
            if authorization_code and code_verifier:
                self._exchange(str(authorization_code), str(code_verifier))
            return
        # pending 口径：官方实现为 403/404；实测亦见 400 + deviceauth_authorization_pending
        if resp.status_code in (403, 404):
            return
        if resp.status_code == 400:
            try:
                err = resp.json().get("error") or {}
            except Exception:
                err = {}
            if isinstance(err, dict) and err.get("code") == (
                "deviceauth_authorization_pending"
            ):
                return
        self._fail(f"设备授权失败 (HTTP {resp.status_code}): {resp.text[:300]}")

    def _exchange(self, code: str, verifier: str) -> None:
        error, account_id = _exchange_code_for_tokens(
            self._auth,
            code=code,
            verifier=verifier,
            redirect_uri=OAUTH_DEVICE_REDIRECT_URI,
        )
        if error:
            self._fail(error)
            return
        self._account_id = account_id
        self._status = "completed"

    def _fail(self, message: str) -> None:
        self._status = "failed"
        self._error = message


# ---------------------------------------------------------------------------
# 进程级当前流程管理（server 蓝图使用；browser/device 两流互斥，同时只允许一个）
# ---------------------------------------------------------------------------

_current_flow: Optional[Any] = None  # CodexOAuthFlow | CodexDeviceFlow
_flow_lock = threading.Lock()


def start_login_flow(auth_manager: CodexAuthManager) -> Dict[str, Any]:
    """开启浏览器登录流程；已有进行中的流程时直接返回其状态（幂等）。"""
    global _current_flow
    with _flow_lock:
        if _current_flow is not None:
            state = _current_flow.poll()
            if state["status"] == "pending":
                if isinstance(_current_flow, CodexOAuthFlow):
                    return {"authorize_url": _current_flow.authorize_url, **state}
                return state  # 对方为设备流：返回其状态，前端按 flow_mode 展示
            _current_flow = None
        flow = CodexOAuthFlow(auth_manager)
        flow.start()
        state = flow.poll()
        if state["status"] == "failed":
            return state
        _current_flow = flow
        return {"authorize_url": flow.authorize_url, **state}


def start_device_login_flow(auth_manager: CodexAuthManager) -> Dict[str, Any]:
    """开启设备码登录流程；已有进行中的流程时直接返回其状态（幂等）。"""
    global _current_flow
    with _flow_lock:
        if _current_flow is not None:
            state = _current_flow.poll()
            if state["status"] == "pending":
                if isinstance(_current_flow, CodexOAuthFlow):
                    return {"authorize_url": _current_flow.authorize_url, **state}
                return state
            _current_flow = None
        flow = CodexDeviceFlow(auth_manager)
        flow.start()
        state = flow.poll()
        if state["status"] == "failed":
            return state
        _current_flow = flow
        return state


def poll_login_flow() -> Dict[str, Any]:
    with _flow_lock:
        if _current_flow is None:
            return {"status": "idle"}
        return _current_flow.poll()


def cancel_login_flow() -> Dict[str, Any]:
    global _current_flow
    with _flow_lock:
        if _current_flow is not None:
            _current_flow.cancel()
            _current_flow = None
        return {"status": "cancelled"}
