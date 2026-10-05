"""Regression checks for the host desktop settings proxy, with no real user state."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask
from server.status.desktop_update import desktop_quick_settings


class QuickSettingsProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.view = desktop_quick_settings.__wrapped__
        self.desktop = {"version": "0.5.1", "bridge_port": "12345"}

    def test_configuration_is_forwarded_to_local_bridge(self) -> None:
        payload = {"enabled": True, "modifier": "alt"}
        with self.app.test_request_context(method="POST", json=payload):
            with patch("server.status.desktop_update._desktop_context", return_value=self.desktop):
                with patch("server.status.desktop_update._bridge_request", return_value=({"ok": True, "data": payload}, None)) as bridge:
                    response = self.view("configure")
        self.assertEqual(response.get_json()["data"], payload)
        bridge.assert_called_once_with(self.desktop, "POST", "/quick/configure", timeout=10.0, json_body=payload)

    def test_operation_method_whitelist(self) -> None:
        for method, operation in [("GET", "configure"), ("POST", "info"), ("POST", "request")]:
            with self.subTest(method=method, operation=operation):
                with self.app.test_request_context(method=method):
                    with patch("server.status.desktop_update._bridge_request") as bridge:
                        response, status = self.view(operation)
                self.assertEqual(status, 404)
                self.assertFalse(response.get_json()["ok"])
                bridge.assert_not_called()

    def test_web_instance_never_calls_desktop_bridge(self) -> None:
        with self.app.test_request_context(method="GET"):
            with patch("server.status.desktop_update._desktop_context", return_value=None):
                with patch("server.status.desktop_update._bridge_request") as bridge:
                    result = self.view("info")
        bridge.assert_not_called()
        response = result[0] if isinstance(result, tuple) else result
        self.assertEqual(response.get_json()["code"], "not_desktop")


if __name__ == "__main__":
    unittest.main()
