"""Codex API 账号查询隔离：status/accounts/logout/refresh 绑定 account_id，未知账号明确报错。"""
import base64
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask  # noqa: E402

from server.codex_auth import codex_auth_bp  # noqa: E402
from utils.api_client.codex.accounts import CodexAccountStore  # noqa: E402


def make_id_token(account_id: str) -> str:
    payload = {
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
        "sub": f"sub-{account_id}",
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{raw}.signature"


class CodexApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = CodexAccountStore(
            legacy_auth_path=str(root / "codex_auth.json"),
            accounts_dir=str(root / "codex_accounts"),
            index_path=str(root / "codex_accounts.json"),
        )
        self.account_a = self.store.upsert_tokens(
            access_token="a",
            refresh_token="a-r",
            id_token=make_id_token("acct-A"),
            account_id="acct-A",
        )
        self.account_b = self.store.upsert_tokens(
            access_token="b",
            refresh_token="b-r",
            id_token=make_id_token("acct-B"),
            account_id="acct-B",
        )
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(codex_auth_bp)
        self.client = self.app.test_client()
        self.fake_models = {}

        def fake_get_models_manager(account_id):
            manager = self.fake_models.get(account_id)
            if manager is None:
                manager = type(
                    "FakeModels",
                    (),
                    {
                        "status_summary": lambda _self: {
                            "model_count": 1,
                            "total_models": 1,
                            "source": "online",
                            "fetched_at": "2026-10-29T00:00:00.000000Z",
                            "visible_models": [f"{account_id}/gpt-test"],
                        }
                    },
                )()
                self.fake_models[account_id] = manager
            return manager

        self.patchers = [
            patch("server.codex_auth.get_codex_account_store", return_value=self.store),
            patch("server.codex_auth.get_models_manager", side_effect=fake_get_models_manager),
            patch("server.auth_helpers.is_logged_in", return_value=True),
            patch("server.auth_helpers.is_admin_user", return_value=True),
            # 登出会清理模型缓存：路径必须指向临时目录
            patch(
                "utils.api_client.codex.settings.CODEX_MODELS_DIR",
                str(root / "codex_models"),
            ),
            patch(
                "utils.api_client.codex.settings.CODEX_DATA_PATH",
                str(root / "codex_models.json"),
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_accounts_list_exposes_hash_not_token(self):
        response = self.client.get("/api/codex/accounts")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["accounts"]), 2)
        hashes = {item["hash"] for item in data["accounts"]}
        self.assertEqual(hashes, {self.account_a["hash"], self.account_b["hash"]})
        self.assertNotIn("tokens", json.dumps(data))
        self.assertNotIn("access", json.dumps(data))

    def test_status_binds_selected_account(self):
        a = self.client.get(f"/api/codex/status?account_id={self.account_a['id']}").get_json()
        b = self.client.get(f"/api/codex/status?account_id={self.account_b['id']}").get_json()
        self.assertEqual(a["account_id"], self.account_a["id"])
        self.assertEqual(a["account_hash"], self.account_a["hash"])
        self.assertEqual(b["account_id"], self.account_b["id"])
        self.assertEqual(b["account_hash"], self.account_b["hash"])
        self.assertNotEqual(a["account_hash"], b["account_hash"])

    def test_unknown_account_returns_404_not_fallback(self):
        response = self.client.get("/api/codex/status?account_id=openai-codex-nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "account_not_found")

    def test_logout_only_removes_selected_account(self):
        response = self.client.post(
            "/api/codex/logout",
            json={"account_id": self.account_b["id"]},
        )
        self.assertEqual(response.status_code, 200)
        remaining = self.store.list_accounts()
        self.assertEqual([item["id"] for item in remaining], [self.account_a["id"]])
        # 删除后主账号顺延到仍在的账号
        self.assertEqual(self.store.primary_account_id(), self.account_a["id"])

    def test_logout_missing_credential_registration_via_api(self):
        """提供商页移除缺凭证账号：logout 容忍 FileNotFound 并删除登记。"""
        (self.store.accounts_dir / f"{self.account_b['id']}.json").unlink()
        missing = self.client.get("/api/codex/accounts").get_json()["accounts"]
        self.assertTrue(
            next(item for item in missing if item["id"] == self.account_b["id"])[
                "credential_missing"
            ]
        )

        response = self.client.post(
            "/api/codex/logout", json={"account_id": self.account_b["id"]}
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["removed"])
        self.assertEqual(
            [item["id"] for item in self.store.list_accounts()], [self.account_a["id"]]
        )

    def test_logout_failure_returns_structured_error_and_keeps_identity(self):
        with patch.object(self.store, "logout", side_effect=OSError("denied")):
            response = self.client.post(
                "/api/codex/logout", json={"account_id": self.account_b["id"]}
            )
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error"], "logout_failed")
        self.assertEqual(data["account_id"], self.account_b["id"])
        # 删除失败不能删登记/损伤其他账号
        self.assertEqual(
            sorted(item["id"] for item in self.store.list_accounts()),
            sorted([self.account_a["id"], self.account_b["id"]]),
        )

    def test_status_without_accounts_is_not_error(self):
        empty = CodexAccountStore(
            legacy_auth_path=str(Path(self.temp.name) / "none" / "codex_auth.json"),
            accounts_dir=str(Path(self.temp.name) / "none" / "codex_accounts"),
            index_path=str(Path(self.temp.name) / "none" / "codex_accounts.json"),
        )
        with patch("server.codex_auth.get_codex_account_store", return_value=empty):
            response = self.client.get("/api/codex/status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["connected"])
        self.assertIsNone(data["account_id"])


class LoginSerialTests(unittest.TestCase):
    def test_second_login_is_rejected_without_cancelling_first(self):
        from utils.api_client.codex import oauth

        class FakeFlow:
            def __init__(self):
                self.cancelled = False

            def poll(self):
                return {"status": "pending", "flow_mode": "browser", "phase": "waiting"}

            def cancel(self):
                self.cancelled = True

        fake = FakeFlow()
        with patch.object(oauth, "_current_flow", fake):
            browser = oauth.start_login_flow(object())
            device = oauth.start_device_login_flow(object())
        for result in (browser, device):
            self.assertEqual(result["error"], "login_in_progress")
            self.assertTrue(result["reused"])
            self.assertEqual(result["status"], "pending")
        self.assertFalse(fake.cancelled, "不得取消/覆盖别人进行中的登录")


if __name__ == "__main__":
    unittest.main()
