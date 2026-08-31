# modules/sandbox_setup_manager.py - Windows WSL 沙箱环境检测与一键安装管理
#
# 背景：Windows 宿主机模式的命令沙箱依赖专用 WSL2 发行版（默认 astrion-sandbox，
# Alpine + bubblewrap，关闭 interop）。此前缺失时只在首次执行命令时被动报错，
# 本模块提供：
#   1. get_sandbox_status()  —— 主动分级检测（wsl_missing / distro_missing / bwrap_missing / ready）
#   2. start_setup()         —— 后台线程执行 scripts/setup-wsl-sandbox.ps1，逐步解析进度
#   3. get_setup_progress()  —— 前端轮询进度（阶段 / 步骤 / 日志尾部 / 下载字节数）
#
# 注意：
# - 安装进程由后端 server 直接在宿主机拉起（direct），不走 run_command 沙箱链路
#   （沙箱尚未建立，属于"鸡生蛋"场景，由前端用户显式点击触发）。
# - WSL 功能未启用时先经 UAC 提权执行 wsl --install --no-distribution，可能需要重启。
# - 进度仅保存在内存（一次性操作，无需持久化）。

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.host_sandbox_runner import WSL_DEFAULT_SANDBOX_DISTRO, _wsl_distro_name
from modules.i18n import tr

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SETUP_SCRIPT = _REPO_ROOT / "scripts" / "setup-wsl-sandbox.ps1"

# 与 scripts/setup-wsl-sandbox.ps1 默认 RootfsUrl 保持一致（用于 HEAD 估算下载总量）
_ROOTFS_URL = (
    "https://mirrors.aliyun.com/alpine/v3.21/releases/x86_64/"
    "alpine-minirootfs-3.21.3-x86_64.tar.gz"
)
# ps1 中下载的临时文件路径（%TEMP%\astrion-alpine-minirootfs.tar.gz）
_ROOTFS_TEMP_NAME = "astrion-alpine-minirootfs.tar.gz"

_STEP_RE = re.compile(r"^==>\s*\[(\d+)/(\d+)\]\s*(.+)$")
_STEP_TOTAL = 6
_LOG_TAIL_MAX = 30
_STATUS_CACHE_TTL = 10.0

# 阶段常量
PHASE_IDLE = "idle"
PHASE_ENABLING_WSL = "enabling_wsl"
PHASE_INSTALLING_WSL = "installing_wsl"
PHASE_INSTALLING = "installing"
PHASE_VERIFYING = "verifying"
PHASE_DONE = "done"
PHASE_NEEDS_REBOOT = "needs_reboot"
PHASE_ERROR = "error"


def _wsl_env() -> Dict[str, str]:
    """子进程环境：强制 wsl.exe 输出 UTF-8（默认 UTF-16 会乱码）。"""
    env = dict(os.environ)
    env["WSL_UTF8"] = "1"
    return env


