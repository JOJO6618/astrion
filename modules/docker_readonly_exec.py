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
"""
from __future__ import annotations

import os
from typing import List, Tuple


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
