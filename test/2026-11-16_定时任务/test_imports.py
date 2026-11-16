"""导入隔离：clock/schema/store 不应依赖项目 config 或运行时凭据。"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest

import schedule_fixtures as fx


class ImportIsolationTest(unittest.TestCase):
    def test_modules_import_without_project_config(self):
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(fx.ROOT)!r})\n"
            "import modules.scheduled_tasks.clock as clock\n"
            "import modules.scheduled_tasks.schema as schema\n"
            "import modules.scheduled_tasks.store as store\n"
            "loaded = sorted(name for name in sys.modules if name == 'config' or name.startswith('config.'))\n"
            "assert not loaded, loaded\n"
            "assert clock.instant('2026-01-01T00:00:00Z').tzinfo is not None\n"
            "print('import-ok')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("import-ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
