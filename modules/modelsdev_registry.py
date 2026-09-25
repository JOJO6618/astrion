"""models.dev 数据注册表：协议裁决 + 模型能力 enrich 数据源（2026-09-25）。

设计文档：``docs/provider_protocol_generalization.md``。

数据源两级（加载时在线缓存优先——它更新鲜；仓库快照兜底）：
- 在线缓存 ``<DATA_DIR>/modelsdev_cache.json``：刷新模型时顺带更新（``refresh_cache``）
- 仓库快照 ``config/modelsdev_snapshot.json``：随版本发布（``scripts/update_modelsdev_snapshot.py`` 生成）

两者同为瘦身结构::

    {"version": 1, "source": ..., "fetched_at": ...,
     "providers": {"<modelsdev_key>": {"npm": ..., "models": {
        "<model_id>": {"npm"?, "reasoning"?, "modalities_input"?, "context"?, "max_output"?}}}}}

协议裁决仅对 catalog 里显式标注 ``protocol_source="models.dev"`` 的多协议网关启用
（单协议 provider 一律 chat_completions——openai 官方在 models.dev 标 @ai-sdk/openai、
xai/openrouter 用自家包名，无脑按 npm 裁决会误判，2026-09-25 实测确认）。
能力 enrich 不受此限：所有配置了 ``modelsdev_key`` 的条目都可查询。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config.paths import DATA_DIR

_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "config" / "modelsdev_snapshot.json"
_CACHE_FILENAME = "modelsdev_cache.json"
_API_URL = "https://models.dev/api.json"
_FETCH_TIMEOUT = 60
# 在线缓存新鲜期：connect/refresh_models 顺带刷新时，新鲜期内跳过（避免每次拉 4.9MB 全量）
_CACHE_TTL_SECONDS = 6 * 3600

# AI SDK 包名 → 内部协议标识（api_protocol）
NPM_TO_PROTOCOL: Dict[str, str] = {
    "@ai-sdk/openai": "responses",
    "@ai-sdk/openai-compatible": "chat_completions",
    "@ai-sdk/anthropic": "anthropic_messages",
    "@ai-sdk/google": "google",
}

DEFAULT_PROTOCOL = "chat_completions"
# 当前请求层实际支持调用的协议（其余协议模型正常注册但调用报错，见泛化文档决策 7）
SUPPORTED_PROTOCOLS = {"chat_completions", "responses"}

_lock = threading.Lock()
_memory: Optional[Dict[str, Any]] = None
# 正在使用的数据源文件签名（path, mtime_ns, size）；refresh_cache 写盘后由签名变化触发重读
_source_sig: Optional[Tuple[str, int, int]] = None


# ------------------------------------------------------------ 数据加载

def _store_path() -> Path:
    return Path(DATA_DIR) / _CACHE_FILENAME


def _sig_of(path: Path) -> Optional[Tuple[str, int, int]]:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _load_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("providers"), dict):
            return raw
    except Exception:
        pass
    return None


def _current_data() -> Dict[str, Any]:
    """缓存优先、快照兜底；带文件签名检测（外部刷新缓存后自动重读）。"""
    global _memory, _source_sig
    cache_path = _store_path()
    active = cache_path if cache_path.exists() else _SNAPSHOT_PATH
    sig = _sig_of(active)
    with _lock:
        if _memory is not None and sig is not None and sig == _source_sig:
            return _memory
        data = _load_file(active) or _load_file(_SNAPSHOT_PATH) or {"providers": {}}
        _memory = data
        _source_sig = sig
        return data


def get_provider_data(modelsdev_key: str) -> Optional[Dict[str, Any]]:
    """取某 provider 的瘦身数据（{"npm":..., "models": {...}}）；无则 None。"""
    if not modelsdev_key:
        return None
    data = _current_data()
    pdata = data.get("providers", {}).get(modelsdev_key)
    return pdata if isinstance(pdata, dict) else None


# ------------------------------------------------------------ 协议裁决

def resolve_protocol(catalog_entry: Dict[str, Any], model_id: str) -> str:
    """裁决某模型在当前 provider 下应走的协议。

    优先级：catalog 静态 protocol_map（最高，人工裁决）
        → models.dev npm（仅 protocol_source="models.dev" 的条目）
        → 默认 chat_completions。
    """
    protocol_map = catalog_entry.get("protocol_map")
    if isinstance(protocol_map, dict):
        hit = protocol_map.get(model_id)
        if isinstance(hit, str) and hit:
            return hit
    if catalog_entry.get("protocol_source") == "models.dev":
        pdata = get_provider_data(str(catalog_entry.get("modelsdev_key") or ""))
        if pdata:
            models = pdata.get("models") or {}
            model = models.get(model_id)
            npm = None
            if isinstance(model, dict):
                npm = model.get("npm")
            if not npm:
                npm = pdata.get("npm")
            protocol = NPM_TO_PROTOCOL.get(str(npm or ""))
            if protocol:
                return protocol
    return DEFAULT_PROTOCOL


# ------------------------------------------------------------ 能力 enrich

def get_model_meta(catalog_entry: Dict[str, Any], model_id: str) -> Optional[Dict[str, Any]]:
    """取模型能力元数据：{reasoning, modalities_input, context, max_output}；未收录返回 None。"""
    pdata = get_provider_data(str(catalog_entry.get("modelsdev_key") or ""))
    if not pdata:
        return None
    model = (pdata.get("models") or {}).get(model_id)
    if not isinstance(model, dict):
        return None
    meta: Dict[str, Any] = {}
    if model.get("reasoning") is not None:
        meta["reasoning"] = bool(model.get("reasoning"))
    if isinstance(model.get("modalities_input"), list):
        meta["modalities_input"] = model["modalities_input"]
    if model.get("context"):
        meta["context"] = model["context"]
    if model.get("max_output"):
        meta["max_output"] = model["max_output"]
    return meta or None


# ------------------------------------------------------------ 在线缓存刷新

def slim_full_dump(full: Dict[str, Any], md_keys: set[str]) -> Dict[str, Any]:
    """全量 api.json → 瘦身结构（只保留指定 provider 与所需字段）。脚本与在线缓存共用。"""
    providers: Dict[str, Any] = {}
    for key in sorted(md_keys):
        pdata = full.get(key)
        if not isinstance(pdata, dict):
            continue
        models: Dict[str, Any] = {}
        for model_id, m in (pdata.get("models") or {}).items():
            if not isinstance(m, dict):
                continue
            entry: Dict[str, Any] = {}
            npm = (m.get("provider") or {}).get("npm")
            if npm:
                entry["npm"] = npm
            if m.get("reasoning") is not None:
                entry["reasoning"] = bool(m.get("reasoning"))
            modalities = (m.get("modalities") or {}).get("input")
            if isinstance(modalities, list):
                entry["modalities_input"] = modalities
            limit = m.get("limit") or {}
            if isinstance(limit, dict):
                if limit.get("context"):
                    entry["context"] = limit["context"]
                if limit.get("output"):
                    entry["max_output"] = limit["output"]
            models[model_id] = entry
        providers[key] = {"npm": pdata.get("npm"), "models": models}
    from datetime import datetime, timezone

    return {
        "version": 1,
        "source": _API_URL,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "providers": providers,
    }


def refresh_cache(
    md_keys: set[str], timeout: int = _FETCH_TIMEOUT, force: bool = False
) -> Tuple[bool, Optional[str]]:
    """在线拉取 models.dev 全量并瘦身写缓存。返回 (成功与否, 错误消息)。

    非 force 且缓存仍在新鲜期（6h）时直接跳过（返回 (True, None)）。
    失败不影响既有数据源（缓存/快照照常可用）。
    """
    import httpx

    global _memory, _source_sig
    if not force:
        cached = _load_file(_store_path())
        fetched_at = str((cached or {}).get("fetched_at") or "")
        if fetched_at:
            try:
                from datetime import datetime, timezone

                ts = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                if age < _CACHE_TTL_SECONDS:
                    return True, None
            except Exception:
                pass
    try:
        resp = httpx.get(_API_URL, timeout=timeout)
        if resp.status_code != 200:
            return False, f"http_{resp.status_code}"
        full = resp.json()
        if not isinstance(full, dict):
            return False, "bad_payload"
    except Exception as exc:
        return False, f"request_error: {exc}"
    slim = slim_full_dump(full, md_keys)
    try:
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        return False, f"write_error: {exc}"
    with _lock:
        _memory = slim
        _source_sig = _sig_of(path)
    return True, None
