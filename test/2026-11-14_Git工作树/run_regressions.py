"""Run offline regression groups with isolated runtime data and separate logs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {
    "worktree": ["-m", "unittest", "discover", "-s", "test/2026-11-14_Git工作树", "-p", "test_*.py"],
    "execution": ["-m", "unittest", "discover", "-s", "test/2026-11-07_公共执行层", "-p", "test_*.py"],
    "permissions": ["-m", "unittest", "discover", "-s", "test/2026-10-07_子智能体权限与单次完全访问", "-p", "test_*.py"],
    "new-conversation": ["test/2026-10-16_新对话初始化/backend.py"],
    "system": ["-m", "unittest", "discover", "-s", "test/2026-11-12_系统提示词统一冻结", "-p", "test_*.py"],
    "tool-loading": ["test/历史测试_日期不明/test_tool_loading.py"],
    "children": ["-m", "unittest", "discover", "-s", "test/2026-10-22_子智能体统一", "-p", "test_*.py"],
    "snapshot": ["test/2026-10-07_运行对话快照/run_regressions.py"],
    "server-smoke": ["-m", "unittest", "discover", "-s", "test/历史测试_日期不明", "-p", "test_server_refactor_smoke.py"],
}


def run_group(name: str) -> int:
    logs = ROOT / "_experiments/worktree-implementation"
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="regression-", dir=logs) as temporary:
        env = dict(os.environ)
        env.update(ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=temporary,
                   TERMINAL_SANDBOX_MODE="host", WEB_SECRET_KEY="isolated-worktree-test-only",
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   PYTHONPATH=os.pathsep.join(filter(None, (str(ROOT), env.get('PYTHONPATH')))))
        for key, leaf in (("DATA_DIR", "data"), ("LOGS_DIR", "logs"),
                          ("DEPLOY_CONFIG_DIR", "deploy"), ("USER_SPACE_DIR", "users"),
                          ("API_USER_SPACE_DIR", "api")):
            env[key] = str(Path(temporary) / leaf)
        with (logs / (name + ".log")).open("w", encoding="utf-8") as output:
            result = subprocess.run([sys.executable, "-B", *GROUPS[name]], cwd=ROOT,
                                    env=env, stdout=output, stderr=subprocess.STDOUT, check=False)
    print(f"{name}: exit={result.returncode}, log={logs / (name + '.log')}", flush=True)
    return result.returncode


if __name__ == "__main__":
    names = sys.argv[1:] or ["worktree"]
    if any(name not in GROUPS for name in names):
        raise SystemExit("Available groups: " + ", ".join(GROUPS))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run_group, names))
    raise SystemExit(1 if any(results) else 0)
