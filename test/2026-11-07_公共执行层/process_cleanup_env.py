"""进程/管道收口测试的共用夹具（2026-11-07）。

导入顺序硬性要求
----------------
必须在导入 ``config`` / ``modules`` 之前 ``import process_cleanup_env``：
本模块在导入时就创建临时运行根并写入隔离环境变量（不读仓库 ``.env``，
清掉宿主 ``HOST_SANDBOX_*`` 避免借用真实机器路径授权）。测试文件只需把它
放在其它项目模块 import 之前即可。

只放跨文件复用的东西，不放任何 ``TestCase``，避免 unittest discover 误收。
"""
from __future__ import annotations

import gc
import os
import sys
import tempfile
import time
import warnings
from contextlib import contextmanager
from pathlib import Path

TMP_ROOT = Path(tempfile.mkdtemp(prefix="astrion_process_cleanup_"))

os.environ["ASTRION_IGNORE_DOTENV"] = "1"
os.environ["ASTRION_DATA_ROOT"] = str(TMP_ROOT / "root")
os.environ["DATA_DIR"] = str(TMP_ROOT / "root" / "data")
os.environ["LOGS_DIR"] = str(TMP_ROOT / "root" / "logs")
os.environ["DEPLOY_CONFIG_DIR"] = str(TMP_ROOT / "root" / "config")
os.environ["TERMINAL_SANDBOX_MODE"] = "host"
for _key in list(os.environ):
    if _key.startswith("HOST_SANDBOX_") and _key != "HOST_SANDBOX_ENABLED":
        os.environ.pop(_key, None)
os.environ.setdefault("HOST_SANDBOX_ENABLED", "0")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class TerminalOpsStub:
    """BackgroundCommandManager 需要的最小 terminal_ops 契约。"""

    def __init__(self, project_path: Path) -> None:
        self.project_path = str(project_path)
        self.container_session = None
        self.host_execution_mode = "direct"  # 直连执行：不套沙箱，便于跑真实短进程
        self._python_env = {}

    def _resolve_active_container_session(self):
        return None

    def _validate_command(self, command):
        return True, ""


def make_bg_record(command_id: str, command: str = "echo x", timeout: int = 30) -> dict:
    """构造一条最小的 running 后台记录（用于直接驱动 run 线程）。"""
    now = time.time()
    return {
        "command_id": command_id,
        "conversation_id": None,
        "status": "running",
        "command": command,
        "timeout": timeout,
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
        "stdout_chunks": [],
        "stderr_chunks": [],
        "truncated": False,
        "result": None,
        "notified": False,
        "claimed_by_sleep": False,
        "pid": None,
    }


@contextmanager
def resource_warnings():
    """收集 ResourceWarning，并在退出前强制一次 GC 让泄漏句柄暴露。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield caught
        gc.collect()


def unclosed_file_warnings(caught) -> list:
    """从 catch_warnings 结果中挑出 "unclosed file" 类 ResourceWarning。"""
    return [
        str(item.message)
        for item in caught
        if issubclass(item.category, ResourceWarning) and "unclosed" in str(item.message)
    ]


__all__ = [
    "TMP_ROOT",
    "REPO_ROOT",
    "TerminalOpsStub",
    "make_bg_record",
    "resource_warnings",
    "unclosed_file_warnings",
]
