"""Codex 多账号：凭证隔离、重复登录更新、哈希稳定/碰撞、迁移、登出、主账号规则。"""
import base64
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils.api_client.codex.accounts import (  # noqa: E402
    CodexAccountStore,
    identity_hash_from_material,
)


def make_id_token(account_id: str, email: str = "") -> str:
    payload = {
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
        "sub": f"sub-{account_id}",
    }
    if email:
        payload["email"] = email
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{raw}.signature"


def auth_payload(access: str, refresh: str, account_id: str) -> dict:
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": make_id_token(account_id),
            "access_token": access,
            "refresh_token": refresh,
            "account_id": account_id,
        },
        "last_refresh": "2026-10-29T00:00:00.000000Z",
    }


class CodexAccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.legacy_path = root / "codex_auth.json"
        self.accounts_dir = root / "codex_accounts"
        self.index_path = root / "codex_accounts.json"
        self.store = CodexAccountStore(
            legacy_auth_path=str(self.legacy_path),
            accounts_dir=str(self.accounts_dir),
            index_path=str(self.index_path),
        )
        # 模型缓存路径必须在临时目录：登出会清理缓存，绝不能碰真实数据目录
        self.patchers = [
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

    def upsert(self, account_id, access="a", refresh="r"):
        return self.store.upsert_tokens(
            access_token=access,
            refresh_token=refresh,
            id_token=make_id_token(account_id),
            account_id=account_id,
        )

    # ------------------------------------------------------------------ 基本
    def test_new_account_gets_stable_appended_hash(self):
        record = self.upsert("acct-A")
        self.assertTrue(record["id"].startswith("openai-codex-"))
        self.assertEqual(len(record["hash"]), 6)
        self.assertTrue(record["display_name"].startswith("OpenAI（ChatGPT 订阅）-"))
        self.assertTrue(record["display_name"].endswith(record["hash"]))
        self.assertTrue((self.accounts_dir / f"{record['id']}.json").exists())
        # 哈希来自账号身份，不来自 token
        expected = identity_hash_from_material("account:acct-A")[:6]
        self.assertEqual(record["hash"], expected)

    def test_duplicate_login_updates_original_credential(self):
        first = self.upsert("acct-A", access="old-access", refresh="old-refresh")
        second = self.upsert("acct-A", access="new-access", refresh="new-refresh")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["hash"], second["hash"])
        self.assertEqual(len(self.store.list_accounts()), 1)
        data = json.loads((self.accounts_dir / f"{first['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(data["tokens"]["access_token"], "new-access")
        self.assertEqual(data["tokens"]["refresh_token"], "new-refresh")

    def test_two_accounts_are_isolated_and_cache_paths_differ(self):
        a = self.upsert("acct-A")
        b = self.upsert("acct-B")
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotEqual(a["hash"], b["hash"])
        manager_a = self.store.get_auth_manager(a["id"])
        manager_b = self.store.get_auth_manager(b["id"])
        self.assertNotEqual(manager_a.auth_path, manager_b.auth_path)
        self.assertNotEqual(
            self.store.get_models_cache_path(a["id"]),
            self.store.get_models_cache_path(b["id"]),
        )
        self.assertEqual(len(self.store.list_accounts()), 2)

    def test_hash_collision_extends_only_new_account(self):
        index = {
            "accounts": {
                "openai-codex": {
                    "identity_hash": "abcdef" + "0" * 58,
                    "display_hash": "abcdef",
                    "created_at": "2026-01-01T00:00:00.000000Z",
                }
            }
        }
        candidate_same_prefix = "abcdef" + "1" * 58
        assigned = self.store._assign_display_hash_locked(
            index, candidate_same_prefix, "openai-codex-new"
        )
        self.assertEqual(len(assigned), 8, "6 位碰撞时新账号加长")
        self.assertTrue(assigned.startswith("abcdef1"))
        # 不相干的账号仍使用 6 位
        other = self.store._assign_display_hash_locked(
            index, "123456" + "2" * 58, "openai-codex-other"
        )
        self.assertEqual(len(other), 6)

    # ------------------------------------------------------------------ 迁移
    def test_legacy_credential_migrates_as_primary_idempotently(self):
        self.legacy_path.write_text(
            json.dumps(auth_payload("legacy-access", "legacy-refresh", "acct-legacy")),
            encoding="utf-8",
        )
        accounts = self.store.list_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["id"], "openai-codex")
        self.assertTrue(accounts[0]["is_legacy"])
        self.assertTrue(accounts[0]["connected"])
        hash_first = accounts[0]["hash"]
        # 幂等：重复读取不会新增条目/改变哈希
        accounts_again = self.store.list_accounts()
        self.assertEqual(len(accounts_again), 1)
        self.assertEqual(accounts_again[0]["hash"], hash_first)
        self.assertEqual(self.store.primary_account_id(), "openai-codex")

        # 新增账号后主账号仍是 legacy；删除后顺延到最早添加的仍保留账号
        second = self.upsert("acct-B")
        self.assertEqual(self.store.primary_account_id(), "openai-codex")
        self.store.logout("openai-codex")
        self.assertFalse(self.legacy_path.exists())
        self.assertEqual(self.store.primary_account_id(), second["id"])

    def test_legacy_duplicate_login_updates_legacy_file_not_new_account(self):
        self.legacy_path.write_text(
            json.dumps(auth_payload("legacy-access", "legacy-refresh", "acct-legacy")),
            encoding="utf-8",
        )
        # 首次访问触发迁移
        before = self.store.list_accounts()
        self.assertEqual(before[0]["id"], "openai-codex")
        updated = self.store.upsert_tokens(
            access_token="rotated-access",
            refresh_token="rotated-refresh",
            id_token=make_id_token("acct-legacy"),
            account_id="acct-legacy",
        )
        self.assertEqual(updated["id"], "openai-codex")
        self.assertEqual(len(self.store.list_accounts()), 1)
        data = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        self.assertEqual(data["tokens"]["access_token"], "rotated-access")

    # ------------------------------------------------------------------ 登出
    def test_logout_removes_file_and_index_entry(self):
        record = self.upsert("acct-A")
        self.assertTrue((self.accounts_dir / f"{record['id']}.json").exists())
        result = self.store.logout(record["id"])
        self.assertTrue(result["removed"])
        self.assertFalse((self.accounts_dir / f"{record['id']}.json").exists())
        self.assertEqual(self.store.list_accounts(), [])
        self.assertIsNone(self.store.primary_account_id())
        self.assertIsNone(result["primary_account_id"])
        with self.assertRaises(KeyError):
            self.store.get_auth_manager(record["id"])

    def test_logout_clears_account_models_cache(self):
        record = self.upsert("acct-A")
        cache_path = Path(self.store.get_models_cache_path(record["id"]))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text('{"models": [{"slug": "gpt-test"}]}', encoding="utf-8")
        result = self.store.logout(record["id"])
        self.assertTrue(result["models_cache_cleared"])
        self.assertFalse(cache_path.exists())

    def test_legacy_logout_clears_models_but_keeps_global_config(self):
        import utils.api_client.codex.settings as settings

        legacy_cache = Path(settings.CODEX_DATA_PATH)
        legacy_cache.parent.mkdir(parents=True, exist_ok=True)
        legacy_cache.write_text(
            json.dumps(
                {
                    "proxy": "http://127.0.0.1:7890",
                    "client_version": "0.50.0",
                    "models": [{"slug": "gpt-test"}],
                    "fetched_at": "2026-10-29T00:00:00.000000Z",
                    "etag": "abc",
                }
            ),
            encoding="utf-8",
        )
        self.legacy_path.write_text(
            json.dumps(auth_payload("legacy-access", "legacy-refresh", "acct-legacy")),
            encoding="utf-8",
        )
        # 触发迁移后登出主账号
        self.assertEqual(self.store.list_accounts()[0]["id"], "openai-codex")
        self.store.logout("openai-codex")
        data = json.loads(legacy_cache.read_text(encoding="utf-8"))
        self.assertNotIn("models", data)
        self.assertNotIn("fetched_at", data)
        self.assertNotIn("etag", data)
        # 代理/版本等全局配置不能被登出损失
        self.assertEqual(data["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(data["client_version"], "0.50.0")

    def test_missing_credential_keeps_registered_identity_and_restores_same_id(self):
        """P2-B：临时文件缺失不是登出；保留身份，恢复后仍用同一连接 id。"""
        record = self.upsert("acct-A")
        credential = self.accounts_dir / f"{record['id']}.json"
        credential.unlink()

        accounts = self.store.list_accounts()
        self.assertEqual(len(accounts), 1, "暂缺凭证不能删登记（更不能再分配新 id）")
        self.assertEqual(accounts[0]["id"], record["id"])
        self.assertEqual(accounts[0]["hash"], record["hash"])
        self.assertEqual(accounts[0]["state"], "credential_missing")
        self.assertTrue(accounts[0]["credential_missing"])
        self.assertFalse(accounts[0]["connected"])

        # 同一账号重新登录：必须恢复到原连接 id，而不是扫描出新 id
        restored = self.upsert("acct-A", access="new", refresh="new-r")
        self.assertEqual(restored["id"], record["id"])
        self.assertEqual(restored["hash"], record["hash"])
        self.assertTrue(restored["connected"])
        self.assertEqual(len(self.store.list_accounts()), 1)

    def test_logout_of_missing_credential_removes_registration(self):
        """凭证文件已缺失时仍可显式移除登记（提供商页的移除操作）。"""
        record = self.upsert("acct-A")
        credential = self.accounts_dir / f"{record['id']}.json"
        credential.unlink()
        self.assertEqual(self.store.list_accounts()[0]["state"], "credential_missing")

        result = self.store.logout(record["id"])
        self.assertTrue(result["removed"])
        self.assertEqual(self.store.list_accounts(), [], "缺凭证登记必须可移除且不留墓碑")
        self.assertIsNone(self.store.primary_account_id())
        with self.assertRaises(KeyError):
            self.store.get_auth_manager(record["id"])

    def test_explicit_logout_removes_registration_without_tombstone(self):
        record = self.upsert("acct-A")
        self.store.logout(record["id"])
        self.assertEqual(self.store.list_accounts(), [])
        self.assertIsNone(self.store.primary_account_id())
        # 无墓碑：登出后同账号重新登录是一个全新连接 id
        rebuilt = self.upsert("acct-A")
        self.assertNotEqual(rebuilt["id"], record["id"])


if __name__ == "__main__":
    unittest.main()
