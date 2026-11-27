#!/usr/bin/env python3
"""生成/更新 models.dev 瘦身快照（config/modelsdev_snapshot.json）。

用法：
    python3 scripts/update_modelsdev_snapshot.py            # 在线拉取 api.json
    python3 scripts/update_modelsdev_snapshot.py /path/to/api.json  # 从本地文件

快照只保留 catalog 中映射了 modelsdev_key 的 provider，瘦身逻辑复用
``modules/modelsdev_registry.py::slim_full_dump``（与在线缓存同构）。
"""

from __future__ import annotations

import json
import sys
import math
from typing import Any, Dict, Optional
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SNAPSHOT_PATH = REPO_ROOT / "config" / "modelsdev_snapshot.json"
CATALOG_PATH = REPO_ROOT / "config" / "providers_catalog.json"
API_URL = "https://models.dev/api.json"


def positive_token_limit(*values: Any) -> Optional[int]:
    """Return the first valid positive token count from the supplied fields."""
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            tokens = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 < tokens <= (1 << 63) - 1 and (isinstance(value, str) or value == tokens):
            return tokens
    return None


def clean_rates(cost: Any) -> dict[str, float]:
    if not isinstance(cost, dict):
        return {}
    rates = {}
    for key in ('input', 'cache_read', 'output'):
        value = cost.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            rates[key] = float(value)
    return rates


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
            limit = m.get("limit")
            if isinstance(limit, dict):
                for field, cached_field in (
                    ("context", "context"), ("input", "max_input"), ("output", "max_output")
                ):
                    if (value := positive_token_limit(limit.get(field))) is not None:
                        entry[cached_field] = value
            if isinstance(m.get("cost"), dict):
                entry["cost"] = clean_rates(m["cost"])
            models[model_id] = entry
        providers[key] = {"npm": pdata.get("npm"), "models": models}
    from datetime import datetime, timezone

    return {
        "version": 3,
        "source": _API_URL,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "providers": providers,
    }


def main() -> None:

    if len(sys.argv) > 1:
        full = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(API_URL, timeout=60) as resp:  # noqa: S310（官方数据源）
            full = json.loads(resp.read())

    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    md_keys = {e["modelsdev_key"] for e in raw.get("providers", []) if e.get("modelsdev_key")}

    snapshot = slim_full_dump(full, md_keys)
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    total_models = sum(len(p["models"]) for p in snapshot["providers"].values())
    size_kb = SNAPSHOT_PATH.stat().st_size // 1024
    print(f"快照已写入 {SNAPSHOT_PATH}：{len(snapshot['providers'])} providers / {total_models} models / {size_kb}KB")
    missing = md_keys - set(snapshot["providers"].keys())
    if missing:
        print("警告：以下 modelsdev_key 在数据源未找到条目：", ", ".join(sorted(missing)))


if __name__ == "__main__":
    main()
