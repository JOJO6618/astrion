"""Offline runtime: no real credentials, conversations, servers or model requests."""
from pathlib import Path
import atexit
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_RUNTIME = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="isolated-")
RUNTIME = Path(_RUNTIME.name)
atexit.register(_RUNTIME.cleanup)
os.environ.update(
    ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=str(RUNTIME),
    DATA_DIR=str(RUNTIME / "data"), LOGS_DIR=str(RUNTIME / "logs"),
    DEPLOY_CONFIG_DIR=str(RUNTIME / "deploy"), TERMINAL_SANDBOX_MODE="host",
    USER_SPACE_DIR=str(RUNTIME / "users"), API_USER_SPACE_DIR=str(RUNTIME / "api"),
)
