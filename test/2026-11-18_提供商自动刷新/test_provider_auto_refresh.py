"""提供商模型目录自动刷新：覆盖批次编排、节流与并发保护。

全程 mock 网络请求，使用临时数据目录，不接触真实 providers.json / codex 凭证。
运行：python3 -B test/2026-11-18_提供商自动刷新/test_provider_auto_refresh.py
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import provider_manager as pm  # noqa: E402


class ProviderRefreshAllTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch_data = mock.patch.object(pm, "DATA_DIR", self.tmp.name)
        self.patch_data.start()
        self.addCleanup(self.patch_data.stop)
        self.manager = pm.ProviderManager()
        # 两个已连接提供商
        self.manager._memory = {
            "version": 2,
            "providers": {
                "deepseek-aaaaaaaa": {"catalog_id": "deepseek", "base_url": "https://x", "models": []},
                "custom-bbbbbbbb": {"catalog_id": None, "base_url": "https://y", "models": []},
            },
        }

    def test_refresh_all_visits_every_provider_and_merges_codex(self):
        calls = []

        def fake_refresh(provider_id):
            calls.append(provider_id)
            return {"success": True, "models_count": 3}

        codex_rows = [{"kind": "codex", "id": "openai-codex", "success": True, "models_count": 12}]
        with mock.patch.object(self.manager, "refresh_models", side_effect=fake_refresh), mock.patch.object(
            self.manager, "_refresh_codex_accounts", return_value=codex_rows
        ):
            result = self.manager.refresh_all()

        self.assertEqual(sorted(calls), ["custom-bbbbbbbb", "deepseek-aaaaaaaa"])
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["failed"], 0)
        self.assertEqual([row["kind"] for row in result["results"]], ["provider", "provider", "codex"])

    def test_single_provider_failure_does_not_abort_batch(self):
        def fake_refresh(provider_id):
            if provider_id.startswith("deepseek"):
                raise RuntimeError("boom")
            return {"success": True, "models_count": 1}

        with mock.patch.object(self.manager, "refresh_models", side_effect=fake_refresh), mock.patch.object(
            self.manager, "_refresh_codex_accounts", return_value=[]
        ):
            result = self.manager.refresh_all()

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["failed"], 1)
        failed = next(row for row in result["results"] if not row["success"])
        self.assertIn("boom", failed["error"])

    def test_throttle_second_call_within_hour_is_skipped(self):
        with mock.patch.object(self.manager, "refresh_all", return_value={"success": True, "count": 0}) as rf:
            first = self.manager.refresh_all_throttled()
            second = self.manager.refresh_all_throttled()

        self.assertFalse(first.get("skipped"))
        self.assertTrue(second.get("skipped"))
        self.assertEqual(second.get("reason"), "throttled")
        self.assertEqual(rf.call_count, 1)

    def test_throttle_allows_call_after_gap(self):
        with mock.patch.object(self.manager, "refresh_all", return_value={"success": True, "count": 0}) as rf:
            self.manager.refresh_all_throttled()
            # 把上次时间拨回 2 小时前
            self.manager._last_auto_refresh_at -= 2 * 60 * 60
            third = self.manager.refresh_all_throttled()

        self.assertFalse(third.get("skipped"))
        self.assertEqual(rf.call_count, 2)

    def test_concurrent_call_reports_in_progress(self):
        self.manager._auto_refresh_running = True
        try:
            result = self.manager.refresh_all_throttled()
        finally:
            self.manager._auto_refresh_running = False
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "in_progress")

    def test_running_flag_cleared_and_timestamp_updated_on_failure(self):
        with mock.patch.object(self.manager, "refresh_all", side_effect=RuntimeError("net down")):
            with self.assertRaises(RuntimeError):
                self.manager.refresh_all_throttled()
        self.assertFalse(self.manager._auto_refresh_running)
        self.assertGreater(self.manager._last_auto_refresh_at, 0)

    def test_codex_accounts_skip_credential_missing(self):
        accounts = [
            {"id": "openai-codex", "state": "ready"},
            {"id": "openai-codex-707", "state": "credential_missing"},
        ]
        fake_models = mock.MagicMock()
        fake_models.refresh_sync.return_value = {"refreshed": True, "count": 12}
        with mock.patch(
            "utils.api_client.codex.accounts.get_codex_account_store"
        ) as store_factory, mock.patch(
            "utils.api_client.codex.models.get_models_manager", return_value=fake_models
        ) as manager_factory:
            store_factory.return_value.list_accounts.return_value = accounts
            rows = self.manager._refresh_codex_accounts()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "openai-codex")
        self.assertTrue(rows[0]["success"])
        self.assertEqual(rows[0]["models_count"], 12)
        manager_factory.assert_called_once_with("openai-codex")

    def test_refresh_all_route_registered(self):
        """前端自动刷新依赖 POST /api/providers/refresh-all 存在。"""
        from flask import Flask

        from server.providers import providers_bp

        app = Flask(__name__)
        app.register_blueprint(providers_bp)
        rules = [rule for rule in app.url_map.iter_rules() if str(rule) == "/api/providers/refresh-all"]
        self.assertEqual(len(rules), 1)
        self.assertIn("POST", rules[0].methods)


if __name__ == "__main__":
    unittest.main(verbosity=2)
