"""传统子智能体创建时的模型选择。多智能体角色不使用此模块。"""
from typing import Any, Mapping

INTELLIGENCE_LEVELS = ("same", "high", "medium", "low")


def resolve_intelligence_model(
    level: str, preferences: Mapping[str, Any], current_model: str | None
) -> str:
    """严格解析档位；未配置或不可用时拒绝创建，不自动降档。"""
    from modules.aux_model_resolver import resolve_locked_model_profile

    if level not in INTELLIGENCE_LEVELS:
        raise ValueError("智能程度必选：same（和当前智能体一致）、high（高）、medium（中）、low（低）。")
    if level == "same":
        model_key = str(current_model or "").strip()
        if not model_key:
            raise ValueError("当前主智能体没有可用的模型，无法创建同模型子智能体。")
    else:
        model_key = str(preferences.get(f"sub_agent_model_{level}") or "").strip()
        if not model_key:
            label = {"high": "高", "medium": "中", "low": "低"}[level]
            raise ValueError(f"尚未配置{label}智能程度的模型。请在设置→子智能体中配置，或使用 same（和当前智能体一致）。")
    resolve_locked_model_profile(model_key)
    return model_key
