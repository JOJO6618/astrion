"""个人指令拦截 HTTP 接口回归，不启动服务器或执行真实命令。

主仓只保留 Host 身份：本文件用 Host 合成会话；旧普通用户主体只用于
断言认证层拒绝它，不再作为可写入个人规则的用户。
"""
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
        self.client = self.app.test_client()
        self.nonce = "blocking-test-host"
        state.active_login_nonces.setdefault("host", set()).add(self.nonce)
        with self.client.session_transaction() as session:
            session.update(username="host", login_nonce=self.nonce, role="admin", host_mode=True)
        self.addCleanup(self._cleanup_nonce)
        browser = patch("server.host_password.browser_session_valid", return_value=True)
        browser.start()
        self.addCleanup(browser.stop)
        self.resources = patch("server.context.decorators.get_user_resources", side_effect=self._resources)
        self.resources.start()
        self.addCleanup(self.resources.stop)
        limiter = patch("server.security.check_rate_limit", return_value=(False, 0))
        limiter.start()
        self.addCleanup(limiter.stop)

    def _cleanup_nonce(self):
        state.active_login_nonces.get("host", set()).discard(self.nonce)

    def _resources(self, username, **kwargs):
        directory = self.root / "host"
        directory.mkdir(exist_ok=True)
        workspace = SimpleNamespace(data_dir=directory, username=username)
        return SimpleNamespace(user_role="admin"), workspace

    def test_host_manages_own_rules(self):
        response = self.client.post("/api/command-blocking", json={"rules": ["secret-command"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["rules"], ["secret-command"])
        self.assertEqual(self.client.get("/api/command-blocking").json["rules"], ["secret-command"])
        response = self.client.post("/api/command-blocking", json={"enabled": False})
        self.assertEqual(response.json["rules"], ["secret-command"])
        self.assertFalse(response.json["enabled"])
        self.assertFalse(self.client.get("/api/command-blocking").json["enabled"])

    def test_recommendations_do_not_enable_rules(self):
        response = self.client.get("/api/command-blocking")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["enabled"])
        self.assertEqual(response.json["rules"], [])
        self.assertTrue(response.json["recommended_rules"])
        self.assertFalse((self.root / "host" / "command_blocking.json").exists())

    def test_invalid_payload_and_other_user_path_are_rejected(self):
        for payload in ({"enabled": "false"}, {"rules": "anything"}, {"rules": [1]},
                        {"rules": [], "username": "bob"}, {"data_dir": str(self.root / "bob")}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/command-blocking", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json["success"])
        self.assertEqual(load_command_blocking_config(self.root / "host"), {"enabled": True, "rules": []})

    def test_unauthenticated_requests_are_rejected(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/api/command-blocking").status_code, 401)
        self.assertEqual(client.post("/api/command-blocking", json={"enabled": False}).status_code, 401)

    def test_legacy_identity_is_rejected(self):
        """旧普通用户会话只保留可识别身份；认证层拒绝，不会映射为 Host。"""
        client = self.app.test_client()
        nonce = "blocking-test-legacy"
        state.active_login_nonces.setdefault("alice", set()).add(nonce)
        self.addCleanup(state.active_login_nonces.get("alice", set()).discard, nonce)
        with client.session_transaction() as session:
            session.update(username="alice", login_nonce=nonce, role="user", host_mode=False)
        self.assertEqual(client.get("/api/command-blocking").status_code, 401)
        self.assertEqual(client.post("/api/command-blocking", json={"enabled": False}).status_code, 401)

    def test_empty_workspace_uses_host_personalization_root(self):
        self.resources.stop()
        with patch("server.context.decorators.get_user_resources", side_effect=NoWorkspaceError("empty")), \
             patch("server.chat.command_blocking.DATA_DIR", self.root / "host-personal"):
            response = self.client.post("/api/command-blocking", json={"rules": ["blocked"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.get("/api/command-blocking").json["rules"], ["blocked"])
        path = self.root / "host-personal" / "command_blocking.json"
        self.assertTrue(path.exists())

    def test_save_failure_returns_error_without_success(self):
        with patch("modules.command_blocking.atomic_write_json", side_effect=OSError("disk failure")):
            response = self.client.post("/api/command-blocking", json={"rules": ["blocked"]})
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json["success"])
        self.assertEqual(load_command_blocking_config(self.root / "host")["rules"], [])

    def test_headless_registers_the_same_endpoints(self):
        from server.headless_app import create_headless_app

        app = create_headless_app()
        rules = [rule for rule in app.url_map.iter_rules() if rule.rule == "/api/command-blocking"]
        self.assertEqual(len(rules), 2)
        self.assertTrue(any("GET" in rule.methods for rule in rules))
        self.assertTrue(any("POST" in rule.methods for rule in rules))


if __name__ == "__main__":
    unittest.main()
