from __future__ import annotations
from server.status import status_bp
from server.status.base import (
    _active_task_counts,
    _close_terminal_for_key,
    _is_docker_project_request,
)
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
def _build_docker_projects_payload(username: str, current_id: str) -> list[dict]:
    running_by_workspace = _active_task_counts(username)
    workspaces = user_manager.list_user_workspaces(username)
    projects = []
    for ws_id, item in workspaces.items():
        projects.append({
            "workspace_id": ws_id,
            "label": item.get("label") or ("默认项目" if ws_id == "default" else ws_id),
            "path": "",
            "is_current": ws_id == current_id,
            "is_default": bool(item.get("is_default", ws_id == "default")),
            "running_task_count": int(running_by_workspace.get(ws_id, 0)),
        })
    return projects

def _parse_android_version_from_gradle(project_root: Path) -> tuple[int, str]:
    """从 android-webview-app/app/build.gradle.kts 读取 versionCode/versionName。"""
    gradle_file = project_root / "android-webview-app" / "app" / "build.gradle.kts"
    if not gradle_file.exists():
        return 1, "1.0.0"
    text = gradle_file.read_text(encoding="utf-8", errors="ignore")
    vc_match = re.search(r"versionCode\s*=\s*(\d+)", text)
    vn_match = re.search(r'versionName\s*=\s*"([^"]+)"', text)
    version_code = int(vc_match.group(1)) if vc_match else 1
    version_name = vn_match.group(1) if vn_match else "1.0.0"
    return version_code, version_name

def _resolve_android_apk_path(project_root: Path) -> Path:
    """优先使用 release APK，其次 fallback 到 debug APK。"""
    release_apk = project_root / "android-webview-app" / "app" / "release" / "app-release.apk"
    if release_apk.exists():
        return release_apk
    return project_root / "android-webview-app" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"

def _load_app_changelog(project_root: Path) -> str:
    """读取 App 更新说明（优先 APP_CHANGELOG.md）。"""
    candidates = [
        project_root / "android-webview-app" / "APP_CHANGELOG.md",
        project_root / "android-webview-app" / "CHANGELOG.md",
    ]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            return text[:4000]
    return ""

@status_bp.route('/api/projects')
@api_login_required
def list_docker_projects():
    if not _is_docker_project_request():
        return jsonify({"success": False, "error": "仅 Docker Web 模式可用"}), 403
    username = session.get("username")
    current_id = (session.get("workspace_id") or "default").strip() or "default"
    # 默认项目用于向后兼容旧 Docker Web 文件/对话。
    user_manager.ensure_user_workspace(username, current_id)
    projects = _build_docker_projects_payload(username, current_id)
    default_workspace_id = user_manager._get_user_default_workspace_id(username)
    return jsonify({
        "success": True,
        "data": {
            "default_workspace_id": default_workspace_id,
            "current_workspace_id": current_id,
            "workspaces": projects,
        }
    })

