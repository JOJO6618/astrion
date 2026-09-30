from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx
from flask import Flask

from server.status.desktop_update import _bridge_request


class BridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/window/state":
            body = json.dumps({"maximized": False}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"error": "window_missing"}).encode()
            self.send_response(409)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size))
        body = json.dumps({"ok": True, "action": payload["action"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class DesktopBridgeProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.ctx = {"bridge_port": str(cls.server.server_port)}
        cls.app = Flask(__name__)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_get_and_post_bypass_system_proxy_discovery(self) -> None:
        # httpx uses this discovery hook for both environment and Windows proxies.
        # The local handler rejects proxy-style absolute URLs, without touching
        # machine settings or relying on platform-specific connection errors.
        proxy = f"http://127.0.0.1:{self.server.server_port}"
        with patch("httpx._utils.getproxies", return_value={"http": proxy}):
            url = f"{proxy}/window/state"
            self.assertEqual(httpx.get(url, timeout=2).status_code, 409)
            with self.app.app_context():
                body, error = _bridge_request(self.ctx, "GET", "/window/state", 2)
                self.assertIsNone(error)
                self.assertEqual(body, {"maximized": False})
                body, error = _bridge_request(
                    self.ctx, "POST", "/window/control", 2,
                    json_body={"action": "minimize"},
                )
                self.assertIsNone(error)
                self.assertEqual(body, {"ok": True, "action": "minimize"})

    def test_bridge_http_failure_is_not_reported_as_success(self) -> None:
        with self.app.app_context():
            body, error = _bridge_request(self.ctx, "GET", "/missing", 2)
            self.assertIsNone(body)
            response, status = error
            self.assertEqual(status, 200)
            self.assertFalse(response.get_json()["success"])
            self.assertEqual(response.get_json()["code"], "bridge_unreachable")
            self.assertIn("409", response.get_json()["detail"])


if __name__ == "__main__":
    unittest.main()
