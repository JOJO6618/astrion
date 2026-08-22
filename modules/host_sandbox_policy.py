from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List

from config import HOST_SANDBOX_MACOS_WRITABLE_PATHS, deploy_config_path


# macOS 默认拒绝读取的敏感路径（支持 ~/ 前缀）。
DEFAULT_MACOS_DENY_READ_PATHS = [
    "~/.ssh",
    "~/.aws",
    "~/.azure",
    "~/.gcp",
    "~/.google",
    "~/.kube",
    "~/.docker",
    "~/.gnupg",
    "~/.npmrc",
    "~/.netrc",
    "~/.pypirc",
    "~/.git-credentials",
    "~/.bash_history",
    "~/.zsh_history",
    "~/.psql_history",
    "~/.mysql_history",
    "~/.pgpass",
    "/Library/Keychains",
    "~/Library/Keychains",
]

# 默认按正则拒绝读取的敏感文件（可匹配文件系统中任意位置）。
DEFAULT_MACOS_DENY_READ_REGEXES = [
    r"^/.*\.env(\.[^/]*)?$",
]

# Windows（WSL2+bwrap 沙箱）默认掩蔽的敏感路径（支持 ~/ 前缀）。
# 目录用 tmpfs 整体掩蔽，文件用 /dev/null 掩蔽；仅掩蔽实际存在的路径。
DEFAULT_WINDOWS_DENY_READ_PATHS = [
    "~/.ssh",
    "~/.aws",
    "~/.azure",
    "~/.gcp",
    "~/.google",
    "~/.kube",
    "~/.docker",
    "~/.gnupg",
    "~/.npmrc",
    "~/.netrc",
    "~/.git-credentials",
]

_LOCK = threading.Lock()
# 部署级配置（机器特定可读写路径，会被运行时写回）→ ~/.astrion/<mode>/config
_POLICY_PATH = Path(deploy_config_path("host_sandbox_policy.json"))


def _default_policy() -> Dict:
    return {
        "macos_writable_paths": [],
        "macos_readable_extra_paths": [],
        "macos_deny_read_paths": list(DEFAULT_MACOS_DENY_READ_PATHS),
        "macos_deny_read_regexes": list(DEFAULT_MACOS_DENY_READ_REGEXES),
        "windows_deny_read_paths": list(DEFAULT_WINDOWS_DENY_READ_PATHS),
    }


def _ensure_file() -> None:
    if _POLICY_PATH.exists():
        return
    _POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _POLICY_PATH.write_text(
        json.dumps(_default_policy(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_policy() -> Dict:
    with _LOCK:
        _ensure_file()
        try:
            data = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = _default_policy()
        if not isinstance(data, dict):
            data = _default_policy()

        defaults = _default_policy()
        needs_save = False
        for key in defaults:
            if key not in data or not isinstance(data.get(key), list):
                data[key] = list(defaults[key])
                needs_save = True

        data["macos_writable_paths"] = [str(x).strip() for x in data["macos_writable_paths"] if str(x).strip()]
        data["macos_readable_extra_paths"] = [str(x).strip() for x in data["macos_readable_extra_paths"] if str(x).strip()]
        data["macos_deny_read_paths"] = [str(x).strip() for x in data["macos_deny_read_paths"] if str(x).strip()]
        data["macos_deny_read_regexes"] = [str(x).strip() for x in data["macos_deny_read_regexes"] if str(x).strip()]
        data["windows_deny_read_paths"] = [str(x).strip() for x in data["windows_deny_read_paths"] if str(x).strip()]

        if needs_save:
            try:
                _POLICY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return data


def save_policy(policy: Dict) -> Dict:
    payload = dict(policy or {})
    defaults = _default_policy()
    for key in defaults:
        items = payload.get(key)
        if not isinstance(items, list):
            items = list(defaults[key])
        payload[key] = [str(x).strip() for x in items if str(x).strip()]
    with _LOCK:
        _ensure_file()
        _POLICY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def get_macos_writable_paths() -> List[str]:
    file_items = load_policy().get("macos_writable_paths", [])
    merged: List[str] = []
    for raw in list(HOST_SANDBOX_MACOS_WRITABLE_PATHS or []) + list(file_items or []):
        val = str(raw).strip()
        if val and val not in merged:
            merged.append(val)
    return merged


def get_macos_readable_paths() -> List[str]:
    data = load_policy()
    readable_extra = data.get("macos_readable_extra_paths", [])
    merged: List[str] = []
    for raw in list(get_macos_writable_paths()) + list(readable_extra or []):
        val = str(raw).strip()
        if val and val not in merged:
            merged.append(val)
    return merged


def get_macos_deny_read_paths() -> List[str]:
    data = load_policy()
    paths = data.get("macos_deny_read_paths", [])
    merged: List[str] = []
    for raw in list(DEFAULT_MACOS_DENY_READ_PATHS) + list(paths or []):
        val = str(raw).strip()
        if val and val not in merged:
            merged.append(val)
    return merged


def get_macos_deny_read_regexes() -> List[str]:
    data = load_policy()
    patterns = data.get("macos_deny_read_regexes", [])
    merged: List[str] = []
    for raw in list(DEFAULT_MACOS_DENY_READ_REGEXES) + list(patterns or []):
        val = str(raw).strip()
        if val and val not in merged:
            merged.append(val)
    return merged


def get_windows_deny_read_paths() -> List[str]:
    """Windows（WSL2+bwrap）沙箱需要掩蔽的敏感路径列表。"""
    data = load_policy()
    paths = data.get("windows_deny_read_paths", [])
    merged: List[str] = []
    for raw in list(DEFAULT_WINDOWS_DENY_READ_PATHS) + list(paths or []):
        val = str(raw).strip()
        if val and val not in merged:
            merged.append(val)
    return merged
