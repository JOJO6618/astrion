"""Native working-directory authority and separate external plugin clients."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from modules.execution_scope import ExecutionScope, bind_execution_scope, current_execution_scope
from modules.file_manager import FileManager
from modules.scoped_execution_policy import fixed_working_dir, scoped_work_path
from modules.sub_agent.runtime import SubAgentRuntimeMixin
from modules.mcp_client_manager.runtime_scope import MCPRuntimeScope, prepare_stdio_server


class DirectoryScopeTests(unittest.TestCase):
    def test_child_relative_files_and_parent_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            work = root / "child"
            work.mkdir()
            (root / "source.txt").write_text("parent source")
            manager = FileManager(str(root), data_dir=str(root / "data"))
            scope = ExecutionScope(executor_kind="sub_agent", actor_id="child", workspace_root=root,
                                   access_level="working_dir_write", working_dir=work)
            with bind_execution_scope(scope), \
                 patch("modules.host_sandbox_policy.get_macos_readable_paths", return_value=[]), \
                 patch("modules.host_sandbox_policy.get_macos_writable_paths", return_value=[]):
                self.assertEqual(fixed_working_dir(scope), work)
                self.assertEqual(scoped_work_path(scope, None), work)
                result = manager.write_file("inside.txt", "allowed")
                self.assertTrue(result["success"], result)
                self.assertEqual((work / "inside.txt").read_text(), "allowed")
                denied = manager.write_file(str(root / "forbidden.txt"), "denied")
                self.assertFalse(denied["success"], denied)
                self.assertFalse((root / "forbidden.txt").exists())
                source = manager.read_file(str(root / "source.txt"))
                self.assertTrue(source["success"], source)

    def test_linux_narrow_write_mount_and_validation(self):
        from modules.linux_sandbox.schema import validate_request, RequestError
        from modules.linux_sandbox.sources import specifications
        request = dict(operation="run", workspace="/project", cwd="/project/child", reads=[], writes=[],
                       readonly=False, workspace_only=True, network="none", argv=["true"], env={}, umask=0o022,
                       workspace_write_root="/project/child")
        mounts = specifications(validate_request(request))
        project = next(item for item in mounts if item["target"] == "/project")
        child = next(item for item in mounts if item["target"] == "/project/child")
        self.assertFalse(project["write"])
        self.assertTrue(child["write"])
        with self.assertRaises(RequestError):
            validate_request({**request, "workspace_write_root": "/outside"})
        with self.assertRaises(RequestError):
            validate_request({**request, "writes": ["/outside"]})

    def test_playwright_profile_separates_parent_and_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = str(Path(temporary).resolve())
            server = {"id": "browser", "transport": "stdio", "command": "npx", "args": ["@playwright/mcp@latest"]}
            scopes = [MCPRuntimeScope(root, root, "parent", actor_id=actor, working_dir=root)
                      for actor in (None, "child-a", "child-b")]
            configs = [prepare_stdio_server(server, scope, None) for scope in scopes]
            self.assertEqual(len({str(config["args"]) for config in configs}), 3)
            self.assertNotIn("cwd", server)
            explicit = {**server, "args": ["@playwright/mcp@latest", "--user-data-dir", "/explicit"]}
            self.assertEqual(prepare_stdio_server(explicit, scopes[1], None)["args"], explicit["args"])


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_has_separate_manager_filters_tools_and_calls_outside_native_scope(self):
        created = []
        binding = SimpleNamespace(server_id="external")

        class Client:
            def __init__(self, registry, **kwargs):
                self.scope = kwargs["runtime_scope_resolver"]()
                self.closed = False
                created.append(self)

            def build_llm_tools(self):
                return ([{"type": "function", "function": {"name": name, "parameters": {}}}
                         for name in ("mcp__external__allowed", "mcp__external__hidden")],
                        {"mcp__external__allowed": binding, "mcp__external__hidden": binding})

            def call_tool_by_alias(self, name, arguments, **kwargs):
                assert current_execution_scope() is None
                assert "intent" not in arguments
                return {"success": True, "message": "external result", "structured_content": {"ok": True}}

            def close_all_clients(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent = SimpleNamespace(mcp_tools_enabled=True, mcp_server_registry=object(), disabled_tools=set(),
                                     mcp_tool_alias_map={"mcp__external__allowed": binding})
            manager = SimpleNamespace(terminal=parent, project_path=root, data_dir=root, container_session=None)
            children = []
            with patch("modules.mcp_client_manager.MCPClientManager", Client):
                for name in ("one", "two"):
                    child = SubAgentRuntimeMixin()
                    child.manager = manager
                    child.task_record = {"round_id": 1, "conversation_id": "parent", "working_dir": str(root)}
                    child.task_id = name
                    child._init_round_runtime()
                    tools = await child._build_runtime_tools()
                    aliases = {item["function"]["name"] for item in tools}
                    self.assertIn("mcp__external__allowed", aliases)
                    self.assertNotIn("mcp__external__hidden", aliases)
                    children.append(child)
                self.assertIsNot(children[0]._mcp_manager, children[1]._mcp_manager)
                self.assertEqual([client.scope.actor_id for client in created], ["one", "two"])
                result = await children[0]._execute_mcp_tool("mcp__external__allowed", {"intent": "test", "x": 1})
                self.assertTrue(result["success"])
                parent.disabled_tools.add("mcp__external__allowed")
                denied = await children[0]._execute_mcp_tool("mcp__external__allowed", {})
                self.assertFalse(denied["success"])
                await children[0]._build_runtime_tools()
                self.assertTrue(created[0].closed)


if __name__ == "__main__":
    unittest.main()
