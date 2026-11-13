"""Report only synthetic package path lengths; no user data is inspected."""
from pathlib import Path
import json

root = Path(__file__).resolve().parents[2]
payload = root / "output/windows-electron/installer-test/0.10.0/win-unpacked"
install = root / "output/windows-electron/installer-test/installed-app"
paths = sorted(((len(str(install / p.relative_to(payload))), str(p.relative_to(payload)))
                for p in payload.rglob("*") if p.is_file()), reverse=True)
print(json.dumps({"max": paths[:8], "over260": sum(length >= 260 for length, _ in paths)}, indent=2))
