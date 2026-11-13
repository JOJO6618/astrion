"""Record real build prerequisites; do not mistake source checks for Windows execution."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/windows-electron/preflight.json"


def main() -> None:
    tools = {name: shutil.which(name) for name in (
        "node", "npm", "python3", "python", "cargo", "rustc", "wine", "makensis",
        "powershell.exe", "cmd.exe",
    )}
    checks = []
    for relative in ("src/backend.js", "src/window.js", "src/browser/index.js",
                     "src/browser/popout-window.js", "src/quick/controller.js"):
        result = subprocess.run([tools["node"], "--check", str(ROOT / "desktop-electron" / relative)],
                                capture_output=True, text=True, check=False)
        checks.append({"file": relative, "success": result.returncode == 0,
                       "stderr": result.stderr[-2000:]})
    report = {
        "platform": platform.platform(), "tools": tools,
        "electron_dependencies_present": (ROOT / "desktop-electron/node_modules/electron").exists(),
        "embedded_windows_python_present": (ROOT / "desktop/src-tauri/runtime/python/python.exe").exists(),
        "source_syntax": checks,
        "windows_execution_verified": False,
        "blockers": ["Current execution environment cannot execute Windows programs.",
                     "Electron dependencies/engine are not installed in the workspace.",
                     "Network policy only permits localhost; full access is prohibited by goal mode."],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
