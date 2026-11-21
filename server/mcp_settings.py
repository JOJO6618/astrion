"""Host settings for MCP services and agent allowlists."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from config import DATA_DIR
from modules.host_workspace_manager import load_host_workspace_catalog
from modules.i18n import tr
from modules.mcp_access import ACTORS, default_selection, legacy_category_enabled, workspace_selection
from modules.mcp_client_manager import MCPClientManager
from modules.mcp_server_registry import MCPServerRegistry
from modules.personalization_manager import load_personalization_config
from utils.logger import setup_logger

from .auth_helpers import api_login_required, resolve_admin_policy
from .state import mcp_server_registry

mcp_settings_bp = Blueprint("mcp_settings", __name__, url_prefix="/api/mcp")
logger = setup_logger(__name__)


def _catalog():
    return load_host_workspace_catalog()


def _server_summary(server):
    fields = ("id", "name", "description", "enabled", "transport", "tools_cache_updated_at", "last_error")
    return {**{key: server[key] for key in fields}, "tools_cache_count": len(server["tools_cache"])}


def _body() -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("Expected an MCP settings object")
    return payload


def _legacy_selection(servers):
    policy = resolve_admin_policy()
    defaults = {key: row["default_enabled"] for key, row in policy["categories"].items()}
    forced = policy["forced_category_states"]
    disabled = load_personalization_config(DATA_DIR).get("disabled_tool_categories", [])
    if not legacy_category_enabled("mcp", defaults, disabled, forced):
        return []
    return [server["id"] for server in servers if legacy_category_enabled(
        MCPServerRegistry.make_server_category_id(server["id"]), defaults, disabled, forced,
    )]


@mcp_settings_bp.get("/settings")
@api_login_required
def get_settings():
    servers, access = mcp_server_registry.read_settings()
    global_defaults = {actor: default_selection(access, actor) for actor in ACTORS}
    if None in global_defaults.values():
        legacy = _legacy_selection(servers)
        global_defaults = {actor: legacy if ids is None else ids for actor, ids in global_defaults.items()}
    catalog = _catalog()
    workspaces = []
    for workspace in catalog["workspaces"]:
        workspace_id = workspace["workspace_id"]
        workspaces.append({
            "id": workspace_id,
            "label": workspace["label"],
            "access": {actor: workspace_selection(access, workspace_id, actor) for actor in ACTORS},
        })
    return jsonify(success=True, data={
        "bridge_enabled": mcp_server_registry.enabled,
        "servers": [_server_summary(server) for server in servers],
        "defaults": global_defaults,
        "workspaces": workspaces,
        "default_workspace_id": catalog["default_workspace_id"],
    })


@mcp_settings_bp.put("/access/<actor>")
@mcp_settings_bp.put("/access/<actor>/<workspace_id>")
@api_login_required
def save_access(actor, workspace_id=None):
    if actor not in ACTORS:
        return jsonify(success=False), 404
    if workspace_id is not None and not any(
        row["workspace_id"] == workspace_id for row in _catalog()["workspaces"]
    ):
        return jsonify(success=False), 404
    try:
        payload = _body()
        mcp_server_registry.save_access(actor, payload if workspace_id else payload.get("server_ids"), workspace_id)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    return jsonify(success=True)


@mcp_settings_bp.post("/servers")
@mcp_settings_bp.route("/servers/<server_id>", methods=["GET", "PUT", "DELETE"])
@api_login_required
def manage_server(server_id=None):
    try:
        if request.method == "GET":
            mcp_server_registry.reload()
            server = mcp_server_registry.get_server(server_id)
            if server is None:
                raise KeyError(server_id)
            return jsonify(success=True, data={key: value for key, value in server.items() if key != "tools_cache"})
        if request.method == "DELETE":
            if not mcp_server_registry.delete_server(server_id):
                raise KeyError(server_id)
            return jsonify(success=True)
        payload = _body()
        if server_id is not None:
            payload["id"] = server_id
        saved = mcp_server_registry.upsert_server(payload, create=server_id is None)
        return jsonify(success=True, data=_server_summary(saved))
    except KeyError:
        return jsonify(success=False, error=tr("admin.server_not_found")), 404
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400


@mcp_settings_bp.post("/sync")
@api_login_required
def sync_servers():
    try:
        server_id = _body().get("id")
        if server_id is not None and (not isinstance(server_id, str) or not server_id):
            raise ValueError("Invalid MCP server ID")
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    manager = MCPClientManager(mcp_server_registry)
    try:
        result = manager.sync_servers(server_id=server_id)
        status = 200 if result["success"] else 502
        return jsonify(success=result["success"], error=result.get("error")), status
    except Exception:
        logger.exception("MCP settings sync failed")
        return jsonify(success=False), 500
    finally:
        manager.close_all_clients()
