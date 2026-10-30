"""Codex Fast capability and request tier, independent of reasoning effort.

Matches openai/codex protocol/config_types.rs (Fast.request_value = priority)
with capability discovery from protocol/openai_models.rs.
"""
from __future__ import annotations

from typing import Any


def supports_fast_mode(info: dict[str, Any] | None) -> bool:
    """Use the account's catalog, including Codex's legacy capability field."""
    info = info or {}
    return any(
        isinstance(tier, dict) and tier.get("id") == "priority"
        for tier in info.get("service_tiers") or []
    ) or "fast" in (info.get("additional_speed_tiers") or [])


def validate_fast_mode(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("ChatGPT fast mode must be a boolean")
    return value
