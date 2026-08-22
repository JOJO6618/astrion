#!/usr/bin/env python3
"""运行态数据迁移脚本：源码树 -> 运行态根目录（~/.astrion/astrion/<mode>）。

把历史遗留在源码树内的运行态数据迁移到新的运行态根目录。目标位置直接复用
``config`` 模块的解析结果（``config.DATA_DIR`` / ``USER_SPACE_DIR`` / ... ），
因此与程序运行时使用的是**同一套路径**——包括 ``.env`` 中的
``TERMINAL_SANDBOX_MODE`` 与各路径环境变量。这样可避免脚本自行推断模式与
程序实际读取位置不一致的问题。

策略（与既定决策一致）：

- logs/        跳过不迁（历史日志直接丢弃）。
- data / users / api / sub_agent/tasks
               先打包备份到 ``<运行态根上级>/backup_<时间戳>.tar.gz``，
               校验备份成功后再复制到目标位置；复制采用「不覆盖已存在目标」的
               保守策略，全程复制而非移动，旧目录保留，便于回滚。
- 部署级配置文件（custom_models / host_workspaces / auto_approval / goal_review /
               forbidden_commands / host_sandbox_policy）
               从源码树 ``config/`` 复制到部署配置目录 ``~/.astrion/astrion/<mode>/config/``，
               同样先备份、不覆盖已存在目标。docker_risk_markers、skill_hints 属于
               程序能力，保留源码树，不迁。

本脚本只做「复制 + 备份」，不删除任何源数据，确保完全可回滚。
确认新目录工作正常后，可自行手动删除源码树内的旧目录。

用法：
    python scripts/migrate_runtime_data.py            # 实际执行（按 .env 决定 host/web）
    python scripts/migrate_runtime_data.py --dry-run  # 仅打印将要做什么
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path

# 确保以 `python scripts/migrate_runtime_data.py` 直接运行时也能导入仓库根的 config 包。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config


# 需要迁移的运行态目录：源码树相对路径 -> 目标绝对路径（取自 config，与程序一致）。
# logs 不在其列：历史日志直接丢弃。
# sub_agent/tasks 旧默认在 ./sub_agent/tasks，新默认跟随 DATA_DIR -> <DATA_DIR>/sub_agent_tasks。
def _migrate_targets() -> dict[str, Path]:
    return {
        "data": Path(config.DATA_DIR),
        "users": Path(config.USER_SPACE_DIR),
        "api": Path(config.API_USER_SPACE_DIR).parent,  # API_USER_SPACE_DIR=<root>/api/users，迁整个 api/
        "sub_agent/tasks": Path(config.SUB_AGENT_TASKS_BASE_DIR),
    }


# 部署级配置文件：源码树 config/<name> -> 部署配置目录 ~/.astrion/astrion/<mode>/config/<name>。
# 仅迁移部署者自定义类（含密钥/机器特定/可调策略）；docker_risk_markers、skill_hints
# 属于程序能力，保留源码树，不迁。
_DEPLOY_CONFIG_FILES = (
    "custom_models.json",
    "host_workspaces.json",
    "auto_approval.json",
    "goal_review.json",
    "forbidden_commands.json",
    "host_sandbox_policy.json",
)


def _migrate_config_files() -> list[tuple[Path, Path]]:
    """返回 (源码树配置文件, 部署目录目标) 列表，仅含实际存在的源文件。"""
    deploy_dir = Path(config.DEPLOY_CONFIG_DIR)
    pairs: list[tuple[Path, Path]] = []
    for name in _DEPLOY_CONFIG_FILES:
        src = REPO_ROOT / "config" / name
        if src.is_file():
            pairs.append((src, deploy_dir / name))
    return pairs


def _human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{num}B"


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _backup(sources: list[Path], backup_root: Path, *, dry_run: bool) -> Path | None:
    if not sources:
        return None
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = backup_root / f"backup_{stamp}.tar.gz"
    print(f"[backup] 打包 {len(sources)} 个目录 -> {archive}")
    if dry_run:
        return archive
    with tarfile.open(archive, "w:gz") as tar:
        for src in sources:
            # 用相对仓库根的路径作为归档内路径，保留 sub_agent/tasks 这类多级结构，避免重名碰撞。
            try:
                arcname = str(src.relative_to(REPO_ROOT))
            except ValueError:
                arcname = src.name
            tar.add(src, arcname=arcname)
    # 校验：备份文件存在且非空，且能成功列出成员
    if not archive.exists() or archive.stat().st_size == 0:
        raise RuntimeError(f"备份文件异常（不存在或为空）: {archive}")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getnames()
    if not members:
        raise RuntimeError(f"备份内容为空，已中止以保护数据: {archive}")
    print(f"[backup] 备份完成并校验通过：{_human_size(archive.stat().st_size)}，成员 {len(members)} 项")
    return archive


def _copy_tree(src: Path, dst: Path, *, dry_run: bool) -> None:
    if dst.exists():
        print(f"[skip]   目标已存在，跳过以避免覆盖：{dst}")
        return
    print(f"[copy]   {src}  ->  {dst}  ({_human_size(_dir_size(src))})")
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def _copy_file(src: Path, dst: Path, *, dry_run: bool) -> None:
    if dst.exists():
        print(f"[skip]   目标已存在，跳过以避免覆盖：{dst}")
        return
    print(f"[copy]   {src}  ->  {dst}  ({_human_size(src.stat().st_size)})")
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移运行态数据到运行态根目录（~/.astrion/astrion/<mode>）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将要执行的操作，不实际写入")
    args = parser.parse_args()

    targets = _migrate_targets()
    runtime_root = Path(config.RUNTIME_ROOT)
    print(f"运行态根目录（取自 config，与程序一致）：{runtime_root}")
    print(f"源码树根目录：{REPO_ROOT}")
    print("注意：logs/ 不迁移（历史日志按既定决策直接丢弃）")
    print("-" * 60)

    # 收集存在且非空的源目录
    present: list[tuple[Path, Path]] = []
    for src_rel, dst in targets.items():
        src = REPO_ROOT / src_rel
        if src.is_dir() and any(src.iterdir()):
            present.append((src, dst))
        else:
            print(f"[absent] 源目录不存在或为空，跳过：{src}")

    # 收集需迁移的部署级配置文件（源码树 config/*.json -> 部署目录）
    config_files = _migrate_config_files()
    for src, dst in config_files:
        print(f"[config] 待迁移配置：{src.name}  ->  {dst}")
    if not config_files:
        print("[config] 无部署级配置文件需迁移。")

    if not present and not config_files:
        print("没有需要迁移的运行态数据。")
        return 0

    # 1) 备份（备份到运行态根的上级目录，与运行态根同级）
    backup_root = runtime_root.parent
    backup_sources = [s for s, _ in present] + [s for s, _ in config_files]
    try:
        _backup(backup_sources, backup_root, dry_run=args.dry_run)
    except Exception as exc:
        print(f"[error]  备份失败，已中止迁移（未改动任何数据）：{exc}", file=sys.stderr)
        return 1

    # 2) 复制（不覆盖已有目标，源保留）
    for src, dst in present:
        try:
            _copy_tree(src, dst, dry_run=args.dry_run)
        except Exception as exc:
            print(f"[error]  复制失败：{src} -> {dst}：{exc}", file=sys.stderr)
            print("        已迁移的部分保留，源数据未删除，可重跑或手动处理。", file=sys.stderr)
            return 1
    for src, dst in config_files:
        try:
            _copy_file(src, dst, dry_run=args.dry_run)
        except Exception as exc:
            print(f"[error]  复制配置失败：{src} -> {dst}：{exc}", file=sys.stderr)
            print("        已迁移的部分保留，源文件未删除，可重跑或手动处理。", file=sys.stderr)
            return 1

    print("-" * 60)
    if args.dry_run:
        print("dry-run 完成：以上为将要执行的操作，未实际写入。")
    else:
        print("迁移完成。源码树内的旧目录已保留，确认新目录工作正常后可手动删除。")
        print(f"备份位置：{backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
