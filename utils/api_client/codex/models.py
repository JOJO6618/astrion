"""Codex 模型发现：/models 端点动态拉取 + 三级降级缓存。

数据源优先级（实测见设计文档 §1.2）：
1. 在线 ``GET /models?client_version=…``（ETag 条件请求，304 复用本地）；
2. ``~/.codex/models_cache.json``（Codex CLI 的缓存，37 字段裁剪版，
   **不含 base_instructions**——此时 instructions 回落到内置兜底）；
3. astrion 自己的上次缓存（``codex_models.json``，在线 52 字段全量）。

缓存文件即 settings.CODEX_DATA_PATH，同时承载 proxy/client_version 配置键。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from utils.api_client.codex.auth import CodexAuthManager, get_auth_manager
from utils.api_client.codex.settings import (
    API_BASE,
    CODEX_AUTH_PATH,
    CODEX_CLI_MODELS_CACHE,
    MODELS_TTL_SECONDS,
    MODELS_URL,
    get_client_version,
    load_codex_data,
    resolve_proxy,
    save_codex_data,
)


def _profile_from_model_info(slug: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """把端点 ModelInfo 转换为 astrion 模型 profile 形态。"""
    input_modalities = info.get("input_modalities") or []
    supports_image = "image" in input_modalities
    context_window = info.get("context_window")
    levels = info.get("supported_reasoning_levels") or []
    display_name = str(info.get("display_name") or slug)
    description = str(info.get("description") or "")
    # 与常规模型（custom_models.json 手写描述）风格对齐：带上模型名让模型知道自己是谁
    model_description = f"你是 {display_name}。{description}" if description else f"你是 {display_name}。"
    base = {
        "base_url": API_BASE,
        "api_key": "",  # codex 凭证走 OAuth，不经 api_key
        "model_id": slug,
        "max_tokens": None,
        "context_window": context_window,
        "extra_params": {},
    }
    return {
        "name": display_name,
        "description": description,
        "model_description": model_description,
        "provider_type": "codex",
        "is_custom_model": False,
        "hidden": False,
        "multimodal": "image" if supports_image else "none",
        "context_window": context_window,
        "fast": dict(base),
        "thinking": dict(base),
        "supports_thinking": True,
        "fast_only": False,
        "thinking_only": True,  # 前端不显示 thinking/fast 切换
        "supports_reasoning_effort": True,
        "supported_reasoning_levels": [
            {
                "effort": str(lv.get("effort") or ""),
                "description": str(lv.get("description") or ""),
            }
            for lv in levels
            if isinstance(lv, dict) and lv.get("effort")
        ],
        "default_reasoning_level": info.get("default_reasoning_level"),
        "base_instructions": info.get("base_instructions"),  # CLI 缓存来源时为 None
        "codex_visibility": info.get("visibility"),
    }


class CodexModelsManager:
    """模型列表的拉取/缓存/读取（host 单用户进程级单例）。"""

    def __init__(self, auth_manager: Optional[CodexAuthManager] = None) -> None:
        self._auth = auth_manager or get_auth_manager()
        self._lock = threading.Lock()
        self._memory: Optional[Dict[str, Any]] = None

    # ---------------------------------------------------------------- 读取

    def _load(self) -> Dict[str, Any]:
        """加载缓存（内存 → astrion 文件 → CLI 缓存）。"""
        if self._memory is not None:
            return self._memory
        data = load_codex_data()
        if isinstance(data.get("models"), list) and data["models"]:
            self._memory = data
            return self._memory
        cli = self._load_cli_cache()
        if cli:
            self._memory = cli
            return self._memory
        self._memory = {"models": [], "source": "empty"}
        return self._memory

    @staticmethod
    def _load_cli_cache() -> Optional[Dict[str, Any]]:
        try:
            with open(CODEX_CLI_MODELS_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            models = data.get("models")
            if isinstance(models, list) and models:
                return {
                    "models": models,
                    "fetched_at": data.get("fetched_at"),
                    "client_version": data.get("client_version"),
                    "source": "cli_cache",
                }
        except Exception:
            pass
        return None

    def is_stale(self) -> bool:
        data = self._load()
        fetched_at = data.get("fetched_at")
        if not fetched_at:
            return True
        try:
            from datetime import datetime

            ts = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            return time.time() - ts.timestamp() > MODELS_TTL_SECONDS
        except Exception:
            return True

    # ---------------------------------------------------------------- 刷新

    def refresh_sync(self) -> Dict[str, Any]:
        """同步在线刷新（持锁）。失败降级旧缓存/CLI 缓存，返回结果状态。"""
        with self._lock:
            current = load_codex_data()
            headers = {
                "OpenAI-Beta": "responses=experimental",
                "originator": "codex_cli_rs",
                "accept": "application/json",
            }
            result: Dict[str, Any] = {"refreshed": False}
            try:
                tokens = self._auth._get_cached().get("tokens") or {}
                access = tokens.get("access_token")
                if not access:
                    result["error"] = "no_credentials"
                    return self._fallback(current, result)
                if self._auth._is_fresh(access) is False:
                    # 刷新是同步持锁操作，这里直接复用
                    self._auth._refresh_sync()
                    access = self._auth._get_cached()["tokens"]["access_token"]
                headers["Authorization"] = f"Bearer {access}"
                account_id = tokens.get("account_id")
                if account_id:
                    headers["chatgpt-account-id"] = account_id
                etag = current.get("etag")
                if etag:
                    headers["If-None-Match"] = etag
                with httpx.Client(proxy=resolve_proxy(), timeout=30) as client:
                    resp = client.get(
                        MODELS_URL,
                        params={"client_version": get_client_version()},
                        headers=headers,
                    )
                if resp.status_code == 304:
                    current["fetched_at"] = _now_iso()
                    current["source"] = "online"
                    save_codex_data(current)
                    self._memory = current
                    result.update({"refreshed": True, "not_modified": True})
                    return result
                if resp.status_code != 200:
                    result["error"] = f"http_{resp.status_code}"
                    return self._fallback(current, result)
                body = resp.json()
                models = body.get("models")
                if not isinstance(models, list):
                    result["error"] = "bad_payload"
                    return self._fallback(current, result)
                new_data = load_codex_data()  # 保留 proxy 等配置键
                new_data.update(
                    {
                        "models": models,
                        "fetched_at": _now_iso(),
                        "etag": resp.headers.get("etag"),
                        "client_version": get_client_version(),
                        "source": "online",
                    }
                )
                save_codex_data(new_data)
                self._memory = new_data
                result.update({"refreshed": True, "count": len(models)})
                return result
            except Exception as exc:
                result["error"] = str(exc)
                return self._fallback(current, result)

    def _fallback(
        self, current: Dict[str, Any], result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """在线失败时的降级：astrion 缓存 → CLI 缓存。"""
        if isinstance(current.get("models"), list) and current["models"]:
            self._memory = current
            result["fallback"] = "cache"
            return result
        cli = self._load_cli_cache()
        if cli:
            self._memory = cli
            result["fallback"] = "cli_cache"
            return result
        result["fallback"] = "empty"
        return result

    async def ensure_fresh(self) -> Dict[str, Any]:
        """异步入口：缓存陈旧则后台线程刷新（不阻塞事件循环）。"""
        self._load()
        if not self.is_stale():
            return {"refreshed": False, "reason": "fresh"}
        return await asyncio.to_thread(self.refresh_sync)

    # ---------------------------------------------------------------- 输出

    def get_profiles(self) -> Dict[str, Dict[str, Any]]:
        """输出合并进 get_registered_model_profiles() 的动态 profile。

        仅暴露 ``visibility == "list"`` 且 ``supported_in_api`` 的模型；
        key 统一加 ``codex/`` 前缀。凭证文件不存在（未登录/已登出）时返回空，
        模型从选择器消失；缓存本身保留，重新登录后立即可用。
        """
        data = self._load()
        if not Path(CODEX_AUTH_PATH).exists():
            return {}
        profiles: Dict[str, Dict[str, Any]] = {}
        for info in data.get("models") or []:
            if not isinstance(info, dict):
                continue
            slug = str(info.get("slug") or "").strip()
            if not slug:
                continue
            if info.get("visibility") != "list" or not info.get("supported_in_api"):
                continue
            profiles[f"codex/{slug}"] = _profile_from_model_info(slug, info)
        return profiles

    def get_base_instructions(self, model_id: str) -> Optional[str]:
        """按 slug（model_id，不含 codex/ 前缀）取 base_instructions。"""
        for info in self._load().get("models") or []:
            if isinstance(info, dict) and info.get("slug") == model_id:
                value = info.get("base_instructions")
                return str(value) if value else None
        return None

    def get_model_info(self, model_id: str) -> Optional[Dict[str, Any]]:
        for info in self._load().get("models") or []:
            if isinstance(info, dict) and info.get("slug") == model_id:
                return info
        return None

    def status_summary(self) -> Dict[str, Any]:
        data = self._load()
        visible = sorted(self.get_profiles().keys())
        return {
            "source": data.get("source"),
            "fetched_at": data.get("fetched_at"),
            # 与模型选择器同口径：只计 visibility=list 且 supported_in_api 的模型，
            # 不含官方隐藏的内部模型（如 gpt-reserve / codex-auto-review）
            "model_count": len(visible),
            "total_models": len(data.get("models") or []),
            "visible_models": visible,
        }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


_MANAGER: Optional[CodexModelsManager] = None
_MANAGER_LOCK = threading.Lock()


def get_models_manager() -> CodexModelsManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = CodexModelsManager()
        return _MANAGER
