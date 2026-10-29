"""Codex OAuth 登录链路（浏览器回环回调 + Device Code 无头双流）。

浏览器流（host 同机场景，与 Codex CLI / opencode / openai-oauth 一致）：
1. 生成 PKCE verifier/challenge 与 state；
2. 后台线程起 HTTP 服务**同时监听 127.0.0.1:1455 与 [::1]:1455**——
   localhost 在 macOS/Windows 双栈解析，浏览器（Happy Eyeballs）优先 IPv6，
   只听 IPv4 时回调会被占着 [::1]:1455 的其他程序（如 opencode）截胡
   （2026-09-24 真实事故：回调落到 opencode 报 "Invalid state"）；
3. 前端打开系统浏览器到授权 URL；
4. 用户授权后浏览器回调 /auth/callback?code&state；
5. 校验 state，code + verifier 换 token（后台线程执行，网络失败自动重试），
   原子写入 astrion 独立凭证文件；poll() 只读状态快照，请求线程零网络 IO。

Device Code 流（docker/无头场景，OpenAI 官方端点，2026-09-24 落地；字段与行为
逐行对齐 codex-rs login/src/device_code_auth.rs + 实测）：
1. POST deviceauth/usercode（JSON 仅 client_id）拿 device_auth_id/user_code/interval；
2. 管理员在任意设备浏览器开固定官方验证页 auth.openai.com/codex/device 输码授权；
3. 后台线程按服务端 interval 轮询 deviceauth/token（JSON device_auth_id+user_code；
   pending = 403/404 或 400+deviceauth_authorization_pending），授权完成后响应
   同时下发 authorization_code 与 PKCE code_verifier（服务端生成，客户端不事先持有）；
4. 同一 token 端点交换（redirect_uri 换设备流专用 + 服务端 verifier，网络失败自动重试），
   token 与浏览器流完全一致；总上限 15 分钟；
5. 全流程（请求码/轮询/换 token）都在后台 daemon 线程，poll() 只读状态快照。
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


class _LoginCancelled(Exception):
    """内部信号：凭证提交边界发现流程已被取消/判负，禁止落盘。"""


class CodexOAuthFlow:
    """一次登录流程的状态机：pending → completed / failed / timeout / cancelled。

    多账号（2026-10-29）：构造时传账号注册表，授权完成后由注册表按账号身份
    决定「更新原账号」还是「新建账号」（不在这里直接写凭证文件）。
    """

    def __init__(self, account_store) -> None:
        self._store = account_store
        self._status = "pending"
        self._error: Optional[str] = None
        self._account_id: Optional[str] = None
        self._connection_id: Optional[str] = None
        self._account_hash: Optional[str] = None
        self._servers: List[HTTPServer] = []
        self._done = threading.Event()
        self._started_at = time.time()

        self._verifier = secrets.token_urlsafe(64)[:64]
        digest = hashlib.sha256(self._verifier.encode()).digest()
        self._challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        self._state = secrets.token_urlsafe(24)
        self._code: Optional[str] = None
        # phase: waiting=等待浏览器授权回调；exchanging=换 token 中（后台线程）
        self._phase = "waiting"
        self._exchange_started = False
        # 取消/提交同步边界：cancel 与凭证落盘互斥，只能二选一
        self._commit_lock = threading.Lock()

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
        """前端轮询入口：纯内存状态快照，零网络 IO。

        回调到达后换 token 在后台线程执行（带重试，单次最长 30s 网络超时），
        本方法只负责 spawn 一次并返回当前 phase。
        """
        if self._status == "pending":
            if time.time() - self._started_at > FLOW_TIMEOUT_SECONDS:
                self._fail("登录超时（5 分钟未完成授权）")
            elif (
                self._done.is_set()
                and self._code
                and not self._exchange_started
            ):
                self._exchange_started = True
                self._phase = "exchanging"
                threading.Thread(
                    target=self._exchange, daemon=True, name="codex-oauth-exchange"
                ).start()
        result: Dict[str, Any] = {
            "status": self._status,
            "error": self._error,
            "account_id": self._account_id,
            "connection_id": self._connection_id,
            "account_hash": self._account_hash,
            "flow_mode": "browser",
        }
        if self._status == "pending":
            result["phase"] = self._phase
        return result

    def cancel(self) -> None:
        # 与凭证提交互斥：如果提交已开始（落盘临界区），取消等待并让登录正常
        # 完成；否则在落盘前把状态置为 cancelled，提交边界会拒绝写入。
        with self._commit_lock:
            if self._status == "pending":
                self._status = "cancelled"
        self._shutdown_server()

    # ------------------------------------------------------------- 内部步骤

    def _exchange(self) -> None:
        """后台线程：授权码换 token（网络失败自动重试）并由注册表落盘。"""
        assert self._code is not None
        try:
            error, account_id, record = _exchange_code_for_tokens_with_retry(
                self._store,
                code=self._code,
                verifier=self._verifier,
                redirect_uri=OAUTH_REDIRECT_URI,
                should_abort=lambda: self._status != "pending",
                commit=self._commit_tokens,
            )
        except _LoginCancelled:
            return  # 取消先于提交：不写凭证，终态仍为 cancelled
        if self._status not in ("pending", "completed"):
            return  # 已取消/超时判负：不覆盖终态
        if error:
            self._fail(error)
            return
        self._account_id = account_id
        if record:
            self._connection_id = str(record.get("id") or "") or None
            self._account_hash = str(record.get("hash") or "") or None
        self._status = "completed"
        self._shutdown_server()

    def _commit_tokens(self, action):
        """在取消/提交同步边界内落盘（与 cancel 互斥，避免取消后仍写凭证）。"""
        with self._commit_lock:
            if self._status != "pending":
                raise _LoginCancelled()
            record = action()
            # 与落盘同一临界区置 completed：之后到来的 cancel 只能看到已完成。
            self._status = "completed"
            return record

    def _fail(self, message: str) -> None:
        with self._commit_lock:
            if self._status != "pending":
                return  # 已完成/已取消：不覆盖终态，也不在完成后报失败
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


def _exchange_code_for_tokens(
    account_store,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    commit: Optional[Any] = None,
) -> tuple[Optional[str], Optional[str], Optional[Dict[str, Any]], bool]:
    """授权码 + PKCE verifier 换 token 并由账号注册表落盘。

    ``commit`` 可选：接收一个执行落盘的 callable，在流程自己的“取消/提交”
    同步边界内调用；被取消时抛 ``_LoginCancelled``。网络请求必须在边界外，
    因此取消按钮不会等网络超时，但落盘绝不会发生在取消之后。

    返回 (error, account_id, account_record, retryable)：retryable=True 表示
    网络层异常（请求可能未到达服务端），值得自动重试；HTTP 非 200 与
    凭证落盘失败不重试。account_record = {id, hash, display_name, ...}，
    重复登录时返回被更新的原账号（不新建）。
    """
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
        return f"换取 token 网络失败: {exc}", None, None, True
    if resp.status_code != 200:
        return f"换取 token 失败 (HTTP {resp.status_code}): {resp.text[:300]}", None, None, False
    payload = resp.json()
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    id_token = payload.get("id_token") or ""
    if not access or not refresh:
        return "token 响应缺少 access_token/refresh_token", None, None, False
    account_id = account_id_from_id_token(id_token)

    def _upsert():
        return account_store.upsert_tokens(
            access_token=access,
            refresh_token=refresh,
            id_token=id_token,
            account_id=account_id,
        )

    try:
        if commit is not None:
            record = commit(_upsert)
        else:
            record = _upsert()
    except _LoginCancelled:
        raise
    except Exception as exc:
        return f"写入凭证失败: {exc}", None, None, False
    return None, account_id, record, False


_EXCHANGE_RETRY_DELAYS = (2.0, 4.0)  # 第 2/3 次尝试前的等待秒数


def _exchange_code_for_tokens_with_retry(
    account_store,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    should_abort: Optional[Any] = None,
    commit: Optional[Any] = None,
) -> tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """换 token 带网络重试（最多 3 次，间隔 2s/4s）。

    返回 (error, account_id, account_record)；仅在后台线程中调用
    （单次尝试最长 30s 网络超时，严禁放请求线程）。should_abort（可选回调）
    在重试等待期间以 0.2s 粒度检查，返回 True 表示流程已被取消/超时，
    立即放弃且保留原错误（调用方据状态丢弃）。``commit`` 透传给底层，
    用于把凭证落盘限定在流程的取消/提交同步边界内。
    """
    delays = (0.0, *_EXCHANGE_RETRY_DELAYS)
    error: Optional[str] = None
    for attempt, delay in enumerate(delays, start=1):
        if delay > 0:
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                if should_abort is not None and should_abort():
                    return error, None, None
                time.sleep(min(0.2, deadline - time.monotonic()))
        error, account_id, record, retryable = _exchange_code_for_tokens(
            account_store,
            code=code,
            verifier=verifier,
            redirect_uri=redirect_uri,
            commit=commit,
        )
        if error is None:
            return None, account_id, record
        if not retryable:
            return error, None, None
    return error, None, None


class CodexDeviceFlow:
    """Device Code 登录流程（无头环境）：pending → completed / failed / timeout / cancelled。

    无 localhost 回调：管理员在任意设备浏览器开固定官方验证页输 user_code，
    本类按服务端 interval 轮询 deviceauth/token；授权码与 PKCE verifier 均由
    服务端在授权完成后随轮询响应下发（官方 codex-rs 同款，客户端不事先生成）。

    执行模型：start() 仅 spawn 后台 daemon 线程（请求设备码 → 按 interval 轮询 →
    拿码换 token，全程网络 IO 都在线程内），poll() 只读内存状态快照——
    status/poll 等 Flask 请求端点零阻塞，慢代理下不会再卡住页面加载。
    """

    def __init__(self, account_store) -> None:
        self._store = account_store
        self._status = "pending"
        self._error: Optional[str] = None
        self._account_id: Optional[str] = None
        self._connection_id: Optional[str] = None
        self._account_hash: Optional[str] = None
        self._started_at = time.time()
        # phase: starting=请求设备码中；waiting=等待用户输码授权；exchanging=换 token 中
        self._phase = "starting"
        self._wake = threading.Event()  # cancel 唤醒，中断轮询间隔等待
        self._commit_lock = threading.Lock()

        self._device_auth_id: Optional[str] = None
        self.user_code: Optional[str] = None
        self.verification_uri: Optional[str] = None
        self._poll_interval = DEVICE_POLL_INTERVAL_SECONDS

    # ------------------------------------------------------------- 生命周期

    def start(self) -> None:
        """起后台线程执行整个设备码流程（调用方立即返回，零网络 IO）。"""
        threading.Thread(
            target=self._run, daemon=True, name="codex-device-flow"
        ).start()

    def _run(self) -> None:
        """后台线程主流程：请求设备码 → 轮询授权 → 拿码换 token。"""
        try:
            self._request_user_code()
            if self._status != "pending":
                return
            self._phase = "waiting"
            while self._status == "pending":
                if time.time() - self._started_at > DEVICE_FLOW_TIMEOUT_SECONDS:
                    self._fail("登录超时（15 分钟未完成授权）")
                    return
                # 可唤醒等待：cancel 时立即退出；不打断已发出的单个请求
                if self._wake.wait(timeout=self._poll_interval):
                    return
                if self._status != "pending":
                    return
                self._poll_once()
        except Exception as exc:  # 兜底：线程异常静默死会让前端永远 pending
            self._fail(f"登录流程内部错误: {exc}")

    def _request_user_code(self) -> None:
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
        """前端轮询入口：纯内存状态快照（网络 IO 全部在后台线程），零阻塞。"""
        result: Dict[str, Any] = {
            "status": self._status,
            "error": self._error,
            "account_id": self._account_id,
            "connection_id": self._connection_id,
            "account_hash": self._account_hash,
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "flow_mode": "device",
        }
        if self._status == "pending":
            result["phase"] = self._phase
        return result

    def cancel(self) -> None:
        with self._commit_lock:
            if self._status == "pending":
                self._status = "cancelled"
        self._wake.set()

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
        """授权码换 token（网络失败自动重试）；仅在后台线程中调用。"""
        self._phase = "exchanging"
        try:
            error, account_id, record = _exchange_code_for_tokens_with_retry(
                self._store,
                code=code,
                verifier=verifier,
                redirect_uri=OAUTH_DEVICE_REDIRECT_URI,
                should_abort=lambda: self._status != "pending",
                commit=self._commit_tokens,
            )
        except _LoginCancelled:
            return  # 取消先于提交：不写凭证
        if self._status not in ("pending", "completed"):
            return  # 已取消：不覆盖终态
        if error:
            self._fail(error)
            return
        self._account_id = account_id
        if record:
            self._connection_id = str(record.get("id") or "") or None
            self._account_hash = str(record.get("hash") or "") or None
        self._status = "completed"

    def _commit_tokens(self, action):
        """与 cancel 互斥的凭证提交边界（设备码流与浏览器流同语义）。"""
        with self._commit_lock:
            if self._status != "pending":
                raise _LoginCancelled()
            record = action()
            self._status = "completed"
            return record

    def _fail(self, message: str) -> None:
        with self._commit_lock:
            if self._status != "pending":
                return
            self._status = "failed"
            self._error = message


# ---------------------------------------------------------------------------
# 进程级当前流程管理（server 蓝图使用；browser/device 两流互斥，同时只允许一个）
# ---------------------------------------------------------------------------

_current_flow: Optional[Any] = None  # CodexOAuthFlow | CodexDeviceFlow
_flow_lock = threading.Lock()


def start_login_flow(account_store) -> Dict[str, Any]:
    """开启浏览器登录流程。

    **串行约束（2026-10-29）**：已有进行中的登录流程时明确拒绝新请求
    （返回 error=login_in_progress + 既有流程状态），绝不静默取消/覆盖
    别人正在进行的登录。
    """
    global _current_flow
    with _flow_lock:
        if _current_flow is not None:
            state = _current_flow.poll()
            if state["status"] == "pending":
                return {**state, "reused": True, "error": "login_in_progress"}
            _current_flow = None
        flow = CodexOAuthFlow(account_store)
        flow.start()
        state = flow.poll()
        if state["status"] == "failed":
            return state
        _current_flow = flow
        return {"authorize_url": flow.authorize_url, **state}


def start_device_login_flow(account_store) -> Dict[str, Any]:
    """开启设备码登录流程；已有进行中的流程时明确拒绝（不覆盖/不取消）。

    start() 仅 spawn 后台线程，本函数立即返回 pending（phase=starting）；
    设备码请求失败等错误由后台线程异步置 failed，经 poll 呈现给前端。
    """
    global _current_flow
    with _flow_lock:
        if _current_flow is not None:
            state = _current_flow.poll()
            if state["status"] == "pending":
                return {**state, "reused": True, "error": "login_in_progress"}
            _current_flow = None
        flow = CodexDeviceFlow(account_store)
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
