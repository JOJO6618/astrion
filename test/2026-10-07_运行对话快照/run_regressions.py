"""Run loading regressions with temporary runtime data, without starting services.

Use a Python interpreter with the existing project dependencies installed:
/opt/homebrew/bin/python3.11 -B test/2026-10-07_运行对话快照/run_regressions.py
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).resolve().parent


def run(command: list[str], environment: dict[str, str]) -> None:
    print("Running " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def main() -> None:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for name in ("projector_tests.py", "snapshot_tests.py"):
        run([sys.executable, "-B", str(SUITE / name)], environment)
    for name in (
        "conversation_session_tests.cjs", "task_polling_tests.cjs",
        "message_identity_tests.cjs", "auxiliary_ownership_tests.cjs",
        "entry_ownership_tests.cjs",
    ):
        run(["node", str(SUITE / name)], environment)
    with tempfile.TemporaryDirectory(prefix="smoke-runtime-", dir=SUITE) as directory:
        runtime = Path(directory)
        isolated = dict(environment, ASTRION_IGNORE_DOTENV="1",
            ASTRION_DATA_ROOT=str(runtime), DATA_DIR=str(runtime / "host/data"),
            LOGS_DIR=str(runtime / "host/logs"), USER_SPACE_DIR=str(runtime / "host/users"),
            API_USER_SPACE_DIR=str(runtime / "host/api/users"),
            DEPLOY_CONFIG_DIR=str(runtime / "config"))
        # Exact discovery directory avoids unrelated repository files and secrets.
        # Executes the same unittest suite also exposed through pytest.
        run([sys.executable, "-B", "-m", "unittest", "discover", "-s",
             "test/历史测试_日期不明", "-p", "test_server_refactor_smoke.py", "-v"], isolated)
    print("All conversation loading regressions passed.")


if __name__ == "__main__":
    main()
