"""Application-side plans shared by foreground, background and terminal exec."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys

from modules.execution_scope import current_execution_scope
from modules.host_sandbox_policy import get_macos_readable_paths, get_macos_writable_paths
from modules.scoped_execution_policy import fixed_workspace_root, scoped_work_path
from .client import probe
from .constants import SAFE_ENVIRONMENT
from .schema import validate_request


def build_plan(work_path, environment, argv, network_permission=None, readonly=False,
               workspace_root=None):
    # Lazy import preserves the existing SandboxPlan/HostSandboxError API.
    from modules.host_sandbox_runner import SandboxPlan, HostSandboxError
    scope = current_execution_scope()
    cwd = Path(work_path).resolve()
    if scope:
        try:
            scoped_work_path(scope.workspace_root, str(work_path), scope)
        except ValueError as error:
            raise HostSandboxError(str(error)) from error
        root = fixed_workspace_root(scope)
    else:
        # 主智能体：cwd 可以是工作区内的子目录，边界锚点由调用方显式给定。
        # 缺省才回落 cwd——那会让沙箱把「当前目录」当工作区，等于没有边界。
        root = Path(workspace_root).resolve() if workspace_root else cwd
    workspace_only = bool(scope and scope.workspace_only)
    reads = get_macos_readable_paths(str(root))
    writes = [] if readonly or workspace_only else get_macos_writable_paths(str(root))
    # Path authorization UI already stores canonical paths. Reject changed
    # symlinks at the helper rather than following them to a new mount source.
    normalized_reads = list(dict.fromkeys(str(Path(path).expanduser()) for path in reads))
    normalized_writes = list(dict.fromkeys(str(Path(path).expanduser()) for path in writes))
    request = {"operation": "run", "workspace": str(root), "cwd": str(cwd),
               "reads": normalized_reads, "writes": normalized_writes, "readonly": readonly,
               "workspace_only": workspace_only, "network": network_permission or "restricted",
               "argv": argv, "env": {k: str(v) for k, v in environment.items() if k in SAFE_ENVIRONMENT},
               "umask": 0o022}
    try:
        validate_request(request)
        probe()
    except (OSError, ValueError, RuntimeError) as error:
        raise HostSandboxError(f"Linux 沙箱未就绪：{error}；请运行 Linux 沙箱安装向导或命令行安装。") from error
    encoded = base64.b64encode(json.dumps(request, ensure_ascii=False).encode()).decode()
    client = str(Path(__file__).with_name("client.py"))
    return SandboxPlan(command=[sys.executable, client, "--request", encoded], env=environment, cwd=str(cwd))


def build_command_plan(command, work_path, env, network_permission=None, workspace_root=None):
    return build_plan(work_path, env, ["/bin/bash", "-c", command], network_permission,
                      workspace_root=workspace_root)


def build_readonly_plan(command, work_path, env, network_permission=None, workspace_root=None):
    return build_plan(work_path, env, ["/bin/bash", "-c", command], network_permission,
                      readonly=True, workspace_root=workspace_root)


def build_shell_plan(work_path, env, network_permission=None, readonly=False, workspace_root=None):
    return build_plan(work_path, env, ["/bin/bash", "-i"], network_permission,
                      readonly=readonly, workspace_root=workspace_root)
