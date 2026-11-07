"""Optional executor integrations; no agent, model or web imports.

MCP retains its own permission model and connection lifecycle. Routing it
through an executor does not place an external MCP server inside the native
filesystem sandbox. Registry configuration and active clients belong to owner.
"""
from __future__ import annotations

import asyncio
from typing import Any


def _error(key: str) -> dict:
    from modules.i18n import tr
    return {"success": False, "error": tr(key)}


async def execute_mcp_tool(owner: Any, tool_name: str, arguments: dict) -> Any:
    manager = getattr(owner, "mcp_client_manager", None)
    if manager is None:
        return _error("tools_exec.mcp_manager_unavailable")
    args = dict(arguments)
    args.pop("intent", None)
    return await asyncio.to_thread(
        manager.call_tool_by_alias, tool_name, args,
        alias_map=getattr(owner, "mcp_tool_alias_map", None),
    )


async def execute_custom_tool(owner: Any, tool_name: str, arguments: dict) -> Any:
    executor = getattr(owner, "custom_tool_executor", None)
    if executor is None:
        return {"success": False, "error": "Executor custom tools are not configured"}
    return await executor.run(tool_name, arguments)


async def list_mcp_servers(owner: Any, arguments: dict) -> dict:
    manager = getattr(owner, "mcp_client_manager", None)
    registry = getattr(owner, "mcp_server_registry", None)
    if manager is None or registry is None:
        return _error("tools_exec.mcp_module_unavailable")
    server_id = str(arguments.get("server_id") or "").strip() or None
    sync_result = None
    if arguments.get("refresh", False):
        sync_result = await asyncio.to_thread(manager.sync_servers, server_id=server_id)
    await asyncio.to_thread(registry.reload)
    servers = registry.list_servers(include_disabled=bool(arguments.get("include_disabled", False)))
    if server_id:
        servers = [item for item in servers if item.get("id") == server_id]
    _, alias_map = manager.build_llm_tools(ensure_discovery=False)
    aliases_by_server: dict[str, list[str]] = {}
    for alias, binding in (alias_map or {}).items():
        sid = getattr(binding, "server_id", "")
        if sid:
            aliases_by_server.setdefault(sid, []).append(alias)
    items = []
    for item in servers:
        sid = str(item.get("id") or "")
        names = [str((tool or {}).get("name") or "").strip()
                 for tool in (item.get("tools_cache") or []) if isinstance(tool, dict)]
        names = [name for name in names if name]
        items.append({
            "id": sid, "name": item.get("name") or sid,
            "enabled": bool(item.get("enabled", True)),
            "transport": item.get("transport"), "timeout_seconds": item.get("timeout_seconds"),
            "tools_cache_count": len(names), "tools_cache_names": names,
            "tool_aliases": sorted(aliases_by_server.get(sid, [])),
            "tools_cache_updated_at": item.get("tools_cache_updated_at"),
            "last_error": item.get("last_error") or "",
        })
    return {"success": True, "count": len(items), "servers": items, "sync_result": sync_result}
