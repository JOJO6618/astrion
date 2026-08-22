import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import resolve_deploy_config


def _env_optional(name: str) -> Optional[str]:
    """从环境变量获取可选配置（已由 config/__init__.py 统一注入）。"""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize_multimodal(value: Any) -> str:
    text = str(value or "none").strip().lower()
    if text in {"image", "video", "none", "image,video", "video,image"}:
        return "image,video" if "image" in text and "video" in text else text
    return "none"


def _parse_env_ref(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    # 1) ${ENV_KEY}
    if text.startswith("${") and text.endswith("}") and len(text) > 3:
        key = text[2:-1].strip()
        return _env_optional(key)
    # 2) env:ENV_KEY
    if text.lower().startswith("env:") and len(text) > 4:
        key = text[4:].strip()
        resolved = _env_optional(key)
        return resolved if resolved is not None else None
    # 3) $ENV_KEY
    if text.startswith("$") and len(text) > 1:
        key = text[1:].strip()
        resolved = _env_optional(key)
        return resolved if resolved is not None else None
    # 4) 直接写 ENV_KEY（若环境/.env存在同名键则读取其值，否则按字面量）
    if text.replace("_", "").isalnum() and text.upper() == text:
        resolved = _env_optional(text)
        if resolved is not None:
            return resolved
    return text


def _to_optional_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _reasoning_flags(capability: str) -> Dict[str, bool]:
    normalized = str(capability or "fast").replace(" ", "").lower()
    if normalized == "thinking":
        return {"supports_thinking": True, "fast_only": False, "thinking_only": True}
    if normalized == "fast,thinking":
        return {"supports_thinking": True, "fast_only": False, "thinking_only": False}
    return {"supports_thinking": False, "fast_only": True, "thinking_only": False}


def _build_custom_profile(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    key = str(item.get("model_name") or "").strip()
    if not key:
        return None
    base_url = _parse_env_ref(item.get("url"))
    api_key = _parse_env_ref(item.get("apikey"))
    if not base_url or not api_key:
        return None

    context_window = _to_optional_int(item.get("context_window"))
    max_output_tokens = _to_optional_int(item.get("max_output_tokens"))

    capability = str(item.get("reasoning_capability") or "fast").replace(" ", "").lower()
    flags = _reasoning_flags(capability)
    thinkmode = item.get("thinkmode_status") if isinstance(item.get("thinkmode_status"), dict) else {}
    mode_type = str((thinkmode.get("type") or thinkmode.get("mode") or "")).strip().lower()
    extra_global = item.get("extra_parameter") if isinstance(item.get("extra_parameter"), dict) else {}
    fast_extra = thinkmode.get("fast_extra_parameter") if isinstance(thinkmode.get("fast_extra_parameter"), dict) else {}
    if not fast_extra and isinstance(thinkmode.get("fast_parameter"), dict):
        fast_extra = thinkmode.get("fast_parameter")
    thinking_extra = thinkmode.get("thinking_extra_parameter") if isinstance(thinkmode.get("thinking_extra_parameter"), dict) else {}
    if not thinking_extra and isinstance(thinkmode.get("thinking_parameter"), dict):
        thinking_extra = thinkmode.get("thinking_parameter")

    fast_model_id = None
    thinking_model_id = None
    if mode_type == "switch_model":
        fast_model_id = str(thinkmode.get("fast_model_id") or "").strip()
        thinking_model_id = str(thinkmode.get("thinking_model_id") or "").strip()
    elif mode_type == "param_toggle":
        common_model = str(thinkmode.get("model_id") or "").strip()
        fast_model_id = common_model
        thinking_model_id = common_model
    else:
        common_model = str(thinkmode.get("model_id") or "").strip()
        if not common_model:
            common_model = str(item.get("model_id") or "").strip()
        fast_model_id = common_model
        thinking_model_id = common_model

    if not fast_model_id:
        fast_model_id = thinking_model_id
    if not thinking_model_id:
        thinking_model_id = fast_model_id
    if not fast_model_id:
        return None

    fast_config = {
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model_id": fast_model_id,
        "max_tokens": max_output_tokens,
        "context_window": context_window,
        "extra_params": {**extra_global, **(fast_extra or {})},
    }
    profile: Dict[str, Any] = {
        "name": str(item.get("display_name") or key),
        "description": str(item.get("description") or ""),
        "model_description": str(item.get("model_description") or ""),
        "is_custom_model": True,
        "hidden": not bool(item.get("visible", True)),
        "multimodal": _normalize_multimodal(item.get("multimodal")),
        "context_window": context_window,
        "fast": fast_config,
        "supports_thinking": bool(flags["supports_thinking"]),
        "fast_only": bool(flags["fast_only"]),
    }
    if flags["thinking_only"]:
        profile["thinking_only"] = True
    # 推理强度开关：配置为真值（如 "reasoning_effort": true）时，思考模式下前端可选档位
    if bool(item.get("reasoning_effort")):
        profile["supports_reasoning_effort"] = True
    if flags["supports_thinking"]:
        profile["thinking"] = {
            "base_url": base_url.rstrip("/"),
            "api_key": api_key,
            "model_id": thinking_model_id or fast_model_id,
            "max_tokens": max_output_tokens,
            "context_window": context_window,
            "extra_params": {**extra_global, **(thinking_extra or {})},
        }
    else:
        profile["thinking"] = None
    return profile


def _load_custom_models() -> Dict[str, Dict[str, Any]]:
    custom_path = Path(resolve_deploy_config("custom_models.json"))
    if not custom_path.exists():
        return {}
    try:
        data = json.loads(custom_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = data if isinstance(data, list) else data.get("models", [])
    if not isinstance(items, list):
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        profile = _build_custom_profile(item)
        if not profile:
            continue
        model_name = str(item.get("model_name") or "").strip()
        if not model_name:
            continue
        output[model_name] = profile
    return output


def get_registered_model_profiles() -> Dict[str, Dict[str, Any]]:
    """返回 API 扩展注册的模型。项目不再内置任何供应商模型。"""
    return _load_custom_models()


def get_registered_model_keys(visible_only: bool = False) -> List[str]:
    keys: List[str] = []
    for key, profile in get_registered_model_profiles().items():
        if visible_only and profile.get("hidden"):
            continue
        keys.append(key)
    return keys


def get_default_model_key(visible_only: bool = True) -> str:
    """解析默认模型：环境变量优先，其次第一个已注册模型。"""
    profiles = get_registered_model_profiles()
    candidates = [key for key, profile in profiles.items() if not visible_only or not profile.get("hidden")]
    preferred = _env_optional("AGENT_DEFAULT_MODEL") or _env_optional("DEFAULT_MODEL_KEY")
    if preferred and preferred in profiles and (not visible_only or not profiles[preferred].get("hidden")):
        return preferred
    if candidates:
        return candidates[0]
    raise ValueError("未配置可用模型，请在 config/custom_models.json 中添加至少一个可用模型")


def resolve_model_key(candidate: Optional[str], *, visible_only: bool = False) -> str:
    """将缺失的模型 key 解析为默认模型；过期/未知 key 仍显式报错。"""
    profiles = get_registered_model_profiles()
    if not candidate:
        return get_default_model_key(visible_only=visible_only)
    if candidate in profiles and (not visible_only or not profiles[candidate].get("hidden")):
        return candidate
    raise ValueError(f"未知模型 key: {candidate}")


def get_model_profile(key: Optional[str]) -> dict:
    resolved_key = resolve_model_key(key)
    profiles = get_registered_model_profiles()
    profile = profiles[resolved_key]
    fast = profile.get("fast") or {}
    if not fast.get("api_key"):
        raise ValueError(f"模型 {resolved_key} 缺少 API Key 配置")
    if not fast.get("base_url") or not fast.get("model_id"):
        raise ValueError(f"模型 {resolved_key} 缺少 API 地址或模型 ID 配置")
    return profile


def get_model_capabilities(key: str) -> Dict[str, Any]:
    profile = get_model_profile(key)
    multimodal = _normalize_multimodal(profile.get("multimodal"))
    supports_image = multimodal in {"image", "image,video"}
    supports_video = multimodal in {"video", "image,video"}
    return {
        "supports_image": supports_image,
        "supports_video": supports_video,
        "multimodal": multimodal,
        "supports_thinking": bool(profile.get("supports_thinking", False)),
        "fast_only": bool(profile.get("fast_only", False)),
        "thinking_only": bool(profile.get("thinking_only", False)),
        "supports_reasoning_effort": bool(profile.get("supports_reasoning_effort", False)),
    }


def model_supports_image(key: str) -> bool:
    try:
        return bool(get_model_capabilities(key).get("supports_image"))
    except Exception:
        return False


def model_supports_video(key: str) -> bool:
    try:
        return bool(get_model_capabilities(key).get("supports_video"))
    except Exception:
        return False


def get_model_prompt_replacements(key: str) -> dict:
    """获取模型相关提示词字段。仅使用 API 扩展配置中的描述，不做模型名硬编码。"""
    try:
        profile = get_registered_model_profiles().get(resolve_model_key(key)) or {}
    except Exception:
        profile = {}
    model_description = str(profile.get("model_description") or profile.get("description") or "")
    return {
        "model_description": model_description,
        "thinking_model_line": "",
        "deep_thinking_line": "",
    }


def get_model_context_window(key: str) -> Optional[int]:
    """
    获取模型的最大上下文窗口（token 数）。
    优先使用 profile.context_window，其次回退到 fast.context_window。
    """
    profile = get_model_profile(key)
    ctx = profile.get("context_window")
    if ctx:
        return ctx
    fast = profile.get("fast") or {}
    return fast.get("context_window")
