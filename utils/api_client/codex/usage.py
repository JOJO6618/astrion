"""Account-isolated subscription queries with connection reuse and single-flight caching."""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from utils.api_client.codex.settings import resolve_proxy

USAGE_TTL_SECONDS = 30
WHAM_TIMEOUT = httpx.Timeout(10, connect=5, pool=5)


@dataclass
class _AccountQueries:
    lock: threading.Lock = field(default_factory=threading.Lock)
    client: httpx.Client | None = None
    proxy: str | None = None
    usage: dict | None = None
    fetched_at: float = 0
    signature: tuple | None = None


class SubscriptionQueries:
    """Serialize each account's queries; never share auth headers or results across accounts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._accounts: dict[str, _AccountQueries] = {}

    def _entry(self, account_id: str) -> _AccountQueries:
        with self._lock:
            return self._accounts.setdefault(account_id, _AccountQueries())

    @staticmethod
    def _signature(auth) -> tuple:
        # File metadata invalidates cached quota after re-login/logout/token rotation.
        from pathlib import Path
        path = Path(auth.auth_path)
        stat = path.stat()
        return (str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def request(self, account_id: str, method: str, url: str,
                headers: dict, json_body: dict | None = None) -> httpx.Response:
        entry = self._entry(account_id)
        with entry.lock:
            proxy = resolve_proxy()
            if entry.client is None or entry.proxy != proxy:
                if entry.client is not None:
                    entry.client.close()
                entry.client = httpx.Client(proxy=proxy, timeout=WHAM_TIMEOUT)
                entry.proxy = proxy
            # A connection failure occurs before any HTTP response; only GET is retried.
            for attempt in range(2 if method == "GET" else 1):
                try:
                    return entry.client.request(method, url, headers=headers, json=json_body)
                except (httpx.ConnectTimeout, httpx.ConnectError):
                    entry.client.close()
                    entry.client = None
                    if method != "GET" or attempt:
                        raise
                    entry.client = httpx.Client(proxy=proxy, timeout=WHAM_TIMEOUT)
            raise RuntimeError("unreachable")

    def usage(self, account_id: str, auth, fetch: Callable[[], dict], *, force: bool = False) -> dict:
        # Separate lock from transport: fetch() calls request(), which uses entry.lock.
        entry = self._entry(f"usage:{account_id}")
        with entry.lock:
            signature = self._signature(auth)
            now = time.monotonic()
            if (not force and entry.usage is not None and entry.signature == signature
                    and now - entry.fetched_at < USAGE_TTL_SECONDS):
                return copy.deepcopy(entry.usage)
            payload = fetch()
            if not isinstance(payload, dict) or not isinstance(payload.get("rate_limit"), dict):
                raise ValueError("invalid_subscription_usage")
            entry.usage = copy.deepcopy(payload)
            entry.signature = self._signature(auth)
            entry.fetched_at = time.monotonic()
            return payload

    def invalidate(self, account_id: str) -> None:
        entry = self._entry(f"usage:{account_id}")
        with entry.lock:
            entry.usage = None

    def close(self) -> None:
        with self._lock:
            entries = list(self._accounts.values())
            self._accounts.clear()
        for entry in entries:
            with entry.lock:
                if entry.client is not None:
                    entry.client.close()


subscription_queries = SubscriptionQueries()
