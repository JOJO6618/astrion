"""Chat preferences share personalization storage, never Agent mode settings."""
from __future__ import annotations

from config.limits import REASONING_EFFORT_LEVELS

CHAT_DEFAULTS = {
    "chat_default_model": "",
    "chat_default_thinking_mode": True,
    "chat_default_reasoning_effort": None,
    "chat_network_enabled": True,
    "chat_instructions": "",
    "chat_deep_compress_trigger_tokens": 250_000,
}
SHARED_FIELDS = {"self_identify", "user_name", "profession", "tone", "considerations",
                 "theme", "communication_style", "conversation_continuity"}
CHAT_FIELDS = set(CHAT_DEFAULTS)


def sanitize_chat_settings(payload, fallback, allowed_models):
    result = {}
    for key, default in CHAT_DEFAULTS.items():
        value = payload.get(key, fallback.get(key, default))
        if key == "chat_default_model":
            value = value.strip() if isinstance(value, str) else ""
            if value and value not in allowed_models:
                if key in payload:
                    raise ValueError("Unknown Chat default model")
                value = ""
        elif key in {"chat_default_thinking_mode", "chat_network_enabled"}:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
        elif key == "chat_default_reasoning_effort":
            if value is not None and (
                not isinstance(value, str) or value not in REASONING_EFFORT_LEVELS
            ):
                raise ValueError("Invalid Chat reasoning effort")
        elif key == "chat_instructions":
            if not isinstance(value, str) or len(value) > 20000:
                raise ValueError("Chat instructions must be at most 20000 characters")
        elif key == "chat_deep_compress_trigger_tokens":
            if type(value) is not int or not 1_000 <= value <= 2_000_000:
                raise ValueError("Chat deep compression context must be an integer from 1000 to 2000000")
        result[key] = value
    return result
