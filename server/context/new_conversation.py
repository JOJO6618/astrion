"""Workspace draft initialization and creation from an explicit client snapshot."""
from __future__ import annotations

from typing import Any

from config import REASONING_EFFORT_LEVELS
from config.model_profiles import get_default_model_key, get_model_profile, get_registered_model_keys
from core.tool_loading import snapshot_overrides_from_prefs
from modules.conversation_modes import (
    EXECUTION_MODES, NETWORK_MODES, PERMISSIONS, WORK_MODES, resolve_conversation_modes,
)
from modules.personalization_manager import load_personalization_config

RESTORE_KEYS = ("pre_plan_permission_mode", "pre_plan_execution_mode", "pre_readonly_execution_mode")
SETTING_KEYS = ("model_key", "run_mode", "thinking_mode", "reasoning_effort",
               "work_mode", "permission_mode", "execution_mode", "network_permission")


def remember_workspace_defaults(terminal: Any, config: dict) -> None:
    """Keep defaults separate from mutable /new selections on workspace runtimes."""
    if getattr(terminal, "_bound_conversation_id", None):
        return
    terminal.new_conversation_defaults = {
        "model_key": config.get("default_model") or get_default_model_key(),
        "run_mode": config.get("default_run_mode") or "fast",
        "reasoning_effort": config.get("default_reasoning_effort"),
        "work_mode": config.get("default_work_mode") or "plan",
        "permission_mode": config.get("default_permission_mode") or "approval",
    }


def _previous_metadata(terminal: Any, previous_id: str | None) -> dict:
    # Only the explicitly departed conversation may supply inherited values.
    # No source means direct/cross-workspace entry: never scan recent history.
    if not previous_id:
        return {}
    previous_id = previous_id if previous_id.startswith("conv_") else f"conv_{previous_id}"
    context = terminal.context_manager
    managers = [context.conversation_manager]
    multi = getattr(context, "multi_agent_conversation_manager", None)
    if multi is not None:
        managers.append(multi)
    for manager in managers:
        data = manager.read_conversation(previous_id)
        if data is not None:
            return data.get("metadata") or {}
    return {}


def draft_settings(terminal: Any) -> dict:
    """Read the workspace terminal without confusing an explicit null with absence."""
    meta = terminal.context_manager.conversation_metadata or {}
    return {
        "model_key": terminal.model_key,
        "run_mode": terminal.run_mode,
        "thinking_mode": terminal.thinking_mode,
        "reasoning_effort": terminal.reasoning_effort,
        "work_mode": terminal.get_work_mode(),
        "permission_mode": terminal.get_permission_mode(),
        "execution_mode": terminal.get_execution_mode(),
        "network_permission": terminal.get_network_permission(),
        "restore_modes": {key: meta.get(key) for key in RESTORE_KEYS},
    }


def prepare_draft_settings(terminal: Any, workspace: Any, previous_id: str | None = None) -> dict:
    """Defaults initialize each /new entry; fields without defaults inherit history."""
    if getattr(terminal, "_bound_conversation_id", None):
        raise ValueError("New conversation settings require a workspace runtime")
    if not hasattr(terminal, "new_conversation_defaults"):
        remember_workspace_defaults(terminal, load_personalization_config(workspace.data_dir))
    defaults = terminal.new_conversation_defaults
    previous = _previous_metadata(terminal, previous_id)
    requested = {
        "work_mode": defaults["work_mode"],
        "permission_mode": defaults["permission_mode"],
        "execution_mode": previous.get("execution_mode") or "sandbox",
        "network_permission": previous.get("network_permission") or "restricted",
    }
    host_mode = terminal._is_host_mode()
    if not (host_mode and getattr(terminal, "user_role", "user") == "admin"):
        requested["execution_mode"] = "sandbox"
        requested["network_permission"] = "restricted"
    modes = resolve_conversation_modes(requested, host_mode=host_mode)
    restore = dict.fromkeys(RESTORE_KEYS)
    if requested["work_mode"] == "plan":
        restore["pre_plan_permission_mode"] = requested["permission_mode"]
        if requested["execution_mode"] == "direct":
            key = ("pre_plan_execution_mode" if requested["permission_mode"] == "unrestricted"
                   else "pre_readonly_execution_mode")
            restore[key] = "direct"
    elif requested["permission_mode"] != "unrestricted" and requested["execution_mode"] == "direct":
        restore["pre_readonly_execution_mode"] = "direct"
    context = terminal.context_manager
    context.current_conversation_id = None
    context.conversation_metadata = {**modes, **restore}
    context.has_images = False
    context.has_videos = False
    model_key = defaults["model_key"]
    if model_key:
        terminal.set_model(model_key)
    profile = getattr(terminal, "model_profile", None) or {}
    run_mode = defaults["run_mode"]
    if profile.get("thinking_only"):
        run_mode = "thinking"
    elif profile.get("fast_only"):
        run_mode = "fast"
    terminal.set_run_mode(run_mode)
    terminal.set_reasoning_effort(defaults["reasoning_effort"])
    terminal.set_work_mode(modes["work_mode"], persist=False)
    terminal.set_permission_mode(modes["permission_mode"], persist=False)
    terminal.set_execution_mode(modes["execution_mode"])
    terminal.set_network_permission(modes["network_permission"])
    # Applying runtime constraints must not replace the draft's restoration record.
    context.conversation_metadata = {**modes, **restore}
    terminal.pending_permission_mode = None
    terminal.pending_execution_mode = None
    terminal.pending_network_permission = None
    return draft_settings(terminal)