def _run_probe(argv: List[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    # stdin=DEVNULL 必须：wsl --uninstall 后 stub 会输出交互式提示并等待按键 60 秒，
    # 继承 stdin 时探测直接卡死；关闭 stdin 后 0.1s 返回 rc=1（实测验证）。
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_wsl_env(),
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _wsl_available() -> bool:
    """WSL 功能是否可用（wsl.exe 存在且能正常列出发行版）。

    未安装任何发行版时 `wsl -l -q` 返回空但 exit==0；
    WSL 功能未启用 / 虚拟机平台缺失时 exit!=0。
    """
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return False
    try:
        proc = _run_probe([wsl, "-l", "-q"])
        return proc.returncode == 0
    except Exception:
        return False


def _distro_usable(distro: str) -> bool:
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return False
    try:
        return _run_probe([wsl, "-d", distro, "-e", "true"]).returncode == 0
    except Exception:
        return False


def _bwrap_ready(distro: str) -> bool:
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return False
    try:
        return _run_probe([wsl, "-d", distro, "-e", "bwrap", "--version"]).returncode == 0
    except Exception:
        return False


class SandboxSetupManager:
    """沙箱状态检测 + 一键安装任务管理（进程级单例）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status_cache: Optional[Dict[str, Any]] = None
        self._status_cache_at = 0.0
        self._progress: Dict[str, Any] = self._fresh_progress()
        self._worker: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ status

    @staticmethod
    def _applicable() -> bool:
        if sys.platform != "win32":
            return False
        try:
            from config import TERMINAL_SANDBOX_MODE
        except Exception:
            return False
        return (TERMINAL_SANDBOX_MODE or "").lower() == "host"

    def get_sandbox_status(self, force: bool = False) -> Dict[str, Any]:
        """分级检测沙箱状态，带短 TTL 缓存（前端多处同时调用时防抖）。"""
        with self._lock:
            if (
                not force
                and self._status_cache is not None
                and time.time() - self._status_cache_at < _STATUS_CACHE_TTL
            ):
                cached = dict(self._status_cache)
                cached["setup_running"] = self._progress.get("active", False)
                return cached

        result: Dict[str, Any] = {
            "applicable": False,
            "platform": sys.platform,
            "state": "not_applicable",
            "distro_name": _wsl_distro_name() if self._applicable() else WSL_DEFAULT_SANDBOX_DISTRO,
            "detail": "",
        }
        if self._applicable():
            result["applicable"] = True
            distro = result["distro_name"]
            if not _wsl_available():
                result["state"] = "wsl_missing"
                result["detail"] = tr("sandbox.setup_wsl_missing")
            elif not _distro_usable(distro):
                result["state"] = "distro_missing"
                result["detail"] = tr("sandbox.setup_distro_missing", distro=distro)
            elif not _bwrap_ready(distro):
                result["state"] = "bwrap_missing"
                result["detail"] = tr("sandbox.setup_bwrap_missing", distro=distro)
            else:
                result["state"] = "ready"

        with self._lock:
            self._status_cache = result
            self._status_cache_at = time.time()
            out = dict(result)
            out["setup_running"] = self._progress.get("active", False)
            return out

    def invalidate_status_cache(self) -> None:
        with self._lock:
            self._status_cache = None
            self._status_cache_at = 0.0

    # ----------------------------------------------------------------- progress

    @staticmethod
    def _fresh_progress() -> Dict[str, Any]:
        return {
            "active": False,
            "phase": PHASE_IDLE,
            "step_index": 0,
            "step_total": _STEP_TOTAL,
            "step_title": "",
            "log_tail": [],
            "download_bytes": None,
            "download_total": None,
            "error": None,
            "error_kind": None,
            "updated_at": time.time(),
        }

    def get_setup_progress(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._progress)

    def _update_progress(self, **fields: Any) -> None:
        with self._lock:
            self._progress.update(fields)
            self._progress["updated_at"] = time.time()

    def _append_log(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        with self._lock:
            tail: List[str] = self._progress["log_tail"]
            tail.append(line)
            if len(tail) > _LOG_TAIL_MAX:
                del tail[: len(tail) - _LOG_TAIL_MAX]
            self._progress["updated_at"] = time.time()

    # -------------------------------------------------------------------- setup

    def start_setup(self, enable_wsl_if_needed: bool) -> Dict[str, Any]:
        """启动安装后台线程。返回 {"started": bool, "error": str|None}。"""
        if sys.platform != "win32":
            return {"started": False, "error": tr("sandbox.setup_not_windows")}
        if not _SETUP_SCRIPT.exists():
            return {"started": False, "error": tr("sandbox.setup_script_missing")}
        with self._lock:
            if self._progress.get("active"):
                return {"started": False, "error": tr("sandbox.setup_already_running")}
            self._progress = self._fresh_progress()
            self._progress["active"] = True
            self._progress["phase"] = PHASE_INSTALLING

        self._worker = threading.Thread(
            target=self._run_setup,
            args=(enable_wsl_if_needed,),
            name="sandbox-setup",
            daemon=True,
        )
        self._worker.start()
        return {"started": True, "error": None}

    def _finish(self, phase: str, error: Optional[str] = None, error_kind: Optional[str] = None) -> None:
        self._update_progress(active=False, phase=phase, error=error, error_kind=error_kind)
        self.invalidate_status_cache()

    def _run_setup(self, enable_wsl_if_needed: bool) -> None:
        try:
            # 阶段一：WSL 功能缺失时先提权安装（UAC 弹窗由用户在系统层确认）
            if not _wsl_available():
                if not enable_wsl_if_needed:
                    self._finish(PHASE_ERROR, tr("sandbox.setup_wsl_missing"), "wsl_enable_failed")
                    return
                if not self._enable_wsl():
                    return  # _enable_wsl 内部已 _finish

            # 阶段二：跑安装脚本（6 步）
            self._update_progress(phase=PHASE_INSTALLING)
            download_total = self._probe_rootfs_size()
            if download_total:
                self._update_progress(download_total=download_total)
            if not self._run_setup_script():
                return  # 内部已 _finish

            # 阶段三：验收（强制重新探测）
            self._update_progress(phase=PHASE_VERIFYING)
            status = self.get_sandbox_status(force=True)
            if status.get("state") == "ready":
                self._finish(PHASE_DONE)
            else:
                self._finish(
                    PHASE_ERROR,
                    tr("sandbox.setup_verify_failed", detail=status.get("detail") or status.get("state")),
                    "verify_failed",
                )
        except Exception as exc:  # 兜底，防止线程无声死亡
            self._finish(PHASE_ERROR, f"{type(exc).__name__}: {exc}", "unexpected")

    def _enable_wsl(self) -> bool:
        """UAC 提权执行 wsl --install --no-distribution。返回是否可继续安装。

        Start-Process -Verb RunAs 在 UAC 弹窗期间阻塞：用户确认后返回进程对象、
        拒绝时抛异常（exit 3）。利用这个时序用标记行把「等待授权」与
        「下载安装 WSL 组件」拆成两个阶段，reader 线程实时解析，前端及时切换：
          ASTRION_UAC_CONFIRMED     —— UAC 已通过，进入组件下载安装
          ASTRION_WSL_INSTALL_EXIT  —— wsl --install 进程退出码
          ASTRION_UAC_CANCELLED     —— 用户拒绝授权（exit 3）
        """
        self._update_progress(phase=PHASE_ENABLING_WSL)
        self._append_log(tr("sandbox.setup_enabling_wsl_log"))
        # 前缀先设控制台输出编码为 UTF-8：PS 5.1 默认按 GBK 输出，后端按 UTF-8 读会乱码
        ps_command = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "try { $p = Start-Process wsl.exe -Verb RunAs "
            "-ArgumentList '--install','--no-distribution' -PassThru -ErrorAction Stop } "
            "catch { Write-Host 'ASTRION_UAC_CANCELLED'; exit 3 }; "
            "Write-Host 'ASTRION_UAC_CONFIRMED'; $p.WaitForExit(); "
            'Write-Host "ASTRION_WSL_INSTALL_EXIT=$($p.ExitCode)"'
        )
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        install_exit: List[int] = []

        def _reader() -> None:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                if line == "ASTRION_UAC_CONFIRMED":
                    self._update_progress(phase=PHASE_INSTALLING_WSL)
                    self._append_log(tr("sandbox.setup_wsl_installing_log"))
                elif line.startswith("ASTRION_WSL_INSTALL_EXIT="):
                    try:
                        install_exit.append(int(line.split("=", 1)[1]))
                    except ValueError:
                        pass
                elif line != "ASTRION_UAC_CANCELLED":  # 由 returncode==3 统一判定，不进日志
                    self._append_log(line)

        reader = threading.Thread(target=_reader, name="sandbox-setup-uac", daemon=True)
        reader.start()
        try:
            proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            self._finish(PHASE_ERROR, tr("sandbox.setup_uac_timeout"), "uac_cancelled")
            return False
        reader.join(timeout=5)

        if proc.returncode == 3:
            self._finish(PHASE_ERROR, tr("sandbox.setup_wsl_enable_failed"), "uac_cancelled")
            return False
        if proc.returncode != 0 or not install_exit or install_exit[-1] != 0:
            self._finish(PHASE_ERROR, tr("sandbox.setup_wsl_enable_failed"), "wsl_enable_failed")
            return False
        # 提权安装完成后复查：仍不可用 → 大概率需要重启（虚拟机平台刚启用）
        if not _wsl_available():
            self._finish(PHASE_NEEDS_REBOOT)
            return False
        self._append_log(tr("sandbox.setup_wsl_enabled_log"))
        return True

    def _run_setup_script(self) -> bool:
        """执行 setup-wsl-sandbox.ps1，逐行解析进度。返回是否成功。"""
        if not _SETUP_SCRIPT.exists():
            self._finish(PHASE_ERROR, tr("sandbox.setup_script_missing"), "script_failed")
            return False
        download_stop = threading.Event()
        download_thread = threading.Thread(
            target=self._poll_download_size,
            args=(download_stop,),
            name="sandbox-setup-dlsize",
            daemon=True,
        )
        download_thread.start()
        try:
            # 与检测逻辑保持同名：检测哪个发行版就装哪个（HOST_SANDBOX_WSL_DISTRO 自定义场景）。
            # 发行版名来自环境变量，拼入 -Command 字符串前必须白名单校验，防命令注入。
            distro = _wsl_distro_name()
            if not re.fullmatch(r"[A-Za-z0-9._-]+", distro):
                self._finish(
                    PHASE_ERROR,
                    tr("sandbox.setup_bad_distro_name", distro=distro),
                    "script_failed",
                )
                return False
            # 非默认名使用独立安装目录，避免与既有发行版的 VHDX 目录冲突（--import 要求空目录）。
            # 用 -Command 包装并先设输出编码：-File 模式下若脚本解析失败（语法/编码问题），
            # 脚本内的 OutputEncoding 设置来不及生效，错误消息会按 GBK 输出而后端读成乱码。
            ps_parts = [f"& '{_SETUP_SCRIPT}'", "-DistroName", f"'{distro}'"]
            if distro != WSL_DEFAULT_SANDBOX_DISTRO:
                install_dir = Path.home() / ".astrion" / f"wsl-sandbox-{distro}"
                ps_parts += ["-InstallDir", f"'{install_dir}'"]
            ps_command = (
                "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                + " ".join(ps_parts)
            )
            argv = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_command,
            ]
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_wsl_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                m = _STEP_RE.match(line.strip())
                if m:
                    self._update_progress(
                        step_index=int(m.group(1)),
                        step_total=int(m.group(2)),
                        step_title=m.group(3).strip(),
                    )
                self._append_log(line)
            proc.wait()
            if proc.returncode != 0:
                self._finish(PHASE_ERROR, tr("sandbox.setup_script_failed"), "script_failed")
                return False
            return True
        except Exception as exc:
            self._finish(PHASE_ERROR, f"{type(exc).__name__}: {exc}", "script_failed")
            return False
        finally:
            download_stop.set()

    def _poll_download_size(self, stop: threading.Event) -> None:
        """安装阶段周期性 stat rootfs 临时文件大小，供前端展示下载量。"""
        temp_dir = os.environ.get("TEMP") or os.environ.get("TMP") or ""
        if not temp_dir:
            return
        target = Path(temp_dir) / _ROOTFS_TEMP_NAME
        while not stop.wait(1.0):
            with self._lock:
                if self._progress.get("step_index") != 3:
                    continue
            try:
                if target.exists():
                    self._update_progress(download_bytes=target.stat().st_size)
            except OSError:
                pass

    @staticmethod
    def _probe_rootfs_size() -> Optional[int]:
        """HEAD 请求 rootfs 下载地址估算总量（失败返回 None，前端退化为只显示已下载量）。"""
        try:
            req = urllib.request.Request(_ROOTFS_URL, method="HEAD")
            with urllib.request.urlopen(req, timeout=8) as resp:
                length = resp.headers.get("Content-Length")
                return int(length) if length else None
        except Exception:
            return None


sandbox_setup_manager = SandboxSetupManager()
