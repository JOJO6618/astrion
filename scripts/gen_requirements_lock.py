#!/usr/bin/env python3
"""根据 requirements.txt 生成 requirements.lock.txt（精确版本）。

读取 requirements.txt 列出的直接依赖名，从当前已安装环境查出精确版本，
写出 requirements.lock.txt。在「已验证可用」的环境中运行，确保锁定的是
一组实测能跑起来的版本。

用法：
    python -m scripts.gen_requirements_lock
    python scripts/gen_requirements_lock.py
"""

from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQ_PATH = REPO_ROOT / "requirements.txt"
LOCK_PATH = REPO_ROOT / "requirements.lock.txt"

_LOCK_HEADER = """\
# 锁定版本依赖（构建 / release 打包用）。由 scripts/gen_requirements_lock.py 生成。
#
# 与 requirements.txt 的关系：
# - requirements.txt   人读、宽松，列出运行时直接依赖（不锁版本）
# - requirements.lock.txt（本文件）  精确版本，供便携 release 打包与可复现安装
#
# 安装：pip install -r requirements.lock.txt
# 更新：在已验证可用的环境执行 python -m scripts.gen_requirements_lock
#
# 注：这里只锁项目运行时直接依赖；其传递依赖由 pip 在安装时解析。
"""


def _parse_requirements(text: str) -> list[tuple[str, list[str]]]:
    """从 requirements.txt 提取 (包名, extras 列表)。

    忽略注释/空行；去掉版本约束；保留 extras。
    例： httpx[http2]>=0.28 -> ("httpx", ["http2"]) ； flask -> ("flask", [])
    """
    items: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 先剥掉版本约束/环境标记： name[extra]>=1 ; marker
        head = re.split(r"[<>=!~ ;]", line, 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)(?:\[([^\]]*)\])?$", head)
        if not m:
            continue
        name = m.group(1)
        extras = [e.strip() for e in (m.group(2) or "").split(",") if e.strip()]
        items.append((name, extras))
    return items


def _extra_dependency_names(pkg: str, extras: list[str]) -> list[str]:
    """解析某包在指定 extras 下引入的依赖包名（用于一并锁定其传递依赖）。

    例： httpx + ["http2"] -> ["h2"]。仅返回包名，版本由调用方查实际安装值。
    无法解析时返回空列表（静默降级）。
    """
    if not extras:
        return []
    try:
        requires = md.requires(pkg) or []
    except md.PackageNotFoundError:
        return []
    wanted = {e.lower() for e in extras}
    found: list[str] = []
    for req in requires:
        # 仅取声明了 extra == '<name>' 且匹配我们启用的 extra 的依赖
        if "extra ==" not in req:
            continue
        if not any(f"extra == '{e}'" in req or f'extra == "{e}"' in req for e in wanted):
            continue
        dep = re.split(r"[<>=!~ ;]", req.strip(), 1)[0].strip()
        dep = re.sub(r"\[[^\]]*\]", "", dep)  # 去掉 extra 自身可能带的 []
        if dep and dep not in found:
            found.append(dep)
    return found


def main() -> int:
    if not REQ_PATH.exists():
        print(f"找不到 {REQ_PATH}", file=sys.stderr)
        return 1
    items = _parse_requirements(REQ_PATH.read_text(encoding="utf-8"))
    if not items:
        print("requirements.txt 中没有可锁定的依赖。", file=sys.stderr)
        return 1

    lines: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()  # 去重（extra 依赖可能与直接依赖重复）

    for name, extras in items:
        spec = f"{name}[{','.join(extras)}]" if extras else name
        try:
            version = md.version(name)
            lines.append(f"{spec}=={version}")
        except md.PackageNotFoundError:
            missing.append(name)
            lines.append(f"# {spec}  (未安装，无法锁定版本)")
        seen.add(name.lower())

        # 一并锁定该包在启用 extras 下引入的依赖（如 httpx[http2] -> h2），
        # 否则干净环境装 lock 时不会拉到这些可选依赖。
        for dep in _extra_dependency_names(name, extras):
            if dep.lower() in seen:
                continue
            seen.add(dep.lower())
            try:
                dep_ver = md.version(dep)
                lines.append(f"{dep}=={dep_ver}  # 由 {name}[{','.join(extras)}] 引入")
            except md.PackageNotFoundError:
                missing.append(dep)
                lines.append(f"# {dep}  (由 {name} extra 引入，但未安装)")

    LOCK_PATH.write_text(_LOCK_HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写出 {LOCK_PATH}（{len(items)} 项直接依赖 + extra 传递依赖）")
    for line in lines:
        print("  " + line)
    if missing:
        print(f"\n警告：以下依赖未安装，未能锁定版本：{', '.join(missing)}", file=sys.stderr)
        print("请在已安装全部依赖的环境中重新运行。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
