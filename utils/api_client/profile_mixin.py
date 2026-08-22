# ========== api_client.py ==========
# utils/api_client.py - OpenAI-compatible API 客户端（支持Web模式）

import httpx
import json
import asyncio
import base64
import mimetypes
import os
from typing import List, Dict, Optional, AsyncGenerator, Any
from pathlib import Path
from datetime import datetime
from pathlib import Path
from typing import Tuple
try:
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )

from utils.log_rotation import append_line, prune_dir



from utils.api_client.utils import _api_dump_enabled

class APIClientProfileMixin:
    def apply_profile(self, profile: Dict):
        """
        动态应用模型配置
        profile 示例：
        {
            "fast": {"base_url": "...", "api_key": "...", "model_id": "...", "max_tokens": 8192},
            "thinking": {...} 或 None,
            "supports_thinking": True/False,
            "fast_only": True/False
        }
        """
        if not profile or "fast" not in profile:
            raise ValueError("无效的模型配置")
        fast = profile["fast"] or {}
        thinking = profile.get("thinking") or fast
        self.fast_api_config = {
            "base_url": fast.get("base_url") or self.fast_api_config.get("base_url"),
            "api_key": fast.get("api_key") or self.fast_api_config.get("api_key"),
            "model_id": fast.get("model_id") or self.fast_api_config.get("model_id")
        }
        self.thinking_api_config = {
            "base_url": thinking.get("base_url") or self.thinking_api_config.get("base_url"),
            "api_key": thinking.get("api_key") or self.thinking_api_config.get("api_key"),
            "model_id": thinking.get("model_id") or self.thinking_api_config.get("model_id")
        }
        self.fast_max_tokens = fast.get("max_tokens")
        self.thinking_max_tokens = thinking.get("max_tokens")
        self.fast_extra_params = fast.get("extra_params") or {}
        self.thinking_extra_params = thinking.get("extra_params") or {}
        self.supports_reasoning_effort = bool(profile.get("supports_reasoning_effort"))
        self.model_multimodal = self._normalize_multimodal_capability(profile.get("multimodal"))
        self.default_context_window = profile.get("context_window") or fast.get("context_window")
        # 同步旧字段
        self.api_base_url = self.fast_api_config["base_url"]
        self.api_key = self.fast_api_config["api_key"]
        self.model_id = self.fast_api_config["model_id"]
        try:
            self._debug_log({
                "event": "apply_profile",
                "model_key": self.model_key,
                "fast_model_id": self.fast_api_config.get("model_id"),
                "thinking_model_id": self.thinking_api_config.get("model_id"),
                "fast_max_tokens": self.fast_max_tokens,
                "thinking_max_tokens": self.thinking_max_tokens,
                "fast_extra_params": self.fast_extra_params,
                "thinking_extra_params": self.thinking_extra_params,
                "default_context_window": self.default_context_window,
            })
        except Exception:
            pass

    def update_context_budget(self, current_tokens: int, max_tokens: Optional[int]):
        """
        由上层在每次调用前告知当前对话占用的token数和模型最大上下文。
        """
        try:
            self.current_context_tokens = max(0, int(current_tokens))
        except (TypeError, ValueError):
            self.current_context_tokens = 0
        try:
            self.max_context_tokens = int(max_tokens) if max_tokens is not None else None
        except (TypeError, ValueError):
            self.max_context_tokens = None

    def get_current_thinking_mode(self) -> bool:
        """获取当前应该使用的思考模式：思考模式下每次请求都用思考配置。"""
        return bool(self.thinking_mode)

    def _build_headers(self, api_key: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def _select_api_config(self, use_thinking: bool) -> Dict[str, str]:
        """
        根据当前模式选择API配置，确保缺失字段回退到默认模型。
        """
        config = self.thinking_api_config if use_thinking else self.fast_api_config
        fallback = self.fast_api_config
        return {
            "base_url": config.get("base_url") or fallback["base_url"],
            "api_key": config.get("api_key") or fallback["api_key"],
            "model_id": config.get("model_id") or fallback["model_id"]
        }
