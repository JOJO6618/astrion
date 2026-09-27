from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional

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

# RLock：save_workspace_entry 等函数会在持锁期间调用 load_policy/save_policy，
# 普通 Lock 会自死锁（2026-09-27 工作区级授权引入后实测触发）
_LOCK = threading.RLock()
# 部署级配置（机器特定可读写路径，会被运行时写回）→ ~/.astrion/<mode>/config
_POLICY_PATH = Path(deploy_config_path("host_sandbox_policy.json"))


def _default_policy() -> Dict:
    return {
        "macos_writable_paths": [],
        "macos_readable_extra_paths": [],
        "macos_deny_read_paths": list(DEFAULT_MACOS_DENY_READ_PATHS),
        "macos_deny_read_regexes": list(DEFAULT_MACOS_DENY_READ_REGEXES),
        "windows_deny_read_paths": list(DEFAULT_WINDOWS_DENY_READ_PATHS),
        # 工作区级授权（2026-09-27 新增）：key = 工作区根目录解析后的绝对路径，
        # value = {"writable": [...], "readable_extra": [...]}。
        # 语义为「只增不减」：工作区级只能在全球授权基础上追加，deny 清单仍只有全球级。
        "workspaces": {},
    }


def _normalize_workspace_key(workspace_path: str) -> str:
    """工作区级授权的 key：工作区根目录 expanduser+resolve 后的绝对路径字符串。"""
    return str(Path(str(workspace_path)).expanduser().resolve())


def _sanitize_workspace_entry(value) -> Dict[str, List[str]]:
    if not isinstance(value, dict):
        return {"writable": [], "readable_extra": []}
    entry = {"writable": [], "readable_extra": []}
    for key in ("writable", "readable_extra"):
        items = value.get(key)
        if isinstance(items, list):
            entry[key] = [str(x).strip() for x in items if str(x).strip()]
    return entry


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
            if key == "workspaces":
                continue
            if key not in data or not isinstance(data.get(key), list):
                data[key] = list(defaults[key])
                needs_save = True

        workspaces = data.get("workspaces")
        if not isinstance(workspaces, dict):
            workspaces = {}
            needs_save = True
        normalized_workspaces: Dict[str, Dict[str, List[str]]] = {}
        for ws_key, ws_value in workspaces.items():
            ws_key_str = str(ws_key).strip()
            if not ws_key_str:
                needs_save = True
                continue
            normalized = _sanitize_workspace_entry(ws_value)
            if not normalized["writable"] and not normalized["readable_extra"]:
                # 空条目不留存（视为未授权）
                needs_save = True
                continue
            if normalized != ws_value:
                needs_save = True
            normalized_workspaces[ws_key_str] = normalized
        if normalized_workspaces != workspaces:
            needs_save = True
        data["workspaces"] = normalized_workspaces
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
        if key == "workspaces":
            continue
        items = payload.get(key)
        if not isinstance(items, list):
            items = list(defaults[key])
        payload[key] = [str(x).strip() for x in items if str(x).strip()]
    # workspaces 不在 defaults 列表键内，需显式保留（否则全局保存会丢工作区级授权）
    workspaces = payload.get("workspaces")
    if not isinstance(workspaces, dict):
        workspaces = load_policy().get("workspaces", {})
    cleaned: Dict[str, Dict[str, List[str]]] = {}
    for ws_key, ws_value in workspaces.items():
        ws_key_str = str(ws_key).strip()
        if not ws_key_str:
            continue
        entry = _sanitize_workspace_entry(ws_value)
        if entry["writable"] or entry["readable_extra"]:
            cleaned[ws_key_str] = entry
    payload["workspaces"] = cleaned
    with _LOCK:
        _ensure_file()
        _POLICY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def get_workspace_entry(workspace_path: str) -> Dict[str, List[str]]:
    """读取指定工作区的工作区级授权（不存在时返回空条目）。"""
    key = _normalize_workspace_key(workspace_path)
    workspaces = load_policy().get("workspaces", {})
    return _sanitize_workspace_entry(workspaces.get(key))


def save_workspace_entry(workspace_path: str, writable: List[str], readable_extra: List[str]) -> Dict[str, List[str]]:
    """保存指定工作区的工作区级授权；两个列表都为空时删除该工作区条目。"""
    key = _normalize_workspace_key(workspace_path)
    entry = _sanitize_workspace_entry({"writable": writable, "readable_extra": readable_extra})
    with _LOCK:
        policy = load_policy()
        workspaces = dict(policy.get("workspaces", {}))
        if entry["writable"] or entry["readable_extra"]:
            workspaces[key] = entry
        else:
            workspaces.pop(key, None)
        policy["workspaces"] = workspaces
        save_policy(policy)
    return entry


def get_macos_writable_paths(workspace_path: Optional[str] = None) -> List[str]:
    """沙箱可写路径全集 = 部署环境变量 + policy 文件（合并去重）。

    仅两个来源（2026-08-30 收敛）：真·环境变量
    HOST_SANDBOX_MACOS_WRITABLE_PATHS（部署通道）+ host_sandbox_policy.json
    的 macos_writable_paths（前端「路径授权」UI）。settings.json 的
    terminal.macos_writable_paths 已不再是来源（config/__init__.py 映射已移除）。

    2026-09-27 起支持工作区级授权：传入 workspace_path 时，额外并入该工作区
    的追加授权（只增不减）。
    """
    file_items = load_policy().get("macos_writable_paths", [])
    merged: List[str] = []
    for raw in list(HOST_SANDBOX_MACOS_WRITABLE_PATHS or []) + list(file_items or []):
        val = str(raw).strip()
        if val and val not in merged:
            merged.append(val)
    if workspace_path:
        for raw in get_workspace_entry(workspace_path).get("writable", []):
            if raw and raw not in merged:
                merged.append(raw)
    return merged


def get_macos_readable_paths(workspace_path: Optional[str] = None) -> List[str]:
    data = load_policy()
    readable_extra = data.get("macos_readable_extra_paths", [])
    merged: List[str] = []
    for raw in list(get_macos_writable_paths(workspace_path)) + list(readable_extra or []):
        val = str(raw).strip()
        if val and val not in merged:
            merged.append(val)
    if workspace_path:
        for raw in get_workspace_entry(workspace_path).get("readable_extra", []):
            if raw and raw not in merged:
                merged.append(raw)
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
