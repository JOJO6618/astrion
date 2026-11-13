"""Validate/extract only the archive left by our isolated NSIS test."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile

root = Path(__file__).resolve().parents[2]
output = root / "output/windows-electron/archive-probe"
output.mkdir(parents=True, exist_ok=True)
marker = root / "desktop-electron/build/stop-installed-backends.ps1"
expected = hashlib.sha256(marker.read_bytes()).digest()
candidates = []
for directory in Path(os.environ["TEMP"]).glob("ns*.tmp"):
    owned = directory / "astrion-stop-backends.ps1"
    archive = directory / "app-64.zip"
    if owned.is_file() and archive.is_file() and hashlib.sha256(owned.read_bytes()).digest() == expected:
        candidates.append(archive)
if not candidates:
    raise RuntimeError("No owned NSIS archive found")
archive = max(candidates, key=lambda item: item.stat().st_mtime)
copy = output / "payload.zip"
shutil.copyfile(archive, copy)
with zipfile.ZipFile(copy) as zipped:
    print(json.dumps({"files": len(zipped.infolist()), "unpacked": sum(item.file_size for item in zipped.infolist()), "crc_error": zipped.testzip()}), flush=True)
started = time.monotonic()
result = subprocess.run([str(root / "desktop-electron/node_modules/7zip-bin/win/x64/7za.exe"), "x", "-y", "-bd", "-bb0", "-o" + str(output / "extracted"), str(copy)], capture_output=True, timeout=300)
evidence = {"exit": result.returncode, "seconds": time.monotonic() - started, "output": result.stdout.decode("utf-8", "replace"), "error": result.stderr.decode("utf-8", "replace")}
(output / "results.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
print(json.dumps(evidence, indent=2), flush=True)
