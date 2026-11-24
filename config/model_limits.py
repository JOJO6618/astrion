"""Normalize model token limits reported by provider catalogs."""

from __future__ import annotations

from typing import Any, Optional


def positive_token_limit(*values: Any) -> Optional[int]:
    """Return the first valid positive token count from the supplied fields."""
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            tokens = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 < tokens <= (1 << 63) - 1 and (isinstance(value, str) or value == tokens):
            return tokens
    return None


def effective_context_window(default: Optional[int], maximum: Optional[int]) -> Optional[int]:
    """Use the available total window, falling back to the reported default."""
    return maximum or default
