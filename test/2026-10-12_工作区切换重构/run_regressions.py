"""Run the isolated workspace navigation suites without starting a server."""
from pathlib import Path
import os
import subprocess
import sys

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]


def main() -> None:
    environment = {**os.environ, "ASTRION_IGNORE_DOTENV": "1"}
    for name in ("backend.py", "layout.py", "integration.py"):
        print(f"Running {name}", flush=True)
        subprocess.run([sys.executable, "-B", str(SUITE / name)], cwd=ROOT,
                       env=environment, check=True)
    for name in ("frontend.cjs", "startup.cjs"):
        print(f"Running {name}", flush=True)
        subprocess.run(["node", str(SUITE / name)], cwd=ROOT,
                       env=environment, check=True)
    print("All workspace navigation regression suites passed.", flush=True)


if __name__ == "__main__":
    main()
