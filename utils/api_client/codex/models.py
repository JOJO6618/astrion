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
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from utils.api_client.codex.auth import CodexAuthManager, get_auth_manager
from utils.api_client.codex.settings import (
    API_BASE,
    CODEX_ACCOUNT_DISPLAY_BASE,
    CODEX_AUTH_PATH,
    CODEX_CLI_MODELS_CACHE,
    CODEX_DATA_PATH,
    CODEX_NPM_LATEST_URL,
    CODEX_VERSION_DISCOVERY_TIMEOUT_SECONDS,
    LEGACY_CODEX_ACCOUNT_ID,
    MODELS_TTL_SECONDS,
    MODELS_URL,
    get_client_version,
    load_codex_data,
    resolve_proxy,
    save_codex_data,
)


def _api_pricing(slug: str) -> Optional[Dict[str, Any]]:
    """models.dev 的 OpenAI API 单价。

    ChatGPT 订阅（OAuth）只是凭证来源不同，对话花费同样按 API 单价折算；
    查不到单价的模型交由计费层标记未计价，不在这里虚构价格。
    """
    try:
        from modules import modelsdev_registry
    except Exception:
        return None
    meta = modelsdev_registry.get_model_meta({"modelsdev_key": "openai"}, slug)
    return (meta or {}).get("pricing")


def _profile_from_model_info(
    slug: str,
    info: Dict[str, Any],
    connection_id: str = LEGACY_CODEX_ACCOUNT_ID,
    provider_name: str = CODEX_ACCOUNT_DISPLAY_BASE,
) -> Dict[str, Any]:
    """把端点 ModelInfo 转换为 astrion 模型 profile 形态。"""
    input_modalities = info.get("input_modalities") or []
    supports_image = "image" in input_modalities
    context_window = info.get("context_window")
    levels = info.get("supported_reasoning_levels") or []
    from utils.api_client.codex.service_tier import supports_fast_mode

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
        # 2026-09-25 协议泛化：codex 不再是特殊 provider_type，统一为普通
        # provider（openai-codex）；行为差异全部配置化——api_protocol 走
        # Responses 通用通道，responses_auth/instructions_source 声明订阅特性。
        # 2026-10-29 多账号：provider_id = 账号连接 id（主账号仍为 openai-codex，
        # 旧模型 key 保持有效）；responses_account_id 使请求链路精确路由该账号凭证。
        "provider_type": "provider",
        "provider_id": connection_id,
        # 与 openai-api（API Key 条目）在模型菜单分组中区分（带稳定账号短哈希）
        "provider_name": provider_name,
        "responses_account_id": connection_id,
        "api_protocol": "responses",
        "responses_auth": "codex_oauth",
        "instructions_source": "codex_models_cache",
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
        "supports_chatgpt_fast_mode": supports_fast_mode(info),
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
        "pricing": _api_pricing(slug),
    }


