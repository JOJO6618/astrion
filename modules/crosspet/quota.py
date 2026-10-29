"""Subscription quota metadata for CrossPet, isolated from task and export locks."""
from __future__ import annotations

import copy
import math
import threading
import time
import uuid
from pathlib import Path

from modules.crosspet.settings import read_enabled


def normalize_usage(value: object) -> dict:
    """Whitelist numbers only; never forward the raw account response."""
    limits = value.get("rate_limit") if isinstance(value, dict) else None
    result = {}
    if not isinstance(limits, dict):
        return result
    for source, target in (("primary_window", "primary"), ("secondary_window", "secondary")):
        window = limits.get(source)
        if not isinstance(window, dict):
            continue
        used = window.get("used_percent")
        duration = window.get("limit_window_seconds")
        reset = window.get("reset_at")
        numbers = (used, duration, reset)
        if not all(type(n) in (int, float) and math.isfinite(n) for n in numbers):
            continue
        if not 0 <= used <= 100 or not 0 < duration <= 366 * 86400 or not 0 < reset <= 253402300799:
            continue
        result[target] = {"usedPercent": used, "windowDurationMins": duration / 60,
                          "resetsAt": reset}
    return result


def fetch_usage(auth) -> dict:
    """Use Astrion's independent OAuth and configured proxy, without any model call."""
    import httpx
    from utils.api_client.codex.settings import USAGE_URL, resolve_proxy

    access = auth.get_access_token_sync()
    if not access:
        raise ValueError("Subscription is not connected")
    headers = {"Authorization": f"Bearer {access}", "originator": "codex_cli_rs",
               "accept": "application/json"}
    account = auth.get_account_id()
    if account:
        headers["chatgpt-account-id"] = account
    with httpx.Client(proxy=resolve_proxy(), timeout=20) as client:
        response = client.get(USAGE_URL, headers=headers)
        response.raise_for_status()
        result = normalize_usage(response.json())
    if not result:
        raise ValueError("No valid subscription windows")
    return result


class SubscriptionQuota:
    """One network worker; snapshot() only updates flags and copies cached numbers."""

    def __init__(self, settings_path: Path):
        self.settings_path = settings_path
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = False
        self._busy = False
        self._generation = 0
        self._identity = None
        self._next_query = 0.0
        self._last_attempt = 0.0
        self._packet = {"connected": False, "status": "unavailable"}

    def snapshot(self, enabled: bool, busy: bool = False) -> dict:
        with self._lock:
            if enabled != self._enabled:
                self._enabled = enabled
                self._generation += 1
                self._identity = None
                self._next_query = 0
                self._packet = {"connected": False, "status": "unavailable"}
                self._wake.set()
            self._busy = busy
            if enabled and self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="crosspet-quota")
                self._thread.start()
            return copy.deepcopy(self._packet)

    @staticmethod
    def _identity_of(auth):
        status = auth.status()
        if not status.get("connected"):
            if status.get("error") and Path(auth.auth_path).exists():
                raise ValueError("Credential state is unavailable")
            return None
        stat = Path(auth.auth_path).stat()
        # Never export this local identity. Token rotation conservatively invalidates cache.
        return (status.get("account_id"), stat.st_mtime_ns, stat.st_size)

    def _allowed(self) -> bool:
        try:
            return read_enabled(self.settings_path)
        except Exception:
            return False

    def _refresh(self) -> None:
        with self._lock:
            if not self._enabled:
                return
            generation = self._generation
        if not self._allowed():
            return
        # Codex 多账号（2026-10-29 用户拍板）：CrossPet 不新增配置、不跟随 Codex 页
        # 查看选择器；固定跟随「主账号」——旧 codex_auth.json 迁移账号优先（稳定
        # 身份，不依赖旧文件是否存在做优先级），否则最早添加且仍保留的账号；被删
        # 除后自然顺延到下一个。运行中的对话不受本选择影响。
        from utils.api_client.codex.accounts import get_codex_account_store

        try:
            store = get_codex_account_store()
            primary = store.primary_account_id()
            auth = store.get_auth_manager(primary) if primary else None
        except Exception:
            auth = None
        if auth is None:
            with self._lock:
                if generation == self._generation:
                    self._packet = {"connected": False, "status": "unavailable"}
                    self._identity = None
            return
        try:
            identity = self._identity_of(auth)
        except Exception:
            # Unknown credentials are not proof of logout; do not mix in another account.
            with self._lock:
                if generation == self._generation:
                    self._packet["status"] = "stale" if self._packet.get("rateLimits") else "unavailable"
            return
        now = time.time()
        with self._lock:
            if generation != self._generation or not self._enabled:
                return
            if identity != self._identity:
                self._identity = identity
                self._next_query = 0
                self._packet = {"connected": identity is not None,
                                "status": "checking" if identity is not None else "unavailable",
                                "epoch": uuid.uuid4().hex}
            if self._busy and self._packet.get("at"):
                self._next_query = min(self._next_query, self._last_attempt + 60)
            if identity is None or now < self._next_query:
                return
            self._last_attempt = now
            self._next_query = now + (60 if self._busy else 120)
        # No export/settings/task locks are held while refreshing tokens or using the network.
        try:
            limits = fetch_usage(auth)
            current_identity = self._identity_of(auth)
            if current_identity is None or current_identity[0] != identity[0]:
                return
            packet = {"connected": True, "status": "live", "at": time.time(),
                      "rateLimits": limits}
        except Exception:
            packet = None  # Do not put exception text or raw responses in the snapshot/log.
            current_identity = identity
        if not self._allowed():
            return
        with self._lock:
            if generation != self._generation or not self._enabled or identity != self._identity:
                return
            if packet is not None:
                # Our own refresh can rotate the file without changing the subscription account.
                self._identity = current_identity
                packet["epoch"] = self._packet["epoch"]
                self._packet = packet
            else:
                self._packet["status"] = "stale" if self._packet.get("rateLimits") else "unavailable"

    def _run(self) -> None:
        while True:
            self._wake.wait(2)
            self._wake.clear()
            try:
                self._refresh()
            except Exception:
                # Optional quota export never affects task execution or exposes auth errors.
                continue
