"""Offline regressions; never load real configuration or launch a real browser."""

import os
from pathlib import Path
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(dir=HERE, prefix="runtime-") as runtime:
        os.environ.update(
            ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=runtime,
            DATA_DIR=str(Path(runtime) / "data"),
            DEPLOY_CONFIG_DIR=str(Path(runtime) / "config"),
            LOGS_DIR=str(Path(runtime) / "logs"),
        )
        sys.path.insert(0, str(ROOT))
        suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
