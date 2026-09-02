"""Per-user Docker container manager for main agent."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from config import (
    MAX_ACTIVE_CONTAINERS_PER_USER,
    MAX_ACTIVE_USER_CONTAINERS,
    OUTPUT_FORMATS,
    TERMINAL_SANDBOX_BIN,
    TERMINAL_SANDBOX_BINDS,
    TERMINAL_SANDBOX_CPUS,
    TERMINAL_SANDBOX_ENV,
    TERMINAL_SANDBOX_IMAGE,
    TERMINAL_SANDBOX_MEMORY,
    TERMINAL_SANDBOX_MODE,
    TERMINAL_SANDBOX_MOUNT_PATH,
    TERMINAL_SANDBOX_NAME_PREFIX,
    TERMINAL_SANDBOX_NETWORK,
    TERMINAL_SANDBOX_PIDS_LIMIT,
    TERMINAL_SANDBOX_REQUIRE,
    LOGS_DIR,
    LINUX_SAFETY,
)
from modules.container_monitor import collect_stats, inspect_state
from modules.i18n import tr


@dataclass
class ContainerHandle:
    """Lightweight record describing a user workspace container."""

    username: str
    mode: str
    workspace_path: str
    mount_path: str
    container_name: Optional[str] = None
    container_id: Optional[str] = None
    sandbox_bin: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def touch(self):
        self.last_active = time.time()

    def to_dict(self) -> Dict:
        return {
            "username": self.username,
            "mode": self.mode,
            "workspace_path": self.workspace_path,
            "mount_path": self.mount_path,
            "container_name": self.container_name,
            "container_id": self.container_id,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }


class UserContainerManager:
    """Create and track long-lived containers for each logged-in user."""

    def __init__(
        self,
        sandbox_mode: Optional[str] = None,
        max_containers: int = MAX_ACTIVE_USER_CONTAINERS,
    ):
        self.sandbox_mode = (sandbox_mode or TERMINAL_SANDBOX_MODE or "host").lower()
        self.max_containers = max_containers
        self.image = TERMINAL_SANDBOX_IMAGE
        self.mount_path = TERMINAL_SANDBOX_MOUNT_PATH or "/workspace"
        self.network = TERMINAL_SANDBOX_NETWORK
        self.cpus = TERMINAL_SANDBOX_CPUS
        self.memory = TERMINAL_SANDBOX_MEMORY
        self.pids_limit = TERMINAL_SANDBOX_PIDS_LIMIT
        self.binds = list(TERMINAL_SANDBOX_BINDS)
        self.sandbox_bin = TERMINAL_SANDBOX_BIN or "docker"
        self.name_prefix = TERMINAL_SANDBOX_NAME_PREFIX or "agent-user"
        self.require = bool(TERMINAL_SANDBOX_REQUIRE)
        self.extra_env = dict(TERMINAL_SANDBOX_ENV)
        # 用 label 标记“本项目创建的容器”，用于启动时安全清理。
        self._project_root = str(Path(__file__).resolve().parents[1])
        self._project_label_key = "agents.project_root"
        self._project_label_value = self._project_root
        self._kind_label_key = "agents.kind"
        self._kind_label_value = "terminal_sandbox"
        self._containers: Dict[str, ContainerHandle] = {}
        self._lock = threading.Lock()
        self._stats_log_path = Path(LOGS_DIR).expanduser().resolve() / "container_stats.log"
        self._stats_log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._stats_log_path.exists():
            self._stats_log_path.touch()
        if self.sandbox_mode == "docker":
            self._assert_docker_runtime_ready()

        # 用户要求：每次启动程序时自动关闭本项目相关容器，避免“程序退出后容器残留”。
        # 默认开启；如确实需要保留容器，可设置 CLEANUP_PROJECT_CONTAINERS_ON_START=0。
        cleanup_flag = os.environ.get("CLEANUP_PROJECT_CONTAINERS_ON_START", "1").strip().lower()
        cleanup_enabled = cleanup_flag not in {"0", "false", "no", "off"}
        if cleanup_enabled:
            try:
                self.cleanup_project_containers()
            except Exception:
                # 清理失败不应影响主流程启动
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def ensure_container(
        self,
        username: str,
        workspace_path: str,
        container_key: Optional[str] = None,
        preferred_mode: Optional[str] = None,
    ) -> ContainerHandle:
        """为指定“容器键”确保一个容器。

        - username：业务用户名（用于日志/权限）
        - container_key：容器缓存的 key，默认等于 username；
          对于多工作区 API，可传入 f\"{username}::{workspace_id}\" 以实现“每工作区一容器”。
        """
        username_norm = self._normalize_username(username)
        key = self._normalize_username(container_key or username_norm)
        workspace = str(Path(workspace_path).expanduser().resolve())
        Path(workspace).mkdir(parents=True, exist_ok=True)

        mode = (preferred_mode or self.sandbox_mode or "host").lower()
        # 安全模式：当 LINUX_SAFETY 开启且要求 host 时，强制回退 docker
        if mode == "host" and LINUX_SAFETY:
            mode = "docker"

        with self._lock:
            handle = self._containers.get(key)
            if handle:
                # 模式不同或 docker 挂掉时重建
                if handle.mode != mode:
                    self._containers.pop(key, None)
                    if handle.mode == "docker":
                        self._kill_container(handle.container_name, handle.sandbox_bin)
                    handle = None
                elif handle.mode == "docker" and not self._is_container_running(handle):
                    self._containers.pop(key, None)
                    self._kill_container(handle.container_name, handle.sandbox_bin)
                    handle = None
                else:
                    handle.workspace_path = workspace
                    handle.touch()
                    return handle

            # 配额检查仅针对 docker 句柄：host 句柄只是会话标识，不占用容器池资源，
            # 创建 host 句柄时直接跳过全局/每用户两道配额闸门
            if mode == "docker":
                if not self._has_capacity(key):
                    raise RuntimeError(tr("container_mgr.quota_exhausted"))

                # 每用户容器配额：防单用户多工作区占满全局容器池，挤占其他用户
                per_user_limit = MAX_ACTIVE_CONTAINERS_PER_USER
                if per_user_limit > 0:
                    owned = sum(
                        1 for k, h in self._containers.items()
                        if (k == username_norm or k.startswith(f"{username_norm}::"))
                        and h.mode == "docker"
                    )
                    if owned >= per_user_limit:
                        raise RuntimeError(tr("container_mgr.per_user_quota_exhausted", limit=per_user_limit))

            # Important: create container using the cache key so each workspace gets its own container name.
            handle = self._create_handle(key, workspace, mode)
            self._containers[key] = handle
            return handle

    def release_container(self, container_key: str, reason: str = "logout"):
        key = self._normalize_username(container_key)
        with self._lock:
            handle = self._containers.pop(key, None)
        if not handle:
            return
        if handle.mode == "docker" and handle.container_name:
            self._kill_container(handle.container_name, handle.sandbox_bin)
            print(f"{OUTPUT_FORMATS['info']} 容器已释放: {handle.container_name} ({reason})")

    def has_capacity(self, username: Optional[str] = None) -> bool:
        username = self._normalize_username(username) if username else None
        with self._lock:
            if username and username in self._containers:
                return True
            if self.max_containers <= 0:
                return True
            docker_total = sum(1 for h in self._containers.values() if h.mode == "docker")
            return docker_total < self.max_containers

    def get_handle(self, container_key: str) -> Optional[ContainerHandle]:
        key = self._normalize_username(container_key)
        with self._lock:
            handle = self._containers.get(key)
            if handle:
                handle.touch()
            return handle

    def list_containers(self) -> Dict[str, Dict]:
        with self._lock:
            return {user: handle.to_dict() for user, handle in self._containers.items()}

    def get_container_status(self, container_key: str, include_stats: bool = True) -> Dict:
        key = self._normalize_username(container_key)
        with self._lock:
            handle = self._containers.get(key)
        if not handle:
            # 未找到句柄，视为未运行
            return {"username": key, "mode": "host", "running": False}

        info = {
            "username": handle.username,
            "mode": handle.mode,
            "workspace_path": handle.workspace_path,
            "mount_path": handle.mount_path,
            "container_name": handle.container_name,
            "created_at": handle.created_at,
            "last_active": handle.last_active,
            # host 模式下，句柄存在即可认为“运行中”，便于监控统计
            "running": handle.mode != "docker",
        }

        if handle.mode == "docker" and include_stats:
            stats = collect_stats(handle.container_name, handle.sandbox_bin)
            state = inspect_state(handle.container_name, handle.sandbox_bin)
            if stats:
                info["stats"] = stats
                self._log_stats(handle.username, stats)
            if state:
                info["state"] = state
                # 尽量从容器状态读取运行标记
                if "State" in state:
                    info["running"] = bool(state.get("State", {}).get("Running", False))
                elif "running" in state:
                    info["running"] = bool(state.get("running"))

        return info

    def cleanup_project_containers(self) -> Dict[str, int]:
        """关闭并移除“本项目创建的终端容器”。

        只在 docker 沙箱模式下生效，并尽量避免误杀其他项目容器：
        1) 优先通过 label 精确匹配；2) 兼容旧容器：在 name_prefix 匹配后，再用 Mounts 路径校验。
        """
        if self.sandbox_mode != "docker":
            return {"candidates": 0, "removed": 0}

        docker_path = shutil.which(self.sandbox_bin or "docker")
        if not docker_path:
            return {"candidates": 0, "removed": 0}

        removed = 0

        # 1) label 精确匹配（最安全）
        labeled = self._list_containers_by_label(
            docker_path,
            label_key=self._project_label_key,
            label_value=self._project_label_value,
        )
        # 2) 兼容旧容器：按 name_prefix 粗筛，再按 Mounts 是否落在本 repo 下精筛
        legacy = self._list_containers_by_name_prefix(docker_path, self.name_prefix)
        candidates = []
        seen = set()
        for name in labeled + legacy:
            if not name or name in seen:
                continue
            seen.add(name)
            candidates.append(name)

        for name in candidates:
            if name in labeled:
                self._kill_container(name, docker_path)
                removed += 1
                continue

            # legacy：必须满足 mounts 落在 repo root 下，才允许清理
            if self._is_container_from_this_project(docker_path, name):
                self._kill_container(name, docker_path)
                removed += 1

        return {"candidates": len(candidates), "removed": removed}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _has_capacity(self, username: str) -> bool:
        if self.max_containers <= 0:
            return True
        # 全局上限同样只统计 docker 句柄（host 句柄不占用容器池）
        docker_total = sum(1 for h in self._containers.values() if h.mode == "docker")
        existing_handle = self._containers.get(username)
        existing = 1 if (existing_handle and existing_handle.mode == "docker") else 0
        return (docker_total - existing) < self.max_containers

    def _create_handle(self, username: str, workspace: str, mode: str) -> ContainerHandle:
        if mode != "docker":
            return self._host_handle(username, workspace)

        docker_path = shutil.which(self.sandbox_bin or "docker")
        if not docker_path:
            raise RuntimeError(tr("container_mgr.runtime_not_found", runtime=self.sandbox_bin))

        if not self.image:
            raise RuntimeError(tr("container_mgr.image_not_configured"))

        # 创建前先做 daemon 就绪检查：daemon 未启动时给出友好中文报错，
        # 而不是 docker run 的英文原始 stderr（如 Cannot connect to the Docker daemon...）
        self._assert_docker_runtime_ready()

        container_name = self._build_container_name(username)
        self._kill_container(container_name, docker_path)

        # 容器主进程刻意保持 root：可写执行（docker exec 不带 -u）依赖它；
        # 只读权限模式的执行通道在 exec 时以非特权 uid 运行（内核 DAC 强制只读），
        # 见 modules/docker_readonly_exec.py。工作区 bind 挂载是唯一可写资产，
        # 切勿给容器挂载 docker.sock 或追加特权/capability（会直接击穿该模型）。
        cmd = [
            docker_path,
            "run",
            "-d",
            "--name",
            container_name,
            "--label",
            f"{self._project_label_key}={self._project_label_value}",
            "--label",
            f"{self._kind_label_key}={self._kind_label_value}",
            "-w",
            self.mount_path,
            "-v",
            f"{workspace}:{self.mount_path}",
        ]

        if self.network:
            cmd += ["--network", self.network]
        if self.cpus:
            cmd += ["--cpus", str(self.cpus)]
        if self.memory:
            cmd += ["--memory", str(self.memory)]
            # 锁定 swap 用量等于内存上限，防止 swap 绕过内存限制
            cmd += ["--memory-swap", str(self.memory)]
        # 安全默认（2026-09-02 审计）：PID 上限防 fork bomb；禁提权防 setuid 类攻击。
        if self.pids_limit:
            cmd += ["--pids-limit", str(self.pids_limit)]
        cmd += ["--security-opt", "no-new-privileges:true"]
        for bind in self.binds:
            chunk = bind.strip()
            if chunk:
                cmd += ["-v", chunk]

        envs = {
            "PYTHONIOENCODING": "utf-8",
            "TERM": "xterm-256color",
        }
        envs.update({k: v for k, v in self.extra_env.items() if v is not None})
        for key, value in envs.items():
            cmd += ["-e", f"{key}={value}"]

        cmd.append(self.image)
        cmd += ["tail", "-f", "/dev/null"]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or tr("container_mgr.docker_start_failed")
            raise RuntimeError(message)

        container_id = result.stdout.strip() or None
        print(f"{OUTPUT_FORMATS['success']} 启动用户容器: {container_name} ({username})")
        return ContainerHandle(
            username=username,
            mode="docker",
            workspace_path=workspace,
            mount_path=self.mount_path,
            container_name=container_name,
            container_id=container_id,
            sandbox_bin=docker_path,
        )

    def _host_handle(self, username: str, workspace: str) -> ContainerHandle:
        return ContainerHandle(
            username=username,
            mode="host",
            workspace_path=workspace,
            mount_path=workspace,
        )

    def _assert_docker_runtime_ready(self):
        docker_path = shutil.which(self.sandbox_bin or "docker")
        if not docker_path:
            raise RuntimeError(tr("container_mgr.docker_mode_runtime_not_found", runtime=self.sandbox_bin))
        try:
            result = subprocess.run(
                [docker_path, "info"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(tr("container_mgr.docker_daemon_unreachable", error=exc)) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip() or tr("container_mgr.docker_info_nonzero")
            raise RuntimeError(tr("container_mgr.docker_mode_failed", message=message))

    def _kill_container(self, container_name: Optional[str], docker_bin: Optional[str]):
        if not container_name or not docker_bin:
            return
        subprocess.run(
            [docker_bin, "rm", "-f", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def _list_containers_by_label(self, docker_bin: str, label_key: str, label_value: str) -> list[str]:
        try:
            result = subprocess.run(
                [
                    docker_bin,
                    "ps",
                    "-a",
                    "--filter",
                    f"label={label_key}={label_value}",
                    "--format",
                    "{{.Names}}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]

    def _list_containers_by_name_prefix(self, docker_bin: str, prefix: str) -> list[str]:
        safe_prefix = (prefix or "").strip()
        if not safe_prefix:
            return []
        try:
            result = subprocess.run(
                [
                    docker_bin,
                    "ps",
                    "-a",
                    "--filter",
                    f"name=^{safe_prefix}-",
                    "--format",
                    "{{.Names}}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]

    def _is_container_from_this_project(self, docker_bin: str, container_name: str) -> bool:
        """通过 docker inspect 的 Mounts/Labels 判断容器是否属于本 repo。"""
        try:
            result = subprocess.run(
                [docker_bin, "inspect", container_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=6,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0 or not result.stdout.strip():
            return False

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        if not payload or not isinstance(payload, list):
            return False
        info = payload[0] if payload else {}

        labels = (info.get("Config") or {}).get("Labels") or {}
        if labels.get(self._project_label_key) == self._project_label_value:
            return True

        # 旧容器：检查 mount source 是否落在当前 repo 下（避免误删其他项目容器）
        mounts = info.get("Mounts") or []
        root = str(Path(self._project_root).resolve())
        root_norm = root.rstrip("/") + "/"
        for m in mounts:
            src = (m or {}).get("Source") or ""
            if not src:
                continue
            src_norm = str(Path(src).resolve())
            if src_norm == root or src_norm.startswith(root_norm):
                return True
        return False

    def _is_container_running(self, handle: ContainerHandle) -> bool:
        if handle.mode != "docker" or not handle.container_name or not handle.sandbox_bin:
            return True
        try:
            result = subprocess.run(
                [
                    handle.sandbox_bin,
                    "inspect",
                    "-f",
                    "{{.State.Running}}",
                    handle.container_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def _build_container_name(self, key: str) -> str:
        """
        Build a docker container name for the given cache key.

        Notes:
        - Multi-workspace API uses key like `api_demo::ws1`, we must encode workspace into the name,
          otherwise all workspaces would share one container (breaking isolation).
        - Docker names should be reasonably short and only contain [a-z0-9-].
        """
        raw = (key or "").strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
        if not slug:
            slug = "user"
        # Keep name short; append a stable hash suffix when truncated to avoid collisions.
        max_slug = 48
        if len(slug) > max_slug:
            suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
            slug = f"{slug[:max_slug].strip('-')}-{suffix}"
        return f"{self.name_prefix}-{slug}"

    def _log_stats(self, username: str, stats: Dict):
        try:
            record = {
                "username": username,
                "timestamp": time.time(),
                "stats": stats,
            }
            with self._stats_log_path.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @staticmethod
    def _normalize_username(username: Optional[str]) -> str:
        return (username or "").strip().lower()
