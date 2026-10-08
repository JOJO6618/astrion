"""Run the compatibility batch without real runtime data or credentials."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent

with tempfile.TemporaryDirectory(prefix="astrion-scrypt-compatibility-") as temporary:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ASTRION_", "AGENT_", "WEB_", "DATA_DIR", "LOGS_DIR",
                                  "USER_SPACE_DIR", "API_USER_SPACE_DIR", "TERMINAL_SANDBOX_MODE"))}
    env.update(ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=temporary,
               TERMINAL_SANDBOX_MODE="host", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover",
                             "-s", str(TESTS), "-p", "test_*.py", "-v"], cwd=ROOT, env=env)
    raise SystemExit(result.returncode)
