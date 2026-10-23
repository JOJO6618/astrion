"""Shared validation for the user's retained child-agent capacity."""
from typing import Any

DEFAULT_MAX_INSTANCES = 20
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def validate_max_instances(value: Any) -> int:
    """Accept positive integers representable by the settings frontend."""
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < 1 or value > MAX_SAFE_INTEGER):
        raise ValueError("允许同时存在的子智能体数量必须是大于等于1的有效整数。")
    return value


def resolve_max_instances(value: Any) -> int:
    """Use the product default for missing or damaged persisted settings."""
    try:
        return validate_max_instances(value)
    except ValueError:
        return DEFAULT_MAX_INSTANCES
