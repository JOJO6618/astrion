"""辅助调用（子智能体 / 审核智能体 / 标题生成）的统一模型解析器。

模型来源唯一 = 主注册表 ``config.model_profiles.get_registered_model_profiles()``
（custom_models.json + codex 动态发现模型）。历史上的独立库
``sub_agent_models.json``（含库级 default_model 字段）已于 2026-09 彻底废弃，
不做向后兼容；每个调用点（角色表单 / 审核智能体 / 标题生成）各自持有自己的
模型设置，留空时走本模块的自动规则。

自动规则：注册表第一个可见模型；**当可用模型全是 codex 时，优先选名称含
``-luna`` 的模型**（快速高效款，适合辅助调用，避免默认落到旗舰款浪费配额）。
"""

from typing import Any, Dict, List, Optional

from config.model_profiles import get_registered_model_profiles

__all__ = [
    "auto_default_model_key",
    "resolve_aux_model_profile",
    "resolve_locked_model_profile",
    "list_aux_models",
]


def _visible_profiles() -> Dict[str, Dict[str, Any]]:
    """注册表可见模型（过滤 hidden；key 含 codex/ 前缀动态模型）。"""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        profiles = get_registered_model_profiles()
    except Exception:
        return out
    for key, profile in profiles.items():
        if not isinstance(profile, dict) or profile.get("hidden"):
            continue
        out[key] = profile
    return out


def auto_default_model_key() -> Optional[str]:
    """留空自动规则：常规取注册表第一个可见模型；纯 Codex（openai-codex）时选 -luna。"""
    profiles = _visible_profiles()
    if not profiles:
        return None
    keys = list(profiles.keys())
    if all(profiles[k].get("provider_id") == "openai-codex" for k in keys):
        for key in keys:
            if "-luna" in key:
                return key
    return keys[0]


def resolve_aux_model_profile(model_key: str) -> Optional[Dict[str, Any]]:
    """按注册表 key 解析 profile（与 ``APIClient.apply_profile`` 输入同形）。

    留空走自动规则；显式指定的 key 即使 hidden 也允许（内部模型可用）。
    解析失败返回 None，由调用方决定兜底行为。
    """
    key = str(model_key or "").strip()
    if not key:
        key = auto_default_model_key() or ""
    if not key:
        return None
    try:
        profile = get_registered_model_profiles().get(key)
    except Exception:
        return None
    if not isinstance(profile, dict):
        return None
    out = dict(profile)
    out["name"] = key
    return out


def resolve_locked_model_profile(model_key: str) -> Dict[str, Any]:
    """创建锁语义：只认创建时记录的 key，解析不到立即抛错（绝不静默回落）。

    子智能体创建时把模型 key 记进 task_record；rerun/restore 必须复用同一模型，
    保证前缀缓存与行为一致性（与是否 codex 无关）。
    """
    from modules.i18n import tr

    key = str(model_key or "").strip()
    if not key:
        raise RuntimeError(tr("sub_agent_task2.locked_model_missing"))
    try:
        profile = get_registered_model_profiles().get(key)
    except Exception:
        profile = None
    if not isinstance(profile, dict):
        raise RuntimeError(tr("sub_agent_task2.locked_model_unavailable", model=key))
    out = dict(profile)
    out["name"] = key
    return out


def list_aux_models() -> List[Dict[str, Any]]:
    """下拉选项源：主注册表可见模型（全部来源，codex 条目带 provider_type 标记）。"""
    items: List[Dict[str, Any]] = []
    for key, profile in _visible_profiles().items():
        fast = profile.get("fast") or {}
        items.append(
            {
                "key": key,
                "name": str(profile.get("name") or key),
                "description": str(profile.get("description") or ""),
                "provider_type": str(profile.get("provider_type") or ""),
                "supports_thinking": bool(profile.get("supports_thinking")),
                "fast_only": bool(profile.get("fast_only")),
                "thinking_only": bool(profile.get("thinking_only")),
                "multimodal": str(profile.get("multimodal") or "none"),
                "context_window": profile.get("context_window")
                or fast.get("context_window"),
                "max_output": fast.get("max_tokens"),
                "supports_reasoning_effort": bool(
                    profile.get("supports_reasoning_effort")
                ),
                "supported_reasoning_levels": profile.get(
                    "supported_reasoning_levels"
                )
                or [],
                "default_reasoning_level": profile.get("default_reasoning_level"),
            }
        )
    return items
