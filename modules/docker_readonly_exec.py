"""docker 容器内只读执行：非特权执行角色（uid/gid）。

设计（2026-08-30 云端实测验证，详见项目记忆 sandbox_readonly_reform_research）：

- 容器主进程与可写执行保持 root；sandbox_write_access=False 的执行通道
  （run_command 前台/后台、只读语境下创建的持久终端）统一改用非特权 uid
  （默认 10001:10001，可用环境变量覆盖）。
- 强制力来自内核 DAC：工作区 bind 挂载的文件属主是宿主机 root，非属主且
  无 o+w → 写/删/chmod/umount 一律 EACCES/EPERM；600 权限的 .env 等敏感
  文件天然不可读。此前的「命令文本特征识别」降级为审批决策的启发式，
  不再是 docker 只读的安全边界。
- 不依赖镜像中存在该用户（数字 uid 直接生效），旧镜像直接受益；
  docker/terminal.Dockerfile 同步创建 agent 用户与 git safe.directory，
  供未来构建的镜像使用。
- 逃逸门槛：需要先提权（setuid 漏洞/内核漏洞），远高于嵌套 namespace
  方案的 umount 逃逸；云端默认 seccomp 会拦 unshare(CLONE_NEWUSER)，
  嵌套方案已被本方案取代。
- 已知边界：macOS Docker Desktop 的 virtiofs（fakeowner）不按 uid 执行
  权限检查，本机制仅在 Linux 宿主机（云端/ Linux 桌面）生效，属预期差异；
  持久终端在 approval/auto_approval 模式下同为只读身份，写入命令请走
  run_command（审批通过后以 root 重跑）。

Landlock 加固（2026-09 起）：

- 纯 DAC 的残留漏洞：工作区内历史遗留的 world-writable（777/o+w）路径
  对只读 uid 仍开放写权限。为此在只读执行时额外用 Landlock 给进程套上
  「工作区写类操作全拒」的内核域（最终权限 = DAC ∩ Landlock），封死该洞。
- launcher 为本目录 landlock_launcher.py，首次只读执行时 docker cp 进容器
  并以只读身份自检（本进程+子进程写工作区都必须被拒），通过后才启用；
  任何一步失败都静默降级为纯 DAC，并以 warning 日志标注 enforcement level。
- 可用环境变量 DOCKER_READONLY_LANDLOCK=0 整体停用（运维逃生门）。
- 语义刻意对齐纯 DAC 现状：仅工作区写类操作被拒，/tmp 等其余路径写、
  全部读/执行行为不变（读保护仍由 DAC 承担）。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("docker_readonly_exec")

LANDLOCK_LAUNCHER_SRC = Path(__file__).resolve().with_name("landlock_launcher.py")
LANDLOCK_LAUNCHER_CONTAINER_PATH = "/opt/astrion-landlock/launcher.py"

# 容器名 -> "available" / "unavailable:<reason>"；None 表示尚未探测
_landlock_states: Dict[str, str] = {}
_landlock_states_lock = threading.Lock()


def docker_readonly_uid_gid() -> Tuple[str, str]:
    """只读执行身份的 uid/gid（默认 10001:10001，环境变量可覆盖）。

    选取原则：避开宿主机工作区文件属主 uid（否则会被 DAC 认成主人而放行），
    10001 为冷门值；如部署环境文件属主恰为该值，用环境变量改。
    """
    uid = os.environ.get("DOCKER_READONLY_EXEC_UID", "10001").strip() or "10001"
    gid = os.environ.get("DOCKER_READONLY_EXEC_GID", uid).strip() or uid
    return uid, gid


def docker_readonly_exec_args() -> List[str]:
    """构造只读执行的 docker exec 前缀参数（紧跟在 "exec" 之后）。

    含：
    - ``-u uid:gid``：非特权执行身份（内核 DAC 强制只读的核心）；
    - ``HOME=/tmp``：uid 在 /etc/passwd 无条目时提供可写 HOME；
    - git safe.directory 修复：工作区属主是 root，非属主 git 会报
      "detected dubious ownership"，用 env 方式注入，不依赖镜像内 gitconfig。
    """
    uid, gid = docker_readonly_uid_gid()
    return [
        "-u", f"{uid}:{gid}",
        "-e", "HOME=/tmp",
        "-e", "GIT_CONFIG_COUNT=1",
        "-e", "GIT_CONFIG_KEY_0=safe.directory",
        "-e", "GIT_CONFIG_VALUE_0=*",
    ]


def _landlock_enabled() -> bool:
    return os.environ.get("DOCKER_READONLY_LANDLOCK", "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


def _docker_run(docker_bin: str, args: List[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        [docker_bin] + args, capture_output=True, timeout=timeout,
    )


def _deploy_and_selftest(container_name: str, mount_path: str,
                         docker_bin: str) -> Tuple[bool, str]:
    """把 launcher 部署进容器并以只读身份自检。返回 (是否可用, 失败原因)。"""
    mount_path = (mount_path or "/workspace").rstrip("/") or "/"
    try:
        # 1. 容器内 python3 可用性
        r = _docker_run(docker_bin, ["exec", container_name, "sh", "-c",
                                     "command -v python3"], timeout=10)
        if r.returncode != 0:
            return False, "no-python3-in-container"
        # 2. 部署 launcher 文件
        r = _docker_run(docker_bin, ["exec", container_name, "mkdir", "-p",
                                     os.path.dirname(LANDLOCK_LAUNCHER_CONTAINER_PATH)],
                        timeout=10)
        if r.returncode != 0:
            return False, f"mkdir-failed:{r.stderr.decode(errors='replace')[:200]}"
        r = _docker_run(docker_bin, ["cp", str(LANDLOCK_LAUNCHER_SRC),
                                     f"{container_name}:{LANDLOCK_LAUNCHER_CONTAINER_PATH}"],
                        timeout=30)
        if r.returncode != 0:
            return False, f"docker-cp-failed:{r.stderr.decode(errors='replace')[:200]}"
        # 3. 以只读身份自检（与真实只读执行同一 uid/环境）
        r = _docker_run(
            docker_bin,
            ["exec", *docker_readonly_exec_args(), container_name,
             "python3", LANDLOCK_LAUNCHER_CONTAINER_PATH,
             "--selftest", "--ro", mount_path],
            timeout=30,
        )
        out = (r.stdout + r.stderr).decode(errors="replace")
        # 4. 清理自检意外成功时的残留（此时说明 Landlock 未生效，但仍要扫尾）
        try:
            _docker_run(docker_bin, ["exec", container_name, "rm", "-f",
                                     f"{mount_path}/.landlock_selftest_probe"],
                        timeout=10)
        except Exception:
            pass
        if r.returncode == 0 and "LANDLOCK_SELFTEST_OK" in out:
            return True, ""
        return False, f"selftest-failed:{out.strip()[:300]}"
    except subprocess.TimeoutExpired:
        return False, "probe-timeout"
    except Exception as e:  # noqa: BLE001 - 部署探测必须兜底为降级而非异常
        return False, f"{type(e).__name__}:{e}"


def ensure_landlock_ready(container_name: str, mount_path: str,
                          docker_bin: Optional[str] = None) -> bool:
    """确保容器内 Landlock 只读域可用（部署+自检，按容器缓存结果）。

    任何失败都返回 False（调用方回退纯 DAC），不会抛出。
    """
    if not _landlock_enabled():
        return False
    with _landlock_states_lock:
        state = _landlock_states.get(container_name)
    if state is not None:
        return state == "available"
    docker_bin = docker_bin or shutil.which("docker") or "docker"
    ok, reason = _deploy_and_selftest(container_name, mount_path, docker_bin)
    with _landlock_states_lock:
        _landlock_states[container_name] = "available" if ok else f"unavailable:{reason}"
    if ok:
        logger.info("landlock readonly ready: container=%s mount=%s",
                    container_name, mount_path)
    else:
        logger.warning(
            "landlock unavailable: container=%s reason=%s — "
            "readonly enforcement falls back to DAC-only (world-writable "
            "paths remain writable by the readonly uid)",
            container_name, reason,
        )
    return ok


def docker_readonly_wrap_inner(container_name: str, mount_path: str,
                               inner_cmd: List[str],
                               docker_bin: Optional[str] = None) -> List[str]:
    """只读执行时包装容器内命令：Landlock 可用则经 launcher 进入只读域。

    可用：  [python3, launcher, --ro, mount, --] + inner_cmd
    不可用：原样返回 inner_cmd（纯 DAC 降级）。
    """
    if ensure_landlock_ready(container_name, mount_path, docker_bin):
        mount = (mount_path or "/workspace").rstrip("/") or "/"
        return ["python3", LANDLOCK_LAUNCHER_CONTAINER_PATH,
                "--ro", mount, "--", *inner_cmd]
    return list(inner_cmd)
