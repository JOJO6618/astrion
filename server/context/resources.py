"""Execution resources, separate from workspace storage/display queries."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from config.model_profiles import get_registered_model_keys
from core.web_terminal import WebTerminal
from modules.personalization_manager import load_personalization_config
from modules.skills_manager import infer_private_skills_dir, sync_workspace_skills
from server import state
from server.context._flask_bridge import has_request_context, session_set
from server.context.identity import RuntimeIdentity
from server.context.personalization import _apply_workspace_personalization_preferences
from server.context.workspace import request_identity, resolve_workspace
from server.utils_common import debug_log

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _resource_lock(key: str):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def make_terminal_callback(username: str):
    """Compatibility symbol: REST task events are the only real-time channel."""
    return None


def _make_terminal_key(username: str, workspace_id: Optional[str] = None,
                       conversation_id: Optional[str] = None) -> str:
    base = f"{username}::{workspace_id}" if workspace_id else username
    return f"{base}::{conversation_id}" if conversation_id else base


def find_runtime(username: str, workspace, conversation_id: str | None = None):
    """Lookup only. A missing runtime must never make a display query create one."""
    terminal = state.user_terminals.get(_make_terminal_key(username, workspace.workspace_id, conversation_id))
    if terminal is None or getattr(terminal, "_reaper_closing", False):
        return None
    if Path(terminal.project_path).resolve() != Path(workspace.project_path).resolve():
        return None
    return terminal


def _touch_terminal_activity(terminal, conversation_id):
    if terminal is not None and conversation_id:
        terminal.last_activity_at = time.time()


def _set_terminal_workspace_label(terminal, label):
    terminal.workspace_label = str(label or "")
    terminal.context_manager.workspace_label = terminal.workspace_label


def _ensure_workspace_skills_synced(terminal, workspace):
    config = load_personalization_config(workspace.data_dir)
    result = sync_workspace_skills(
        workspace.project_path, config.get("enabled_skills"),
        private_dir=infer_private_skills_dir(workspace.data_dir),
    )
    if not result.get("success"):
        raise RuntimeError(str(result.get("error") or "Skill preparation failed"))


def _apply_admin_policy(terminal):
    from core.tool_config import ToolCategory
    from modules import admin_policy_manager

    policy = admin_policy_manager.get_effective_policy(terminal.username, terminal.user_role, None)
    categories = {
        key: ToolCategory(label=item.get("label") or key, tools=list(item.get("tools") or []),
                          default_enabled=bool(item.get("default_enabled", True)),
                          silent_when_disabled=bool(item.get("silent_when_disabled", False)))
        for key, item in policy.get("categories", {}).items()
    }
    disabled = policy.get("disabled_models") or []
    terminal.set_admin_policy(categories, policy.get("forced_category_states") or {}, disabled)
    terminal.admin_policy_ui_blocks = policy.get("ui_blocks") or {}
    terminal.admin_policy_version = policy.get("updated_at")
    if terminal.model_key in disabled:
        for candidate in get_registered_model_keys(visible_only=True):
            if candidate not in disabled:
                try:
                    terminal.set_model(candidate)
                    break
                except Exception:
                    continue


def _discard_runtime(terminal):
    for name, method in (('terminal_manager', 'close_all'), ('mcp_client_manager', 'close_all_clients')):
        manager = getattr(terminal, name, None)
        if manager is None:
            continue
        try:
            getattr(manager, method)()
        except Exception as exc:
            debug_log(f'[RuntimePreparation] {name} cleanup failed: {exc}')


def get_user_resources(username=None, workspace_id=None, update_session=True,
                       conversation_id=None, identity: RuntimeIdentity | None = None):
    """Acquire one Host runtime per workspace and conversation."""
    explicit = identity is not None
    if username is None:
        if explicit:
            username = "host"
        else:
            from server.auth_helpers import get_current_username
            username = get_current_username()
    if not username:
        return None, None
    identity = identity if explicit else request_identity()
    workspace = resolve_workspace(username, workspace_id, identity=identity, prepare=True)
    key = _make_terminal_key(username, workspace.workspace_id, conversation_id)
    # Resources become visible only after policy and workspace setup succeed.
    with _resource_lock(key):
        terminal = find_runtime(username, workspace, conversation_id)
        previous = state.user_terminals.get(key)
        if terminal is None and previous is not None:
            from server.context.reaper import _conversation_terminal_has_running_work
            if (getattr(previous, '_reaper_closing', False) or
                    getattr(getattr(previous, 'sub_agent_manager', None), '_running_tasks', None) or
                    _conversation_terminal_has_running_work(username, workspace.workspace_id, conversation_id, previous)):
                raise RuntimeError('Workspace runtime is still active at its original path')
            _discard_runtime(previous)
            if state.user_terminals.get(key) is previous:
                state.user_terminals.pop(key)

        created = terminal is None
        if created:
            run_mode = identity.preferred_run_mode
            if run_mode not in {"fast", "thinking", "deep"}:
                run_mode = "fast"
            terminal = WebTerminal(
                project_path=str(workspace.project_path), thinking_mode=run_mode != "fast",
                run_mode=run_mode, message_callback=None, data_dir=str(workspace.data_dir),
                conversation_id=conversation_id,
            )
        terminal.username = username
        terminal.workspace_id = workspace.workspace_id
        terminal.user_role = "admin"
        _set_terminal_workspace_label(terminal, workspace.label or workspace.workspace_id)
        try:
            _apply_admin_policy(terminal)
            _apply_workspace_personalization_preferences(
                terminal, workspace, update_session=False,
                session_model=identity.preferred_model_key, allow_session_io=False,
            )
            _ensure_workspace_skills_synced(terminal, workspace)
        except Exception:
            if created:
                _discard_runtime(terminal)
            raise
        if created:
            state.user_terminals[key] = terminal
        if conversation_id:
            terminal.last_activity_at = time.time()
            terminal.sub_agent_manager.restore_for_activation()
        if not explicit and update_session and has_request_context() and created:
            session_set("run_mode", terminal.run_mode)
            session_set("thinking_mode", terminal.thinking_mode)
        return terminal, workspace
