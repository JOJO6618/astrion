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
    "promptTokenCount",
)
OUTPUT_TOKEN_KEYS = (
    "completion_tokens",
    "output_tokens",
    "outputTokens",
    "completionTokens",
    "generated_tokens",
    "generatedTokens",
    "candidatesTokenCount",
)
TOTAL_TOKEN_KEYS = (
    "total_tokens",
    "totalTokens",
    "total_token_count",
    "totalTokenCount",
)
# 缓存命中 token 数的所有已知字段位置（2026-08 调研，见 cache_research/SUMMARY.md）：
# - OpenAI 系/Qwen/GLM/MiniMax/xAI/Mistral/千帆/OpenRouter: usage.prompt_tokens_details.cached_tokens
#   （Responses API 为 usage.input_tokens_details.cached_tokens）
# - DeepSeek: usage.prompt_cache_hit_tokens（顶层）
# - Kimi / 阶跃Step / 部分 DashScope 地域: usage.cached_tokens（顶层）
# - Anthropic / Bedrock / MiniMax-Anthropic 模式/中转站: usage.cache_read_input_tokens（顶层）
# - Gemini: usageMetadata.cachedContentTokenCount
CACHED_INPUT_TOKEN_KEYS = (
    "cached_input_tokens",  # normalize 输出自身的字段名（保证二次归一化幂等）
    "cached_tokens",
    "cachedTokens",
    "prompt_cache_hit_tokens",
    "promptCacheHitTokens",
    "cache_read_input_tokens",
    "cacheReadInputTokens",
    "cached_content_token_count",
    "cachedContentTokenCount",
)
CACHE_WRITE_TOKEN_KEYS = (
    "cache_creation_input_tokens",
    "cacheCreationInputTokens",
    "cache_write_tokens",
    "cacheWriteTokens",
)
# 缓存详情可能出现的嵌套容器（OpenAI 风格 details 对象）
PROMPT_DETAILS_KEYS = (
    "prompt_tokens_details",
    "input_tokens_details",
    "promptTokensDetails",
    "inputTokensDetails",
)
# Anthropic 语义的输入键与顶层缓存字段组合：仅当【输入命中 input_tokens 类键】
# 且【顶层存在 cache_read_input_tokens / cache_creation_input_tokens】时才判定为
# Anthropic 语义（input_tokens 不含缓存部分），需要把缓存部分加回总输入。
# 注意：OpenAI Responses API 也用 input_tokens 键但其缓存字段在 input_tokens_details 里
# （input_tokens 本身含缓存），因此不能用键名单独判断，必须同时要求顶层 Anthropic 字段存在。
ANTHROPIC_STYLE_INPUT_KEYS = {"input_tokens", "inputTokens"}
ANTHROPIC_CACHE_READ_KEYS = ("cache_read_input_tokens", "cacheReadInputTokens")
ANTHROPIC_CACHE_WRITE_KEYS = ("cache_creation_input_tokens", "cacheCreationInputTokens")
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
    _, value = _first_int_with_key(payload, keys)
    return value


def _first_int_with_key(payload: Dict[str, Any], keys: Iterable[str]) -> tuple:
    """返回 (命中键名, 值)；未命中返回 (None, None)。"""
    for key in keys:
        if key in payload:
            value = _to_int(payload.get(key))
            if value is not None:
                return key, value
    return None, None


def normalize_usage_payload(raw: Any) -> Optional[Dict[str, int]]:
    if not isinstance(raw, dict):
        return None

    prompt_key, prompt_tokens = _first_int_with_key(raw, INPUT_TOKEN_KEYS)
    completion_tokens = _first_int(raw, OUTPUT_TOKEN_KEYS)
    total_tokens = _first_int(raw, TOTAL_TOKEN_KEYS)
    current_context_tokens = _first_int(raw, CURRENT_CONTEXT_KEYS)

    # 缓存命中：先查顶层字段（DeepSeek/Kimi/Step/Anthropic/Gemini），再查 details 容器（OpenAI 系）
    cached_input_tokens = _first_int(raw, CACHED_INPUT_TOKEN_KEYS)
    cache_write_tokens = _first_int(raw, CACHE_WRITE_TOKEN_KEYS)
    for details_key in PROMPT_DETAILS_KEYS:
        prompt_details = raw.get(details_key)
        if not isinstance(prompt_details, dict):
            continue
        if cached_input_tokens is None:
            cached_input_tokens = _first_int(prompt_details, CACHED_INPUT_TOKEN_KEYS)
        if cache_write_tokens is None:
            cache_write_tokens = _first_int(prompt_details, CACHE_WRITE_TOKEN_KEYS)

    # Anthropic 语义校准：顶层出现 cache_read/cache_creation 字段且输入键为 input_tokens 时，
    # input_tokens 不含缓存读取/写入部分，加回以统一“总输入”口径；
    # OpenAI 系（prompt_tokens 或 details 内 cached_tokens）本身含缓存部分，不校准。
    anthropic_read = _first_int(raw, ANTHROPIC_CACHE_READ_KEYS)
    anthropic_write = _first_int(raw, ANTHROPIC_CACHE_WRITE_KEYS)
    if prompt_key in ANTHROPIC_STYLE_INPUT_KEYS and (anthropic_read or anthropic_write):
        prompt_tokens = (prompt_tokens or 0) + (anthropic_read or 0) + (anthropic_write or 0)

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
        "cached_input_tokens": int(cached_input_tokens or 0),
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
