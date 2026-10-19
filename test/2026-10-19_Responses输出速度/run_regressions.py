"""Run focused speed tests plus existing usage/protocol compatibility checks."""
from pathlib import Path
import runpy
import sys
import unittest

SUITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SUITE))
import support  # Isolate runtime paths and disable dotenv before project imports.


def main():
    suite = unittest.defaultTestLoader.discover(str(SUITE), pattern="test_*.py")
    suite.addTests(unittest.defaultTestLoader.discover(
        str(support.ROOT / "test/历史测试_日期不明"),
        pattern="test_token_usage_extractor.py", top_level_dir=str(support.ROOT / "test/历史测试_日期不明"),
    ))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    runpy.run_path(str(support.ROOT / "test/历史测试_日期不明/test_codex_translate.py"), run_name="__main__")


if __name__ == "__main__":
    main()
