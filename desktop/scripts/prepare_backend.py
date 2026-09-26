#!/usr/bin/env python3
"""打包前准备：把后端源码 staging 到 desktop/src-tauri/runtime/backend/（跨平台）。

与原 macOS 版 prepare-backend.sh 逻辑逐条对应（该脚本现为调用本脚本的薄壳，
排除清单以本文件为单一来源）：
- 目录结构与源码树一致（Flask 静态目录、import 路径依赖相对布局）
- 排除一切私有/部署级配置（.env、custom_models.json 等）——全新用户
  必须走「部署目录无配置 → .example 种子/空注册表」的干净初始态
- 用内嵌 Python 预编译 pyc（安装目录只读，运行时 PYTHONDONTWRITEBYTECODE=1 读现成）

仅用标准库，任意 Python 3.8+ 可跑（Windows/macOS 通用）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_DIR = REPO_ROOT / "desktop" / "src-tauri" / "runtime"
BACKEND_DIR = RUNTIME_DIR / "backend"

# 内嵌解释器路径（python-build-standalone 布局，与 backend.rs 的解析一致）
if sys.platform == "win32":
    PYBIN = RUNTIME_DIR / "python" / "python.exe"
else:
    PYBIN = RUNTIME_DIR / "python" / "bin" / "python3.12"

# 1) 源码与程序级资源（保持相对结构）
ITEMS = ("server", "core", "modules", "utils", "config", "prompts", "agentskills", "multi_agent_roles")

# 3) 私有/部署级配置剔除（打包卫生，防泄露 + 保证全新初始态）
PRIVATE_CONFIGS = (
    ".env",
    "config/custom_models.json",
    "config/host_workspaces.json",
    "config/auto_approval.json",
    "config/goal_review.json",
    "config/forbidden_commands.json",
    "config/host_sandbox_policy.json",
    # custom_models.json.example 是开发者写法示例（含 Kimi-K3/deepseek-chat 示例条目），
    # 随包分发会让全新用户经回退链（部署目录→源码树.json→.example）预装示例模型。
    # 删除后回退链落空，模型注册表为干净空态（加载器对缺失文件返回 {}）。
    # host_sandbox_policy.json.example 是有益安全种子（禁读 ~/.ssh 等），保留。
    "config/custom_models.json.example",
)

SOURCE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")

# 预编译 pyc 的顶层包（对齐原 bash 版范围）
COMPILE_TARGETS = ("server", "core", "modules", "utils", "config")


def copy_static_payload() -> None:
    """前端构建产物 + static 根散文件（排除 dist/src）+ 提供商图标。"""
    static_src = REPO_ROOT / "static"
    static_dst = BACKEND_DIR / "static"
    static_dst.mkdir(exist_ok=True)

    # Flask 同源 serve 的前端构建产物
    shutil.copytree(static_src / "dist", static_dst / "dist")

    # favicon 等 static 根散文件（对应 bash: rsync --exclude dist/src/icons/providers）
    for child in static_src.iterdir():
        if child.name in ("dist", "src"):
            continue
        dest = static_dst / child.name
        if child.is_dir():
            if child.name == "icons":
                # icons 目录本体要复制（codex.svg 等根散件），仅排除 providers 子目录
                shutil.copytree(
                    child, dest,
                    ignore=shutil.ignore_patterns("providers", "__pycache__", "*.pyc", ".DS_Store"),
                )
            else:
                shutil.copytree(child, dest, ignore=SOURCE_IGNORE)
        else:
            shutil.copy2(child, dest)

    # 提供商图标（设置页需要）
    shutil.copytree(static_src / "icons" / "providers", static_dst / "icons" / "providers")


def dir_size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


def main() -> int:
    if not PYBIN.is_file():
        print(f"[prepare-backend] 内嵌 Python 不存在: {PYBIN}", file=sys.stderr)
        print("[prepare-backend] 请先完成 python-build-standalone 运行时就位", file=sys.stderr)
        return 1

    print(f"[prepare-backend] 清理旧 staging: {BACKEND_DIR}")
    shutil.rmtree(BACKEND_DIR, ignore_errors=True)
    BACKEND_DIR.mkdir(parents=True)

    # 1) 源码与程序级资源
    for item in ITEMS:
        shutil.copytree(REPO_ROOT / item, BACKEND_DIR / item, ignore=SOURCE_IGNORE)

    # 1.5) 运行时脚本（仅复制运行依赖的文件，不带 dev 工具）
    # - setup-wsl-sandbox.ps1：沙箱安装向导唯一依赖（modules/sandbox_setup_manager.py
    #   的 _SETUP_SCRIPT 在打包形态下解析为 runtime/backend/scripts/...，遗漏会致
    #   桌面版沙箱安装报「安装脚本不存在」；copy2 保留 UTF-8 BOM，PS 5.1 依赖它解析中文）
    (BACKEND_DIR / "scripts").mkdir(exist_ok=True)
    shutil.copy2(REPO_ROOT / "scripts" / "setup-wsl-sandbox.ps1",
                 BACKEND_DIR / "scripts" / "setup-wsl-sandbox.ps1")

    # 2) 前端构建产物（Flask 同源 serve）
    copy_static_payload()

    # 3) 私有/部署级配置剔除
    for rel in PRIVATE_CONFIGS:
        (BACKEND_DIR / rel).unlink(missing_ok=True)

    # 4) 预编译 pyc（用内嵌解释器，magic 版本一致；失败不阻断，对齐 bash `|| true`）
    print("[prepare-backend] 预编译 pyc...")
    subprocess.run(
        [str(PYBIN), "-m", "compileall", "-q", "-f"]
        + [str(BACKEND_DIR / t) for t in COMPILE_TARGETS],
        check=False,
    )

    print(f"[prepare-backend] staging 完成: {dir_size_mb(BACKEND_DIR):.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
