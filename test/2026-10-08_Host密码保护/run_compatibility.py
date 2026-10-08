"""Existing backend and sandbox API regressions under disposable runtime data."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    ("test/历史测试_日期不明", "test_server_refactor_smoke.py"),
    ("test/2026-10-08_Linux_host沙箱适配", "test_*.py"),
)
with tempfile.TemporaryDirectory(prefix="astrion-host-auth-compatibility-") as temporary:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ASTRION_", "AGENT_", "WEB_", "DATA_DIR", "LOGS_DIR",
                                  "USER_SPACE_DIR", "API_USER_SPACE_DIR", "TERMINAL_SANDBOX_MODE"))}
    env.update(ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=temporary,
               TERMINAL_SANDBOX_MODE="host", PYTHONDONTWRITEBYTECODE="1")
    for directory, pattern in TARGETS:
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover",
                                 "-s", directory, "-p", pattern, "-v"], cwd=ROOT, env=env)
        if result.returncode:
            raise SystemExit(result.returncode)
