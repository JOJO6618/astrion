"""第三方 OpenAI 兼容提供商管理（opencode 模式，2026-09-24）。

职责：
- 预置提供商目录（``config/providers_catalog.json``，程序能力配置随版本走）；
- 已连接提供商的凭证与模型缓存（``<DATA_DIR>/providers.json``，0600；
  api_key 明文仅存于此文件，API 层永远不向前端回传明文，只回掩码）；
- ``GET {base_url}/models`` 自动发现，解析结果转 profile 合并进
  ``config.model_profiles.get_registered_model_profiles()``（key 形如
  ``deepseek/deepseek-chat``，``provider_type="provider"``）。

协议范围：仅 OpenAI Chat Completions（POST ``{base_url}/chat/completions``）。
多协议网关（OpenCode Zen/Go 等）仅其 chat/completions 子集可用，
由目录条目的 ``protocol_note`` 标注，前端负责提示。

权限模型：连接/断开/刷新等全部管理操作仅管理员（host 或 docker 模式），
由 server/providers.py 蓝图门控；发现的模型与自定义模型一样对全员可见。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config.paths import DATA_DIR

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "providers_catalog.json"
_STORE_FILENAME = "providers.json"
_FETCH_TIMEOUT = 20
# /models 返回中明显不是聊天模型的 id 片段（embedding/tts/图像/审核等），发现时剔除
_NON_CHAT_ID_PATTERN = re.compile(
    r"(embed|embedding|tts|whisper|dall-e|moderation|transcri|speech|rerank|babbage|davinci|clip)",
    re.IGNORECASE,
)
_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _mask_key(api_key: Optional[str]) -> Optional[str]:
    """密钥掩码：sk-...1234。永远不向 API 调用方回传明文。"""
    if not api_key:
        return None
    text = str(api_key)
    if len(text) <= 8:
        return text[:2] + "..." if len(text) > 2 else "***"
    return f"{text[:5]}...{text[-4:]}"


def parse_models_payload(body: Any) -> List[Dict[str, Any]]:
    """兼容三种 /models 响应形状，统一输出 [{id, name, context_length}]。

    - 标准 OpenAI：``{"object": "list", "data": [...]}``
    - Mistral 裸数组：``[...]``
    - 兜底：``{"models": [...]}``
    """
    items: Any = None
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        if isinstance(body.get("data"), list):
            items = body["data"]
        elif isinstance(body.get("models"), list):
            items = body["models"]
    if not isinstance(items, list):
        return []
    output: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            output.append({"id": item.strip()})
            continue
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("name") or "").strip()
        if not model_id:
            continue
        entry: Dict[str, Any] = {"id": model_id}
        display = str(
            item.get("display_name") or item.get("displayName") or item.get("name") or ""
        ).strip()
        if display and display != model_id:
            entry["name"] = display
        # 上下文窗口：OpenRouter/xAI = context_length；Mistral = max_context_length；
        # 部分兼容网关直接给 context_window
        ctx = (
            item.get("context_length")
            or item.get("max_context_length")
            or item.get("context_window")
        )
        try:
            ctx_int = int(ctx) if ctx is not None else None
        except (TypeError, ValueError):
            ctx_int = None
        if ctx_int and ctx_int > 0:
            entry["context_length"] = ctx_int
        output.append(entry)
    return output


class ProviderManager:
    """已连接提供商的存储、发现与 profile 输出（进程级单例）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._memory: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------ 存储

    @staticmethod
    def _store_path() -> Path:
        return Path(DATA_DIR) / _STORE_FILENAME

    def _load(self) -> Dict[str, Any]:
        if self._memory is not None:
            return self._memory
        path = self._store_path()
        data: Dict[str, Any] = {"version": 1, "providers": {}}
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("providers"), dict):
                    data = raw
        except Exception:
            pass
        self._memory = data
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        path = self._store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        self._memory = data

    # ------------------------------------------------------------ 目录

    @staticmethod
    def catalog_entries() -> List[Dict[str, Any]]:
        try:
            raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
            entries = raw.get("providers")
            if isinstance(entries, list):
                return [e for e in entries if isinstance(e, dict) and e.get("id")]
        except Exception:
            pass
        return []

    def catalog(self) -> List[Dict[str, Any]]:
        """目录 + 连接状态合并（供提供商页展示）。"""
        connected = self._load()["providers"]
        codex_connected = self._codex_connected()
        output: List[Dict[str, Any]] = []
        for entry in self.catalog_entries():
            item = dict(entry)
            # 图标 URL 由后端拼接（静态目录伺服），前端零约定开箱即用
            icon = item.get("icon")
            item["icon_url"] = f"/static/icons/providers/{icon}" if icon else None
            pid = entry["id"]
            if entry.get("auth") == "codex_oauth":
                item["connected"] = codex_connected
            else:
                item["connected"] = pid in connected and bool(connected[pid].get("enabled", True))
            if item["connected"] and pid in connected:
                summary = self._public_summary(pid, connected[pid])
                item["models_count"] = len(summary.get("models") or [])
                item["models_fetched_at"] = summary.get("models_fetched_at")
                item["models_error"] = summary.get("models_error")
            output.append(item)
        return output

    @staticmethod
    def _codex_connected() -> bool:
        try:
            from utils.api_client.codex.settings import CODEX_AUTH_PATH

            return Path(CODEX_AUTH_PATH).exists()
        except Exception:
            return False

    # ------------------------------------------------------------ 查询

    def get_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        return self._load()["providers"].get(provider_id)

    def list_connected(self) -> List[Dict[str, Any]]:
        providers = self._load()["providers"]
        return [self._public_summary(pid, p) for pid, p in providers.items()]

    @staticmethod
    def _public_summary(provider_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        """对外输出：key 掩码化，剥离一切敏感字段。"""
        return {
            "id": provider_id,
            "catalog_id": record.get("catalog_id"),
            "name": record.get("name"),
            "base_url": record.get("base_url"),
            "api_key_masked": _mask_key(record.get("api_key")),
            "has_custom_headers": bool(record.get("headers")),
            "enabled": bool(record.get("enabled", True)),
            "connected_at": record.get("connected_at"),
            "models": record.get("models") or [],
            "models_fetched_at": record.get("models_fetched_at"),
            "models_error": record.get("models_error"),
        }

    # ------------------------------------------------------------ 连接管理

    def connect(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """连接提供商：校验 → 保存凭证 → 立即拉取模型列表。

        payload 两种形态：
        - 目录条目：``{catalog_id, api_key?}``（base_url 等从目录取）
        - 自定义：``{provider_id, name, base_url, api_key?, headers?}``
        """
        data = self._load()
        catalog_id = str(payload.get("catalog_id") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()

        if catalog_id:
            entry = next((e for e in self.catalog_entries() if e["id"] == catalog_id), None)
            if not entry:
                return {"success": False, "error": "unknown_catalog_id"}
            if entry.get("auth") == "codex_oauth":
                return {"success": False, "error": "codex_oauth_use_codex_api"}
            provider_id = entry["id"]
            name = entry["name"]
            base_url = entry["base_url"]
            auth = entry.get("auth") or "api_key"
        else:
            provider_id = str(payload.get("provider_id") or "").strip()
            name = str(payload.get("name") or "").strip() or provider_id
            base_url = str(payload.get("base_url") or "").strip()
            auth = "api_key" if api_key else "none"
            if not _PROVIDER_ID_PATTERN.match(provider_id):
                return {"success": False, "error": "invalid_provider_id"}
            if not base_url.startswith(("http://", "https://")):
                return {"success": False, "error": "invalid_base_url"}
            if provider_id in data["providers"]:
                return {"success": False, "error": "provider_id_exists"}

        if auth == "api_key" and not api_key:
            return {"success": False, "error": "api_key_required"}
        # 本地/无密钥提供商：填占位 key，使下游 profile 校验（api_key 非空）通过；
        # 服务端不校验 Authorization，占位值不会被使用。
        if auth == "none" and not api_key:
            api_key = "not-required"

        headers = payload.get("headers")
        if not isinstance(headers, dict):
            headers = {}
        headers = {str(k).strip(): str(v) for k, v in headers.items() if str(k).strip()}

        record: Dict[str, Any] = {
            "catalog_id": catalog_id or None,
            "name": name,
            "base_url": base_url.rstrip("/"),
            "api_key": api_key,
            "headers": headers,
            "enabled": True,
            "connected_at": _now_iso(),
            "models": [],
            "models_fetched_at": None,
            "models_error": None,
        }

        with self._lock:
            data["providers"][provider_id] = record
            models, error = self._fetch_models(record["base_url"], record["api_key"], headers)
            record["models"] = models
            record["models_fetched_at"] = _now_iso()
            record["models_error"] = error
            self._save(data)

        result: Dict[str, Any] = {
            "success": True,
            "provider": self._public_summary(provider_id, record),
            "models_count": len(models),
        }
        if error:
            result["models_error"] = error
        return result

    def disconnect(self, provider_id: str) -> bool:
        data = self._load()
        if provider_id not in data["providers"]:
            return False
        with self._lock:
            data["providers"].pop(provider_id, None)
            self._save(data)
        return True

    def refresh_models(self, provider_id: str) -> Dict[str, Any]:
        data = self._load()
        record = data["providers"].get(provider_id)
        if not record:
            return {"success": False, "error": "provider_not_found"}
        with self._lock:
            models, error = self._fetch_models(
                record["base_url"], record.get("api_key") or "", record.get("headers") or {}
            )
            if error and models:
                # 部分失败（如解析告警）仍更新列表
                pass
            if not error or models:
                record["models"] = models
                record["models_fetched_at"] = _now_iso()
            record["models_error"] = error
            self._save(data)
        result: Dict[str, Any] = {
            "success": not error or bool(models),
            "models_count": len(record.get("models") or []),
            "provider": self._public_summary(provider_id, record),
        }
        if error:
            result["models_error"] = error
        return result

    # ------------------------------------------------------------ 模型发现

    @staticmethod
    def _fetch_models(
        base_url: str, api_key: str, headers: Dict[str, str]
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """GET {base_url}/models。返回 (模型列表, 错误消息)；成功时错误为 None。"""
        url = f"{base_url.rstrip('/')}/models"
        request_headers = {"Accept": "application/json"}
        if api_key:
            request_headers["Authorization"] = f"Bearer {api_key}"
        request_headers.update(headers or {})
        try:
            with httpx.Client(timeout=_FETCH_TIMEOUT) as client:
                resp = client.get(url, headers=request_headers)
        except httpx.ConnectError as exc:
            return [], f"connect_error: {exc}"
        except httpx.TimeoutException:
            return [], "timeout"
        except Exception as exc:
            return [], f"request_error: {exc}"
        if resp.status_code in (401, 403):
            return [], f"auth_failed (http_{resp.status_code})"
        if resp.status_code != 200:
            return [], f"http_{resp.status_code}"
        try:
            body = resp.json()
        except Exception:
            return [], "bad_payload"
        models = parse_models_payload(body)
        # 剔除明显非聊天模型（embedding/tts/图像等）
        models = [m for m in models if not _NON_CHAT_ID_PATTERN.search(m["id"])]
        if not models:
            return [], "empty_models"
        return models, None

    # ------------------------------------------------------------ profile 输出

    def get_profiles(self) -> Dict[str, Dict[str, Any]]:
        """把已连接提供商的已发现模型转成注册表 profile（key = ``{pid}/{model_id}``）。"""
        profiles: Dict[str, Dict[str, Any]] = {}
        for pid, record in self._load()["providers"].items():
            if not record.get("enabled", True):
                continue
            base_url = str(record.get("base_url") or "").rstrip("/")
            api_key = str(record.get("api_key") or "")
            headers = record.get("headers") or {}
            if not base_url:
                continue
            for model in record.get("models") or []:
                model_id = str(model.get("id") or "").strip()
                if not model_id:
                    continue
                ctx = model.get("context_length")
                display_name = str(model.get("name") or model_id)
                profile: Dict[str, Any] = {
                    "name": display_name,
                    "description": str(record.get("name") or pid),
                    "model_description": f"你是 {display_name}。",
                    "provider_type": "provider",
                    "provider_id": pid,
                    "provider_name": str(record.get("name") or pid),
                    "is_custom_model": False,
                    "hidden": False,
                    # 默认图片+视频双模态（用户拍板：provider 同步模型默认全能，
                    # 与思考/快速双支持同理；此前写死 none 导致全部标记为纯文本）
                    "multimodal": "image,video",
                    "context_window": ctx,
                    "fast": {
                        "base_url": base_url,
                        "api_key": api_key,
                        "model_id": model_id,
                        "max_tokens": None,
                        "context_window": ctx,
                        "extra_params": {},
                    },
                    # 默认 fast+thinking 双支持（对齐手写模型 param_toggle 无参形态：
                    # thinking 块与 fast 同端点同模型，思考行为由 run_mode 请求层处理；
                    # 此前写死 fast_only 导致所有提供商模型只显示「快速」）
                    "thinking": {
                        "base_url": base_url,
                        "api_key": api_key,
                        "model_id": model_id,
                        "max_tokens": None,
                        "context_window": ctx,
                        "extra_params": {},
                    },
                    "supports_thinking": True,
                    "fast_only": False,
                }
                if headers:
                    profile["headers"] = dict(headers)
                profiles[f"{pid}/{model_id}"] = profile
        return profiles


_MANAGER: Optional[ProviderManager] = None
_MANAGER_LOCK = threading.Lock()


def get_provider_manager() -> ProviderManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ProviderManager()
        return _MANAGER
