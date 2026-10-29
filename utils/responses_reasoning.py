"""Responses 加密 reasoning 的回放边界（Agent / Chat 共用，2026-10-29）。

加密 reasoning items 由具体上游（ChatGPT 订阅 / OpenAI API / xAI 等）加密，只有
同一上游连接才能解密回放。模型 key 自身已包含「连接身份」：

- 提供商同步模型：``{内部连接id}/{model}``（同一目录不同 API Key = 不同连接 id）
- ChatGPT 订阅：``openai-codex/{slug}``（主账号）/ ``openai-codex-<hash>/{slug}``

因此这里用中性的 profile 比较：双方 ``provider_id`` 非空且相等，且
``responses_account_id`` 也相等（Codex 多账号双保险）；任一解析失败一律视为不同源，
宁可丢掉推理连续性，也不能把不兼容的加密块发给错误上游。

本模块只依赖 ``config.model_profiles``，不引入 Agent 循环/终端依赖，Chat 与
Agent 两条历史构建路径共用同一判定。
"""

from __future__ import annotations

__all__ = ["same_responses_provider"]


def same_responses_provider(source_model_key: str, current_model_key: str) -> bool:
    """两个 model_key 是否同属一个 responses provider（加密 reasoning 可回放）。

    空 key、未知/已失效模型、provider_id 缺失、账号身份不同 → False。
    """
    if not source_model_key or not current_model_key:
        return False
    try:
        from config.model_profiles import get_registered_model_profiles

        profiles = get_registered_model_profiles()
        src_profile = profiles.get(source_model_key) or {}
        cur_profile = profiles.get(current_model_key) or {}
        src = src_profile.get("provider_id")
        cur = cur_profile.get("provider_id")
        if not src or not cur or src != cur:
            return False
        return (src_profile.get("responses_account_id") or None) == (
            cur_profile.get("responses_account_id") or None
        )
    except Exception:
        return False
