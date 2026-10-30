"""Run offline regressions without loading real credentials or conversations."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

with TemporaryDirectory(dir=HERE, prefix="isolated-") as temporary:
    runtime = Path(temporary)
    os.environ.update(
        ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=str(runtime),
        DATA_DIR=str(runtime / "data"), LOGS_DIR=str(runtime / "logs"),
        DEPLOY_CONFIG_DIR=str(runtime / "deploy"), USER_SPACE_DIR=str(runtime / "users"),
        API_USER_SPACE_DIR=str(runtime / "api"), TERMINAL_SANDBOX_MODE="host",
        AGENT_API_DUMP_ENABLED="0",
    )
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2, buffer=True).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
