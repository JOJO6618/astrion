"""Host-wide CrossPet export preference, independent of workspace and pet setup."""
from __future__ import annotations

import json
from pathlib import Path
import threading

from utils.atomic_io import atomic_write_json

SETTINGS_LOCK = threading.RLock()


def control_path() -> Path:
    from config.paths import deploy_config_path
    return Path(deploy_config_path("crosspet.json"))


def read_enabled(path: Path) -> bool:
    """Missing means disabled; malformed settings must not silently enable export."""
    with SETTINGS_LOCK:
        if path.is_symlink():
            raise ValueError("CrossPet preference must not be a symbolic link")
        if not path.exists():
            return False
        if not path.is_file() or path.stat().st_size > 8192:
            raise ValueError("Invalid CrossPet preference file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1 or type(value.get("enabled")) is not bool:
            raise ValueError("Invalid CrossPet preference")
        return value["enabled"]


def save_enabled(path: Path, enabled: bool) -> None:
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    with SETTINGS_LOCK:
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("CrossPet preference must not be a symbolic link")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": 1, "enabled": enabled})
