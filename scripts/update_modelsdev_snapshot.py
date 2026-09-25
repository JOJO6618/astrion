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
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SNAPSHOT_PATH = REPO_ROOT / "config" / "modelsdev_snapshot.json"
CATALOG_PATH = REPO_ROOT / "config" / "providers_catalog.json"
API_URL = "https://models.dev/api.json"


def main() -> None:
    from modules.modelsdev_registry import slim_full_dump

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
