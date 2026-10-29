"""第三方 OpenAI 兼容提供商管理（opencode 模式，2026-09-24；多连接 2026-10-29）。

职责：
- 预置提供商目录（``config/providers_catalog.json``，程序能力配置随版本走）；
- 已连接提供商的凭证与模型缓存（``<DATA_DIR>/providers.json``，0600；
  api_key 明文仅存于此文件，API 层永远不向前端回传明文，只回掩码）；
- ``GET {base_url}/models`` 自动发现，解析结果转 profile 合并进
  ``config.model_profiles.get_registered_model_profiles()``（key 形如
  ``deepseek/deepseek-chat``，``provider_type="provider"``）。

多连接语义（2026-10-29 用户要求）：
- 同一目录提供商可添加多个独立 API Key 连接，每个连接独立模型列表/刷新/删除；
- 存储用 **内部连接 id** 作键：新目录连接一律随机
  ``<catalog_id>-<8位十六进制>``，新自定义连接一律随机
  ``custom-<请求id>-<8位十六进制>``，**永不复用**（删除后也不会再生成同 id），
  因此旧对话/旧模型配置里的 ``{连接id}/{model}`` 在连接删除后只会解析失败，
  绝不会静默落到新 Key；用户填写的自定义 ``provider_id`` 只作基础标识/展示，
  不作为可复用路由身份；
- 展示名由「目录名 + 展示序号」派生：序号 0 = 原名，1 = 原名-1，2 = 原名-2…
  序号在创建时分配并存盘，删除不改剩余编号；新增取当前最小空闲序号
  （删除「原名-1」后新连接重新占「原名-1」，再添加则占「原名-3」之类的下一个
  空闲号）。序号只影响展示，不参与请求路由；
- 不存删除墓碑、不存 Key 历史；重复活跃 Key（同一目录 + 同一 api_key）拒绝
  重复添加（``duplicate_connection``）；
- 旧版 ``providers.json``（v1，单连接）启动时一次性迁移：原记录保持原键
  （如 ``deepseek``）作为内部连接 id、展示序号 0，旧模型引用保持有效。

协议范围：OpenAI Chat Completions + Responses（Responses 见
``utils/api_client/responses``）。多协议网关仅其 chat/completions 子集可用，
由目录条目的 ``protocol_note`` 标注，前端负责提示。

权限模型：连接/断开/刷新等全部管理操作仅管理员（host 或 docker 模式），
由 server/providers.py 蓝图门控；发现的模型与自定义模型一样对全员可见。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config.paths import DATA_DIR

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "providers_catalog.json"
_STORE_FILENAME = "providers.json"
_STORE_VERSION = 2
_FETCH_TIMEOUT = 20
# /models 返回中明显不是聊天模型的 id 片段（embedding/tts/图像/审核等），发现时剔除
_NON_CHAT_ID_PATTERN = re.compile(
    r"(embed|embedding|tts|whisper|dall-e|moderation|transcri|speech|rerank|babbage|davinci|clip)",
    re.IGNORECASE,
)
_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CODEX_CATALOG_ID = "openai-codex"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _mask_key(api_key: Optional[str]) -> Optional[str]:
    """密钥掩码：sk-...1234。永远不向 API 调用方回传明文。"""
    if not api_key:
        return None
    text = str(api_key)
    # 无密钥提供商（Ollama / LM Studio）存的内部占位值不是真实凭证，不应展示掩码
    if text == "not-required":
        return None
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
        self._lock = threading.RLock()
        self._memory: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------ 存储

    @staticmethod
    def _store_path() -> Path:
        return Path(DATA_DIR) / _STORE_FILENAME

    def _load_locked(self, reload: bool = False) -> Dict[str, Any]:
        """读存储（锁内调用）。version<2 时一次性迁移到多连接结构。"""
        if self._memory is not None and not reload:
            return self._memory
        path = self._store_path()
        data: Dict[str, Any] = {"version": _STORE_VERSION, "providers": {}}
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("providers"), dict):
                    data = raw
        except Exception:
            pass
        if not isinstance(data.get("providers"), dict):
            data["providers"] = {}
        if self._ensure_schema_locked(data):
            # 受限只读目录（测试沙箱）不阻断读取；真正写入操作会单独失败报错。
            try:
                self._save_locked(data)
            except OSError:
                pass
        self._memory = data
        return data

    def _load(self) -> Dict[str, Any]:
        # 无锁读取：仅凭内存缓存，必要时进入加锁路径初始化
        if self._memory is not None:
            return self._memory
        with self._lock:
            return self._load_locked()

    def _save_locked(self, data: Dict[str, Any]) -> None:
        path = self._store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".json.tmp-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        self._memory = data

    def _ensure_schema_locked(self, data: Dict[str, Any]) -> bool:
        """v1 → v2 一次性迁移（幂等）：补 display_index/base_name/name。

        旧记录保持原键作内部连接 id（如 ``deepseek``），展示序号 0，因此
        ``deepseek/xxx`` 等旧模型引用继续有效，不会串到任何新连接。
        """
        changed = False
        catalog_names = {e["id"]: str(e.get("name") or e["id"]) for e in self.catalog_entries()}
        records = data.get("providers") or {}
        # 按目录分组，为缺失序号的记录分配最小空闲序号（组内已有序号保持不变）
        groups: Dict[str, List[str]] = {}
        for pid, record in records.items():
            if not isinstance(record, dict):
                continue
            catalog_id = str(record.get("catalog_id") or "")
            group_key = catalog_id or f"__custom__:{pid}"
            groups.setdefault(group_key, []).append(pid)
        for group_key, ids in groups.items():
            used = {
                int(records[pid]["display_index"])
                for pid in ids
                if isinstance(records[pid].get("display_index"), int)
            }
            missing = [
                pid for pid in ids if not isinstance(records[pid].get("display_index"), int)
            ]
            if not missing:
                continue
            # 旧记录按 connected_at 排序，先来先得，保证迁移顺序稳定
            missing.sort(key=lambda pid: (str(records[pid].get("connected_at") or ""), pid))
            next_index = 0
            for pid in missing:
                while next_index in used:
                    next_index += 1
                record = records[pid]
                record["display_index"] = next_index
                used.add(next_index)
                next_index += 1
                changed = True
        for pid, record in records.items():
            if not isinstance(record, dict):
                continue
            catalog_id = str(record.get("catalog_id") or "")
            if "requested_id" not in record:
                # 存量迁移身份：旧记录键就是当年的路由身份（目录 id 或用户自定义 id）
                record["requested_id"] = catalog_id or pid
                changed = True
            if "base_name" not in record:
                record["base_name"] = (
                    catalog_names.get(catalog_id) or str(record.get("name") or pid)
                )
                changed = True
            expected_name = self._display_name(
                str(record.get("base_name") or pid), int(record.get("display_index") or 0)
            )
            if record.get("name") != expected_name:
                record["name"] = expected_name
                changed = True
        if int(data.get("version") or 1) < _STORE_VERSION:
            data["version"] = _STORE_VERSION
            changed = True
        return changed

    @staticmethod
    def _display_name(base_name: str, display_index: int) -> str:
        return base_name if display_index <= 0 else f"{base_name}-{display_index}"

    @staticmethod
    def _allocate_display_index(records: Dict[str, Any], catalog_id: str) -> int:
        """新增连接取当前最小空闲展示序号（删除不改剩余编号）。"""
        used = {
            int(record.get("display_index") or 0)
            for record in records.values()
            if str(record.get("catalog_id") or "") == catalog_id
            and isinstance(record.get("display_index"), int)
        }
        index = 0
        while index in used:
            index += 1
        return index

    @staticmethod
    def _new_connection_id(records: Dict[str, Any], catalog_id: str) -> str:
        """分配永不复用的内部连接 id（随机后缀，不以展示序号派生）。"""
        for _ in range(32):
            candidate = f"{catalog_id}-{uuid.uuid4().hex[:8]}"
            if candidate not in records:
                return candidate
        raise RuntimeError("无法分配提供商连接 id")

    @staticmethod
    def _requested_id_of(provider_id: str, record: Dict[str, Any]) -> str:
        """连接的用户可见基础标识（自定义 provider_id / 目录 id）；仅作展示与配置。"""
        value = str(record.get("requested_id") or "").strip()
        if value:
            return value
        return str(record.get("catalog_id") or provider_id or "")

    def _new_custom_connection_id(self, records: Dict[str, Any], requested_id: str) -> str:
        """自定义连接内部 id：``custom-<请求id>-<随机8位>``，永不复用。

        用户填写的 provider_id 只当基础标识；绝不能直接当路由身份（否则删除后同
        ID 重建会让旧 ``{pid}/{model}`` 静默串到新 Key，甚至能彽持已删订阅的
        ``openai-codex/...`` 旧模型 key）。``custom-`` 前缀同时与目录/订阅 id 隔离。
        """
        slug = re.sub(r"[^a-z0-9_-]", "", str(requested_id or "").lower())[:32] or "provider"
        catalog_ids = {e["id"] for e in self.catalog_entries()}
        for _ in range(32):
            candidate = f"custom-{slug}-{uuid.uuid4().hex[:8]}"
            if candidate not in records and candidate not in catalog_ids:
                return candidate
        raise RuntimeError("无法分配自定义提供商连接 id")

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
        """目录 + 连接状态合并（供提供商页展示）。

        输出两类条目（用 ``entry_kind`` 明确区分，而不是用 connected 布尔分类）：
        - ``entry_kind="catalog"``（``connection=False``）：目录服务商行，用于「连接 /
          添加另一个 Key」入口；已有连接（含凭证暂缺的已登记账号）时
          ``has_connections=True``；
        - ``entry_kind="connection"``（``connection=True``）：每一条已登记连接（一个 Key
          一条；ChatGPT 订阅一个账号一条），带独立 id/模型统计/刷新与断开入口；
          凭证暂缺的订阅账号仍输出，``connected=False``、``state="credential_missing"``，
          模型统计归零，供前端展示状态并执行移除。
        """
        data = self._load()
        records: Dict[str, Any] = data["providers"]
        entries = self.catalog_entries()
        catalog_by_id = {e["id"]: e for e in entries}
        # ChatGPT 订阅账号（不属于 providers.json，单独存储）。
        # 2026-10-29 复审修正：**所有已登记账号**都要作为连接行输出（包括凭证暂缺
        # 的 credential_missing），否则登记身份在唯一管理入口里不可见、无法移除；
        # connected/state 保留真实值，由前端展示为“凭证缺失”而不是已连接。
        account_rows: List[Dict[str, Any]] = []
        try:
            from utils.api_client.codex.accounts import get_codex_account_store

            account_rows = list(get_codex_account_store().list_accounts())
        except Exception:
            account_rows = []

        output: List[Dict[str, Any]] = []
        connections_by_catalog: Dict[str, int] = {}
        for pid, record in records.items():
            catalog_id = str(record.get("catalog_id") or "")
            connections_by_catalog[catalog_id] = connections_by_catalog.get(catalog_id, 0) + 1

        # 目录入口行
        for entry in entries:
            item = dict(entry)
            icon = item.get("icon")
            item["icon_url"] = f"/static/icons/providers/{icon}" if icon else None
            item["catalog_id"] = entry["id"]
            item["kind"] = "catalog"
            item["entry_kind"] = "catalog"
            item["connection"] = False
            item["connected"] = False
            if entry.get("auth") == "codex_oauth":
                item["has_connections"] = bool(account_rows)
                item["connection_count"] = len(account_rows)
            else:
                item["has_connections"] = connections_by_catalog.get(entry["id"], 0) > 0
                item["connection_count"] = connections_by_catalog.get(entry["id"], 0)
            # 本地无密钥提供商（auth=none）同一地址再次连接没有意义（无独立 Key），
            # 已有连接时不再提供添加入口；其余目录条目允许继续添加独立 Key。
            item["can_add"] = entry.get("auth") != "none" or not item["has_connections"]
            output.append(item)

        # 每一条 API Key 连接（含自定义提供商 / 目录已下线的孤儿记录）
        for pid, record in records.items():
            if not record.get("enabled", True):
                continue
            catalog_id = str(record.get("catalog_id") or "")
            entry = catalog_by_id.get(catalog_id)
            summary = self._public_summary(pid, record)
            item: Dict[str, Any] = {
                "id": pid,
                "catalog_id": catalog_id or None,
                "requested_id": self._requested_id_of(pid, record),
                "kind": "connection",
                "entry_kind": "connection",
                "connection": True,
                "name": summary.get("name") or pid,
                "base_name": record.get("base_name"),
                "display_index": record.get("display_index") or 0,
                "base_url": summary.get("base_url") or "",
                "icon": entry.get("icon") if entry else None,
                "icon_url": (
                    f"/static/icons/providers/{entry.get('icon')}" if entry and entry.get("icon") else None
                ),
                "icon_mono": bool(entry.get("icon_mono")) if entry else False,
                "auth": "none" if record.get("api_key") == "not-required" else "api_key",
                "key_url": entry.get("key_url") if entry else None,
                "badge": entry.get("badge") if entry else None,
                "local": bool(entry.get("local")) if entry else False,
                "protocol_note": entry.get("protocol_note") if entry else None,
                "custom": entry is None,
                "connected": True,
                "state": "ready",
                "credential_missing": False,
                "models_count": len(summary.get("models") or []),
                "models_fetched_at": summary.get("models_fetched_at"),
                "models_error": summary.get("models_error"),
                "api_key_masked": summary.get("api_key_masked"),
                "has_custom_headers": summary.get("has_custom_headers"),
            }
            if entry:
                from modules import modelsdev_registry

                unsupported = 0
                for model in summary.get("models") or []:
                    model_id = str(model.get("id") or "").strip()
                    if not model_id:
                        continue
                    if (
                        modelsdev_registry.resolve_protocol(entry, model_id)
                        not in modelsdev_registry.SUPPORTED_PROTOCOLS
                    ):
                        unsupported += 1
                if unsupported:
                    item["unsupported_protocol_count"] = unsupported
            output.append(item)

        # 每一条 ChatGPT 订阅账号连接（连接/断开/刷新仍只在提供商页）
        codex_entry = catalog_by_id.get(_CODEX_CATALOG_ID)
        for account in account_rows:
            registered_connected = bool(account.get("connected"))
            state = str(
                account.get("state") or ("ready" if registered_connected else "credential_missing")
            )
            credential_missing = bool(
                account.get("credential_missing") or state == "credential_missing"
            )
            # 凭证暂缺的登记账号保留为连接行，但不能读取/展示旧模型缓存。
            models_summary: Dict[str, Any] = {}
            if registered_connected:
                try:
                    from utils.api_client.codex.models import get_models_manager

                    models_summary = get_models_manager(account["id"]).status_summary()
                except Exception:
                    models_summary = {}
            output.append(
                {
                    "id": account["id"],
                    "catalog_id": _CODEX_CATALOG_ID,
                    "kind": "connection",
                    "entry_kind": "connection",
                    "connection": True,
                    "name": account.get("display_name"),
                    "account_hash": account.get("hash"),
                    "base_url": (codex_entry or {}).get("base_url") or "",
                    "icon": (codex_entry or {}).get("icon"),
                    "icon_url": (
                        f"/static/icons/providers/{codex_entry.get('icon')}"
                        if codex_entry and codex_entry.get("icon")
                        else None
                    ),
                    "icon_mono": bool((codex_entry or {}).get("icon_mono")),
                    "auth": "codex_oauth",
                    "badge": (codex_entry or {}).get("badge"),
                    "protocol_note": None,
                    "custom": False,
                    "connected": registered_connected,
                    "state": state or "ready",
                    "credential_missing": credential_missing,
                    "models_count": int(models_summary.get("model_count") or 0),
                    "models_fetched_at": models_summary.get("fetched_at"),
                    "models_error": None,
                }
            )
        return output

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
            "requested_id": ProviderManager._requested_id_of(provider_id, record),
            "name": record.get("name"),
            "base_name": record.get("base_name"),
            "display_index": record.get("display_index") or 0,
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
        - 目录条目：``{catalog_id, api_key?}``（base_url 等从目录取；同一目录可
          多次连接，产生多个独立 Key 连接）
        - 自定义：``{provider_id, name, base_url, api_key?, headers?}``（``provider_id``
          只是基础标识，内部路由 id 由后端随机分配、永不复用）
        """
        catalog_id = str(payload.get("catalog_id") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()

        if catalog_id:
            entry = next((e for e in self.catalog_entries() if e["id"] == catalog_id), None)
            if not entry:
                return {"success": False, "error": "unknown_catalog_id"}
            if entry.get("auth") == "codex_oauth":
                return {"success": False, "error": "codex_oauth_use_codex_api"}
            base_name = entry["name"]
            base_url = entry["base_url"]
            auth = entry.get("auth") or "api_key"
            requested_id = catalog_id
        else:
            entry = None
            requested_id = str(payload.get("provider_id") or "").strip()
            base_name = str(payload.get("name") or "").strip() or requested_id
            base_url = str(payload.get("base_url") or "").strip()
            auth = "api_key" if api_key else "none"
            if not _PROVIDER_ID_PATTERN.match(requested_id):
                return {"success": False, "error": "invalid_provider_id"}
            if not base_url.startswith(("http://", "https://")):
                return {"success": False, "error": "invalid_base_url"}

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

        # 模型探测是网络请求（最长 _FETCH_TIMEOUT 秒），必须在锁外完成，
        # 否则会阻塞其他连接/断开/刷新；提交阶段再锁内做重复校验 + 分配序号。
        models, error = self._fetch_models(base_url.rstrip("/"), api_key, headers)
        models, error = self._static_models_fallback(entry, models, error)

        with self._lock:
            data = self._load_locked(reload=True)
            records: Dict[str, Any] = data["providers"]
            if catalog_id:
                # 重复活跃 Key 拒绝重复添加（历史删除记录不留墓碑，因此只比对现存连接）
                for existing in records.values():
                    if (
                        str(existing.get("catalog_id") or "") == catalog_id
                        and existing.get("enabled", True)
                        and str(existing.get("api_key") or "") == api_key
                    ):
                        return {"success": False, "error": "duplicate_connection"}
                display_index = self._allocate_display_index(records, catalog_id)
                provider_id = self._new_connection_id(records, catalog_id)
            else:
                # 用户填写的 provider_id 只作基础标识：活跃重复仍拒绝，但内部路由 id
                # 必须是随机且永不复用的，避免删除后用同一 ID 重建时旧模型引用串新 Key。
                for existing_id, existing in records.items():
                    if existing.get("catalog_id"):
                        continue  # 目录命名空间与自定义命名空间互不占用
                    if self._requested_id_of(existing_id, existing) == requested_id:
                        return {"success": False, "error": "provider_id_exists"}
                display_index = 0
                provider_id = self._new_custom_connection_id(records, requested_id)

            now = _now_iso()
            record: Dict[str, Any] = {
                "catalog_id": catalog_id or None,
                "requested_id": requested_id,
                "base_name": base_name,
                "display_index": display_index,
                "name": self._display_name(base_name, display_index),
                "base_url": base_url.rstrip("/"),
                "api_key": api_key,
                "headers": headers,
                "enabled": True,
                "connected_at": now,
                "models": models,
                "models_fetched_at": now,
                "models_error": error,
            }
            records[provider_id] = record
            self._save_locked(data)
        self._refresh_modelsdev_cache()

        result: Dict[str, Any] = {
            "success": True,
            "provider": self._public_summary(provider_id, record),
            "models_count": len(models),
        }
        if error:
            result["models_error"] = error
        return result

    def disconnect(self, provider_id: str) -> bool:
        with self._lock:
            data = self._load_locked(reload=True)
            if provider_id not in data["providers"]:
                return False
            data["providers"].pop(provider_id, None)
            self._save_locked(data)
        return True

    def refresh_models(self, provider_id: str) -> Dict[str, Any]:
        # 锁内只读取快照，网络探测在锁外；提交前再校验记录是否已被删除/替换，
        # 避免把旧连接的模型列表写回新记录。
        with self._lock:
            data = self._load_locked(reload=True)
            record = data["providers"].get(provider_id)
            if not record:
                return {"success": False, "error": "provider_not_found"}
            catalog_id = str(record.get("catalog_id") or "")
            snapshot = (
                record.get("connected_at"),
                record.get("base_url"),
                record.get("api_key"),
            )
            entry = (
                next((e for e in self.catalog_entries() if e["id"] == catalog_id), None)
                if catalog_id
                else None
            )
        models, error = self._fetch_models(
            record["base_url"], record.get("api_key") or "", record.get("headers") or {}
        )
        models, error = self._static_models_fallback(entry, models, error)
        with self._lock:
            data = self._load_locked(reload=True)
            current = data["providers"].get(provider_id)
            if not current:
                return {"success": False, "error": "provider_not_found"}
            if (
                current.get("connected_at"),
                current.get("base_url"),
                current.get("api_key"),
            ) != snapshot:
                # 期间连接被删除并重建为另一个记录：丢弃本次旧探测结果
                return {"success": False, "error": "provider_not_found"}
            if not error or models:
                current["models"] = models
                current["models_fetched_at"] = _now_iso()
            current["models_error"] = error
            self._save_locked(data)
            record = current
        self._refresh_modelsdev_cache()
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
    def _modelsdev_keys() -> set:
        """catalog 中配置了 modelsdev_key 的集合（在线缓存瘦身范围）。"""
        return {e["modelsdev_key"] for e in ProviderManager.catalog_entries() if e.get("modelsdev_key")}

    def _refresh_modelsdev_cache(self) -> None:
        """顺带刷新 models.dev 在线缓存（6h 节流；失败静默，不影响 /models 主流程）。"""
        try:
            from modules import modelsdev_registry

            modelsdev_registry.refresh_cache(self._modelsdev_keys())
        except Exception:
            pass

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

    @staticmethod
    def _static_models_fallback(
        entry: Optional[Dict[str, Any]],
        models: List[Dict[str, Any]],
        error: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """目录静态模型清单回退（catalog 条目的 ``static_models`` 字段）。

        部分编程订阅套餐端点官方不提供 /models（如百炼 Coding Plan 的 Anthropic 端点
        官方 FAQ 明文 404），或列表接口未公开。/models 失败或返回空时回退到目录里
        人工维护的模型清单；/models 可用时永远以在线发现为准。
        """
        if not entry or (models and not error):
            return models, error
        fallback = parse_models_payload(entry.get("static_models"))
        if fallback:
            return fallback, None
        return models, error

    # ------------------------------------------------------------ profile 输出

    def get_profiles(self) -> Dict[str, Dict[str, Any]]:
        """把已连接提供商的已发现模型转成注册表 profile（key = ``{连接id}/{model_id}``）。

        连接 id 是内部唯一身份（不复用），所以每个 Key 连接的模型 key、请求路由、
        加密 reasoning 隔离边界都天然独立；展示名（``provider_name``）才带序号。
        """
        from modules import modelsdev_registry

        catalog_by_id = {e["id"]: e for e in self.catalog_entries()}
        profiles: Dict[str, Dict[str, Any]] = {}
        for pid, record in self._load()["providers"].items():
            if not record.get("enabled", True):
                continue
            base_url = str(record.get("base_url") or "").rstrip("/")
            api_key = str(record.get("api_key") or "")
            headers = record.get("headers") or {}
            if not base_url:
                continue
            catalog_id = str(record.get("catalog_id") or "")
            catalog_entry = catalog_by_id.get(catalog_id)
            display_name = str(record.get("name") or record.get("base_name") or pid)
            for model in record.get("models") or []:
                model_id = str(model.get("id") or "").strip()
                if not model_id:
                    continue
                ctx = model.get("context_length")
                model_display_name = str(model.get("name") or model_id)

                # ── 协议裁决与能力 enrich（models.dev，命中才覆盖）──
                api_protocol = (
                    modelsdev_registry.resolve_protocol(catalog_entry, model_id)
                    if catalog_entry
                    else modelsdev_registry.DEFAULT_PROTOCOL
                )
                meta = (
                    modelsdev_registry.get_model_meta(catalog_entry, model_id)
                    if catalog_entry
                    else None
                ) or {}
                reasoning = meta.get("reasoning")  # True / False / None（未知）
                supports_thinking = True if reasoning is None else bool(reasoning)
                # 推理强度滑块跟随思考能力（用户拍板 2026-09-25）：所有支持思考的模型
                # 一律开放档位，不看协议/提供商；Responses 链路 effort 透传，chat 链路随请求体发
                supports_reasoning_effort = supports_thinking
                mods = meta.get("modalities_input")
                if isinstance(mods, list):
                    has_image = "image" in mods
                    has_video = "video" in mods
                    multimodal = (
                        "image,video"
                        if (has_image and has_video)
                        else "image"
                        if has_image
                        else "video"
                        if has_video
                        else "none"
                    )
                else:
                    # 未知时保持原默认：provider 同步模型默认全能（用户拍板）
                    multimodal = "image,video"
                if meta.get("context"):
                    ctx = meta["context"]
                max_output = meta.get("max_output")

                profile: Dict[str, Any] = {
                    "name": model_display_name,
                    "description": display_name,
                    "model_description": f"你是 {model_display_name}。",
                    "provider_type": "provider",
                    "provider_id": pid,
                    "provider_name": display_name,
                    "provider_base_name": str(record.get("base_name") or display_name),
                    "api_protocol": api_protocol,
                    "is_custom_model": False,
                    "hidden": False,
                    "multimodal": multimodal,
                    "context_window": ctx,
                    "supports_reasoning_effort": supports_reasoning_effort,
                    "fast": {
                        "base_url": base_url,
                        "api_key": api_key,
                        "model_id": model_id,
                        "max_tokens": max_output,
                        "context_window": ctx,
                        "extra_params": {},
                    },
                    # thinking 块与 fast 同端点同模型，思考行为由 run_mode 请求层处理
                    "thinking": {
                        "base_url": base_url,
                        "api_key": api_key,
                        "model_id": model_id,
                        "max_tokens": max_output,
                        "context_window": ctx,
                        "extra_params": {},
                    },
                    "supports_thinking": supports_thinking,
                    "fast_only": not supports_thinking,
                    # 单价统一来自 models.dev；对话花费不看订阅/本地身份
                    "pricing": meta.get("pricing"),
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
