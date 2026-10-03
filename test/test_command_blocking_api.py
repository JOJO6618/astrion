"""个人指令拦截 HTTP 接口回归，不启动服务器或执行真实命令。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from modules.command_blocking import load_command_blocking_config
from server import state
from server.chat.command_blocking import get_command_blocking, update_command_blocking
from server.context.identity import NoWorkspaceError


class CommandBlockingApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.secret_key = "command-blocking-test"
        self.app.add_url_rule("/api/command-blocking", view_func=get_command_blocking, methods=["GET"])
        self.app.add_url_rule("/api/command-blocking", view_func=update_command_blocking, methods=["POST"])
        self.app.testing = True
        self.clients = {}
        self.nonces = {}
        for username in ("alice", "bob"):
            client = self.app.test_client()
            nonce = f"blocking-test-{username}"
            self.nonces[username] = nonce
            state.active_login_nonces.setdefault(username, set()).add(nonce)
            with client.session_transaction() as session:
                session.update(username=username, login_nonce=nonce, role="user")
            self.clients[username] = client
        self.addCleanup(self._cleanup_nonces)
        self.resources = patch("server.context.decorators.get_user_resources", side_effect=self._resources)
        self.resources.start()
        self.addCleanup(self.resources.stop)
        limiter = patch("server.security.check_rate_limit", return_value=(False, 0))
        limiter.start()
        self.addCleanup(limiter.stop)

    def _cleanup_nonces(self):
        for username, nonce in self.nonces.items():
            state.active_login_nonces.get(username, set()).discard(nonce)

    def _resources(self, username, **kwargs):
        directory = self.root / username
        directory.mkdir(exist_ok=True)
        workspace = SimpleNamespace(data_dir=directory, username=username)
        return SimpleNamespace(user_role="user"), workspace

    def test_ordinary_users_manage_only_their_own_rules(self):
        response = self.clients["alice"].post("/api/command-blocking", json={"rules": ["secret-command"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["rules"], ["secret-command"])
        self.assertEqual(self.clients["bob"].get("/api/command-blocking").json["rules"], [])
        response = self.clients["alice"].post("/api/command-blocking", json={"enabled": False})
        self.assertEqual(response.json["rules"], ["secret-command"])
        self.assertFalse(response.json["enabled"])
        self.assertTrue(self.clients["bob"].get("/api/command-blocking").json["enabled"])

    def test_recommendations_do_not_enable_rules(self):
        response = self.clients["alice"].get("/api/command-blocking")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["enabled"])
        self.assertEqual(response.json["rules"], [])
        self.assertTrue(response.json["recommended_rules"])
        self.assertFalse((self.root / "alice" / "command_blocking.json").exists())

    def test_invalid_payload_and_other_user_path_are_rejected(self):
        for payload in ({"enabled": "false"}, {"rules": "anything"}, {"rules": [1]},
                        {"rules": [], "username": "bob"}, {"data_dir": str(self.root / "bob")}):
            with self.subTest(payload=payload):
                response = self.clients["alice"].post("/api/command-blocking", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json["success"])
        self.assertEqual(load_command_blocking_config(self.root / "alice"), {"enabled": True, "rules": []})

    def test_unauthenticated_requests_are_rejected(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/api/command-blocking").status_code, 401)
        self.assertEqual(client.post("/api/command-blocking", json={"enabled": False}).status_code, 401)

    def test_empty_workspace_does_not_use_global_personal_data(self):
        self.resources.stop()
        with patch("server.context.decorators.get_user_resources", side_effect=NoWorkspaceError("empty")), \
             patch.object(state.user_manager, "_personal_root", side_effect=lambda username: self.root / username / "personal"):
            response = self.clients["alice"].post("/api/command-blocking", json={"rules": ["blocked"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.clients["bob"].get("/api/command-blocking").json["rules"], [])
        path = self.root / "alice" / "personal" / "personalization" / "command_blocking.json"
        self.assertTrue(path.exists())

    def test_save_failure_returns_error_without_success(self):
        with patch("modules.command_blocking.atomic_write_json", side_effect=OSError("disk failure")):
            response = self.clients["alice"].post("/api/command-blocking", json={"rules": ["blocked"]})
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json["success"])
        self.assertEqual(load_command_blocking_config(self.root / "alice")["rules"], [])

    def test_headless_registers_the_same_endpoints(self):
        from server.headless_app import create_headless_app

        app = create_headless_app()
        rules = [rule for rule in app.url_map.iter_rules() if rule.rule == "/api/command-blocking"]
        self.assertEqual(len(rules), 2)
        self.assertTrue(any("GET" in rule.methods for rule in rules))
        self.assertTrue(any("POST" in rule.methods for rule in rules))


if __name__ == "__main__":
    unittest.main()
