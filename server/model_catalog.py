"""模型目录接口（Host 主界面 / Quick Chat / CLI 共用）。

从原 `server.api_v1.list_models_api` 原样抽出：URL 仍为 `/api/v1/models`，
响应字段与模型能力描述不变。full 形态与 headless 形态各自注册本蓝图即可，
不再依赖 API 用户模块。

认证口径保持原样：端点自身不加装饰器（原实现同样没有），与 full 形态中
`api_v1_bp` 内注册时一致；桌面/Quick Chat 走既有会话或 Host Bearer 通道。
"""
from __future__ import annotations

from flask import Blueprint, jsonify

from config.model_profiles import get_registered_model_profiles

model_catalog_bp = Blueprint("model_catalog", __name__, url_prefix="/api/v1")


@model_catalog_bp.route("/models", methods=["GET"])
def list_models_api():
    items = []
    for key, profile in get_registered_model_profiles().items():
        if profile.get("hidden"):
            continue
        multimodal = str(profile.get("multimodal") or "none")
        description = profile.get("description") or ""
        items.append({
            "model_key": key,
            "name": profile.get("name", key),
            "description": description,
            "visible": not bool(profile.get("hidden")),
            "supports_thinking": profile.get("supports_thinking", False),
            "fast_only": profile.get("fast_only", False),
            "thinking_only": profile.get("thinking_only", False),
            "supports_reasoning_effort": profile.get("supports_reasoning_effort", False),
            "is_chatgpt_model": profile.get("responses_auth") == "codex_oauth",
            "supports_chatgpt_fast_mode": profile.get("supports_chatgpt_fast_mode", False),
            "multimodal": multimodal,
            "context_window": profile.get("context_window"),
            "max_output_tokens": (profile.get("fast") or {}).get("max_tokens"),
            # 协议标识（2026-09-25 泛化）：前端按它区分 chat / responses 行为
            "api_protocol": profile.get("api_protocol") or "chat_completions",
            # Codex 动态模型字段（常规模型为 None/缺省，前端按 provider_type 分支）
            "provider_type": profile.get("provider_type"),
            # 提供商同步模型的分组字段（手写 custom / Codex 为 None）
            "provider_id": profile.get("provider_id"),
            "provider_name": profile.get("provider_name"),
            "supported_reasoning_levels": profile.get("supported_reasoning_levels"),
            "default_reasoning_level": profile.get("default_reasoning_level"),
        })
    return jsonify({"success": True, "items": items})


__all__ = ["model_catalog_bp", "list_models_api"]
