"""MCP settings, runtime access and server registry regression tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from flask import Flask

from modules.execution_plane.integrations import describe_mcp_servers
from modules.mcp_access import available_mcp_servers
from modules.mcp_client_manager import MCPClientManager
from modules.mcp_server_registry import MCPServerRegistry
from server import mcp_settings, state


class RegistryCase(TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.path = Path(root.name) / "mcp_servers.json"
        self.registry = MCPServerRegistry(str(self.path), enabled=True)
        self.registry.upsert_server({"id": "docs", "transport": "stdio", "command": "unused"})
        self.registry.upsert_server({"id": "browser", "transport": "stdio", "command": "unused"})


class RuntimeTests(RegistryCase):
    def setUp(self):
        super().setUp()
        self.owner = SimpleNamespace(
            mcp_tools_enabled=True, mcp_server_registry=self.registry, workspace_id="alpha",
        )

    def assert_servers(self, actor, expected):
        self.assertEqual({item["id"] for item in available_mcp_servers(self.owner, actor)}, set(expected))

    def test_workspace_override_inheritance_and_global_disable(self):
        self.owner.default_disabled_tool_categories = ["mcp"]
        self.assert_servers("main", [])
        self.assert_servers("sub", [])
        self.registry.save_access("main", ["docs"])
        self.registry.save_access("sub", ["browser"])
        self.assert_servers("main", ["docs"])
        self.assert_servers("sub", ["browser"])
        self.registry.save_access("main", {"inherit": False, "server_ids": []}, "alpha")
        self.registry.save_access("sub", {"inherit": False, "server_ids": ["docs"]}, "alpha")
        self.assert_servers("main", [])
        self.assert_servers("sub", ["docs"])
        self.owner.workspace_id = "beta"
        self.assert_servers("main", ["docs"])
        self.assert_servers("sub", ["browser"])
        self.owner.runtime_tool_category_overrides = {"mcp": False}
        self.assert_servers("main", [])
        self.assert_servers("sub", ["browser"])
        self.owner.workspace_id = "alpha"
        self.registry.save_access("sub", {"inherit": True, "server_ids": ["docs"]}, "alpha")
        self.assert_servers("sub", ["browser"])
        self.registry.upsert_server({"id": "browser", "enabled": False}, create=False)
        self.assert_servers("sub", [])

    def test_discovery_visibility_and_revoking_a_previously_visible_alias(self):
        self.registry.save_access("main", ["docs"])
        manager = MCPClientManager(self.registry, available_servers_resolver=lambda: available_mcp_servers(self.owner))
        self.addCleanup(manager.close_all_clients)
        with patch.object(manager, "_run_with_client", return_value=(
            [{"name": "find"}, {"name": "hidden"}], {},
        )) as discover:
            self.registry.upsert_server({"id": "docs", "exclude_tools": ["hidden"]}, create=False)
            tools, bindings = manager.build_llm_tools()
            self.assertEqual([item["function"]["name"] for item in tools], ["mcp__docs__find"])
            self.assertEqual([call.args[0]["id"] for call in discover.call_args_list], ["docs"])
            self.assertFalse(manager.sync_servers("browser")["success"])
            self.assertEqual(discover.call_count, 1)
            visible = describe_mcp_servers(manager, {"include_disabled": True})["servers"]
            self.assertEqual([row["id"] for row in visible], ["docs"])
            self.assertEqual(visible[0]["tools_cache_names"], ["find"])
            self.assertFalse(manager.call_tool_by_alias("mcp__docs__find", {}, alias_map={})["success"])
            self.registry.save_access("main", [])
            self.assertFalse(manager.call_tool_by_alias("mcp__docs__find", {}, alias_map=bindings)["success"])
            self.assertEqual(describe_mcp_servers(manager, {"refresh": True})["servers"], [])
            self.assertEqual(discover.call_count, 1)

    def test_discovery_cache_merges_with_newer_registry_writes(self):
        second = MCPServerRegistry(str(self.path), enabled=True)
        old_connection = self.registry.get_server("docs")
        second.save_access("main", ["browser"])
        second.upsert_server({"id": "docs", "command": "replacement"}, create=False)
        self.assertIsNone(self.registry.update_tools_cache("docs", tools=[{"name": "stale"}], expected_server=old_connection))
        self.registry.update_tools_cache("browser", tools=[{"name": "inspect"}])
        services, access = second.read_settings()
        self.assertEqual(access["defaults"]["main"], ["browser"])
        self.assertEqual({item["id"]: item["command"] for item in services}["docs"], "replacement")
        self.assertEqual(second.get_server("browser")["tools_cache"], [{"name": "inspect"}])
        self.registry.delete_server("browser")
        self.registry.upsert_server({"id": "browser", "transport": "stdio", "command": "new"}, create=True)
        self.assert_servers("main", [])


class SettingsApiTests(RegistryCase):
    def test_authenticated_host_crud_without_secondary_password(self):
        app = Flask(__name__)
        app.secret_key = "test-only"
        app.register_blueprint(mcp_settings.mcp_settings_bp)
        client = app.test_client()
        self.assertEqual(client.get("/api/mcp/settings").status_code, 401)
        self.assertEqual(client.put("/api/mcp/access/main", json={"server_ids": []}).status_code, 401)
        with client.session_transaction() as session:
            session.update(username="host", host_mode=True, login_nonce="mcp-settings-test")
        state.active_login_nonces["host"].add("mcp-settings-test")
        self.addCleanup(state.active_login_nonces["host"].discard, "mcp-settings-test")
        catalog = {"workspaces": [{"workspace_id": "alpha", "label": "Alpha"}], "default_workspace_id": "alpha"}
        with patch.object(mcp_settings, "mcp_server_registry", self.registry), \
                patch.object(mcp_settings, "_catalog", return_value=catalog), \
                patch.object(mcp_settings, "_legacy_selection", return_value=["docs"]), \
                patch("server.host_password.browser_session_valid", return_value=True):
            result = client.get("/api/mcp/settings")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["data"]["defaults"], {"main": ["docs"], "sub": ["docs"]})
            self.assertEqual(client.put("/api/mcp/access/main", json={"server_ids": []}).status_code, 200)
            choice = {"inherit": False, "server_ids": ["browser"]}
            self.assertEqual(client.put("/api/mcp/access/sub/missing", json=choice).status_code, 404)
            self.assertEqual(client.put("/api/mcp/access/sub/alpha", json=choice).status_code, 200)
            server = {"id": "test", "transport": "streamable_http", "url": "https://example.invalid/mcp",
                      "headers": {"Authorization": "test-placeholder"}}
            self.assertEqual(client.post("/api/mcp/servers", json=server).status_code, 200)
            self.assertEqual(client.post("/api/mcp/servers", json=server).status_code, 400)
            result = client.get("/api/mcp/settings").json["data"]
            self.assertEqual(result["defaults"]["main"], [])
            self.assertEqual(result["workspaces"][0]["access"]["sub"], choice)
            self.assertNotIn("test-placeholder", str(result))
            self.assertEqual(client.put("/api/mcp/servers/test", json={"enabled": False}).status_code, 200)
            detail = client.get("/api/mcp/servers/test").json["data"]
            self.assertFalse(detail["enabled"])
            self.assertEqual(detail["headers"], server["headers"])
            self.assertEqual(client.delete("/api/mcp/servers/test").status_code, 200)
            self.assertEqual(client.get("/api/mcp/servers/test").status_code, 404)
