"""Run a compatibility fixture against the last tracked Python implementation."""
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "fb0a270323557410753e168216a38a7eeb2f6dc9"


def run(source):
    sys.path.insert(0, str(source))
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    if source := os.environ.get("ASTRION_LEGACY_TEST_SOURCE"):
        run(Path(source).resolve(strict=True))
    else:
        with tempfile.TemporaryDirectory(prefix="astrion-legacy-source-") as directory:
            archive = subprocess.check_output(
                ["git", "archive", "--format=zip", BASELINE, "--", "utils", "modules", "server", "core", "config", "prompts"],
                cwd=ROOT,
            )
            with zipfile.ZipFile(io.BytesIO(archive)) as files:
                files.extractall(directory)
            run(Path(directory))
