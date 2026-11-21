"""执行环境快照：LocalExecutionBackend.get_environment 的实现细节。

拆出来的原因：local.py 只保留「契约面 + 资源解析」，环境探测（大小写敏感性、
能力实测、平台事实）属于纯读逻辑，独立成模块便于单独测试，也让 local.py 保持
在可读长度内。

环境报告描述执行后端所在的 Host。能力检查只确认资源实现存在，不验证路径权限或 OS 隔离。
工作区的大小写敏感性通过只读探测获得。探测失败时保留未知状态。

本模块只依赖标准库，不 import Flask / APIClient / 模型栈，可在无 Agent 的
轻量执行节点里使用。
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def detect_case_sensitivity(root: Optional[Path]) -> Optional[bool]:
    """探测大小写敏感性（只读：只做 stat，不列目录、不新建文件）。

    返回 True=区分大小写，False=不区分，None=无从判断（不猜）。
    优先在**工作区路径自身**上做大小写互换的 stat：大小写不敏感文件系统会把
    ``WS`` 解析到同一个 ``ws``（samefile），既不依赖目录内有无文件，也不泄露
    父目录条目；路径名无字母时才回退到枚举少量条目。
    """
    if root is None:
        return None
    candidates = [root]
    if not (root.name and any(ch.isalpha() for ch in root.name)):
        candidates = []
        try:
            for entry in root.iterdir():
                candidates.append(entry)
                if len(candidates) >= 32:
                    break
        except OSError:
            return None

    for candidate in candidates:
        name = candidate.name
        if not any(ch.isalpha() for ch in name):
            continue
        swapped = candidate.with_name(name.swapcase())
        if swapped == candidate:
            continue
        try:
            if not swapped.exists():
                # 大小写不敏感文件系统上「换大小写后的名字」必定能 stat 到
                return True
            try:
                if swapped.samefile(candidate):
                    return False
            except OSError:
                return False
            # 换名后指向另一个真实存在的条目：样本不可用，继续看下一个
            continue
        except OSError:
            continue
    return None


def probe_capabilities(backend: Any) -> Dict[str, Any]:
    """实测当前 owner 暴露的能力（只判断"有没有可用实现"，不产生副作用）。"""
    optional = getattr(backend, "_optional", None)
    getter = optional if callable(optional) else (lambda name: getattr(backend.owner, name, None))
    ops = getter("terminal_ops")
    terminals = getter("terminal_manager")
    background = getter("background_command_manager")
    caps: Dict[str, Any] = {
        "run_command": callable(getattr(ops, "run_command", None)),
        "persistent_terminals": callable(getattr(terminals, "open_terminal", None)),
        "background_commands": callable(
            getattr(background, "create_background_command", None)
        ),
        "cancel_background": callable(getattr(background, "cancel_command", None)),
        "files": False,
        "files_metadata": False,
        "binary_reads": False,
        "full_access": bool(getattr(backend, "supports_full_access", False)),
    }
    try:
        files = backend.files
    except Exception:  # noqa: BLE001 - 能力探测失败就是"不可用"，不是异常
        return caps
    caps["files"] = True
    caps["files_metadata"] = all(
        callable(getattr(files, name, None))
        for name in ("inspect_path", "get_file_info", "list_files")
    )
    caps["binary_reads"] = callable(getattr(files, "read_binary", None))
    return caps


def describe_environment(backend: Any) -> Dict[str, Any]:
    """构造 JSON 可序列化的环境快照（字段语义见模块文档）。"""
    root: Optional[Path] = None
    workspace_error: Optional[str] = None
    try:
        root = backend.workspace_root
    except Exception as exc:  # noqa: BLE001 - report other facts without a workspace
        workspace_error = str(exc)

    case_sensitive = detect_case_sensitivity(root)
    target_root = str(root) if root is not None else None
    return {
        "backend": backend.backend_name,
        "is_local": bool(backend.is_local),
        "supports_full_access": bool(backend.supports_full_access),
        "platform": sys.platform,
        "case_sensitive": case_sensitive,
        "case_sensitive_source": "workspace-probe" if case_sensitive is not None else "unknown",
        "execution_target": "host",
        "execution_mode": backend.execution_mode,
        "network_permission": backend.network_permission,
        "workspace_root": target_root,
        "cwd": target_root,
        "workspace_error": workspace_error,
        "capabilities": probe_capabilities(backend),
        "declared_capabilities": dict(getattr(backend, "capabilities", {}) or {}),
        "os_scope": "host",
        "host": {
            "platform": sys.platform,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "container": None,
        "notes": [
            "host.* 描述执行后端所在机器。",
            "supports_full_access 表示后端能力。单次完全访问仍需权限层裁决。",
            "case_sensitive 通过只读探测获得，无可靠样本时为 null。",
            "capabilities 仅检查当前 owner 的资源实现，不验证路径权限或 OS 隔离；declared_capabilities 为静态声明。",
        ],
    }


__all__ = ["detect_case_sensitivity", "probe_capabilities", "describe_environment"]
