"""Run all goal regressions against temporary data and synthetic providers."""
import atexit
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="isolated-")
atexit.register(TEMP.cleanup)
RUNTIME = Path(TEMP.name)
os.environ.update(ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=str(RUNTIME),
                  DATA_DIR=str(RUNTIME / "data"), LOGS_DIR=str(RUNTIME / "logs"),
                  DEPLOY_CONFIG_DIR=str(RUNTIME / "deploy"), TERMINAL_SANDBOX_MODE="host",
                  USER_SPACE_DIR=str(RUNTIME / "users"), API_USER_SPACE_DIR=str(RUNTIME / "api"))
