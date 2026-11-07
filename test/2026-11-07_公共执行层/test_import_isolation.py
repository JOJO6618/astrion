"""执行层 import 隔离：全新解释器里只装配 resources/local，不拉 Agent 栈。

为什么单独成文件、且必须独立解释器：
- 同目录的其他测试（如 test_server_routing）会先 import Flask；在本进程查
  ``sys.modules`` 既可能假失败，也可能因执行顺序不同而假通过。
- 子进程环境**自己构造**，不从共享进程 ``os.environ`` 照抄：其他测试模块会改写
  ``ASTRION_DATA_ROOT`` / ``DATA_DIR``，照抄会让 data_dir 断言与本次临时根不匹配
  （全目录 discover 时实际踩到过）。

判定标准：一个干净 python 进程里 ``import resources/local`` + 装配资源后，
Flask / werkzeug / jinja2 / APIClient / MainTerminal / server 包均未进入
``sys.modules``，且 config 只读到我们为它指定的临时根。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if __package__ in (None, ""):
    _REPO = Path(__file__).resolve().parents[2]
else:  # pragma: no cover - discover 入口
    _REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

#: 子进程只继承这些与定位解释器/系统有关的最小变量，其余全部显式指定。
_ENV_WHITELIST = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "PYTHONHOME",
    "VIRTUAL_ENV",
)

_CHILD_SCRIPT = """
import json, os, sys
from pathlib import Path

# 只 import 执行层（顺序即真实装配顺序）
from modules.execution_plane.resources import create_local_resources
from modules.execution_plane.local import LocalExecutionBackend
from modules.execution_plane import get_execution_backend

root = Path(os.environ["ASTRION_DATA_ROOT"])
ws = root / "ws"
ws.mkdir(parents=True, exist_ok=True)

resources = create_local_resources(project_path=str(ws), data_dir=str(root / "data"))
backend = LocalExecutionBackend(resources)
env = backend.get_environment()
resolved = get_execution_backend(resources)

forbidden_prefixes = (
    "flask",
    "werkzeug",
    "jinja2",
    "utils.api_client",
    "core.main_terminal",
    "server",
)
violations = sorted(
    name for name in sys.modules
    if any(name == p or name.startswith(p + ".") for p in forbidden_prefixes)
)
import config  # 配置模块允许：它只读环境变量
print(json.dumps({
    "violations": violations,
    "data_dir": str(config.DATA_DIR),
    "backend": env.get("backend"),
    "is_local": bool(env.get("is_local")),
    "same_backend": resolved is backend or getattr(resolved, "backend_name", None) == "local",
    "capabilities": env.get("capabilities"),
}))
"""


class ImportIsolationTest(unittest.TestCase):
    def test_execution_plane_imports_without_agent_stack(self):
        temporary = tempfile.TemporaryDirectory(prefix="astrion_import_isolation_")
        self.addCleanup(temporary.cleanup)
        child_root = Path(temporary.name).resolve()
        env = {key: os.environ[key] for key in _ENV_WHITELIST if key in os.environ}
        env.update(
            {
                "PYTHONPATH": str(_REPO),
                "PYTHONDONTWRITEBYTECODE": "1",
                "ASTRION_IGNORE_DOTENV": "1",
                "ASTRION_DATA_ROOT": str(child_root / "root"),
                "DATA_DIR": str(child_root / "root" / "data"),
                "LOGS_DIR": str(child_root / "root" / "logs"),
                "DEPLOY_CONFIG_DIR": str(child_root / "root" / "config"),
                "TERMINAL_SANDBOX_MODE": "host",
            }
        )

        proc = subprocess.run(
            [sys.executable, "-B", "-c", _CHILD_SCRIPT],
            cwd=str(_REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"fresh-interpreter 装配失败\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(
            payload["violations"],
            [],
            f"执行层单独装配时拉入了非执行栈模块: {payload['violations']}",
        )
        self.assertEqual(payload["backend"], "local")
        self.assertTrue(payload["is_local"])
        self.assertTrue(payload["same_backend"])
        # 子进程 config 必须落在**本次子进程**的临时根内（不受父进程 env 影响）
        self.assertEqual(
            Path(payload["data_dir"]).resolve(),
            child_root / "root" / "data",
            f"config.DATA_DIR 未落在指定子进程数据目录: {payload['data_dir']}",
        )
        self.assertTrue(payload["capabilities"]["files"])
        self.assertTrue(payload["capabilities"]["binary_reads"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
