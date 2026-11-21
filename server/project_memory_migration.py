"""Authenticated, single-flight migration of indexed Host workspace memories."""
from __future__ import annotations

from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

from flask import Blueprint, jsonify, request

from config import DATA_DIR
from modules.host_workspace_manager import load_host_workspace_catalog
from modules.project_memory import legacy_files
from modules.project_memory_migration import WorkspaceMemories, clean_workspace, copy_workspace, verify_workspace
from server.auth_helpers import api_login_required
from server.context.workspace import request_identity
from server.utils_common import debug_log

project_memory_migration_bp = Blueprint("project_memory_migration", __name__)
_PREFIX = "/api/project-memory-migration"
_ACTIVE = {"copying", "verifying"}
_LOCK = Lock()
_JOB: dict | None = None
_STARTUP_SHOWN = False


def _mode() -> str | None:
    # Host-only product: the legacy multi-user/container mode has no migration
    # surface here. The response keeps the "host" mode field for the existing UI.
    return "host" if request_identity().host_mode else None


def _candidates(mode):
    for entry in load_host_workspace_catalog()["workspaces"]:
        yield entry["workspace_id"], entry.get("label") or entry["workspace_id"], entry["path"], DATA_DIR


def _scan(mode):
    workspaces, errors, seen = [], [], set()
    for workspace_id, label, project, data in _candidates(mode):
        try:
            project, data = Path(project).resolve(), Path(data).resolve()
            if project in seen:
                continue
            seen.add(project)
            files = legacy_files(project)
            if files:
                workspaces.append(WorkspaceMemories("host", workspace_id, label, project, data,
                                                     tuple(files)))
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append({"workspace": label, "reason": str(exc)})
    return workspaces, errors


def _overview(mode):
    workspaces, errors = _scan(mode)
    return workspaces, {
        "mode": mode,
        "data_root": str(Path(DATA_DIR) / "memory"),
        "users": 1,
        "total": sum(len(w.files) for w in workspaces),
        "workspaces": [{"label": w.label, "target": str(w.destination), "count": len(w.files)}
                       for w in workspaces],
        "errors": errors,
    }


def _update(job, **changes):
    with _LOCK:
        job.update(changes)


def _increment(job, field):
    with _LOCK:
        job[field] += 1


def _run(job, workspaces):
    errors = list(job["errors"])
    copies = []
    conflicts = []
    try:
        for workspace in workspaces:
            _update(job, current=workspace.label)
            try:
                result = copy_workspace(workspace, lambda: _increment(job, "copied"))
                copies.append((workspace, result))
            except Exception as exc:
                errors.append({"workspace": workspace.label, "reason": str(exc)})
        _update(job, phase="verifying")
        for workspace, result in copies:
            _update(job, current=workspace.label)
            try:
                verify_workspace(workspace, result, lambda: _increment(job, "verified"))
                clean_workspace(workspace, result)
                for copy in result:
                    if copy.source.name != copy.destination.name:
                        conflicts.append({"workspace": workspace.label, "source": copy.source.name,
                                          "target": copy.destination.name})
                with _LOCK:
                    job["completed_workspaces"] += 1
                    job["migrated"] += len(result)
            except Exception as exc:
                errors.append({"workspace": workspace.label, "reason": str(exc)})
    except Exception as exc:
        debug_log(f"[ProjectMemoryMigration] {exc}")
        errors.append({"workspace": job["current"], "reason": str(exc)})
    finally:
        _update(job, phase="failed" if errors else "done", current="", errors=errors, conflicts=conflicts)


@project_memory_migration_bp.get(_PREFIX)
@api_login_required
def migration_overview():
    mode = _mode()
    if mode is None:
        return jsonify(error="当前账户无权访问项目记忆迁移。"), 403
    job_id = request.args.get("job")
    with _LOCK:
        if job_id:
            if _JOB and _JOB["id"] == job_id and _JOB["mode"] == mode:
                return jsonify(job=dict(_JOB))
            return jsonify(error="迁移记录已过期，请重新检查待迁移的记忆。"), 404
        active = dict(_JOB) if _JOB and _JOB["mode"] == mode and _JOB["phase"] in _ACTIVE else None
    if active:
        return jsonify(job=active)
    _, overview = _overview(mode)
    return jsonify(overview=overview)


@project_memory_migration_bp.post(_PREFIX + "/startup")
@api_login_required
def migration_startup():
    global _STARTUP_SHOWN
    if _mode() != "host":
        return jsonify(error="启动迁移提示仅在宿主机模式下可用。"), 403
    with _LOCK:
        if _STARTUP_SHOWN or (_JOB and _JOB["phase"] in _ACTIVE):
            return jsonify({})
        _, overview = _overview("host")
        _STARTUP_SHOWN = True
    return jsonify(overview=overview) if overview["total"] else jsonify({})


@project_memory_migration_bp.post(_PREFIX)
@api_login_required
def migration_start():
    global _JOB
    mode = _mode()
    if mode is None:
        return jsonify(error="当前账户无权执行项目记忆迁移。"), 403
    with _LOCK:
        if _JOB and _JOB["phase"] in _ACTIVE:
            return jsonify(job=dict(_JOB))
        workspaces, overview = _overview(mode)
        if not workspaces:
            return jsonify(overview=overview)
        _JOB = {"id": uuid4().hex, "mode": mode, "phase": "copying", "current": "",
                "total": overview["total"], "copied": 0, "verified": 0, "migrated": 0,
                "completed_workspaces": 0, "errors": overview["errors"], "conflicts": []}
        job = _JOB
        result = dict(job)
    Thread(target=_run, args=(job, workspaces), daemon=True, name="project-memory-migration").start()
    return jsonify(job=result), 202
