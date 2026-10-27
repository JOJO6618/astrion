"""Authenticated conversation archive and cross-workspace transfer endpoints."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from flask import Blueprint, jsonify, request, session

from modules.conversation_transfer.archive import export_archive, read_archive
from modules.conversation_transfer.assets import collect
from modules.conversation_transfer.service import import_document, read_document, source_info, verify_import
from server.auth_helpers import api_login_required, get_current_username
from server.context.data import get_workspace_data
from server.context.data_decorators import requested_workspace_id
from server.conversation_view.snapshots import VIEW_LOCK
from server.conversation_transfer_support import chat_materials, expire_exports, export_path, finish_migration
from server.runtime import principal_from_session_snapshot
from utils.conversation_manager import ConversationManager

conversation_transfer_bp = Blueprint("conversation_transfer", __name__)
_IMPORT_LOCK = RLock()


def _scope(*, writable=False):
    if request.args.get("kind") == "chat":
        from server.chat_mode.context import resources
        principal, scope, storage = resources(read_only=not writable)
        return principal, None, storage.manager, {"workspace_name": "Chat", "environment": "chat"}
    username = get_current_username()
    data = get_workspace_data(username, requested_workspace_id())
    principal = principal_from_session_snapshot(dict(session), data.workspace_id, username=username)
    manager = data.manager()
    if writable:
        manager = ConversationManager(base_dir=str(data.workspace.data_dir),
                                      project_path=str(data.workspace.project_path))
    return principal, data, manager, source_info(data.workspace, host_mode=data.identity.host_mode)


@contextmanager
def _idle(principal, data, manager, conversation_id):
    """Admission and transfer share VIEW_LOCK, with the normal terminal gate held."""
    from server.tasks import task_manager
    from server.context.background import background_status
    from server.context.resources import find_runtime
    from server.main_task_gate import try_acquire_main_task_gate, release_main_task_gate
    with VIEW_LOCK:
        records = task_manager.list_tasks(principal.username, principal.workspace_id)
        if any(row.conversation_id == conversation_id and row.status in {"pending", "running", "cancel_requested"}
               and row.principal.conversation_kind == principal.conversation_kind for row in records):
            raise RuntimeError("Stop the conversation and its background activity before transferring it")
        terminal = None
        token = None
        if data is not None:
            if any(background_status(data, conversation_id).values()):
                raise RuntimeError("Stop background activity before transferring this conversation")
            terminal = find_runtime(data.username, data.workspace, conversation_id)
            if terminal is not None:
                token = try_acquire_main_task_gate(terminal)
                if not token:
                    raise RuntimeError("Conversation is busy")
        else:
            from server.chat_mode.handoff_execution import is_running
            from modules.chat_mode.storage import ChatStorage
            storage = ChatStorage(manager.base_dir, read_only=True)
            document = read_document(manager, conversation_id)
            tree = document["metadata"]["chat"]
            if tree.get("active_turn_id") or any(is_running(storage, conversation_id, key)
                                                 for key in tree.get("handoffs", {})):
                raise RuntimeError("Wait for Chat generation and Agent handoff to finish")
        try:
            yield
        finally:
            release_main_task_gate(terminal, token)


def _failure(exc):
    if isinstance(exc, FileNotFoundError):
        status = 404
    elif isinstance(exc, (ValueError, TypeError, KeyError)):
        status = 400
    elif isinstance(exc, RuntimeError):
        status = 409
    else:
        import traceback
        from server.utils_common import debug_log
        debug_log(f"[ConversationTransfer] {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return jsonify(success=False, error="Conversation transfer failed; the source is preserved"), 500
    return jsonify(success=False, error=str(exc)), status


def _operation(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError("A stable transfer operation ID is required")
    return value


def _modes(workspace, identity):
    from modules.personalization_manager import load_personalization_config
    from modules.conversation_modes import resolve_conversation_modes
    prefs = load_personalization_config(workspace.data_dir)
    return resolve_conversation_modes({"work_mode": prefs.get("default_work_mode", "plan"),
        "permission_mode": prefs.get("default_permission_mode", "approval"),
        "execution_mode": "sandbox", "network_permission": "restricted"}, host_mode=identity.host_mode)


def _check_model(document, selected=None, source_protocol=None):
    from config.model_profiles import get_registered_model_profiles
    profiles = get_registered_model_profiles()
    key = selected or document["metadata"].get("model_key")
    if key and key not in profiles:
        raise ValueError(f"Model is unavailable on this installation: {key}")
    if selected:
        old = document["metadata"].get("model_key")
        original = profiles.get(old, {})
        current = profiles[selected]
        original_protocol = source_protocol or document["metadata"].get("api_protocol") or original.get("api_protocol")
        if original_protocol and original_protocol != (current.get("api_protocol") or "chat_completions"):
            raise ValueError("The selected model uses an incompatible conversation protocol")
    return selected


@conversation_transfer_bp.route("/api/conversation-transfer/workspaces", methods=["GET"])
@api_login_required
def transfer_workspaces():
    try:
        from server.chat_mode.handoff_workspace import projects
        principal = principal_from_session_snapshot(dict(session), requested_workspace_id(), username=get_current_username())
        return jsonify(success=True, data=projects(principal))
    except Exception as exc:
        return _failure(exc)


@conversation_transfer_bp.route("/api/conversations/<conversation_id>/export", methods=["POST"])
@api_login_required
def export_conversation(conversation_id):
    try:
        principal, data, manager, source = _scope()
        with _idle(principal, data, manager, conversation_id):
            document = read_document(manager, conversation_id)
            from config.model_profiles import get_registered_model_profiles
            profile = get_registered_model_profiles().get(document["metadata"].get("model_key"))
            protocol = document["metadata"].get("api_protocol") or (
                profile.get("api_protocol") or "chat_completions" if profile is not None else None)
            source = {**source, "api_protocol": protocol}
            project = Path(data.workspace.project_path) if data is not None else None
            extra_resources, extra_files = chat_materials(document, principal, manager.base_dir) if data is None else ([], {})
            archive = export_archive(document, manager.base_dir, project, source,
                                     extra_resources=extra_resources, extra_files=extra_files)
        token = uuid4().hex
        path = export_path(data, manager, token)
        relative = f".astrion/conversation_exports/{token}.astrion.zip" if project is not None else None
        path.parent.mkdir(parents=True, exist_ok=True)
        expire_exports(path.parent)
        with path.open("xb") as stream:
            stream.write(archive.getbuffer())
        filename = f"{conversation_id}.astrion.zip"
        return jsonify(success=True, data={"path": relative, "filename": filename,
            "url": f"/api/conversation-transfer/download/{token}?kind={'chat' if data is None else 'agent'}"
                   + (f"&workspace_id={data.workspace_id}" if data is not None else "")})
    except Exception as exc:
        return _failure(exc)


@conversation_transfer_bp.route("/api/conversation-transfer/download/<token>", methods=["GET", "POST"])
@api_login_required
def download_conversation(token):
    from flask import send_file
    try:
        import re
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("Invalid export identity")
        _, data, manager, _ = _scope()
        path = export_path(data, manager, token)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("Conversation export is unavailable")
        if request.method == "POST":
            from config import IS_HOST_MODE
            if not IS_HOST_MODE:
                return jsonify(success=False, error="Native saving is only available on the host"), 403
            import os
            import shutil
            import sys
            from server.status.file_open import _pick_save_file_macos, _pick_save_file_windows
            from modules.i18n import tr
            picker = _pick_save_file_macos if sys.platform == "darwin" else (
                _pick_save_file_windows if os.name == "nt" else None)
            if picker is None:
                raise ValueError("Native saving is unavailable on this platform")
            filename = (request.get_json(silent=True) or {}).get("filename", "conversation.astrion.zip")
            if (not isinstance(filename, str) or len(filename) > 200
                    or not re.fullmatch(r"[A-Za-z0-9_.-]+\.astrion\.zip", filename)):
                raise ValueError("Invalid conversation export filename")
            ok, target = picker(tr("status_file_open.save_file_prompt"), filename)
            if not ok:
                raise RuntimeError("The save dialog could not be opened")
            if target and os.path.abspath(target) != os.path.abspath(path):
                shutil.copy2(path, target)
            return jsonify(success=True, data={"saved": bool(target)})
        response = send_file(path, as_attachment=True, download_name="conversation.astrion.zip",
                             mimetype="application/zip", conditional=False, max_age=0)
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as exc:
        return _failure(exc)


@conversation_transfer_bp.route("/api/conversations/import", methods=["POST"])
@api_login_required
def import_conversation():
    try:
        from modules.conversation_transfer.assets import MAX_BYTES
        if request.content_length and request.content_length > MAX_BYTES + 32 * 1024 * 1024:
            raise ValueError("Conversation file exceeds the import size limit")
        upload = request.files.get("file")
        if upload is None:
            raise ValueError("Select an Astrion conversation file")
        operation = _operation(request.form.get("operation_id"))
        document, manifest, files = read_archive(upload.stream)
        principal, data, manager, target = _scope(writable=True)
        selected = _check_model(document, request.form.get("model_key") or None,
                                source_protocol=manifest["source"].get("api_protocol"))
        with VIEW_LOCK, _IMPORT_LOCK:
            result = import_document(document, manifest, files, manager,
                project=Path(data.workspace.project_path) if data is not None else None,
                target=target, modes=_modes(data.workspace, data.identity) if data is not None else {},
                model_key=selected, operation_id=operation)
        return jsonify(success=True, data={"conversation_id": result["id"],
            "workspace_id": data.workspace_id if data is not None else None})
    except Exception as exc:
        return _failure(exc)


@conversation_transfer_bp.route("/api/conversations/<conversation_id>/migrate", methods=["POST"])
@api_login_required
def migrate_conversation(conversation_id):
    try:
        payload = request.get_json(silent=True) or {}
        operation = _operation(payload.get("operation_id"))
        principal, data, source_manager, source = _scope(writable=True)
        if data is None:
            raise ValueError("Chat conversations do not belong to workspaces")
        target_id = payload.get("target_workspace_id")
        if not isinstance(target_id, str) or not target_id or target_id == data.workspace_id:
            raise ValueError("Choose a different target workspace")
        target_identity = replace(data.identity, host_workspace_id=target_id)
        target_data = get_workspace_data(principal.username, target_id, identity=target_identity)
        project = Path(target_data.workspace.project_path)
        target_manager = ConversationManager(base_dir=str(target_data.workspace.data_dir), project_path=str(project))
        target = source_info(target_data.workspace, host_mode=target_identity.host_mode)
        receipt = source_manager.base_dir / "conversation_migrations" / (hashlib.sha256(
            f"{data.workspace_id}:{conversation_id}:{operation}".encode()).hexdigest() + ".json")
        import_operation = f"migration:{data.workspace_id}:{conversation_id}:{operation}"
        with VIEW_LOCK, _IMPORT_LOCK:
            if receipt.exists():
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                if saved["workspace_id"] != target_id:
                    raise ValueError("Migration operation reused for a different target")
                existing = target_manager.read_conversation(saved["conversation_id"])
                if existing is None:
                    raise RuntimeError("The migration destination is unavailable; the source is preserved")
                verify_import(existing, conversation_id, saved["document_sha256"], import_operation)
                if source_manager._get_conversation_file_path(conversation_id).exists():
                    with _idle(principal, data, source_manager, conversation_id):
                        original = read_document(source_manager, conversation_id)
                        current_hash = hashlib.sha256(json.dumps(original, ensure_ascii=False,
                            separators=(",", ":")).encode()).hexdigest()
                        if current_hash != saved["document_sha256"]:
                            raise RuntimeError("The source has changed since migration; both conversations are preserved")
                        finish_migration(principal, data, source_manager, conversation_id, saved,
                                         target.get("workspace_name", ""))
                else:
                    finish_migration(principal, data, source_manager, conversation_id, saved,
                                     target.get("workspace_name", ""))
                return jsonify(success=True, data={name: saved[name] for name in ("conversation_id", "workspace_id")})
            with _idle(principal, data, source_manager, conversation_id):
                original = read_document(source_manager, conversation_id)
                _check_model(original)
                shared_media = source_manager.base_dir.resolve() == target_manager.base_dir.resolve()
                resources, files = collect(original, source_manager.base_dir,
                    Path(data.workspace.project_path), shared_media=shared_media)
                fingerprint = hashlib.sha256(json.dumps(original, ensure_ascii=False,
                    separators=(",", ":")).encode()).hexdigest()
                manifest = {"source": source, "resources": resources,
                            "document_sha256": fingerprint, "shared_media": shared_media}
                result = import_document(original, manifest, files, target_manager,
                    project=project, target=target, modes=_modes(target_data.workspace, target_identity),
                    operation_id=import_operation, preserve_id=True)
                destination = {"conversation_id": result["id"], "workspace_id": target_id}
                # Durable destination first: a retry can finish source retirement
                # even if the process stops immediately after removing its file.
                receipt.parent.mkdir(parents=True, exist_ok=True)
                source_manager._atomic_write_json(receipt, {**destination, "document_sha256": fingerprint})
                finish_migration(principal, data, source_manager, conversation_id, destination,
                                 target.get("workspace_name", ""))
        return jsonify(success=True, data=destination)
    except Exception as exc:
        return _failure(exc)
