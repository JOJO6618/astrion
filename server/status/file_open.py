from __future__ import annotations
from server.status import status_bp
from server.status.base import _is_host_mode_request
from server.status.git import _run_project_git
import time
import re
import os
import json
import shutil
import subprocess
import sys
import tempfile
import plistlib
from pathlib import Path
from flask import Blueprint, jsonify, request, send_file, session

from server.auth_helpers import api_login_required, resolve_admin_policy
from server.context import with_terminal, attach_user_broadcast
from server.state import (
    PROJECT_STORAGE_CACHE,
    PROJECT_STORAGE_CACHE_TTL_SECONDS,
    PROJECT_MAX_STORAGE_MB,
    container_manager,
    user_manager,
)
from config import AGENT_VERSION, TERMINAL_SANDBOX_MODE
from modules.host_workspace_manager import (
    create_host_workspace,
    delete_host_workspace,
    load_host_workspace_catalog,
    rename_host_workspace,
    resolve_host_workspace,
    set_default_host_workspace,
)
from utils.host_workspace_debug import write_host_workspace_debug
from server.utils_common import log_conn_diag
import server.state as state
def _open_path_in_file_manager(path: Path) -> bool:
    target = path.expanduser().resolve()
    if not target.exists():
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
            return True
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.Popen([opener, str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        return False
    return False

def _resolve_project_git_file(workspace, rel_path: str) -> tuple[Path | None, str]:
    project_path = Path(getattr(workspace, "project_path", "") or "").expanduser().resolve()
    ok, root_text = _run_project_git(project_path, ["rev-parse", "--show-toplevel"])
    repo_root = Path(root_text).expanduser().resolve() if ok and root_text else project_path
    try:
        target = (repo_root / rel_path).expanduser().resolve()
        target.relative_to(repo_root)
    except Exception:
        return None, "文件路径无效"
    if not target.exists() or not target.is_file():
        return None, "文件不存在"
    return target, ""

def _mac_open_app_candidates(file_path: Path | None = None) -> list[dict]:
    app_paths: list[Path] = []
    default_app_path = ""
    if file_path is not None:
        try:
            from AppKit import NSWorkspace
            from Foundation import NSURL
            file_url = NSURL.fileURLWithPath_(str(file_path))
            workspace = NSWorkspace.sharedWorkspace()
            urls = workspace.URLsForApplicationsToOpenURL_(file_url) or []
            app_paths.extend(Path(str(url.path())) for url in urls if url and url.path())
            default_url = workspace.URLForApplicationToOpenURL_(file_url)
            if default_url and default_url.path():
                default_app_path = str(Path(str(default_url.path())).expanduser().absolute())
        except Exception:
            app_paths = []
    if not app_paths:
        # 只在 LaunchServices 不可用时做很小的兜底；正常路径必须使用系统针对该文件的结果。
        for base in ["/System/Applications/TextEdit.app", "/Applications/Xcode.app", "/Applications/Cursor.app"]:
            path = Path(base).expanduser()
            if path.exists():
                app_paths.append(path)
    seen = set()
    apps: list[dict] = []
    for path in app_paths:
        try:
            visible_path = path.expanduser().absolute()
        except Exception:
            continue
        path_text = str(visible_path)
        if (
            path_text in seen
            or "__pycache__" in path_text
            or "/Library/Application Support/" in path_text
            or "/System/Library/PrivateFrameworks/" in path_text
            or "/System/Library/CoreServices/" in path_text
            or "/System/Library/Services/" in path_text
        ):
            continue
        info = _read_mac_app_info(visible_path)
        label = info.get("label") or visible_path.stem
        bundle_id = str(info.get("bundle_id") or "")
        if bundle_id == "com.microsoft.VSCode" or label == "Code":
            label = visible_path.stem
        label_lower = label.lower()
        if label_lower.endswith("agent") or label_lower in {"finder", "system settings"}:
            continue
        seen.add(path_text)
        apps.append({
            "id": path_text,
            "label": f"{label}（默认）" if default_app_path and path_text == default_app_path else label,
            "bundle_id": bundle_id,
            "icon_url": f"/api/project/app-icon?app_id={path_text}",
            "rank": 0 if default_app_path and path_text == default_app_path else 1,
        })
    apps.sort(key=lambda item: (item.get("rank", 1000), item.get("label", "").lower()))
    return [{k: v for k, v in item.items() if k != "rank"} for item in apps[:40]]

def _read_mac_app_info(app_path: Path) -> dict:
    info_path = app_path / "Contents" / "Info.plist"
    try:
        data = plistlib.loads(info_path.read_bytes())
    except Exception:
        data = {}
    return {
        "label": data.get("CFBundleDisplayName") or data.get("CFBundleName") or app_path.stem,
        "bundle_id": data.get("CFBundleIdentifier") or "",
        "icon_file": data.get("CFBundleIconFile") or "",
    }

def _mac_app_icon_path(app_path: Path) -> Path | None:
    info = _read_mac_app_info(app_path)
    icon_name = str(info.get("icon_file") or "").strip()
    resources = app_path / "Contents" / "Resources"
    candidates = []
    if icon_name:
        candidates.append(resources / icon_name)
        if not icon_name.endswith(".icns"):
            candidates.append(resources / f"{icon_name}.icns")
    candidates.extend(resources.glob("*.icns"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

def _windows_open_app_candidates(file_path: Path | None = None) -> list[dict]:
    if os.name != "nt" or file_path is None:
        return []
    try:
        import ctypes
        import shlex
        import winreg
    except Exception:
        return []

    ext = file_path.suffix.lower()
    if not ext:
        return []

    def expand_env(value: str) -> str:
        try:
            return os.path.expandvars(value)
        except Exception:
            return value

    def parse_command_exe(command: str) -> str:
        command = expand_env((command or "").strip())
        if not command:
            return ""
        try:
            parts = shlex.split(command, posix=False)
            if parts:
                return parts[0].strip('"')
        except Exception:
            pass
        if command.startswith('"'):
            end = command.find('"', 1)
            return command[1:end] if end > 1 else ""
        return command.split(" ", 1)[0].strip('"')

    def read_reg_value(root, subkey: str, value_name: str = "") -> str:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                return str(value or "")
        except Exception:
            return ""

    def enum_subkey_values(root, subkey: str) -> list[str]:
        values: list[str] = []
        try:
            with winreg.OpenKey(root, subkey) as key:
                idx = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, idx)
                        idx += 1
                        if name and name != "MRUList":
                            values.append(str(value or name))
                    except OSError:
                        break
        except Exception:
            pass
        return values

    def command_for_progid(progid: str) -> str:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_CLASSES_ROOT, winreg.HKEY_LOCAL_MACHINE):
            for prefix in ("Software\\Classes\\", ""):
                command = read_reg_value(root, f"{prefix}{progid}\\shell\\open\\command")
                if command:
                    return command
        return ""

    def app_label(exe: Path, default: bool = False) -> str:
        stem = exe.stem
        label_map = {
            "notepad": "记事本",
            "code": "Visual Studio Code",
            "cursor": "Cursor",
            "wordpad": "写字板",
        }
        label = label_map.get(stem.lower(), stem)
        return f"{label}（默认）" if default else label

    candidates: list[tuple[str, bool]] = []

    # 默认打开程序：Windows Shell 关联查询，等价于系统“打开方式”的默认项。
    try:
        ASSOCF_NONE = 0
        ASSOCSTR_EXECUTABLE = 2
        buffer_len = ctypes.c_ulong(0)
        ctypes.windll.Shlwapi.AssocQueryStringW(
            ASSOCF_NONE, ASSOCSTR_EXECUTABLE, ext, None, None, ctypes.byref(buffer_len)
        )
        if buffer_len.value > 0:
            buffer = ctypes.create_unicode_buffer(buffer_len.value)
            result = ctypes.windll.Shlwapi.AssocQueryStringW(
                ASSOCF_NONE, ASSOCSTR_EXECUTABLE, ext, None, buffer, ctypes.byref(buffer_len)
            )
            if result == 0 and buffer.value:
                candidates.append((buffer.value, True))
    except Exception:
        pass

    progids = set()
    for root, subkey in (
        (winreg.HKEY_CURRENT_USER, f"Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\{ext}\\OpenWithProgids"),
        (winreg.HKEY_CLASSES_ROOT, f"{ext}\\OpenWithProgids"),
    ):
        progids.update(enum_subkey_values(root, subkey))
    default_progid = read_reg_value(winreg.HKEY_CLASSES_ROOT, ext)
    if default_progid:
        progids.add(default_progid)
    user_choice = read_reg_value(
        winreg.HKEY_CURRENT_USER,
        f"Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\{ext}\\UserChoice",
        "ProgId",
    )
    if user_choice:
        progids.add(user_choice)

    for progid in progids:
        command = command_for_progid(progid)
        exe = parse_command_exe(command)
        if exe:
            candidates.append((exe, False))

    app_names = enum_subkey_values(
        winreg.HKEY_CURRENT_USER,
        f"Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\{ext}\\OpenWithList",
    )
    app_names += enum_subkey_values(
        winreg.HKEY_CLASSES_ROOT,
        f"{ext}\\OpenWithList",
    )
    for app_name in app_names:
        app_key = f"Applications\\{app_name}\\shell\\open\\command"
        command = read_reg_value(winreg.HKEY_CLASSES_ROOT, app_key)
        exe = parse_command_exe(command)
        if exe:
            candidates.append((exe, False))

    seen = set()
    apps = []
    for exe, is_default in candidates:
        try:
            path = Path(expand_env(exe)).expanduser().resolve()
        except Exception:
            continue
        key = str(path).lower()
        if key in seen or not path.exists() or path.suffix.lower() != ".exe":
            continue
        seen.add(key)
        apps.append({"id": str(path), "label": app_label(path, is_default)})
    return apps[:40]

def _open_file_with_app(file_path: Path, app_id: str) -> bool:
    try:
        if sys.platform == "darwin":
            app_path = Path(app_id).expanduser().resolve()
            if not app_path.exists() or app_path.suffix != ".app":
                return False
            subprocess.Popen(["open", "-a", str(app_path), str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if os.name == "nt":
            exe_path = Path(app_id).expanduser().resolve()
            if not exe_path.exists() or exe_path.suffix.lower() != ".exe":
                return False
            subprocess.Popen([str(exe_path), str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        return False
    return False

@status_bp.route('/api/project/open-in-file-manager', methods=['POST'])
@api_login_required
@with_terminal
def open_project_in_file_manager(terminal, workspace, username):
    data = request.get_json(silent=True) or {}
    target_workspace_id = (data.get("workspace_id") or "").strip()

    if target_workspace_id:
        if _is_host_mode_request():
            catalog, _ = resolve_host_workspace()
            target = next(
                (item for item in (catalog.get("workspaces") or [])
                 if item.get("workspace_id") == target_workspace_id),
                None,
            )
            if not target:
                return jsonify({"success": False, "error": "工作区不存在"}), 404
            project_path = Path(target.get("path") or "").expanduser().resolve()
        else:
            if target_workspace_id not in user_manager.list_user_workspaces(username):
                return jsonify({"success": False, "error": "项目不存在"}), 404
            ws_obj = user_manager.ensure_user_workspace(username, target_workspace_id)
            project_path = Path(getattr(ws_obj, "project_path", "") or "").expanduser().resolve()
    else:
        project_path = Path(getattr(workspace, "project_path", "") or "").expanduser().resolve()

    ok, root_text = _run_project_git(project_path, ["rev-parse", "--show-toplevel"])
    target = Path(root_text).expanduser().resolve() if ok and root_text else project_path
    if _open_path_in_file_manager(target):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "无法打开文件管理器"}), 500

@status_bp.route('/api/project/file-open-apps')
@api_login_required
@with_terminal
def list_project_file_open_apps(terminal, workspace, username):
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": "仅宿主机模式可用"}), 403
    rel_path = request.args.get("path", "").strip()
    target, error = _resolve_project_git_file(workspace, rel_path)
    if not target:
        return jsonify({"success": False, "error": error or "文件路径无效"}), 400
    if sys.platform == "darwin":
        apps = _mac_open_app_candidates(target)
    elif os.name == "nt":
        apps = _windows_open_app_candidates(target)
    else:
        apps = []
    return jsonify({"success": True, "data": {"apps": apps}})

@status_bp.route('/api/project/app-icon')
@api_login_required
def get_project_open_app_icon():
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": "仅宿主机模式可用"}), 403
    app_id = request.args.get("app_id", "").strip()
    if sys.platform != "darwin" or not app_id:
        return jsonify({"success": False, "error": "应用图标不可用"}), 404
    try:
        app_path = Path(app_id).expanduser().resolve()
        if not app_path.exists() or app_path.suffix != ".app":
            return jsonify({"success": False, "error": "应用不存在"}), 404
        icon_path = _mac_app_icon_path(app_path)
        if not icon_path:
            return jsonify({"success": False, "error": "应用图标不存在"}), 404
        cache_dir = Path(tempfile.gettempdir()) / "agents_app_icons"
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = cache_dir / f"{abs(hash(str(app_path)))}.png"
        if not out_path.exists() or out_path.stat().st_mtime < icon_path.stat().st_mtime:
            sips = shutil.which("sips")
            if not sips:
                return jsonify({"success": False, "error": "图标转换工具不可用"}), 404
            subprocess.run(
                [sips, "-s", "format", "png", str(icon_path), "--out", str(out_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        if out_path.exists():
            return send_file(str(out_path), mimetype="image/png")
    except Exception:
        pass
    return jsonify({"success": False, "error": "应用图标不可用"}), 404

@status_bp.route('/api/project/open-file-with-app', methods=['POST'])
@api_login_required
@with_terminal
def open_project_file_with_app(terminal, workspace, username):
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": "仅宿主机模式可用"}), 403
    payload = request.get_json(silent=True) or {}
    rel_path = str(payload.get("path") or "").strip()
    app_id = str(payload.get("app_id") or "").strip()
    if not app_id:
        return jsonify({"success": False, "error": "未选择应用"}), 400
    target, error = _resolve_project_git_file(workspace, rel_path)
    if not target:
        return jsonify({"success": False, "error": error or "文件路径无效"}), 400
    available = _mac_open_app_candidates(target) if sys.platform == "darwin" else _windows_open_app_candidates(target) if os.name == "nt" else []
    if not any(item.get("id") == app_id for item in available):
        return jsonify({"success": False, "error": "应用不可用"}), 400
    if _open_file_with_app(target, app_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "打开文件失败"}), 500
