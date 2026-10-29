"""OAuth 取消/提交同步边界：取消后绝不落盘，提交中取消等待完成。

P2-C：``_exchange_code_for_tokens`` 里的 upsert 必须经过流程的取消/提交锁；
仅靠网络前/后的状态检查仍存在“检查后被取消、随后仍落盘”的窗口。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils.api_client.codex import oauth  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeClient:
    """httpx.Client 替身：post 直接返回合成 token。"""

    payload = {"access_token": "access", "refresh_token": "refresh", "id_token": ""}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return _FakeResponse(self.payload)


class _FakeStore:
    def __init__(self, gate: threading.Event | None = None, entered: threading.Event | None = None):
        self.calls = 0
        self.gate = gate
        self.entered = entered or threading.Event()

    def upsert_tokens(self, **kwargs):
        self.calls += 1
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5)
        return {"id": "openai-codex-aaaaaaaa", "hash": "aaaaaa"}


class OAuthCancelBoundaryTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(oauth.httpx, "Client", _FakeClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        proxy_patcher = patch.object(oauth, "resolve_proxy", return_value=None)
        proxy_patcher.start()
        self.addCleanup(proxy_patcher.stop)

    def test_cancel_before_commit_never_writes_credentials(self):
        store = _FakeStore()
        flow = oauth.CodexOAuthFlow(store)
        flow._code = "synthetic-code"
        flow.cancel()  # 取消先于后台换 token 提交
        flow._exchange()
        self.assertEqual(store.calls, 0, "取消后不得落盘")
        self.assertEqual(flow.poll()["status"], "cancelled")

    def test_cancel_during_commit_waits_and_completes_without_orphan_state(self):
        gate = threading.Event()
        store = _FakeStore(gate=gate)
        flow = oauth.CodexOAuthFlow(store)
        flow._code = "synthetic-code"

        worker = threading.Thread(target=flow._exchange)
        worker.start()
        self.assertTrue(store.entered.wait(2), "落盘必须真正进入提交临界区")

        cancel_thread = threading.Thread(target=flow.cancel)
        cancel_thread.start()
        # 提交临界区内 cancel 必须等待，不能把状态改成 cancelled 后留下已写凭证
        time.sleep(0.1)
        self.assertTrue(cancel_thread.is_alive(), "cancel 应等待提交临界区结束")

        gate.set()
        worker.join(5)
        cancel_thread.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(cancel_thread.is_alive())
        self.assertEqual(store.calls, 1)
        self.assertEqual(flow.poll()["status"], "completed")
        self.assertEqual(flow.poll()["connection_id"], "openai-codex-aaaaaaaa")

    def test_device_flow_cancel_before_commit_never_writes(self):
        store = _FakeStore()
        flow = oauth.CodexDeviceFlow(store)
        flow.cancel()
        flow._exchange("synthetic-code", "synthetic-verifier")
        self.assertEqual(store.calls, 0)
        self.assertEqual(flow.poll()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
