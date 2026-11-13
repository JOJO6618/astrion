"""Remove only the incomplete, isolated installation made by this test."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[2]
target = root / "output/windows-electron/installer-test/installed-app"
if target.is_symlink() or target.is_junction():
    raise RuntimeError("Refusing linked test installation")
if target.exists():
    shutil.rmtree("\\\\?\\" + str(target.resolve()))
print("Removed only the failed installer-test/installed-app fixture", flush=True)
