#!/usr/bin/env python3
"""离线合并旧多智能体主对话。默认预览；停服务后显式 --apply。

目录参数均指 conversations 层，脚本保留其下工作区相对路径。
不加载应用配置，不读取或迁移旧子任务，不覆盖已有主对话。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

REMOVED_METADATA = {
    "multi_agent_mode", "frozen_available_agents_prompt",
    "frozen_main_system_prompt_multi_agent",
}
INDEX_FIELDS = (
    "project_path", "project_relative_path", "thinking_mode", "run_mode",
    "model_key", "has_images", "has_videos", "total_messages", "total_tools", "status",
)


def encode(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def checked_root(value: Path, *, required: bool = False) -> Path:
    path = Path(os.path.abspath(value))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"目录不能经过符号链接: {component}")
    if required and not path.is_dir():
        raise ValueError(f"源目录不存在: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"不是目录: {path}")
    if path.exists():
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                candidate = Path(directory) / name
                if candidate.is_symlink():
                    raise ValueError(f"目录内存在符号链接: {candidate}")
    return path


def load_conversation(path: Path) -> tuple[bytes, dict]:
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("id") != path.stem:
        raise ValueError(f"对话 ID 与文件名不一致: {path}")
    if not isinstance(data.get("messages"), list) or not isinstance(data.get("metadata"), dict):
        raise ValueError(f"无效对话结构: {path}")
    return raw, data


def index_entry(data: dict) -> dict:
    metadata = data["metadata"]
    return {
        "title": data.get("title") or "未命名对话",
        "created_at": data.get("created_at"), "updated_at": data.get("updated_at"),
        **{key: metadata.get(key) for key in INDEX_FIELDS},
    }


def migrate(source_dir: Path, target_dir: Path, *, apply: bool = False,
            backup_dir: Path | None = None) -> dict:
    source = checked_root(source_dir, required=True)
    target = checked_root(target_dir)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("源与目标不能相同或互相包含")
    files = sorted(source.rglob("conv_*.json"))
    existing_ids = {p.stem for p in target.rglob("conv_*.json")} if target.exists() else set()
    records, ids, indexes = [], set(), {}
    for path in files:
        raw, data = load_conversation(path)
        if path.stem in ids or path.stem in existing_ids:
            raise ValueError(f"对话 ID 冲突，整批迁移取消: {path.stem}")
        ids.add(path.stem)
        destination = target / path.relative_to(source)
        if destination.exists():
            raise ValueError(f"目标已存在: {destination}")
        clean = dict(data)
        clean["metadata"] = {k: v for k, v in data["metadata"].items() if k not in REMOVED_METADATA}
        records.append((path, destination, raw, clean))
        for index in (path.parent / "index.json", destination.parent / "index.json"):
            if index not in indexes:
                original = index.read_bytes() if index.exists() else None
                decoded = json.loads(original) if original is not None else {}
                if not isinstance(decoded, dict):
                    raise ValueError(f"无效索引: {index}")
                indexes[index] = (original, decoded)
    report = {"apply": apply, "source": str(source), "target": str(target),
              "conversation_count": len(records), "workspace_count": len({p.parent for p in files})}
    if not apply or not records:
        return report
    backup = checked_root(backup_dir or source.parent / (
        "conversation-merge-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")))
    if backup.exists() or any(root == backup or root in backup.parents for root in (source, target)):
        raise ValueError("备份目录必须全新且位于源、目标目录树之外")
    backup.mkdir(parents=True)
    manifest = {"source": str(source), "target": str(target), "files": [], "indexes": []}
    for path, destination, raw, _ in records:
        saved = backup / "conversations" / path.relative_to(source)
        atomic_write(saved, raw)
        manifest["files"].append({"source": str(path), "target": str(destination),
                                  "backup": str(saved), "sha256": hashlib.sha256(raw).hexdigest()})
    for number, (index, (raw, _)) in enumerate(indexes.items()):
        saved = backup / "indexes" / f"{number}.json"
        if raw is not None:
            atomic_write(saved, raw)
        manifest["indexes"].append({"path": str(index), "backup": str(saved) if raw is not None else None})
    atomic_write(backup / "manifest.json", encode(manifest))
    created, deleted, modified_indexes = [], [], []
    try:
        for path, _, raw, _ in records:
            if path.read_bytes() != raw:
                raise RuntimeError("源数据在备份期间改变，请先停止服务")
        for index, (raw, _) in indexes.items():
            if (index.read_bytes() if index.exists() else None) != raw:
                raise RuntimeError("索引在备份期间改变，请先停止服务")
        for _, destination, _, data in records:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # 独占创建，绝不覆盖预检之后并发出现的目标文件。
            with destination.open("xb") as stream:
                created.append(destination)
                stream.write(encode(data))
                stream.flush()
                os.fsync(stream.fileno())
            if destination.read_bytes() != encode(data):
                raise RuntimeError(f"目标写入校验失败: {destination}")
        for path, destination, raw, data in records:
            if path.read_bytes() != raw:
                raise RuntimeError("源数据在迁移期间改变，请先停止服务")
            indexes[path.parent / "index.json"][1].pop(path.stem, None)
            indexes[destination.parent / "index.json"][1][path.stem] = index_entry(data)
            path.unlink()
            deleted.append((path, raw))
        for index, (original, updated) in indexes.items():
            if (index.read_bytes() if index.exists() else None) != original:
                raise RuntimeError("索引在迁移期间改变，请先停止服务")
            modified_indexes.append(index)
            atomic_write(index, encode(updated))
    except BaseException:
        for path, raw in deleted:
            atomic_write(path, raw)
        for index in modified_indexes:
            raw = indexes[index][0]
            if raw is None:
                index.unlink(missing_ok=True)
            else:
                atomic_write(index, raw)
        for path in created:
            path.unlink(missing_ok=True)
        raise
    report["backup"] = str(backup)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="停服务后应用；不传则只读预览")
    args = parser.parse_args()
    try:
        result = migrate(args.source_dir, args.target_dir, apply=args.apply, backup_dir=args.backup_dir)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"迁移未完成: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
