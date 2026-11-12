"""Build the main Agent system once per conversation/compression boundary."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from config.model_profiles import get_model_prompt_replacements, model_supports_image, model_supports_video
from modules.personalization_manager import (
    build_personalization_prompt,
    load_personalization_config,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
)
from modules.skills_manager import (
    build_skills_list, get_skills_catalog, infer_private_skills_dir, merge_enabled_skills,
)
from modules.system_prompt_snapshot import (
    LEGACY_PROMPT_KEYS, SYSTEM_PROMPT_KEY, legacy_system_text,
)


def _render(owner: Any, name: str, **values: Any) -> str:
    template = owner.load_prompt(f"main_agent/dynamic/{name}")
    if not template:
        raise RuntimeError(f"Missing main Agent prompt template: {name}")
    return template.format(**values).strip()


def _project_rules(owner: Any, prefs: dict, filename: str, enabled_key: str) -> str:
    if not prefs.get(enabled_key, False):
        return ""
    # Imported lazily to keep the history builder independent of this module.
    from .messages import _build_sub_md_notice

    content = owner._load_root_md_content(filename)
    if not content:
        return ""
    paths, total = owner._load_sub_md_paths(filename)
    return _render(
        owner, "project_rules", filename=filename,
        updated_at=owner._load_root_md_updated_at(filename), content=content,
        sub_notice=_build_sub_md_notice(filename, paths, total),
    )


def _recent_conversations(owner: Any, prefs: dict) -> str:
    if not prefs.get("recent_conversations_prompt_enabled", False):
        return ""
    try:
        limit = int(prefs.get("recent_conversations_prompt_limit", RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT))
    except (TypeError, ValueError):
        limit = RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT
    limit = max(RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN, min(RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX, limit))
    content = owner._build_recent_conversations_message(limit=limit) or ""
    return _render(owner, "recent_conversations", recent_conversations=content) if content else ""


def _skills(owner: Any, prefs: dict) -> str:
    catalog = get_skills_catalog(
        private_dir=infer_private_skills_dir(owner.data_dir),
        project_path=owner.project_path,
        scan_project_agents=bool(prefs.get("agents_skills_scan_enabled", True)),
    )
    enabled = merge_enabled_skills(prefs.get("enabled_skills"), catalog, prefs.get("skills_catalog_snapshot"))
    return _render(owner, "skills", skills_list=build_skills_list(catalog, enabled) or "暂无可用的skills")


def _tool_catalog(owner: Any) -> str:
    from core.tool_loading import get_tool_loading_state, render_catalog

    state = get_tool_loading_state(owner.context_manager.conversation_metadata)
    if not state or not state["deferred_set"]:
        return ""
    catalog = render_catalog(state["deferred_set"], unavailable=set(getattr(owner, "disabled_tools", None) or ()))
    return _render(owner, "tool_loading", tool_catalog=catalog) if catalog else ""


def _features(owner: Any) -> str:
    from modules.preview_targets import is_preview_enabled
    from modules.native_browser.availability import native_browser_supported
    from modules.native_browser.state import browser_mode, BROWSER_MODE_ON

    browser = native_browser_supported() and getattr(owner, "conversation_kind", "agent") != "chat"
    browser_state = "不可用"
    if browser:
        browser_state = "开启" if browser_mode(owner) == BROWSER_MODE_ON else "关闭"
    return _render(
        owner, "features", preview_state="可用" if is_preview_enabled() else "不可用",
        browser_state=browser_state,
    )


def _modify_history(owner: Any) -> str:
    from modules.modify_history import get_modify_history_dir, is_modify_history_enabled

    cm = owner.context_manager
    if not is_modify_history_enabled(getattr(owner, "data_dir", None)):
        return ""
    path = get_modify_history_dir(owner.project_path, cm.current_conversation_id)
    return _render(owner, "modify_history", modify_history_dir=str(path)) if path is not None else ""


def build_system_prompt(owner: Any, context: dict) -> tuple[str, dict]:
    """The static file is verbatim; interpolation is confined to dynamic parts."""
    cm = owner.context_manager
    prefs = getattr(cm, "custom_personalization_config", None) or load_personalization_config(owner.data_dir)
    prefs = prefs if isinstance(prefs, dict) else {}
    static = owner.load_prompt("main_agent/static").strip()
    if not static:
        raise RuntimeError("Missing main Agent static system prompt")
    model = getattr(owner, "model_key", None)
    vision = "vision_video" if model_supports_video(model) else "vision_image" if model_supports_image(model) else "vision_text"
    personal = build_personalization_prompt(prefs, include_header=False)
    from modules.workspace_memory_context import prepare_main_prompt

    memory_text, memory_updates = prepare_main_prompt(owner, template_name="main_agent/dynamic/workspace_memory")
    custom = getattr(cm, "custom_system_prompt", "")
    disabled_notice = owner._format_disabled_tool_notice() or ""
    parts = [
        static,
        _render(owner, "personalization", personalization_block=personal) if personal else "",
        _project_rules(owner, prefs, "AGENTS.md", "agents_md_auto_inject"),
        _project_rules(owner, prefs, "CLAUDE.md", "claude_md_auto_inject"),
        _skills(owner, prefs),
        owner._build_memory_system_content(template_name="main_agent/dynamic/memory"),
        memory_text,
        _render(owner, "custom_system", custom_system_prompt=custom) if isinstance(custom, str) and custom.strip() else "",
        _render(owner, "model", model_description=get_model_prompt_replacements(model).get("model_description", "")),
        _render(owner, vision),
        owner._build_permission_mode_message() or "",
        owner._build_execution_mode_message() or "",
        owner._build_work_mode_message() or "",
        _features(owner),
        _tool_catalog(owner),
        _render(owner, "disabled_tools", disabled_notice=disabled_notice) if disabled_notice else "",
        _recent_conversations(owner, prefs),
        cm._build_workspace_system_message(context) or "",
        _modify_history(owner),
    ]
    return "\n\n".join(part for part in parts if part), memory_updates


def _provider_prefix(owner: Any) -> str:
    client = getattr(owner, "api_client", None)
    if getattr(client, "api_protocol", None) != "responses":
        return ""
    config = client._select_api_config(client.get_current_thinking_mode())
    return client._build_responses_instructions(config["model_id"]) or ""


def frozen_system_prompt(owner: Any, context: dict) -> str:
    """One durable field is authoritative. Pure readers never migrate files."""
    cm = owner.context_manager
    cached = cm.conversation_metadata.get(SYSTEM_PROMPT_KEY)
    if isinstance(cached, str) and cached:
        return cached
    conversation_id = cm.current_conversation_id
    if not conversation_id:
        raise RuntimeError("Cannot freeze the system prompt before a conversation is created")
    manager = cm._get_conversation_manager_for_id(conversation_id)
    original_metadata = cm.conversation_metadata

    def initialize(document: dict) -> str:
        metadata = document.setdefault("metadata", {})
        existing = metadata.get(SYSTEM_PROMPT_KEY)
        if isinstance(existing, str) and existing:
            return existing
        if existing is not None:
            raise ValueError("Invalid frozen system prompt; refusing to silently rebuild it")
        cm.conversation_metadata = deepcopy(metadata)
        legacy = legacy_system_text(metadata)
        if legacy:
            body, updates = legacy, {}
        else:
            body, updates = build_system_prompt(owner, context)
        prefix = _provider_prefix(owner)
        text = f"{prefix}\n\n{body}" if prefix else body
        if not text:
            raise ValueError("Cannot persist an empty system prompt")
        metadata.update(updates)
        metadata[SYSTEM_PROMPT_KEY] = text
        for key in LEGACY_PROMPT_KEYS:
            metadata.pop(key, None)
        return text

    try:
        document, text = manager.mutate_conversation(conversation_id, initialize)
    except BaseException:
        cm.conversation_metadata = original_metadata
        raise
    cm.conversation_metadata = deepcopy(document["metadata"])
    return text
