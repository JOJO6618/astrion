"""@rate_limited 的 host 模式放行规则。

运行：python3 -m unittest discover -s "test/2026-11-18_自动保存与限流" -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

import server.security as security  # noqa: E402
from server import state  # noqa: E402


def _build_app(host_mode: bool) -> Flask:
    app = Flask(__name__)
    with mock.patch.object(security, "IS_HOST_MODE", host_mode):
        @app.route("/biz")
        @security.rate_limited("t_biz", 2, 60, scope="ip")
        def biz():
            return "ok"

        @app.route("/guard")
        @security.rate_limited("t_guard", 2, 60, scope="ip", host_exempt=False)
        def guard():
            return "ok"
    return app


class RateLimitHostExemptTest(unittest.TestCase):
    def setUp(self):
        state.RATE_LIMIT_BUCKETS.clear()

    def _codes(self, app: Flask, path: str, times: int = 5):
        client = app.test_client()
        return [client.get(path).status_code for _ in range(times)]

    def test_host_mode_business_endpoint_not_limited(self):
        app = _build_app(host_mode=True)
        self.assertEqual(self._codes(app, "/biz"), [200] * 5)

    def test_host_mode_password_like_endpoint_still_limited(self):
        app = _build_app(host_mode=True)
        codes = self._codes(app, "/guard")
        self.assertEqual(codes[:2], [200, 200])
        self.assertTrue(all(code == 429 for code in codes[2:]))

    def test_web_mode_limits_everything(self):
        app = _build_app(host_mode=False)
        for path in ("/biz", "/guard"):
            codes = self._codes(app, path)
            self.assertEqual(codes[:2], [200, 200], path)
            self.assertTrue(all(code == 429 for code in codes[2:]), path)

    def test_web_mode_429_payload(self):
        app = _build_app(host_mode=False)
        client = app.test_client()
        for _ in range(2):
            client.get("/biz")
        resp = client.get("/biz")
        body = resp.get_json()
        self.assertEqual(resp.status_code, 429)
        self.assertFalse(body["success"])
        self.assertTrue(body["error"])
        self.assertGreaterEqual(body["retry_after"], 1)


if __name__ == "__main__":
    unittest.main()
