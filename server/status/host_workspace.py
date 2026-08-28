from __future__ import annotations
from server.status import status_bp
from server.status.base import (
    _active_task_counts,
    _close_terminal_for_key,
    _is_host_mode_request,
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
from modules.i18n import tr

def _build_host_workspaces_payload(catalog: dict, current_id: str) -> list[dict]:
    running_by_workspace = _active_task_counts("host")
    workspaces = []
    for item in catalog.get("workspaces") or []:
        ws_id = item.get("workspace_id")
        workspaces.append({
            "workspace_id": ws_id,
            "label": item.get("label"),
            "path": item.get("path"),
            "is_current": ws_id == current_id,
            "running_task_count": int(running_by_workspace.get(ws_id, 0)),
        })
    return workspaces

@status_bp.route('/api/host/workspaces')
@api_login_required
def list_host_workspaces():
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": tr("status_host_workspace.host_mode_only")}), 403

    catalog, current = resolve_host_workspace(session.get("host_workspace_id"))
    current_id = current.get("workspace_id") if current else None
    if current_id:
        session['host_workspace_id'] = current_id
        session['workspace_id'] = current_id
    else:
        # 无任何工作区：清理会话中的工作区指向，等待用户手动创建
        session.pop('host_workspace_id', None)
        session.pop('workspace_id', None)

    workspaces = _build_host_workspaces_payload(catalog, current_id)

    return jsonify({
        "success": True,
        "data": {
            "source_path": catalog.get("source_path"),
            "default_workspace_id": catalog.get("default_workspace_id"),
            "current_workspace_id": current_id,
            "workspaces": workspaces,
        }
    })

@status_bp.route('/api/host/workspaces/create', methods=['GET', 'POST'])
@api_login_required
def create_host_workspace_api():
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": tr("status_host_workspace.host_mode_only")}), 403

    payload = request.get_json(silent=True) if request.method != "GET" else None
    workspace_path = (
        request.args.get("path")
        or (payload or {}).get("path")
        or ""
    ).strip()
    label = (
        request.args.get("label")
        or (payload or {}).get("label")
        or ""
    ).strip()
    set_default_raw = (
        request.args.get("set_default")
        or (payload or {}).get("set_default")
        or ""
    )
    set_default = str(set_default_raw).lower() in {"1", "true", "yes", "on"}

    if not workspace_path:
        return jsonify({"success": False, "error": tr("status_host_workspace.missing_path")}), 400

    try:
        result = create_host_workspace(workspace_path, label=label, set_default=set_default)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    catalog = result.get("catalog") or {}
    workspaces = []
    current_id = (session.get("host_workspace_id") or "").strip()
    for item in catalog.get("workspaces") or []:
        workspaces.append({
            "workspace_id": item.get("workspace_id"),
            "label": item.get("label"),
            "path": item.get("path"),
            "is_current": item.get("workspace_id") == current_id,
        })

    return jsonify({
        "success": True,
        "data": {
            "created": bool(result.get("created")),
            "workspace": result.get("workspace") or {},
            "source_path": catalog.get("source_path"),
            "default_workspace_id": catalog.get("default_workspace_id"),
            "current_workspace_id": current_id,
            "workspaces": workspaces,
        }
    })

@status_bp.route('/api/host/workspaces/delete', methods=['POST'])
@api_login_required
def delete_host_workspace_api():
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": tr("status_host_workspace.host_mode_only")}), 403

    payload = request.get_json(silent=True) or {}
    workspace_id = (payload.get("workspace_id") or "").strip()
    if not workspace_id:
        return jsonify({"success": False, "error": tr("status_host_workspace.missing_workspace_id")}), 400
    if _active_task_counts("host").get(workspace_id):
        return jsonify({"success": False, "error": tr("status_host_workspace.workspace_has_running_tasks")}), 409
    try:
        _close_terminal_for_key(f"host::{workspace_id}")
        container_manager.release_container(f"host::{workspace_id}", reason="delete_host_workspace")
        result = delete_host_workspace(workspace_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    catalog = result.get("catalog") or load_host_workspace_catalog()
    current_id = (session.get("host_workspace_id") or "").strip()
    if current_id == workspace_id or not any(
        item.get("workspace_id") == current_id for item in (catalog.get("workspaces") or [])
    ):
        _, current = resolve_host_workspace(catalog.get("default_workspace_id"))
        if current:
            current_id = current.get("workspace_id") or catalog.get("default_workspace_id") or ""
            session["host_workspace_id"] = current_id
            session["workspace_id"] = current_id
        else:
            # 删除后已无任何工作区：清空指向，等待用户手动创建
            current_id = None
            session.pop("host_workspace_id", None)
            session.pop("workspace_id", None)
        with state.HOST_ACTIVE_WORKSPACE_LOCK:
            state.HOST_ACTIVE_WORKSPACE_ID = current_id
            state.HOST_ACTIVE_WORKSPACE_PATH = str(Path(current.get("path") or "").expanduser().resolve()) if current else None
            state.HOST_ACTIVE_WORKSPACE_VERSION += 1
    return jsonify({
        "success": True,
        "data": {
            "deleted_workspace_id": workspace_id,
            "source_path": catalog.get("source_path"),
            "default_workspace_id": catalog.get("default_workspace_id"),
            "current_workspace_id": current_id,
            "workspaces": _build_host_workspaces_payload(catalog, current_id),
        }
    })

@status_bp.route('/api/host/workspaces/rename', methods=['POST'])
@api_login_required
def rename_host_workspace_api():
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": tr("status_host_workspace.host_mode_only")}), 403

    payload = request.get_json(silent=True) or {}
    workspace_id = (payload.get("workspace_id") or "").strip()
    label = (payload.get("label") or payload.get("name") or "").strip()
    if not workspace_id:
        return jsonify({"success": False, "error": tr("status_host_workspace.missing_workspace_id")}), 400
    if not label:
        return jsonify({"success": False, "error": tr("status_host_workspace.workspace_name_required")}), 400
    try:
        result = rename_host_workspace(workspace_id, label)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
    catalog = result.get("catalog") or load_host_workspace_catalog()
    current_id = (session.get("host_workspace_id") or "").strip()
    return jsonify({
        "success": True,
        "data": {
            "workspace": result.get("workspace") or {},
            "source_path": catalog.get("source_path"),
            "default_workspace_id": catalog.get("default_workspace_id"),
            "current_workspace_id": current_id,
            "workspaces": _build_host_workspaces_payload(catalog, current_id),
        }
    })

@status_bp.route('/api/host/workspaces/set-default', methods=['POST'])
@api_login_required
def set_default_host_workspace_api():
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": tr("status_host_workspace.host_mode_only")}), 403

    payload = request.get_json(silent=True) or {}
    workspace_id = (payload.get("workspace_id") or "").strip()
    if not workspace_id:
        return jsonify({"success": False, "error": tr("status_host_workspace.missing_workspace_id")}), 400
    try:
        result = set_default_host_workspace(workspace_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    catalog = result.get("catalog") or load_host_workspace_catalog()
    current_id = (session.get("host_workspace_id") or "").strip()
    return jsonify({
        "success": True,
        "data": {
            "default_workspace_id": catalog.get("default_workspace_id"),
            "current_workspace_id": current_id,
            "workspaces": _build_host_workspaces_payload(catalog, current_id),
        }
    })

@status_bp.route('/api/host/workspaces/select', methods=['GET', 'POST'])
@api_login_required
def select_host_workspace():
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": tr("status_host_workspace.host_mode_only")}), 403

    payload = request.get_json(silent=True) if request.method != "GET" else None
    workspace_id = (
        request.args.get("workspace_id")
        or (payload or {}).get("workspace_id")
        or ""
    ).strip()
    if not workspace_id:
        return jsonify({"success": False, "error": tr("status_host_workspace.missing_workspace_id")}), 400
    write_host_workspace_debug(
        "status.select_host_workspace.request",
        workspace_id=workspace_id,
        current_session_workspace_id=session.get("host_workspace_id"),
    )

    catalog = load_host_workspace_catalog()
    current_id = (session.get("host_workspace_id") or "").strip()
    target = next(
        (item for item in (catalog.get("workspaces") or []) if item.get("workspace_id") == workspace_id),
        None,
    )
    if not target:
        return jsonify({"success": False, "error": tr("status_host_workspace.workspace_id_not_found")}), 404
    target_path = str(Path(target.get("path") or "").expanduser().resolve())
    write_host_workspace_debug(
        "status.select_host_workspace.target",
        workspace_id=workspace_id,
        target_path=target_path,
        current_workspace_id=current_id,
    )

    previous_workspace_id = session.get("workspace_id")
    with state.HOST_ACTIVE_WORKSPACE_LOCK:
        state.HOST_ACTIVE_WORKSPACE_ID = workspace_id
        state.HOST_ACTIVE_WORKSPACE_PATH = target_path
        state.HOST_ACTIVE_WORKSPACE_VERSION += 1
    session['host_workspace_id'] = workspace_id
    session['workspace_id'] = workspace_id

    try:
        container_handle = state.container_manager.ensure_container(
            "host",
            target_path,
            container_key=f"host::{workspace_id}",
            preferred_mode="host",
        )
    except RuntimeError as exc:
        if current_id:
            session['host_workspace_id'] = current_id
        if previous_workspace_id is not None:
            session['workspace_id'] = previous_workspace_id
        elif current_id:
            session['workspace_id'] = current_id
        write_host_workspace_debug(
            "status.select_host_workspace.ensure_container_failed",
            workspace_id=workspace_id,
            target_path=target_path,
            error=str(exc),
        )
        return jsonify({"success": False, "error": str(exc)}), 503

    host_terminal = state.user_terminals.get(f"host::{workspace_id}")
    if host_terminal:
        try:
            host_terminal.update_container_session(container_handle)
            attach_user_broadcast(host_terminal, "host")
            host_terminal.username = "host"
            host_terminal.user_role = "admin"
            write_host_workspace_debug(
                "status.select_host_workspace.updated_terminal",
                terminal_id=id(host_terminal),
                workspace_id=workspace_id,
                project_path=str(getattr(host_terminal, "project_path", "")),
                context_project_path=str(getattr(getattr(host_terminal, "context_manager", None), "project_path", "")),
                current_conversation_id=getattr(getattr(host_terminal, "context_manager", None), "current_conversation_id", None),
            )
        except Exception:
            try:
                if getattr(host_terminal, "terminal_manager", None):
                    host_terminal.terminal_manager.close_all()
            except Exception:
                pass
            state.user_terminals.pop(f"host::{workspace_id}", None)
            write_host_workspace_debug(
                "status.select_host_workspace.drop_terminal_after_error",
                workspace_id=workspace_id,
            )

    write_host_workspace_debug(
        "status.select_host_workspace.success",
        workspace_id=workspace_id,
        target_path=target_path,
    )
    return jsonify({
        "success": True,
        "data": {
            "current_workspace_id": workspace_id,
            "project_path": target_path,
            "default_workspace_id": catalog.get("default_workspace_id"),
            "reloaded": current_id != workspace_id,
        }
    })
