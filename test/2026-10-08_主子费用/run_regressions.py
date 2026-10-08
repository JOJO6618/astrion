"""Run new regressions and backend smoke tests with isolated runtime configuration."""
from pathlib import Path
import subprocess
import sys
import unittest

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
sys.path.insert(0, str(SUITE))
import test_costs  # Configures temporary data paths before project imports.


def main() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_costs)
    suite.addTests(unittest.defaultTestLoader.discover(
        str(ROOT / 'test/历史测试_日期不明'), pattern='test_server_refactor_smoke.py'))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    subprocess.run(['/opt/homebrew/bin/node' if Path('/opt/homebrew/bin/node').exists() else 'node',
                    str(SUITE / 'avatar_tests.cjs')], cwd=ROOT, check=True)


if __name__ == '__main__':
    try:
        main()
    finally:
        test_costs._RUNTIME.cleanup()
