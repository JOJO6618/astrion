"""Extract Windows build tools without the archive's unused macOS symlinks.

This does not disable resource editing or signature verification, and does not
change symlink privileges, Electron builder code, or build configuration.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("This cache preparation requires Windows")
    cache = Path(os.environ["LOCALAPPDATA"]) / "electron-builder/Cache/winCodeSign"
    archives = sorted(cache.glob("*.7z"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not archives:
        raise FileNotFoundError("No builder-downloaded winCodeSign archive exists")
    destination = cache / "winCodeSign-2.6.0"
    sevenzip = ROOT / "desktop-electron/node_modules/7zip-bin/win/x64/7za.exe"
    subprocess.run([str(sevenzip), "x", "-y", "-bd", str(archives[0]),
                    f"-o{destination}", "-xr!darwin"], check=True, timeout=60,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    for name in ("rcedit-x64.exe", "rcedit-ia32.exe"):
        with (destination / name).open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise RuntimeError(f"Invalid Windows PE tool: {name}")
    print("Windows PE resource tools ready; unused Darwin symlinks were not extracted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
