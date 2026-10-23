"""Run isolated desktop navigation tests without booting the application."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).resolve().parent


def main() -> None:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", ASTRION_IGNORE_DOTENV="1")
    commands = [
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(SUITE), "-p", "test_*.py", "-v"],
        ["node", str(SUITE / "frontend.cjs")],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