@status_bp.route('/api/projects/select', methods=['GET', 'POST'])
@api_login_required
def select_docker_project():
    if not _is_docker_project_request():
        return jsonify({"success": False, "error": "仅 Docker Web 模式可用"}), 403
    payload = request.get_json(silent=True) if request.method != "GET" else None
    workspace_id = (
        request.args.get("workspace_id")
        or (payload or {}).get("workspace_id")
        or ""
    ).strip()
    if not workspace_id:
        return jsonify({"success": False, "error": "缺少项目 ID"}), 400
    username = session.get("username")
    previous_workspace_id = session.get("workspace_id") or "default"
    known_projects = user_manager.list_user_workspaces(username)
    if workspace_id not in known_projects:
        return jsonify({"success": False, "error": "项目不存在"}), 404
    try:
        workspace = user_manager.ensure_user_workspace(username, workspace_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    session["workspace_id"] = getattr(workspace, "workspace_id", workspace_id)
    try:
        container_manager.ensure_container(
            username,
            str(workspace.project_path),
            container_key=f"{username}::{session['workspace_id']}",
            preferred_mode="docker",
        )
    except RuntimeError as exc:
        session["workspace_id"] = previous_workspace_id
        return jsonify({"success": False, "error": str(exc)}), 503
    terminal = state.user_terminals.get(f"{username}::{session['workspace_id']}")
    if terminal:
        try:
            attach_user_broadcast(terminal, username)
        except Exception:
            pass
    default_workspace_id = user_manager._get_user_default_workspace_id(username)
    return jsonify({
        "success": True,
            "data": {
                "current_workspace_id": session["workspace_id"],
                "project_path": "",
                "default_workspace_id": default_workspace_id,
                "reloaded": previous_workspace_id != session["workspace_id"],
            }
        })

@status_bp.route('/api/projects/create', methods=['POST'])
@api_login_required
def create_docker_project():
    if not _is_docker_project_request():
        return jsonify({"success": False, "error": "仅 Docker Web 模式可用"}), 403
    payload = request.get_json(silent=True) or {}
    label = (payload.get("label") or payload.get("name") or "").strip()
    if not label:
        return jsonify({"success": False, "error": "项目名称不能为空"}), 400
    username = session.get("username")
    try:
        workspace = user_manager.create_user_workspace(username, "", label=label)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    projects = []
    current_id = session.get("workspace_id") or "default"
    default_workspace_id = user_manager._get_user_default_workspace_id(username)
    for ws_id, item in user_manager.list_user_workspaces(username).items():
        projects.append({
            "workspace_id": ws_id,
            "label": item.get("label") or ws_id,
            "path": "",
            "is_current": ws_id == current_id,
            "is_default": ws_id == default_workspace_id,
        })
    return jsonify({
        "success": True,
        "data": {
            "created": True,
            "workspace": {
                "workspace_id": getattr(workspace, "workspace_id", ""),
                "label": label,
                "path": "",
            },
            "default_workspace_id": default_workspace_id,
            "current_workspace_id": current_id,
            "workspaces": projects,
        }
    })

@status_bp.route('/api/projects/rename', methods=['POST'])
@api_login_required
def rename_docker_project():
    if not _is_docker_project_request():
        return jsonify({"success": False, "error": "仅 Docker Web 模式可用"}), 403
    payload = request.get_json(silent=True) or {}
    workspace_id = (payload.get("workspace_id") or "").strip()
    label = (payload.get("label") or payload.get("name") or "").strip()
    if not workspace_id:
        return jsonify({"success": False, "error": "缺少项目 ID"}), 400
    if not label:
        return jsonify({"success": False, "error": "项目名称不能为空"}), 400
    username = session.get("username")
    try:
        workspace = user_manager.rename_user_workspace(username, workspace_id, label)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    current_id = session.get("workspace_id") or "default"
    default_workspace_id = user_manager._get_user_default_workspace_id(username)
    return jsonify({
        "success": True,
        "data": {
            "workspace": workspace,
            "default_workspace_id": default_workspace_id,
            "current_workspace_id": current_id,
            "workspaces": _build_docker_projects_payload(username, current_id),
        }
    })

@status_bp.route('/api/projects/delete', methods=['POST'])
@api_login_required
def delete_docker_project():
    if not _is_docker_project_request():
        return jsonify({"success": False, "error": "仅 Docker Web 模式可用"}), 403
    payload = request.get_json(silent=True) or {}
    workspace_id = (payload.get("workspace_id") or "").strip()
    if not workspace_id:
        return jsonify({"success": False, "error": "缺少项目 ID"}), 400
    if workspace_id == "default":
        return jsonify({"success": False, "error": "默认项目不能删除"}), 400
    username = session.get("username")
    if _active_task_counts(username).get(workspace_id):
        return jsonify({"success": False, "error": "该项目有运行中的任务，暂不能删除"}), 409
    known_projects = user_manager.list_user_workspaces(username)
    if workspace_id not in known_projects:
        return jsonify({"success": False, "error": "项目不存在"}), 404
    term_key = f"{username}::{workspace_id}"
    try:
        _close_terminal_for_key(term_key)
        container_manager.release_container(term_key, reason="delete_project")
        user_manager.delete_user_workspace(username, workspace_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    current_id = session.get("workspace_id") or "default"
    if current_id == workspace_id:
        current_id = "default"
        session["workspace_id"] = current_id
        try:
            workspace = user_manager.ensure_user_workspace(username, current_id)
            container_manager.ensure_container(
                username,
                str(workspace.project_path),
                container_key=f"{username}::{current_id}",
                preferred_mode="docker",
            )
        except RuntimeError as exc:
            return jsonify({"success": False, "error": str(exc)}), 503

    # 如果删除的是当前默认项目，重置为 default
    if user_manager._get_user_default_workspace_id(username) == workspace_id:
        try:
            user_manager.set_default_user_workspace(username, "default")
        except Exception:
            pass

    default_workspace_id = user_manager._get_user_default_workspace_id(username)
    return jsonify({
        "success": True,
        "data": {
            "deleted_workspace_id": workspace_id,
            "default_workspace_id": default_workspace_id,
            "current_workspace_id": current_id,
            "workspaces": _build_docker_projects_payload(username, current_id),
        }
    })

@status_bp.route('/api/projects/set-default', methods=['POST'])
@api_login_required
def set_default_docker_project_api():
    if not _is_docker_project_request():
        return jsonify({"success": False, "error": "仅 Docker Web 模式可用"}), 403

    payload = request.get_json(silent=True) or {}
    workspace_id = (payload.get("workspace_id") or "").strip()
    if not workspace_id:
        return jsonify({"success": False, "error": "缺少项目 ID"}), 400
    username = session.get("username")
    try:
        user_manager.set_default_user_workspace(username, workspace_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    current_id = session.get("workspace_id") or "default"
    return jsonify({
        "success": True,
        "data": {
            "default_workspace_id": user_manager._get_user_default_workspace_id(username),
            "current_workspace_id": current_id,
            "workspaces": _build_docker_projects_payload(username, current_id),
        }
    })
