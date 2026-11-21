"""Resolve MCP availability from persisted settings and conversation switches."""
from __future__ import annotations

from typing import Any

ACTORS = ("main", "sub")
MCP_CATEGORY = "mcp"
SERVER_CATEGORY_PREFIX = "mcp_server__"


def normalize_selection(value: Any, server_ids: set[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("MCP selection must be a list of server IDs")
    return sorted(set(value) & server_ids)


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def default_selection(access: dict, actor: str) -> list[str] | None:
    selected = _mapping(access.get("defaults")).get(actor)
    return selected if isinstance(selected, list) else None


def workspace_selection(access: dict, workspace_id: str | None, actor: str) -> dict | None:
    workspace = _mapping(_mapping(access.get("workspaces")).get(workspace_id))
    selection = _mapping(workspace.get(actor))
    if isinstance(selection.get("inherit"), bool) and isinstance(selection.get("server_ids"), list):
        return selection
    return None


def remove_server_from_access(access: Any, server_id: str) -> None:
    access = _mapping(access)
    for actor in ACTORS:
        selected = default_selection(access, actor)
        if selected is not None:
            selected[:] = [item for item in selected if item != server_id]
        for workspace_id in _mapping(access.get("workspaces")):
            selection = workspace_selection(access, workspace_id, actor)
            if selection is not None:
                selected = selection["server_ids"]
                selected[:] = [item for item in selected if item != server_id]


def legacy_category_enabled(key: str, defaults: dict, disabled: list, forced: dict) -> bool:
    state = forced.get(key)
    if isinstance(state, bool):
        return state
    return key not in disabled and defaults.get(key, True)


def available_mcp_servers(owner, actor: str = "main", *, conversation: bool = True) -> list[dict]:
    """Return the services that can be discovered, listed and called now."""
    registry = getattr(owner, "mcp_server_registry", None)
    if registry is None or not getattr(owner, "mcp_tools_enabled", False):
        return []
    servers, access = registry.read_settings()
    workspace = workspace_selection(access, getattr(owner, "workspace_id", None), actor)
    selected = (
        workspace["server_ids"] if workspace is not None and not workspace["inherit"]
        else default_selection(access, actor)
    )
    legacy = selected is None
    selected_ids = {item for item in selected or [] if isinstance(item, str)}
    categories = getattr(owner, "tool_categories_map", {}) if legacy else {}
    defaults = {key: cat.default_enabled for key, cat in categories.items()}
    forced = getattr(owner, "admin_forced_category_states", {}) if legacy else {}
    disabled = getattr(owner, "default_disabled_tool_categories", []) if legacy else []
    overrides = getattr(owner, "runtime_tool_category_overrides", {}) if conversation and actor == "main" else {}
    overrides = overrides or {}

    def enabled(key: str) -> bool:
        return overrides.get(key, True) and legacy_category_enabled(key, defaults, disabled, forced)

    if not enabled(MCP_CATEGORY):
        return []
    return [server for server in servers if server["enabled"]
            and (legacy or server["id"] in selected_ids)
            and enabled(registry.make_server_category_id(server["id"]))]


def is_mcp_category(key: str) -> bool:
    return key == MCP_CATEGORY or key.startswith(SERVER_CATEGORY_PREFIX)


def is_mcp_tool(name: str) -> bool:
    return name == "list_mcp_servers" or name.startswith("mcp__")


def replace_mcp_tools(tools: list[dict], mcp_tools: list[dict]) -> list[dict]:
    normal = [item for item in tools if not is_mcp_tool(item["function"]["name"])]
    if normal and normal[-1]["function"]["name"] == "load_tools":
        return normal[:-1] + mcp_tools + normal[-1:]
    return normal + mcp_tools


def list_mcp_tool(inject_intent=None) -> dict:
    properties = {
        "refresh": {"type": "boolean", "description": "是否刷新可用 MCP 服务的工具缓存（默认 false）"},
        "server_id": {"type": "string", "description": "仅查看指定的可用 MCP 服务（可选）"},
    }
    return {
        "type": "function",
        "function": {
            "name": "list_mcp_servers",
            "description": "列出当前智能体可用的 MCP 服务与工具别名。可选刷新工具缓存。",
            "parameters": {
                "type": "object",
                "properties": inject_intent(properties) if inject_intent else properties,
                "required": [],
            },
        },
    }
