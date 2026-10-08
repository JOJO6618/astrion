"""Daily USD/CNY reference rate cache; refresh off the request thread.

Frankfurter publishes ECB reference rates (business days). A successful fetch date
and the underlying rate date are separate; a failed refresh retains the prior rate.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import threading
import time
from typing import Any

from config.paths import DATA_DIR
from utils.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)
_SOURCE = 'https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY'
_lock = threading.Lock()
_refreshing = False
_last_attempt = 0.0


def _path() -> Path:
    return Path(DATA_DIR) / 'exchange_rates.json'


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding='utf-8'))
        rate = float(data['usd_cny'])
        if math.isfinite(rate) and rate > 0:
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}


def refresh_rate() -> None:
    import httpx

    global _refreshing
    try:
        response = httpx.get(_SOURCE, timeout=8)
        response.raise_for_status()
        raw = response.json()
        rate = float(raw['rates']['CNY'])
        if raw.get('base') != 'USD' or not math.isfinite(rate) or rate <= 0:
            raise ValueError('Invalid USD/CNY reference rate')
        now = datetime.now(timezone.utc).isoformat()
        atomic_write_json(_path(), {
            'usd_cny': rate, 'rate_date': raw['date'], 'fetched_at': now,
            'source': _SOURCE,
        })
    except Exception:
        logger.warning('USD/CNY refresh failed; retaining cached rate', exc_info=True)
    finally:
        with _lock:
            _refreshing = False


def get_rate(*, refresh: bool = True) -> dict[str, Any]:
    global _refreshing, _last_attempt
    data = _read()
    today = datetime.now(timezone.utc).date().isoformat()
    stale = not str(data.get('fetched_at', '')).startswith(today)
    if refresh and stale:
        with _lock:
            if not _refreshing and (not _last_attempt or time.monotonic() - _last_attempt >= 3600):
                _refreshing = True
                _last_attempt = time.monotonic()
                threading.Thread(target=refresh_rate, name='exchange-rate-refresh', daemon=True).start()
    return {**data, 'stale': stale}
