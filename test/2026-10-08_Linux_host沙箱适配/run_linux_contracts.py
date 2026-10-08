"""Run copied helper contracts without importing any production backend/data."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
suite = unittest.TestLoader().discover(str(Path(__file__).parent), pattern="test_*.py")
selected = unittest.TestSuite()
omitted = []


def add(tests):
    for test in tests:
        if isinstance(test, unittest.TestSuite):
            add(test)
        elif test.id().startswith("test_setup_api.") or test.id().endswith((
                "test_application_plans_encode_trusted_scope_and_fail_closed",
                "test_non_linux_import_does_not_require_unix_transport")):
            # These tests require the app's dependency graph. They run locally;
            # no deployed backend source is imported by this remote harness.
            omitted.append(test.id())
        else:
            selected.addTest(test)


add(suite)
result = unittest.TextTestRunner(verbosity=2).run(selected)
report = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
          "skipped": len(result.skipped), "omitted_app_import_tests": omitted, "ok": result.wasSuccessful()}
(Path(__file__).parent / "linux_contract_results.json").write_text(json.dumps(report, indent=2))
sys.exit(0 if result.wasSuccessful() else 1)
