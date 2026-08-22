"""Token usage extraction helpers.

The project intentionally avoids model/provider-name special cases. These helpers
normalize common OpenAI-compatible and provider-specific response shapes by
looking for usage-like payloads in known response locations and field aliases.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


INPUT_TOKEN_KEYS = (
    "prompt_tokens",
    "input_tokens",
    "inputTokens",
    "promptTokens",
    "prefill_tokens",
)
OUTPUT_TOKEN_KEYS = (
    "completion_tokens",
    "output_tokens",
    "outputTokens",
    "completionTokens",
    "generated_tokens",
    "generatedTokens",
)
TOTAL_TOKEN_KEYS = (
    "total_tokens",
    "totalTokens",
    "total_token_count",
    "totalTokenCount",
)
CURRENT_CONTEXT_KEYS = (
    "current_context_tokens",
    "currentContextTokens",
    "context_tokens",
    "contextTokens",
)
KNOWN_CONTAINER_KEYS = {
    "usage",
    "token_usage",
    "tokenUsage",
    "token_usages",
    "response_metadata",
    "responseMetadata",
    "metadata",
    "meta",
}


def _to_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _first_int(payload: Dict[str, Any], keys: Iterable[str]) -> Optional[int]:
    for key in keys:
        if key in payload:
            value = _to_int(payload.get(key))
            if value is not None:
                return value
    return None


def normalize_usage_payload(raw: Any) -> Optional[Dict[str, int]]:
    if not isinstance(raw, dict):
        return None

    prompt_tokens = _first_int(raw, INPUT_TOKEN_KEYS)
    completion_tokens = _first_int(raw, OUTPUT_TOKEN_KEYS)
    total_tokens = _first_int(raw, TOTAL_TOKEN_KEYS)
    current_context_tokens = _first_int(raw, CURRENT_CONTEXT_KEYS)

    prompt_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
    if isinstance(prompt_details, dict):
        cached = _first_int(prompt_details, ("cached_tokens", "cachedTokens"))
        # cached tokens are still part of prompt tokens in most APIs. Keep the
        # detail accessible for callers that need it, but do not add it again.
    completion_details = raw.get("completion_tokens_details") or raw.get("output_tokens_details")
    if isinstance(completion_details, dict):
        reasoning = _first_int(completion_details, ("reasoning_tokens", "reasoningTokens"))
        if completion_tokens is None and reasoning is not None:
            completion_tokens = reasoning

    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None

    if prompt_tokens is None:
        prompt_tokens = max(0, (total_tokens or 0) - (completion_tokens or 0)) if total_tokens is not None else 0
    if completion_tokens is None:
        completion_tokens = max(0, (total_tokens or 0) - prompt_tokens) if total_tokens is not None else 0
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens
    if current_context_tokens is None:
        current_context_tokens = prompt_tokens

    return {
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(total_tokens),
        "current_context_tokens": int(current_context_tokens),
    }


def _usage_score(payload: Dict[str, int]) -> int:
    return int(payload.get("total_tokens", 0)) + int(payload.get("prompt_tokens", 0)) + int(payload.get("completion_tokens", 0))


def extract_usage_payload(obj: Any) -> Optional[Dict[str, int]]:
    """Find and normalize the best token usage payload in a response chunk/object."""
    best: Optional[Dict[str, int]] = None

    def consider(value: Any) -> None:
        nonlocal best
        normalized = normalize_usage_payload(value)
        if not normalized:
            return
        if best is None or _usage_score(normalized) >= _usage_score(best):
            best = normalized

    def walk(value: Any, *, depth: int = 0, in_known_container: bool = False) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            if in_known_container:
                consider(value)
            else:
                # Also accept dicts that directly look like usage payloads.
                consider(value)
            for key, child in value.items():
                child_known = in_known_container or key in KNOWN_CONTAINER_KEYS
                if key in KNOWN_CONTAINER_KEYS:
                    consider(child)
                walk(child, depth=depth + 1, in_known_container=child_known)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth=depth + 1, in_known_container=in_known_container)

    walk(obj)
    return best


__all__ = ["extract_usage_payload", "normalize_usage_payload"]