def validate_creation_settings(settings: Any, *, host_mode: bool, can_manage: bool) -> dict:
    """A complete snapshot is required; creation never re-reads default modes."""
    if not isinstance(settings, dict) or any(key not in settings for key in SETTING_KEYS):
        raise ValueError("Complete new conversation settings are required")
    allowed = {"work_mode": WORK_MODES, "permission_mode": PERMISSIONS,
               "execution_mode": EXECUTION_MODES, "network_permission": NETWORK_MODES,
               "run_mode": {"fast", "thinking"}}
    for key, values in allowed.items():
        if not isinstance(settings[key], str) or settings[key] not in values:
            raise ValueError(f"Invalid {key}")
    if not isinstance(settings["thinking_mode"], bool) or settings["thinking_mode"] != (settings["run_mode"] == "thinking"):
        raise ValueError("thinking_mode does not match run_mode")
    effort = settings["reasoning_effort"]
    if effort is not None and (not isinstance(effort, str) or effort not in REASONING_EFFORT_LEVELS):
        raise ValueError("Invalid reasoning_effort")
    if settings["execution_mode"] == "direct" and not (host_mode and can_manage):
        raise PermissionError("Direct execution is only available to host administrators")
    if settings["network_permission"] != "restricted" and not (host_mode and can_manage):
        raise PermissionError("Network permission changes require a host administrator")
    modes = resolve_conversation_modes(settings, host_mode=host_mode)
    if any(modes[key] != settings[key] for key in modes):
        raise ValueError("Conversation settings violate plan or permission sandbox rules")
    model = settings["model_key"]
    if not isinstance(model, str) or model not in get_registered_model_keys():
        raise ValueError("Selected model is unavailable")
    profile = get_model_profile(model)
    if not profile:
        raise ValueError("Selected model is unavailable")
    if ((profile.get("thinking_only") and settings["run_mode"] != "thinking") or
            (profile.get("fast_only") and settings["run_mode"] != "fast")):
        raise ValueError("Selected model does not support the requested thinking mode")
    restore = settings.get("restore_modes", {})
    if not isinstance(restore, dict):
        raise ValueError("Invalid restore_modes")
    for key in RESTORE_KEYS:
        value = restore.get(key)
        allowed_values = PERMISSIONS if key == "pre_plan_permission_mode" else EXECUTION_MODES
        if value is not None and (not isinstance(value, str) or value not in allowed_values):
            raise ValueError(f"Invalid {key}")
        if value == "direct" and not (host_mode and can_manage):
            raise PermissionError("Direct execution restoration requires a host administrator")
    return {**{key: settings[key] for key in SETTING_KEYS},
            "restore_modes": {key: restore.get(key) for key in RESTORE_KEYS}}


def create_from_settings(terminal: Any, workspace: Any, settings: Any, *, multi_agent: bool = False) -> dict:
    """Create a file without changing any running conversation or workspace focus."""
    if getattr(terminal, "_bound_conversation_id", None):
        raise ValueError("Creation requires a workspace runtime")
    resolved = validate_creation_settings(
        settings, host_mode=terminal._is_host_mode(),
        can_manage=getattr(terminal, "user_role", "user") == "admin",
    )
    if resolved["model_key"] in (getattr(terminal, "admin_disabled_models", None) or []):
        raise PermissionError("Selected model is disabled by administrator policy")
    prefs = load_personalization_config(workspace.data_dir)
    context = terminal.context_manager
    manager = (context.multi_agent_conversation_manager if multi_agent else context.conversation_manager)
    metadata = {key: resolved[key] for key in
                ("work_mode", "permission_mode", "execution_mode", "network_permission", "reasoning_effort")}
    metadata.update(resolved["restore_modes"])
    metadata.update(multi_agent_mode=multi_agent, pending_permission_mode=None,
                    pending_execution_mode=None, pending_network_permission=None)
    metadata.update(snapshot_overrides_from_prefs(prefs, multi_agent_mode=multi_agent))
    with manager._io_lock:
        previous_id = manager.current_conversation_id
        try:
            conversation_id = manager.create_conversation(
                project_path=str(workspace.project_path), model_key=resolved["model_key"],
                run_mode=resolved["run_mode"], thinking_mode=resolved["thinking_mode"],
                initial_messages=[], metadata_overrides=metadata,
            )
        finally:
            manager.current_conversation_id = previous_id
    if prefs.get("versioning_enabled_by_default", True):
        from server.conversation import _ensure_conversation_versioning_enabled
        from server.utils_common import debug_log
        try:
            _ensure_conversation_versioning_enabled(terminal, workspace, conversation_id)
        except Exception as exc:
            debug_log(f"[Versioning] New conversation initialization failed: {exc}")
    return {"success": True, "conversation_id": conversation_id, "multi_agent_mode": multi_agent}
