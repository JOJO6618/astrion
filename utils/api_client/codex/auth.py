"""Codex OAuth 凭证管理：读取、JWT 过期解析、刷新、原子回写、401 自愈。

opencode 模式（独立凭证，见 docs/codex_integration_plan.md）：
- 凭证存 astrion 自己的 ``codex_auth.json``，与 Codex CLI 的 ``~/.codex/auth.json``
  完全解耦——各自独立 OAuth 会话（独立 refresh token 族），互不干扰；
- 登出 = :meth:`CodexAuthManager.logout` 删除自己的凭证文件，CLI 登录态不受影响；
- refresh_token 轮换制：任何刷新成功后必须原子回写，否则本侧登录态报废；
- 回写保留原文件结构（auth_mode/OPENAI_API_KEY/tokens/last_refresh），0600；
- 过期时间从 access_token JWT 的 ``exp`` claim 解析（不验签，只读时间）；
- 401 错误体 ``detail.code == "token_expired"`` 时走 :meth:`handle_unauthorized`。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from utils.api_client.codex.settings import (
    CODEX_AUTH_PATH,
    OAUTH_CLIENT_ID,
    OAUTH_TOKEN_URL,
    REFRESH_SKEW_SECONDS,
    resolve_proxy,
)


class CodexAuthError(Exception):
    """凭证缺失/刷新失败等不可恢复认证错误。"""


def _jwt_payload(token: str) -> Dict[str, Any]:
    """解析 JWT payload（不验证签名，仅读取 claim）。"""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return {}


def _token_expires_at(token: str) -> Optional[int]:
    exp = _jwt_payload(token).get("exp")
    try:
        return int(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def account_id_from_id_token(id_token: str) -> Optional[str]:
    """从 id_token JWT 提取 chatgpt_account_id（OAuth 登录后落盘用）。"""
    claims = _jwt_payload(id_token)
    auth_claim = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        account_id = auth_claim.get("chatgpt_account_id")
        if account_id:
            return str(account_id)
    return None


class CodexAuthManager:
    """astrion 独立凭证文件（``codex_auth.json``）的读取/刷新/回写管理器（host 单用户）。

    线程模型：刷新走同步 HTTP + ``threading.Lock``（跨事件循环安全），
    异步调用方经 ``asyncio.to_thread`` 包装，避免阻塞主事件循环。
    """

    def __init__(self, auth_path: Optional[str] = None) -> None:
        self.auth_path = auth_path or CODEX_AUTH_PATH
        self._lock = threading.Lock()
        self._cached: Optional[Dict[str, Any]] = None
        # 文件签名 (mtime_ns, size)，用于检测外部改写/删除（CLI 登录、手动删除等）
        self._cached_stat: Optional[tuple] = None

    # ------------------------------------------------------------------ 读取

    def _read_file(self) -> Dict[str, Any]:
        try:
            with open(self.auth_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise CodexAuthError(
                f"未找到 Codex 凭证（{self.auth_path}）。"
                "请在个人空间连接 Codex 账号。"
            )
        except Exception as exc:
            raise CodexAuthError(f"读取 Codex 凭证失败: {exc}")
        if not isinstance(data, dict) or not isinstance(data.get("tokens"), dict):
            raise CodexAuthError("Codex 凭证格式不正确（缺少 tokens）。")
        return data

    def _file_stat(self) -> Optional[tuple]:
        """凭证文件签名 (mtime_ns, size)；文件不存在或不可读返回 None。"""
        try:
            st = os.stat(self.auth_path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def reload(self) -> Dict[str, Any]:
        """强制重读文件并更新缓存（OAuth 登录流程落盘后调用）。"""
        with self._lock:
            self._cached = self._read_file()
            self._cached_stat = self._file_stat()
            return self._cached

    def logout(self) -> None:
        """登出：删除 astrion 自己的凭证文件并清空缓存（不影响 Codex CLI 登录态）。"""
        with self._lock:
            try:
                os.remove(self.auth_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise CodexAuthError(f"删除 Codex 凭证失败: {exc}")
            self._cached = None
            self._cached_stat = None

    def _get_cached(self) -> Dict[str, Any]:
        with self._lock:
            # 外部变更检测：签名变化（含文件被删除）时自动重读，
            # 保证 ~/.codex/auth.json 始终是单一事实源
            sig = self._file_stat()
            if self._cached is None or sig != self._cached_stat:
                self._cached = self._read_file()
                self._cached_stat = sig
            return self._cached

    # ------------------------------------------------------------------ 状态

    def status(self) -> Dict[str, Any]:
        """供 /api/codex/status 使用的连接状态。"""
        try:
            data = self._get_cached()
        except CodexAuthError as exc:
            return {"connected": False, "error": str(exc)}
        tokens = data.get("tokens") or {}
        access = tokens.get("access_token") or ""
        expires_at = _token_expires_at(access)
        return {
            "connected": bool(access and tokens.get("refresh_token")),
            "auth_mode": data.get("auth_mode"),
            "account_id": tokens.get("account_id"),
            "expires_at": expires_at,
            "expires_in_seconds": (
                max(0, expires_at - int(time.time())) if expires_at else None
            ),
            "last_refresh": data.get("last_refresh"),
        }

    def get_account_id(self) -> Optional[str]:
        return (self._get_cached().get("tokens") or {}).get("account_id")

    def get_access_token_sync(self) -> Optional[str]:
        """同步路径（Flask 视图用）：返回可用 access_token，临期/过期同步刷新并回写；无凭证返回 None。

        注意与下方 async 版 ``get_access_token``（mixin 请求链路用）区分，不要同名覆盖。
        """
        tokens = self._get_cached().get("tokens") or {}
        access = tokens.get("access_token")
        if not access:
            return None
        if self._is_fresh(access) is False:
            self._refresh_sync()
            access = (self._get_cached().get("tokens") or {}).get("access_token")
        return access

    # ------------------------------------------------------------------ 刷新

    def _is_fresh(self, access_token: str) -> bool:
        exp = _token_expires_at(access_token)
        if exp is None:
            # 无法解析过期时间时保守视为新鲜，交给服务端 401 兜底
            return True
        return exp - time.time() > REFRESH_SKEW_SECONDS

    def _refresh_sync(self) -> Dict[str, Any]:
        """同步刷新并原子回写（持锁调用）。返回新 tokens。"""
        data = self._read_file()
        tokens = data.get("tokens") or {}
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise CodexAuthError("Codex 凭证缺少 refresh_token，请重新登录。")

        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            }
        ).encode()
        proxy = resolve_proxy()
        try:
            with httpx.Client(proxy=proxy, timeout=30) as client:
                resp = client.post(
                    OAUTH_TOKEN_URL,
                    content=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as exc:
            raise CodexAuthError(f"刷新 Codex token 网络失败: {exc}")
        if resp.status_code != 200:
            raise CodexAuthError(
                f"刷新 Codex token 失败 (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        payload = resp.json()
        new_access = payload.get("access_token")
        new_refresh = payload.get("refresh_token")
        if not new_access or not new_refresh:
            raise CodexAuthError("刷新响应缺少 access_token/refresh_token。")

        # 原子回写：保留原结构，仅更新 tokens 与 last_refresh
        data["tokens"] = {
            "id_token": payload.get("id_token") or tokens.get("id_token"),
            "access_token": new_access,
            "refresh_token": new_refresh,
            "account_id": tokens.get("account_id")
            or account_id_from_id_token(payload.get("id_token") or ""),
        }
        data["last_refresh"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        self._atomic_write(data)
        self._cached = data
        self._cached_stat = self._file_stat()
        return data["tokens"]

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        """原子回写 auth.json：临时文件 + os.replace，0600，保留原结构。"""
        path = Path(self.auth_path)
        tmp = path.with_suffix(path.suffix + ".tmp-astrion")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    async def get_access_token(self) -> str:
        """获取可用 access_token：临期（<5 分钟）自动刷新并回写。"""
        data = self._get_cached()
        access = (data.get("tokens") or {}).get("access_token")
        if access and self._is_fresh(access):
            return access
        return await self._refresh_async()

    async def _refresh_async(self) -> str:
        def _do() -> str:
            with self._lock:
                # 双检：等锁期间可能已被其他线程刷新
                data = self._read_file()
                access = (data.get("tokens") or {}).get("access_token")
                if access and self._is_fresh(access):
                    self._cached = data
                    self._cached_stat = self._file_stat()
                    return access
                return self._refresh_sync()["access_token"]

        return await asyncio.to_thread(_do)

    async def handle_unauthorized(self) -> str:
        """401（token_expired）自愈：重读文件（可能刚重新登录）→ 刷新 → 返回新 token。"""
        def _do() -> str:
            with self._lock:
                try:
                    data = self._read_file()
                    access = (data.get("tokens") or {}).get("access_token")
                    if access and self._is_fresh(access):
                        self._cached = data
                        self._cached_stat = self._file_stat()
                        return access
                except CodexAuthError:
                    pass
                return self._refresh_sync()["access_token"]

        return await asyncio.to_thread(_do)


def _default_manager_factory() -> CodexAuthManager:
    return CodexAuthManager()


_MANAGER: Optional[CodexAuthManager] = None
_MANAGER_LOCK = threading.Lock()


def get_auth_manager() -> CodexAuthManager:
    """进程级单例（host 单用户语义）。"""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = _default_manager_factory()
        return _MANAGER
