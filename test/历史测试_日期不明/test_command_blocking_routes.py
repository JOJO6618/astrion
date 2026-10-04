from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from command_blocking_helpers import (
    _CommandBlockingCase, _BASE_TMP, _LEGACY_WORD, _PERSONAL_WORD, _PERSONAL_WORD_2,
    _import_cb, _cb_path, _save_ok, _save_rejected, _validate,
)

class TestCommandBlockingApi(_CommandBlockingCase):

    def _build_client(self):
        try:
            cb_api = importlib.import_module("server.chat.command_blocking")
        except Exception as exc:  # noqa: BLE001
            self.fail(
                "server/chat/command_blocking.py 无法导入（API 契约未落地）: "
                f"{type(exc).__name__}: {exc}"
            )
        from flask import Flask
        from server.chat import chat_bp
        from server import state as server_state
        from server.context import decorators as ctx_decorators

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "cb-test-secret"
        app.register_blueprint(chat_bp)

        # 伪造普通登录用户（非 admin）：session + nonce 池
        username = "cbtester"
        nonce = "cbtestnonce"
        server_state.active_login_nonces[username].add(nonce)
        self.addCleanup(server_state.active_login_nonces[username].discard, nonce)

        # 注入工作区：data_dir 指向本用例的隔离目录（与 personalization 路由同模式）。
        # with_terminal 要求 terminal/workspace 均真值，否则 503；terminal 用空壳即可，
        # 被测视图只读取 workspace.data_dir。
        workspace = SimpleNamespace(data_dir=str(self.data_dir),
                                    project_path=str(self.tmp))
        terminal_stub = SimpleNamespace()
        patcher = mock.patch.object(
            ctx_decorators, "get_user_resources",
            lambda _username, conversation_id=None: (terminal_stub, workspace),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["username"] = username
            sess["login_nonce"] = nonce
            sess["role"] = "user"  # 普通用户
        return client, cb_api

    def test_api_get_default_shape(self):
        client, _ = self._build_client()
        resp = client.get("/api/command-blocking")
        self.assertEqual(resp.status_code, 200,
                         f"GET 应对普通登录用户返回 200: {resp.status_code} {resp.get_data(as_text=True)[:300]}")
        data = resp.get_json(silent=True) or {}
        self.assertIs(data.get("success"), True, f"GET 应 success=True: {data!r}")
        self.assertIs(data.get("enabled"), True, f"默认 enabled=True: {data!r}")
        self.assertEqual(data.get("rules"), [], f"默认 rules=[]: {data!r}")
        recommended = data.get("recommended_rules")
        self.assertIsInstance(recommended, list, f"应含 recommended_rules 列表: {data!r}")
        for item in recommended:
            self.assertIsInstance(item, str)

    def test_api_post_partial_toggle_does_not_clobber_rules(self):
        client, _ = self._build_client()
        resp1 = client.post("/api/command-blocking",
                            json={"enabled": True, "rules": [_PERSONAL_WORD]})
        data1 = resp1.get_json(silent=True) or {}
        self.assertIs(data1.get("success"), True, f"POST 全量应成功: {data1!r}")

        resp2 = client.post("/api/command-blocking", json={"enabled": False})
        data2 = resp2.get_json(silent=True) or {}
        self.assertIs(data2.get("success"), True, f"POST 单字段开关应成功: {data2!r}")
        self.assertIs(data2.get("enabled"), False)
        self.assertEqual(data2.get("rules"), [_PERSONAL_WORD],
                         f"API 单字段开关不得覆盖 rules: {data2!r}")

        resp3 = client.get("/api/command-blocking")
        data3 = resp3.get_json(silent=True) or {}
        self.assertIs(data3.get("enabled"), False)
        self.assertEqual(data3.get("rules"), [_PERSONAL_WORD],
                         f"GET 应回读部分更新后的配置: {data3!r}")

    def test_api_post_malformed_rejected_and_file_untouched(self):
        client, _ = self._build_client()
        client.post("/api/command-blocking",
                    json={"enabled": True, "rules": [_PERSONAL_WORD]})
        path = _cb_path(self.cb, self.data_dir)
        before = path.read_bytes() if path.exists() else None

        resp = client.post("/api/command-blocking", json={"enabled": "yes"})
        data = resp.get_json(silent=True) or {}
        self.assertFalse(
            resp.status_code < 300 and data.get("success") is True,
            f"畸形 enabled 不得被 API 接受: {resp.status_code} {data!r}",
        )
        after = path.read_bytes() if path.exists() else None
        self.assertEqual(before, after, "畸形 POST 不得改动磁盘配置")

        # 原配置仍完整可读
        resp_get = client.get("/api/command-blocking")
        data_get = resp_get.get_json(silent=True) or {}
        self.assertEqual(data_get.get("rules"), [_PERSONAL_WORD])


class TestHeadlessRegistration(unittest.TestCase):

    def test_headless_app_registers_command_blocking_route(self):
        # .venv(py3.9) 缺少 yaml（workflow_manager 运行期依赖，仅在函数内使用），
        # 为路由登记检查注入最小存根；有真实 yaml 时不干预。
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            stub = SimpleNamespace(
                safe_load=lambda *_a, **_k: {},
                safe_dump=lambda *_a, **_k: "",
            )
            sys.modules.setdefault("yaml", stub)
        try:
            from server.headless_app import create_headless_app
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"headless_app 无法导入（环境问题）: {exc!r}")
        app = create_headless_app()
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn("/api/command-blocking", rules,
                      f"headless 形态应登记 /api/command-blocking，当前规则样例: "
                      f"{sorted(r for r in rules if 'command' in r)[:10]!r}")



if __name__ == "__main__":
    unittest.main()
