#!/usr/bin/env python3
"""把误存到常规对话目录的多智能体会话迁移到 mutiagents/conversations/。

用法:
    python3 scripts/migrate_multi_agent_conversations.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# 加载项目配置以确定数据目录
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
from config import DATA_DIR, IS_HOST_MODE
from modules.multi_agent.role_store import DEFAULT_MUTIAGENTS_DIR


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{int(time.time() * 1000)}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.move(str(tmp), str(path))


def migrate(dry_run: bool = False) -> dict:
    regular_dir = Path(DATA_DIR) / "conversations" / "workspace"
    ma_dir = Path(DEFAULT_MUTIAGENTS_DIR) / "conversations" / "workspace"

    regular_index_path = regular_dir / "index.json"
    ma_index_path = ma_dir / "index.json"

    regular_index = load_json(regular_index_path) if regular_index_path.exists() else {}
    ma_index = load_json(ma_index_path) if ma_index_path.exists() else {}

    moved = []
    skipped = []
    errors = []

    for f in sorted(regular_dir.glob("conv_*.json")):
        if f.name == "index.json":
            continue
        try:
            data = load_json(f)
        except Exception as exc:
            errors.append({"file": str(f), "error": f"读取失败: {exc}"})
            continue

        meta = data.get("metadata", {}) or {}
        if not meta.get("multi_agent_mode"):
            skipped.append(f.name)
            continue

        conv_id = data.get("id") or f.stem
        target_file = ma_dir / f.name

        # 目标已存在时跳过，避免覆盖
        if target_file.exists():
            errors.append({"file": str(f), "error": f"目标已存在: {target_file}"})
            continue

        if dry_run:
            moved.append({"id": conv_id, "file": f.name, "target": str(target_file)})
            continue

        try:
            # 移动文件
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(target_file))

            # 更新索引
            if conv_id in regular_index:
                ma_index[conv_id] = regular_index.pop(conv_id)
            else:
                # 从文件重建索引条目
                ma_index[conv_id] = {
                    "title": data.get("title") or "未命名对话",
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "project_path": meta.get("project_path"),
                    "project_relative_path": meta.get("project_relative_path"),
                    "thinking_mode": meta.get("thinking_mode", False),
                    "run_mode": meta.get("run_mode") or ("thinking" if meta.get("thinking_mode") else "fast"),
                    "model_key": meta.get("model_key"),
                    "has_images": meta.get("has_images", False),
                    "has_videos": meta.get("has_videos", False),
                    "total_messages": meta.get("total_messages", 0),
                    "total_tools": meta.get("total_tools", 0),
                    "status": meta.get("status", "active"),
                    "multi_agent_mode": True,
                }

            moved.append({"id": conv_id, "file": f.name, "target": str(target_file)})
        except Exception as exc:
            errors.append({"file": str(f), "error": f"迁移失败: {exc}"})

    if not dry_run:
        # 保存索引
        save_json(regular_index_path, regular_index)
        save_json(ma_index_path, ma_index)

    return {
        "dry_run": dry_run,
        "regular_dir": str(regular_dir),
        "ma_dir": str(ma_dir),
        "moved": moved,
        "skipped_count": len(skipped),
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="迁移多智能体会话到 mutiagents 目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不实际移动")
    args = parser.parse_args()

    result = migrate(dry_run=args.dry_run)

    print(f"常规目录: {result['regular_dir']}")
    print(f"目标目录: {result['ma_dir']}")
    print(f"模式: {'预览' if args.dry_run else '实际迁移'}")
    print(f"将迁移: {len(result['moved'])} 个对话")
    print(f"跳过（非多智能体）: {result['skipped_count']} 个")
    print(f"错误: {len(result['errors'])}")

    for item in result["moved"]:
        print(f"  → {item['id']} -> {item.get('target', item['file'])}")

    for err in result["errors"]:
        print(f"  ⚠ {err['file']}: {err['error']}")


if __name__ == "__main__":
    main()
