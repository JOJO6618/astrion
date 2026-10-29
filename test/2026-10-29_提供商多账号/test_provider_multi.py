"""提供商多连接：展示序号空洞复用、内部连接身份永不复用、旧引用不串号。

运行：
    python3 -m unittest discover -s test/2026-10-29_提供商多账号 -p 'test_*.py'
"""
import json
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.provider_manager import ProviderManager  # noqa: E402


def _models(_base_url, _api_key, _headers):
    return [{"id": "m1"}], None


class ProviderMultiConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.patchers = [
            patch("modules.provider_manager.DATA_DIR", str(self.data_dir)),
            patch.object(ProviderManager, "_fetch_models", side_effect=_models),
            patch.object(ProviderManager, "_refresh_modelsdev_cache", return_value=None),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = ProviderManager()

    def connect(self, catalog_id, api_key):
        return self.manager.connect({"catalog_id": catalog_id, "api_key": api_key})

    def records(self):
        return json.loads((self.data_dir / "providers.json").read_text(encoding="utf-8"))[
            "providers"
        ]

    def connection_for_key(self, api_key):
        for pid, record in self.records().items():
            if record.get("api_key") == api_key:
                return pid, record
        raise AssertionError(f"connection for {api_key} not found")

    # ------------------------------------------------------------------ 编号
    def test_display_index_and_smallest_free_reuse_after_delete(self):
        first = self.connect("deepseek", "k1")
        second = self.connect("deepseek", "k2")
        third = self.connect("deepseek", "k3")
        self.assertTrue(first["success"] and second["success"] and third["success"])
        self.assertEqual(first["provider"]["name"], "DeepSeek")
        self.assertEqual(second["provider"]["name"], "DeepSeek-1")
        self.assertEqual(third["provider"]["name"], "DeepSeek-2")
        ids = {first["provider"]["id"], second["provider"]["id"], third["provider"]["id"]}
        self.assertEqual(len(ids), 3, "每条连接必须有自己的内部 id")

        deleted_id = second["provider"]["id"]
        old_model_key = f"{deleted_id}/m1"
        self.assertIn(old_model_key, self.manager.get_profiles())
        self.assertTrue(self.manager.disconnect(deleted_id))
        self.assertNotIn(old_model_key, self.manager.get_profiles())

        # 删除「DeepSeek-1」后，新 Key 重新占用展示序号 1，但内部 id 绝不复用
        fourth = self.connect("deepseek", "k4")
        self.assertEqual(fourth["provider"]["name"], "DeepSeek-1")
        self.assertNotEqual(fourth["provider"]["id"], deleted_id)
        self.assertNotIn(fourth["provider"]["id"], {first["provider"]["id"], third["provider"]["id"]})

        # 再添加一个：序号 0/1/2 已占用 → 取最小空闲 3
        fifth = self.connect("deepseek", "k5")
        self.assertEqual(fifth["provider"]["name"], "DeepSeek-3")

        # 旧连接的两条模型 key 都不再存在；新连接 key 只属于新 id
        profiles = self.manager.get_profiles()
        self.assertNotIn(old_model_key, profiles)
        self.assertIn(f"{fourth['provider']['id']}/m1", profiles)
        self.assertNotIn(f"{fourth['provider']['id']}/m1", {old_model_key})

    def test_old_reference_never_silently_uses_new_key(self):
        first = self.connect("deepseek", "k1")
        old_id = first["provider"]["id"]
        old_key = f"{old_id}/m1"
        old_profile = self.manager.get_profiles()[old_key]
        self.assertEqual(old_profile["fast"]["api_key"], "k1")
        self.assertEqual(old_profile["provider_name"], "DeepSeek")

        self.manager.disconnect(old_id)
        replacement = self.connect("deepseek", "k2")
        self.assertEqual(replacement["provider"]["name"], "DeepSeek")
        self.assertNotEqual(replacement["provider"]["id"], old_id)
        # 旧模型 key 解析不到 → 上层明确报错，而不是拿到新 Key
        self.assertNotIn(old_key, self.manager.get_profiles())
        self.assertNotIn(old_id, self.records())

    def test_duplicate_active_key_rejected(self):
        self.assertTrue(self.connect("deepseek", "same-key")["success"])
        duplicate = self.connect("deepseek", "same-key")
        self.assertFalse(duplicate["success"])
        self.assertEqual(duplicate["error"], "duplicate_connection")
        self.assertEqual(len(self.records()), 1)

    def test_custom_provider_single_identity(self):
        payload = {
            "provider_id": "myproxy",
            "name": "My Proxy",
            "base_url": "https://example.com/v1",
            "api_key": "sk-x",
        }
        first = self.manager.connect(payload)
        self.assertTrue(first["success"])
        internal_id = first["provider"]["id"]
        self.assertTrue(internal_id.startswith("custom-myproxy-"), internal_id)
        self.assertNotEqual(internal_id, "myproxy")
        # 活跃期间同一基础 id 仍拒绝重复添加
        again = self.manager.connect(payload)
        self.assertFalse(again["success"])
        self.assertEqual(again["error"], "provider_id_exists")
        profile = self.manager.get_profiles()[f"{internal_id}/m1"]
        self.assertEqual(profile["provider_name"], "My Proxy")
        self.assertNotIn("myproxy/m1", self.manager.get_profiles())

    def test_custom_connection_uses_opaque_non_reusable_internal_id(self):
        """P1-A：自定义连接不能用用户填的 provider_id 当可复用路由身份。"""
        payload = {
            "provider_id": "myproxy",
            "name": "My Proxy",
            "base_url": "https://example.com/v1",
            "api_key": "k-old",
        }
        first = self.manager.connect(payload)
        self.assertTrue(first["success"])
        old_id = first["provider"]["id"]
        old_key = f"{old_id}/m1"
        self.assertEqual(self.manager.get_profiles()[old_key]["fast"]["api_key"], "k-old")

        # 删除后用同一个用户 id 重新连接：内部 id 必须变，旧模型引用不能串新 Key
        self.assertTrue(self.manager.disconnect(old_id))
        replacement = self.manager.connect({**payload, "api_key": "k-new"})
        self.assertTrue(replacement["success"])
        new_id = replacement["provider"]["id"]
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(replacement["provider"]["requested_id"], "myproxy")
        profiles = self.manager.get_profiles()
        self.assertNotIn(old_key, profiles)
        self.assertNotIn("myproxy/m1", profiles)
        self.assertEqual(profiles[f"{new_id}/m1"]["fast"]["api_key"], "k-new")

    def test_custom_provider_id_cannot_hijack_deleted_subscription_keys(self):
        """自定义 provider_id=openai-codex 不得占用订阅主账号的旧模型 key。"""
        result = self.manager.connect(
            {
                "provider_id": "openai-codex",
                "name": "伪装订阅",
                "base_url": "https://evil.example.com/v1",
                "api_key": "k-attacker",
            }
        )
        self.assertTrue(result["success"])
        internal_id = result["provider"]["id"]
        self.assertTrue(internal_id.startswith("custom-openai-codex-"), internal_id)
        self.assertNotEqual(internal_id, "openai-codex")
        profiles = self.manager.get_profiles()
        # 订阅主账号旧 key 仍然不存在，也没有被自定义连接顶替
        self.assertNotIn("openai-codex/gpt-5", profiles)
        self.assertIn(f"{internal_id}/m1", profiles)

    def test_legacy_custom_record_keeps_identity_until_explicit_delete(self):
        """存量自定义记录（旧键就是用户 id）保持旧引用；删除后同 id 重建不串号。"""
        legacy_store = {
            "version": 1,
            "providers": {
                "myproxy": {
                    "catalog_id": None,
                    "name": "My Proxy",
                    "base_url": "https://example.com/v1",
                    "api_key": "legacy-key",
                    "headers": {},
                    "enabled": True,
                    "connected_at": "2026-09-24T00:00:00.000000Z",
                    "models": [{"id": "m1"}],
                    "models_fetched_at": "2026-09-24T00:00:00.000000Z",
                    "models_error": None,
                }
            },
        }
        (self.data_dir / "providers.json").write_text(json.dumps(legacy_store), encoding="utf-8")
        manager = ProviderManager()
        self.assertIn("myproxy/m1", manager.get_profiles())
        self.assertEqual(manager.get_provider("myproxy")["requested_id"], "myproxy")

        self.assertTrue(manager.disconnect("myproxy"))
        rebuilt = manager.connect(
            {
                "provider_id": "myproxy",
                "name": "My Proxy",
                "base_url": "https://example.com/v1",
                "api_key": "new-key",
            }
        )
        self.assertTrue(rebuilt["success"])
        self.assertNotEqual(rebuilt["provider"]["id"], "myproxy")
        self.assertNotIn("myproxy/m1", manager.get_profiles())

    def test_local_provider_does_not_expose_placeholder_key_mask(self):
        """auth=none 的占位 key（not-required）不是凭证，不应展示掩码。"""
        result = self.manager.connect(
            {
                "provider_id": "ollama-local",
                "name": "Ollama Local",
                "base_url": "http://127.0.0.1:11434/v1",
                "api_key": "",
            }
        )
        self.assertTrue(result["success"])
        self.assertIsNone(result["provider"]["api_key_masked"])
        row = next(
            item
            for item in self.manager.catalog()
            if item.get("connection") and item["id"] == result["provider"]["id"]
        )
        self.assertIsNone(row["api_key_masked"])

    # -------------------------------------------------------------- 迁移
    def test_v1_single_connection_migration_keeps_old_model_keys(self):
        legacy_store = {
            "version": 1,
            "providers": {
                "deepseek": {
                    "catalog_id": "deepseek",
                    "name": "DeepSeek",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "legacy-key",
                    "headers": {},
                    "enabled": True,
                    "connected_at": "2026-09-24T00:00:00.000000Z",
                    "models": [{"id": "deepseek-chat"}],
                    "models_fetched_at": "2026-09-24T00:00:00.000000Z",
                    "models_error": None,
                }
            },
        }
        (self.data_dir / "providers.json").write_text(
            json.dumps(legacy_store), encoding="utf-8"
        )
        manager = ProviderManager()
        profiles = manager.get_profiles()
        # 旧模型引用保持有效（连接 id 沿用旧键，展示序号 0）
        self.assertIn("deepseek/deepseek-chat", profiles)
        self.assertEqual(profiles["deepseek/deepseek-chat"]["fast"]["api_key"], "legacy-key")

        # 迁移幂等：重复加载不会改变 id/序号
        reloaded = ProviderManager()
        self.assertIn("deepseek/deepseek-chat", reloaded.get_profiles())
        record = reloaded.get_provider("deepseek")
        self.assertEqual(record["display_index"], 0)
        self.assertEqual(record["name"], "DeepSeek")

        # 迁移后新增连接：内部 id 随机、展示序号 1，旧 key 仍只指向旧 Key
        added = self.connect("deepseek", "new-key")
        self.assertEqual(added["provider"]["name"], "DeepSeek-1")
        self.assertNotEqual(added["provider"]["id"], "deepseek")
        after = ProviderManager().get_profiles()
        self.assertEqual(after["deepseek/deepseek-chat"]["fast"]["api_key"], "legacy-key")
        self.assertEqual(
            after[f"{added['provider']['id']}/m1"]["fast"]["api_key"],
            "new-key",
        )

    # -------------------------------------------------------------- 并发
    def test_parallel_connects_do_not_lose_updates(self):
        import time

        def slow_fetch(base_url, api_key, headers):
            # 拉宽 read-modify-write 窗口：没有锁保护时第二个连接会覆盖第一个
            time.sleep(0.05)
            return [{"id": "m1"}], None

        errors = []

        def worker(key):
            try:
                # 生产环境为进程级单例，同一实例的锁串行化 read-modify-write
                result = self.manager.connect({"catalog_id": "deepseek", "api_key": key})
                if not result.get("success"):
                    errors.append(result)
            except Exception as exc:  # pragma: no cover - 诊断用
                errors.append(exc)

        with patch.object(ProviderManager, "_fetch_models", side_effect=slow_fetch):
            threads = [
                threading.Thread(target=worker, args=("k-a",)),
                threading.Thread(target=worker, args=("k-b",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertEqual(errors, [])
        records = self.records()
        keys = sorted(record.get("api_key") for record in records.values())
        self.assertEqual(keys, ["k-a", "k-b"], "并发连接不能丢更新")
        indexes = sorted(record.get("display_index") for record in records.values())
        self.assertEqual(indexes, [0, 1])

    def test_catalog_lists_each_connection_and_keeps_add_entry(self):
        first = self.connect("deepseek", "k1")
        second = self.connect("deepseek", "k2")
        catalog = self.manager.catalog()
        add_rows = [row for row in catalog if not row.get("connection")]
        connection_rows = [row for row in catalog if row.get("connection")]
        deepseek_add = next(row for row in add_rows if row["catalog_id"] == "deepseek")
        self.assertTrue(deepseek_add["has_connections"])
        self.assertEqual(deepseek_add["connection_count"], 2)
        self.assertTrue(deepseek_add["can_add"], "已有连接仍可继续添加独立 Key")
        names = sorted(row["name"] for row in connection_rows if row["catalog_id"] == "deepseek")
        self.assertEqual(names, ["DeepSeek", "DeepSeek-1"])
        ids = {row["id"] for row in connection_rows if row["catalog_id"] == "deepseek"}
        self.assertEqual(ids, {first["provider"]["id"], second["provider"]["id"]})
        self.assertTrue(all(row["entry_kind"] == "connection" for row in connection_rows))
        self.assertTrue(all(row["entry_kind"] == "catalog" for row in add_rows))

    def test_catalog_includes_credential_missing_codex_registration(self):
        """已登记但本地凭证暂缺的账号仍须作为连接行可见、可管理。"""
        account = {
            "id": "codex-abc12345",
            "hash": "abc12345",
            "display_name": "ChatGPT",
            "connected": False,
            "state": "credential_missing",
            "credential_missing": True,
        }
        store = MagicMock()
        store.list_accounts.return_value = [account]
        with (
            patch(
                "utils.api_client.codex.accounts.get_codex_account_store", return_value=store
            ),
            patch("utils.api_client.codex.models.get_models_manager") as models_manager,
        ):
            catalog = self.manager.catalog()

        codex_rows = [row for row in catalog if row["catalog_id"] == "openai-codex"]
        connection_rows = [row for row in codex_rows if row.get("entry_kind") == "connection"]
        self.assertEqual(len(connection_rows), 1, "凭证暂缺的登记账号不能被目录过滤掉")
        row = connection_rows[0]
        self.assertEqual(row["id"], account["id"])
        self.assertEqual(row["account_hash"], account["hash"])
        self.assertTrue(row["connection"])
        self.assertFalse(row["connected"], "必须保留真实 connected=false")
        self.assertEqual(row["state"], "credential_missing")
        self.assertTrue(row["credential_missing"])
        self.assertEqual(row["models_count"], 0, "凭证暂缺不能展示旧模型数")
        self.assertIsNone(row["models_fetched_at"])
        models_manager.assert_not_called()

        catalog_row = next(row for row in codex_rows if row.get("entry_kind") == "catalog")
        self.assertTrue(catalog_row["has_connections"], "登记仍在，has_connections 不能为 false")
        self.assertEqual(catalog_row["connection_count"], 1)
        self.assertTrue(catalog_row["can_add"], "目录按钮仍应可添加账号")

    def test_catalog_with_zero_codex_accounts_still_offers_add(self):
        store = MagicMock()
        store.list_accounts.return_value = []
        with patch(
            "utils.api_client.codex.accounts.get_codex_account_store", return_value=store
        ):
            catalog = self.manager.catalog()

        codex_rows = [row for row in catalog if row["catalog_id"] == "openai-codex"]
        self.assertTrue(codex_rows, "零账号时目录行仍须存在")
        self.assertFalse(
            [row for row in codex_rows if row.get("entry_kind") == "connection"],
            "零账号不应凭空产生连接行",
        )
        catalog_row = next(row for row in codex_rows if row.get("entry_kind") == "catalog")
        self.assertFalse(catalog_row["has_connections"])
        self.assertEqual(catalog_row["connection_count"], 0)
        self.assertTrue(catalog_row["can_add"], "零账号目录按钮必须正常")


if __name__ == "__main__":
    unittest.main()
