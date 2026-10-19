"""Run via run_regressions.py so configuration and runtime data stay isolated."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modules.mcp_client_manager.manager import MCPClientManager
from modules.mcp_client_manager.models import MCPToolBinding
from modules.mcp_client_manager.runtime_scope import MCPRuntimeScope, prepare_stdio_server
from modules.mcp_client_manager.stdio_client import _StdioMCPClient


HERE = Path(__file__).resolve().parent
ALIAS = "mcp__playwright__browser_probe"
BINDING = MCPToolBinding(ALIAS, "playwright", "Playwright", "browser_probe", "stdio", 5)


class RuntimeScopeTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in list(os.environ):
            if name.startswith("PLAYWRIGHT_MCP_"):
                os.environ.pop(name)
        temp = tempfile.TemporaryDirectory(dir=HERE, prefix="case-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.scope = MCPRuntimeScope(str(self.workspace), str(self.root / "data"), "conv_a")
        self.session = SimpleNamespace(mode="host", workspace_path=str(self.workspace),
                                       mount_path=str(self.workspace))
        self.server = {
            "id": "playwright", "transport": "stdio", "command": sys.executable,
            "args": [str(HERE / "fake_playwright.py"), "@playwright/mcp@latest"],
            "env": {}, "enabled": True,
        }

    def prepared(self, scope=None, server=None, session=None):
        return prepare_stdio_server(server or self.server, scope or self.scope,
                                    session or self.session)

    def profile(self, **kwargs):
        return self.prepared(**kwargs)["args"][-1]

    def manager(self, cid="conv_a", resolver=None):
        registry = SimpleNamespace(get_server=lambda _: self.server)
        scope = replace(self.scope, conversation_id=cid)
        manager = MCPClientManager(registry, container_session=self.session,
                                   runtime_scope_resolver=resolver or (lambda: scope))
        self.addCleanup(manager.close_all_clients)
        return manager

    def probe(self, manager, **arguments):
        result = manager.call_tool_by_alias(ALIAS, arguments, alias_map={ALIAS: BINDING})
        self.assertTrue(result["success"], result)
        return json.loads(result["content"][0]["text"])

    def test_profile_identity_is_stable_and_fully_scoped(self):
        path = self.profile()
        self.assertEqual(path, self.profile())
        self.assertEqual(Path(path).parent, Path(self.scope.data_dir) / "mcp_profiles")
        for scope in (
            replace(self.scope, conversation_id="conv_b"),
            replace(self.scope, workspace_path=str(self.root / "other-project")),
            replace(self.scope, data_dir=str(self.root / "other-user")),
        ):
            with self.subTest(scope=scope):
                self.assertNotEqual(path, self.profile(scope=scope))
        self.assertNotEqual(path, self.profile(server={**self.server, "id": "second-browser"}))
        malicious = replace(self.scope, conversation_id="../../outside")
        self.assertEqual(Path(self.profile(scope=malicious)).parent, Path(path).parent)

    def test_configuration_and_cwd_are_preserved(self):
        original = json.loads(json.dumps(self.server))
        prepared = self.prepared()
        self.assertEqual(self.server, original)
        client = _StdioMCPClient(prepared, 5, "2024-11-05", self.session)
        command, cwd, _ = client._prepare_launch()
        self.assertEqual(cwd, str(self.workspace))
        self.assertEqual(command[-2:], ["--user-data-dir", self.profile()])

    def test_other_mcp_and_http_are_unchanged(self):
        for server in (
            {**self.server, "args": ["other-package"]},
            {**self.server, "transport": "streamable_http", "url": "http://localhost/mcp"},
        ):
            self.assertIs(self.prepared(server=server), server)
        self.assertIs(prepare_stdio_server(self.server, None, self.session), self.server)
        self.assertIs(self.prepared(scope=replace(self.scope, conversation_id=None)), self.server)

    def test_playwright_launch_detection(self):
        for command, args in (
            ("npx", ["-y", "@playwright/mcp@latest"]),
            ("npm", ["exec", "--package", "@playwright/mcp", "--", "playwright-mcp"]),
            ("node", ["/opt/node_modules/@playwright/mcp/cli.js"]),
            (r"C:\tools\playwright-mcp.cmd", []),
        ):
            with self.subTest(command=command, args=args):
                server = {**self.server, "command": command, "args": args}
                self.assertEqual(self.prepared(server=server)["args"][-2], "--user-data-dir")

    def test_explicit_modes_and_profile_overrides_are_honored(self):
        for args in (
            ["--isolated"], ["--isolated=true"], ["--user-data-dir", "/custom"],
            ["--user-data-dir=/custom"], ["--config", "browser.json"],
            ["--extension"], ["--cdp-endpoint=http://localhost:9222"],
            ["--endpoint", "ws://localhost/browser"],
        ):
            with self.subTest(args=args):
                server = {**self.server, "args": [*self.server["args"], *args]}
                self.assertIs(self.prepared(server=server), server)
        for name in ("USER_DATA_DIR", "CONFIG", "CDP_ENDPOINT", "ENDPOINT", "ISOLATED", "EXTENSION"):
            key = "PLAYWRIGHT_MCP_" + name
            value = "true" if name in {"ISOLATED", "EXTENSION"} else "/custom"
            with self.subTest(env=key):
                server = {**self.server, "env": {key: value}}
                self.assertIs(self.prepared(server=server), server)
                with patch.dict(os.environ, {key: value}):
                    self.assertIs(self.prepared(), self.server)
        with patch.dict(os.environ, {"PLAYWRIGHT_MCP_ISOLATED": "false"}):
            self.assertIsNot(self.prepared(), self.server)

    def test_docker_uses_mounted_conversation_directory(self):
        session = SimpleNamespace(mode="docker", workspace_path=str(self.workspace),
                                  mount_path="/workspace", container_name="fixture", sandbox_bin="docker")
        prepared = self.prepared(session=session)
        self.assertTrue(prepared["args"][-1].startswith("/workspace/.astrion/mcp_profiles/"))
        client = _StdioMCPClient(prepared, 5, "2024-11-05", session)
        with patch("modules.mcp_client_manager.stdio_client.shutil.which", return_value="/bin/docker"):
            command, cwd, _ = client._prepare_launch()
        self.assertIn("fixture", command)
        self.assertEqual(command[-2:], ["--user-data-dir", prepared["args"][-1]])
        self.assertIsNone(cwd)
        # Host-only env is not inherited by docker exec.
        with patch.dict(os.environ, {"PLAYWRIGHT_MCP_USER_DATA_DIR": "/host-only"}):
            self.assertIsNot(self.prepared(session=session), self.server)

    def test_two_conversations_keep_independent_processes_pages_and_storage(self):
        first, second = self.manager("conv_a"), self.manager("conv_b")
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(self.probe, first, url="https://a.test", login="alice")
            b = executor.submit(self.probe, second, url="https://b.test", login="bob")
            left, right = a.result(), b.result()
        self.assertNotEqual(left["pid"], right["pid"])
        self.assertNotEqual(left["profile"], right["profile"])
        self.assertEqual(left["cwd"], right["cwd"])
        self.assertEqual(left["cwd"], str(self.workspace))
        self.assertEqual(self.probe(first)["page"], "https://a.test")
        self.assertEqual(self.probe(second)["login"], "bob")
        first.close_all_clients()
        restored = self.probe(self.manager("conv_a"))
        self.assertEqual(restored["profile"], left["profile"])
        self.assertEqual(restored["login"], "alice")
        self.assertEqual(self.probe(second)["page"], "https://b.test")

    def test_switching_scope_recreates_client_and_keeps_persistent_state(self):
        current = [self.scope]
        manager = self.manager(resolver=lambda: current[0])
        first = self.probe(manager, login="alice")
        self.assertEqual(first["pid"], self.probe(manager)["pid"])
        current[0] = replace(self.scope, conversation_id="conv_b")
        second = self.probe(manager, login="bob")
        self.assertNotEqual(first["profile"], second["profile"])
        current[0] = self.scope
        restored = self.probe(manager)
        self.assertEqual(restored["profile"], first["profile"])
        self.assertEqual(restored["login"], "alice")

    def test_fixture_detects_shared_profile_conflict(self):
        # A control case proves the fixture really enforces exclusive ownership.
        path = str(self.root / "shared-profile")
        self.server["args"].extend(["--user-data-dir", path])
        first, second = self.manager("conv_a"), self.manager("conv_b")
        self.probe(first)
        result = second.call_tool_by_alias(ALIAS, {}, alias_map={ALIAS: BINDING})
        self.assertFalse(result["success"])
        self.assertIn("already in use", result["message"])
