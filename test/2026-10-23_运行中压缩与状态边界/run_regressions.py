"""Run offline boundaries and the older admission suite without real goal/workspace IO."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
os.environ["ASTRION_IGNORE_DOTENV"] = "1"
sys.path.insert(0, str(ROOT))


def run_suite(directory: str, pattern: str) -> bool:
    suite = unittest.TestLoader().discover(str(ROOT / directory), pattern=pattern)
    return unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()


if __name__ == "__main__":
    success = run_suite("test/2026-10-23_运行中压缩与状态边界", "test_boundaries.py")
    success = run_suite("test/2026-10-04_对话压缩", "test_compression_lifecycle.py") and success
    success = run_suite("test/2026-10-04_对话压缩", "test_runtime_service.py") and success
    success = run_suite("test/2026-10-07_子智能体权限与单次完全访问", "test_approval_authority.py") and success
    result = subprocess.run(["node", str(Path(__file__).with_name("frontend.cjs"))], cwd=ROOT)
    sys.exit(0 if success and result.returncode == 0 else 1)
