"""Authenticated, single-flight migration of indexed workspace memories."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

from flask import Blueprint, jsonify, request

from config import DATA_DIR, TERMINAL_SANDBOX_MODE
from modules.host_workspace_manager import load_host_workspace_catalog
from modules.project_memory import legacy_files
from modules.project_memory_migration import WorkspaceMemories, clean_workspace, copy_workspace, verify_workspace
from server import state
from server.auth_helpers import api_login_required, is_admin_user
from server.context.workspace import request_identity
from server.utils_common import debug_log

project_memory_migration_bp = Blueprint("project_memory_migration", __name__)
_PREFIX = "/api/project-memory-migration"
_ACTIVE = {"copying", "verifying"}
_LOCK = Lock()
_JOB: dict | None = None
_STARTUP_SHOWN = False


def _mode() -> str | None:
    if TERMINAL_SANDBOX_MODE == "host" and request_identity().host_mode:
        return "host"
    if TERMINAL_SANDBOX_MODE == "docker" and is_admin_user():
        return "docker"
    return None


def _candidates(mode):
    if mode == "host":
        for entry in load_host_workspace_catalog()["workspaces"]:
            yield "host", entry["workspace_id"], entry.get("label") or entry["workspace_id"], entry["path"], DATA_DIR, None
        return
    users = state.user_manager
    for user in users.list_users():
        for workspace_id, entry in users.list_user_workspaces(user, prepare=False).items():
            base = Path(users.workspace_root) / user / "projects" / workspace_id
            yield user, workspace_id, entry.get("label") or workspace_id, base / "project", base / "data", Path(users.workspace_root)
    api = state.api_user_manager
    for user in api.list_users():
        for workspace_id in api.list_workspaces(user):
            base = Path(api.workspace_root) / user / "workspaces" / workspace_id
            yield user, workspace_id, workspace_id, base / "project", base / "data", Path(api.workspace_root)


def _scan(mode):
    workspaces, errors, seen = [], [], set()
    for user, workspace_id, label, project, data, root in _candidates(mode):
        try:
            project, data = Path(project), Path(data)
            if root is not None and (not project.is_relative_to(root) or not data.is_relative_to(root)
                                     or project.resolve() != project or data.resolve() != data):
                raise PermissionError("工作区项目或数据目录不能经过链接。")
            project, data = project.resolve(), data.resolve()
            if project in seen:
                continue
            seen.add(project)
            files = legacy_files(project)
            if files:
                workspaces.append(WorkspaceMemories(user, workspace_id, label, project, data,
                                                     mode == "docker", tuple(files)))
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append({"workspace": f"{user} / {label}" if mode == "docker" else label,
                           "reason": str(exc)})
    return workspaces, errors


def _overview(mode):
    workspaces, errors = _scan(mode)
    return workspaces, {
        "mode": mode,
        "data_root": str(Path(DATA_DIR) / "memory") if mode == "host" else None,
        "users": len({(w.user, w.project.parent.parent.parent) for w in workspaces}) if mode == "docker" else 1,
        "total": sum(len(w.files) for w in workspaces),
        "workspaces": [{"label": _label(w), "target": str(w.destination), "count": len(w.files)}
                       for w in workspaces],
        "errors": errors,
    }


def _label(workspace):
    return f"{workspace.user} / {workspace.label}" if workspace.docker else workspace.label


def _with_idle_docker_workspace(workspace, verify, clean):
    from server.context.reaper import _conversation_terminal_has_running_work
    from server.context.resources import _make_terminal_key, _resource_lock
    from server.main_task_gate import release_main_task_gate, try_acquire_main_task_gate
    from server.tasks import task_manager

    key = _make_terminal_key(workspace.user, workspace.workspace_id)
    busy = "工作区有运行中的任务或打开的终端，请在空闲后重试。"
    with _resource_lock(key), ExitStack() as guards:
        if any(record.status in {"pending", "running", "cancel_requested"}
               for record in task_manager.list_tasks(workspace.user, workspace.workspace_id)):
            raise RuntimeError(busy)
        for terminal_key, terminal in list(state.user_terminals.items()):
            if terminal_key != key and not terminal_key.startswith(key + "::"):
                continue
            token = try_acquire_main_task_gate(terminal)
            if token is None:
                raise RuntimeError(busy)
            guards.callback(release_main_task_gate, terminal, token)
            conversation_id = terminal_key[len(key) + 2:]
            if (terminal.terminal_manager.list_terminals().get("total")
                    or (conversation_id and _conversation_terminal_has_running_work(
                        workspace.user, workspace.workspace_id, conversation_id, terminal))):
                raise RuntimeError(busy)
        verify()
        state.container_manager.migrate_topic_mount(key, workspace.project, clean)


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
            _update(job, current=_label(workspace))
            try:
                result = copy_workspace(workspace, lambda: _increment(job, "copied"))
                copies.append((workspace, result))
            except Exception as exc:
                errors.append({"workspace": _label(workspace), "reason": str(exc)})
        _update(job, phase="verifying")
        for workspace, result in copies:
            _update(job, current=_label(workspace))
            try:
                def verify():
                    verify_workspace(workspace, result, lambda: _increment(job, "verified"))

                def clean():
                    clean_workspace(workspace, result)

                if workspace.docker:
                    _with_idle_docker_workspace(workspace, verify, clean)
                else:
                    verify()
                    clean()
                for copy in result:
                    if copy.source.name != copy.destination.name:
                        conflicts.append({"workspace": _label(workspace), "source": copy.source.name,
                                          "target": copy.destination.name})
                with _LOCK:
                    job["completed_workspaces"] += 1
                    job["migrated"] += len(result)
            except Exception as exc:
                errors.append({"workspace": _label(workspace), "reason": str(exc)})
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
