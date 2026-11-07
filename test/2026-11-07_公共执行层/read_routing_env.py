"""测试运行态隔离（读取侧/环境侧测试共用）。

必须在导入 ``config`` / ``modules`` / ``utils.context_manager`` 之前调用
``prepare_isolated_runtime()``：它创建临时数据目录（纯临时目录，绝不读真实
``.env``/``settings.json``），并卸载 ``ASTRION_TRUSTED_PROXIES``。
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


def prepare_isolated_runtime(prefix: str = "astrion_read_routing_") -> Path:
    """创建临时运行目录、写入隔离环境变量，返回该根目录。"""
    home = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    os.environ["ASTRION_IGNORE_DOTENV"] = "1"
    os.environ["ASTRION_DATA_ROOT"] = str(home / "runtime")
    os.environ["DATA_DIR"] = str(home / "runtime" / "data")
    os.environ["LOGS_DIR"] = str(home / "runtime" / "logs")
    os.environ["USER_SPACE_DIR"] = str(home / "runtime" / "users")
    os.environ["API_USER_SPACE_DIR"] = str(home / "runtime" / "api_users")
    os.environ["TERMINAL_SANDBOX_MODE"] = "host"
    os.environ["WEB_SECRET_KEY"] = "test-only-not-a-real-secret"
    os.environ.pop("ASTRION_TRUSTED_PROXIES", None)
    atexit.register(lambda: shutil.rmtree(home, ignore_errors=True))
    return home
