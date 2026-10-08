"""Capture provider-specific prices before a request; calculate USD from actual usage."""
from __future__ import annotations

from decimal import Decimal
import logging
import math
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)
_refresh_lock = threading.Lock()
_last_refresh_attempt = 0.0


def refresh_prices() -> None:
    """Reuse models.dev's cache, with a short failure backoff and schema upgrade."""
    from modules import modelsdev_registry as registry
    from modules.provider_manager import ProviderManager

    global _last_refresh_attempt
    with _refresh_lock:
        if _last_refresh_attempt and time.monotonic() - _last_refresh_attempt < 60:
            return
        _last_refresh_attempt = time.monotonic()
        data = registry._current_data()
        keys = {e['modelsdev_key'] for e in ProviderManager.catalog_entries() if e.get('modelsdev_key')}
        ok, error = registry.refresh_cache(keys, timeout=8, force=data.get('version', 1) < 2)
        if not ok:
            logger.debug('Model price refresh unavailable: %s', error)


def capture_price(model_key: str, *, refresh: bool = True) -> dict[str, Any]:
    """Never fall back to another provider/model's price or interpret missing as free."""
    from config.model_profiles import get_registered_model_profiles

    profile = get_registered_model_profiles().get(model_key) or {}
    billing = 'subscription' if profile.get('responses_auth') == 'codex_oauth' else profile.get('billing', 'metered')
    if refresh and billing == 'metered':
        try:
            refresh_prices()
            profile = get_registered_model_profiles().get(model_key) or profile
        except Exception:
            logger.warning('Price refresh unavailable; using existing price data', exc_info=True)
    pricing = profile.get('pricing') or {}
    return {
        'model_key': model_key,
        'provider_id': profile.get('provider_id'),
        'billing': billing,
        'rates': dict(pricing.get('rates') or {}),
        'source': pricing.get('source'),
        'fetched_at': pricing.get('fetched_at'),
    }


def clean_rates(cost: Any) -> dict[str, float]:
    if not isinstance(cost, dict):
        return {}
    rates = {}
    for key in ('input', 'cache_read', 'output'):
        value = cost.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            rates[key] = float(value)
    return rates


def calculate_cost(usage: dict[str, Any] | None, price: dict[str, Any]) -> dict[str, Any]:
    from utils.token_usage import normalize_usage_payload

    normalized = normalize_usage_payload(usage)
    tokens = normalized or {}
    prompt = max(0, int(tokens.get('prompt_tokens', 0)))
    cached = min(prompt, max(0, int(tokens.get('cached_input_tokens', 0))))
    output = max(0, int(tokens.get('completion_tokens', 0)))
    counts = {'input': prompt - cached, 'cache_read': cached, 'output': output}
    rates = clean_rates(price.get('rates'))
    price = {**price, 'rates': rates}
    billing = price.get('billing', 'metered')
    missing = billing == 'metered' and (normalized is None or any(n and key not in rates for key, n in counts.items()))
    amount = None
    if billing in ('subscription', 'local'):
        status = billing
    elif missing:
        status = 'unpriced'
    else:
        status = 'priced'
        amount = str(sum((Decimal(n) * Decimal(str(rates.get(key, 0))) for key, n in counts.items()), Decimal(0)) / Decimal(1_000_000))
    return {
        'input_tokens': prompt, 'cached_input_tokens': cached, 'output_tokens': output,
        'total_tokens': int(tokens.get('total_tokens', prompt + output)),
        'current_context_tokens': int(tokens.get('current_context_tokens', prompt)),
        'cost_usd': amount, 'status': status, 'price': price,
    }
