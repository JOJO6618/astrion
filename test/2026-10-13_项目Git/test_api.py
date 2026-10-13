"""Real Flask routes, synthetic auth/runtime, temporary Git repositories."""
import importlib.util
import types
import sys
import unittest
from unittest.mock import patch
from flask import Flask
import test_project_git as fixtures
from modules.project_git.discovery import discover


class GitApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GitHistoryTests()
        self.fixture.setUp()
        identity = types.SimpleNamespace(host_mode=True)
        package = fixtures.fake_module("server", state=types.SimpleNamespace())
        package.__path__ = [str(fixtures.ROOT / "server")]
        bindings = {
            "config": fixtures.fake_module("config", TERMINAL_SANDBOX_MODE="host"),
            "modules.host_workspace_manager": fixtures.fake_module("modules.host_workspace_manager", load_host_workspace_catalog=lambda: {"workspaces": self.fixture.workspaces}),
            "server": package,
            "server.auth_helpers": fixtures.fake_module("server.auth_helpers", api_login_required=lambda fn: fn, login_required=lambda fn: fn, get_current_username=lambda: "fixture"),
            "server.context.workspace": fixtures.fake_module("server.context.workspace", request_identity=lambda: identity, resolve_workspace=lambda *a, **k: types.SimpleNamespace(project_path=self.fixture.repo)),
            "server.project_git_review": fixtures.fake_module("server.project_git_review", launch_review=lambda *a: (_ for _ in ()).throw(RuntimeError("workspace busy")), list_reviews=lambda *a: []),
            "server.security": fixtures.fake_module("server.security", rate_limited=lambda *a, **k: lambda fn: fn),
        }
        spec = importlib.util.spec_from_file_location("git_api_fixture", fixtures.ROOT / "server/project_git.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, bindings): spec.loader.exec_module(module)
        app = Flask(__name__)
        app.register_blueprint(module.project_git_bp)
        self.client = app.test_client()
        self.repo_id = discover(self.fixture.workspaces, refresh=True)["repositories"][0]["id"]

    def tearDown(self):
        self.fixture.tearDown()

    def test_requires_registered_explicit_workspace(self):
        self.assertEqual(self.client.get(f"/api/project-git/{self.repo_id}/history").status_code, 400)
        self.assertEqual(self.client.get(f"/api/project-git/{self.repo_id}/history?workspace_id=other").status_code, 400)
        response = self.client.get(f"/api/project-git/{self.repo_id}/history?workspace_id=fixture")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["commits"][0]["sha"], self.fixture.head)

    def test_invalid_payload_and_busy_launch(self):
        url = f"/api/project-git/{self.repo_id}/reviews?workspace_id=fixture"
        self.assertEqual(self.client.post(url, json=["invalid"]).status_code, 400)
        self.assertEqual(self.client.post(url, json={"sha": 123}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"sha": self.fixture.head}).status_code, 409)

    def test_historical_comparison(self):
        url = f"/api/project-git/{self.repo_id}/comparison?workspace_id=fixture&sha={self.fixture.head}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["base_sha"], self.fixture.initial)
        self.assertEqual(len(response.json["data"]["files"]), 2)