_CODEX_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _discover_latest_client_version() -> Optional[str]:
    """从官方 npm Registry 读取 Codex CLI latest 版本。

    /models 没有提供版本发现接口，因此使用 OpenAI 发布的
    ``@openai/codex`` 元数据作为候选版本；调用方仍必须用 /models
    实际验证，失败时回退到上一次成功版本。
    """
    try:
        with httpx.Client(
            proxy=resolve_proxy(),
            timeout=CODEX_VERSION_DISCOVERY_TIMEOUT_SECONDS,
        ) as client:
            response = client.get(
                CODEX_NPM_LATEST_URL,
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            return None
        version = response.json().get("version")
        if not isinstance(version, str):
            return None
        version = version.strip()
        return version if _CODEX_VERSION_RE.fullmatch(version) else None
    except Exception:
        return None


class CodexModelsManager:
    """模型列表的拉取/缓存/读取（按账号独立实例，进程级注册表）。

    多账号（2026-10-29）：每个账号一个 manager，缓存写入各自文件（主账号沿用
    旧的 ``codex_models.json`` 以保留存量数据与 proxy/client_version 配置键），
    刷新/ETag/内存缓存完全隔离。
    """

    def __init__(
        self,
        auth_manager: Optional[CodexAuthManager] = None,
        account_id: Optional[str] = None,
        cache_path: Optional[str] = None,
    ) -> None:
        self.account_id = str(account_id or "").strip()
        if not self.account_id:
            if auth_manager is None:
                self.account_id = _default_account_id()
            else:
                # 显式传入凭证的构造方式（历史测试/工具）默认仍是主账号凭证文件
                self.account_id = LEGACY_CODEX_ACCOUNT_ID
        if auth_manager is None:
            if self.account_id == LEGACY_CODEX_ACCOUNT_ID:
                auth_manager = get_auth_manager()
            else:
                from utils.api_client.codex.accounts import get_codex_account_store

                auth_manager = get_codex_account_store().get_auth_manager(self.account_id)
        self._auth = auth_manager
        if cache_path is None and self.account_id != LEGACY_CODEX_ACCOUNT_ID:
            # 多账号：模型缓存必须按账号隔离（主账号沿用旧的 codex_models.json）
            try:
                from utils.api_client.codex.accounts import get_codex_account_store

                cache_path = get_codex_account_store().get_models_cache_path(self.account_id)
            except Exception:
                cache_path = None
        self._cache_path = str(cache_path or CODEX_DATA_PATH)
        self._legacy_cache = self._cache_path == str(CODEX_DATA_PATH)
        self._lock = threading.Lock()
        self._memory: Optional[Dict[str, Any]] = None

    # ---------------------------------------------------------------- 缓存文件

    def _read_cache(self) -> Dict[str, Any]:
        """读本账号缓存文件；主账号沿用全局读函数（保留 proxy 等配置键）。"""
        if self._legacy_cache:
            return load_codex_data()
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_cache(self, data: Dict[str, Any]) -> None:
        if self._legacy_cache:
            save_codex_data(data)
            return
        path = Path(self._cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    # ---------------------------------------------------------------- 读取

    def _load(self) -> Dict[str, Any]:
        """加载缓存（内存 → 本账号文件 → CLI 缓存仅主账号降级）。"""
        if self._memory is not None:
            return self._memory
        data = self._read_cache()
        if isinstance(data.get("models"), list) and data["models"]:
            self._memory = data
            return self._memory
        cli = self._load_cli_cache() if self._legacy_cache else None
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
        """同步在线刷新（持锁），自动发现并验证 Codex CLI 版本。"""
        with self._lock:
            current = self._read_cache()
            cached_version = get_client_version()
            discovered_version = _discover_latest_client_version()
            candidates = [v for v in (discovered_version, cached_version) if v]
            candidates = list(dict.fromkeys(candidates))
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
                    tokens = self._auth._get_cached().get("tokens") or {}
                    access = tokens.get("access_token")
                headers["Authorization"] = f"Bearer {access}"
                account_id = tokens.get("account_id")
                if account_id:
                    headers["chatgpt-account-id"] = account_id

                last_error = "models_request_failed"
                with httpx.Client(proxy=resolve_proxy(), timeout=30) as client:
                    for version in candidates:
                        request_headers = dict(headers)
                        # ETag 与模型目录版本绑定；版本变化时不能拿旧 ETag
                        # 误判为 304，否则会把旧目录保存到新版本下。
                        if version == cached_version and current.get("etag"):
                            request_headers["If-None-Match"] = current["etag"]
                        try:
                            resp = client.get(
                                MODELS_URL,
                                params={"client_version": version},
                                headers=request_headers,
                            )
                        except Exception as exc:
                            last_error = str(exc)
                            continue

                        if resp.status_code == 304 and version == cached_version:
                            current["fetched_at"] = _now_iso()
                            current["source"] = "online"
                            self._write_cache(current)
                            self._memory = current
                            result.update(
                                {
                                    "refreshed": True,
                                    "not_modified": True,
                                    "count": len(current.get("models") or []),
                                    "client_version": version,
                                    "version_source": "cache",
                                }
                            )
                            return result
                        if resp.status_code != 200:
                            last_error = f"http_{resp.status_code}"
                            continue
                        try:
                            models = resp.json().get("models")
                        except Exception as exc:
                            last_error = f"bad_json: {exc}"
                            continue
                        if not isinstance(models, list) or not models:
                            last_error = "bad_payload"
                            continue

                        new_data = self._read_cache()  # 保留 proxy 等配置键（主账号）
                        new_data.update(
                            {
                                "models": models,
                                "fetched_at": _now_iso(),
                                "etag": resp.headers.get("etag"),
                                "client_version": version,
                                "source": "online",
                            }
                        )
                        self._write_cache(new_data)
                        self._memory = new_data
                        result.update(
                            {
                                "refreshed": True,
                                "count": len(models),
                                "client_version": version,
                                "version_source": (
                                    "npm_latest" if version == discovered_version else "cache"
                                ),
                            }
                        )
                        if version != cached_version:
                            result["version_fallback"] = False
                        return result

                result["error"] = last_error
                if discovered_version and discovered_version != cached_version:
                    result["version_fallback"] = True
                return self._fallback(current, result)
            except Exception as exc:
                result["error"] = str(exc)
                return self._fallback(current, result)

    def _fallback(
        self, current: Dict[str, Any], result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """在线失败时的降级：astrion 缓存 → CLI 缓存（CLI 缓存仅主账号）。

        ``~/.codex/models_cache.json`` 属于本机 Codex CLI 登录的那个账号，
        多账号下不能给其他账号当降级数据源（会拿到别人的模型目录）——与
        ``_load`` 的 ``_legacy_cache`` 门控保持一致。
        """
        if isinstance(current.get("models"), list) and current["models"]:
            self._memory = current
            result["fallback"] = "cache"
            return result
        cli = self._load_cli_cache() if self._legacy_cache else None
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
        key 统一加 ``openai-codex/`` 前缀（2026-09-25 泛化，原 codex/ 前缀废除，
        不做向后兼容）。凭证文件不存在（未登录/已登出）时返回空，
        模型从选择器消失；缓存本身保留，重新登录后立即可用。
        """
        data = self._load()
        credential_path = getattr(self._auth, "auth_path", None) or (
            CODEX_AUTH_PATH if self.account_id == LEGACY_CODEX_ACCOUNT_ID else None
        )
        if not credential_path or not Path(credential_path).exists():
            return {}
        # 账号展示名（带稳定哈希；主账号同样追加哈希）——分组/选择器按连接隔离
        connection_id = self.account_id
        provider_name = CODEX_ACCOUNT_DISPLAY_BASE
        try:
            from utils.api_client.codex.accounts import get_codex_account_store

            account = get_codex_account_store().get_account(connection_id)
            provider_name = str(account.get("display_name") or provider_name)
        except Exception:
            pass
        profiles: Dict[str, Dict[str, Any]] = {}
        for info in data.get("models") or []:
            if not isinstance(info, dict):
                continue
            slug = str(info.get("slug") or "").strip()
            if not slug:
                continue
            if info.get("visibility") != "list" or not info.get("supported_in_api"):
                continue
            profiles[f"{connection_id}/{slug}"] = _profile_from_model_info(
                slug, info, connection_id, provider_name
            )
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


_MANAGERS: Dict[str, CodexModelsManager] = {}
_MANAGER_LOCK = threading.Lock()


def _default_account_id() -> str:
    """缺省账号 = 注册表主账号（与 ``get_auth_manager(None)`` 同一解析）。

    没有任何登记账号时明确报错，绝不静默挑一个别的身份（避免凭证/缓存错配）。
    """
    from utils.api_client.codex.accounts import get_codex_account_store

    primary = get_codex_account_store().primary_account_id()
    if not primary:
        raise KeyError("no_codex_account")
    return str(primary)


def get_models_manager(account_id: Optional[str] = None) -> CodexModelsManager:
    """按账号取模型管理器（每账号独立缓存/刷新/ETag；主账号为 legacy 文件）。

    ``account_id=None`` 与 ``CodexAccountStore.get_auth_manager(None)`` 保持一致：
    都解析到注册表主账号；没有登记账号时两者都明确报 ``no_codex_account``，
    避免“凭证用 A、模型缓存用 legacy B”的错配。
    """
    key = str(account_id or "").strip() or _default_account_id()
    with _MANAGER_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = CodexModelsManager(account_id=key)
            _MANAGERS[key] = manager
        return manager


def reset_models_manager(account_id: str) -> None:
    """登出/重登后丢弃指定账号的内存模型缓存（不影响其他账号）。"""
    key = str(account_id or "").strip()
    if not key:
        return
    with _MANAGER_LOCK:
        manager = _MANAGERS.pop(key, None)
    if manager is not None:
        manager._memory = None


def reset_models_managers() -> None:
    """测试/重登后用：清空实例缓存（下次按磁盘重建）。"""
    with _MANAGER_LOCK:
        _MANAGERS.clear()


def get_all_codex_profiles() -> Dict[str, Dict[str, Any]]:
    """全部已连接账号的模型 profile（key 带各自连接 id 前缀）。"""
    profiles: Dict[str, Dict[str, Any]] = {}
    try:
        from utils.api_client.codex.accounts import get_codex_account_store

        accounts = get_codex_account_store().list_accounts()
    except Exception:
        # 注册表不可用时退回主账号（保持旧行为可用）
        try:
            return get_models_manager().get_profiles()
        except Exception:
            return {}
    for account in accounts:
        if not account.get("connected"):
            continue
        try:
            profiles.update(get_models_manager(str(account["id"])).get_profiles())
        except Exception:
            continue
    return profiles
