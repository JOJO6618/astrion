"""请求链路按账号路由：profile 携带账号身份、模型缓存/instructions/effort 使用该账号。"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils.api_client import APIClient  # noqa: E402
from utils.api_client.codex.models import (  # noqa: E402
    CodexModelsManager,
    _profile_from_model_info,
)


def codex_profile(connection_id: str, provider_name: str) -> dict:
    return _profile_from_model_info(
        "gpt-test", {"slug": "gpt-test", "visibility": "list"}, connection_id, provider_name
    )


class _FakeAuth:
    def __init__(self, path):
        self.auth_path = str(path)


class CodexRoutingTests(unittest.TestCase):
    def test_profile_carries_connection_identity_and_grouping(self):
        profile_a = codex_profile("openai-codex", "OpenAI（ChatGPT 订阅）-aaaaaa")
        profile_b = codex_profile("openai-codex-bbbbbbbb", "OpenAI（ChatGPT 订阅）-bbbbbb")
        self.assertEqual(profile_a["provider_id"], "openai-codex")
        self.assertEqual(profile_a["responses_account_id"], "openai-codex")
        self.assertEqual(profile_b["provider_id"], "openai-codex-bbbbbbbb")
        self.assertEqual(profile_b["responses_account_id"], "openai-codex-bbbbbbbb")
        self.assertEqual(profile_b["provider_name"], "OpenAI（ChatGPT 订阅）-bbbbbb")
        # 分组按连接（provider_id 不同 → 两个组）
        self.assertNotEqual(profile_a["provider_id"], profile_b["provider_id"])

    def test_apply_profile_sets_account_and_auth_manager_follows_it(self):
        client = APIClient(thinking_mode=True)
        client.apply_profile(codex_profile("openai-codex-deadbeef", "OpenAI（ChatGPT 订阅）-deadbe"))
        self.assertEqual(client.responses_account_id, "openai-codex-deadbeef")
        self.assertTrue(client._is_codex_oauth())

        fake_store = MagicMock()
        fake_store.get_auth_manager.return_value = "AUTH-FOR-ACCOUNT"
        with patch(
            "utils.api_client.codex.accounts.get_codex_account_store", return_value=fake_store
        ):
            self.assertEqual(client._codex_auth_manager(), "AUTH-FOR-ACCOUNT")
        fake_store.get_auth_manager.assert_called_once_with("openai-codex-deadbeef")

    def test_models_manager_and_instructions_use_selected_account(self):
        client = APIClient(thinking_mode=True)
        client.apply_profile(codex_profile("openai-codex-deadbeef", "OpenAI（ChatGPT 订阅）-deadbe"))
        fake_manager = MagicMock()
        fake_manager.get_model_info.return_value = {
            "supported_reasoning_levels": [{"effort": "high"}],
            "default_reasoning_level": "medium",
        }
        fake_manager.get_base_instructions.return_value = "OFFICIAL-INSTRUCTIONS"
        client.reasoning_effort = "low"
        with patch(
            "utils.api_client.codex.models.get_models_manager", return_value=fake_manager
        ) as factory, patch(
            "utils.api_client.codex.prompt.get_base_instructions",
            return_value="OFFICIAL-INSTRUCTIONS",
        ):
            effort = client._resolve_responses_effort("gpt-test")
            instructions = client._build_responses_instructions("gpt-test")
        self.assertEqual(effort, "medium", "不支持的档位回落到该账号模型的默认档")
        self.assertIn("OFFICIAL-INSTRUCTIONS", instructions)
        factory.assert_called_with("openai-codex-deadbeef")

    def test_models_cache_isolated_per_account(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        a = CodexModelsManager(
            auth_manager=_FakeAuth(root / "a.json"),
            account_id="openai-codex-aaaaaaaa",
            cache_path=str(root / "a-cache.json"),
        )
        b = CodexModelsManager(
            auth_manager=_FakeAuth(root / "b.json"),
            account_id="openai-codex-bbbbbbbb",
            cache_path=str(root / "b-cache.json"),
        )
        (root / "a.json").write_text("{}", encoding="utf-8")
        (root / "b.json").write_text("{}", encoding="utf-8")
        a._write_cache({"models": [{"slug": "model-a"}], "source": "online"})
        b._write_cache({"models": [{"slug": "model-b"}], "source": "online"})
        fresh_a = CodexModelsManager(
            auth_manager=a._auth,
            account_id=a.account_id,
            cache_path=str(root / "a-cache.json"),
        )
        fresh_b = CodexModelsManager(
            auth_manager=b._auth,
            account_id=b.account_id,
            cache_path=str(root / "b-cache.json"),
        )
        self.assertEqual(fresh_a._load()["models"][0]["slug"], "model-a")
        self.assertEqual(fresh_b._load()["models"][0]["slug"], "model-b")

    def test_default_cache_path_is_per_account(self):
        """未显式传 cache_path 时（生产路径），新增账号必须解析到自己的缓存文件。"""
        fake_store = MagicMock()
        fake_store.get_models_cache_path.return_value = "/tmp/accounts-cccc.json"
        with patch(
            "utils.api_client.codex.accounts.get_codex_account_store", return_value=fake_store
        ):
            manager = CodexModelsManager(
                auth_manager=_FakeAuth("/tmp/c.json"), account_id="openai-codex-cccccccc"
            )
        self.assertEqual(manager._cache_path, "/tmp/accounts-cccc.json")
        self.assertFalse(manager._legacy_cache)
        fake_store.get_models_cache_path.assert_called_once_with("openai-codex-cccccccc")

    def test_legacy_default_cache_keeps_shared_file(self):
        from utils.api_client.codex.settings import CODEX_DATA_PATH

        manager = CodexModelsManager(
            auth_manager=_FakeAuth("/tmp/legacy.json"), account_id="openai-codex"
        )
        self.assertEqual(manager._cache_path, str(CODEX_DATA_PATH))
        self.assertTrue(manager._legacy_cache)

    def test_non_legacy_fallback_never_reads_cli_account_cache(self):
        """P2-A：非 legacy 账号在线失败时不得拿 CLI 账号的 models_cache 充数。"""
        import json

        from utils.api_client.codex import models as models_module

        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        cli_cache = root / "cli_models_cache.json"
        cli_cache.write_text(
            json.dumps({"models": [{"slug": "cli-only-model"}]}), encoding="utf-8"
        )
        with patch.object(models_module, "CODEX_CLI_MODELS_CACHE", str(cli_cache)):
            manager = CodexModelsManager(
                auth_manager=_FakeAuth(root / "a.json"),
                account_id="openai-codex-aaaaaaaa",
                cache_path=str(root / "a-cache.json"),
            )
            result = manager._fallback({}, {})
        self.assertEqual(result["fallback"], "empty")
        self.assertEqual(manager._load()["models"], [])

    def test_legacy_fallback_can_still_use_cli_cache(self):
        import json

        from utils.api_client.codex import models as models_module

        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        legacy_cache = root / "codex_models.json"
        cli_cache = root / "cli_models_cache.json"
        cli_cache.write_text(
            json.dumps({"models": [{"slug": "cli-only-model"}]}), encoding="utf-8"
        )
        with patch.object(models_module, "CODEX_DATA_PATH", str(legacy_cache)), patch.object(
            models_module, "CODEX_CLI_MODELS_CACHE", str(cli_cache)
        ):
            manager = CodexModelsManager(
                auth_manager=_FakeAuth(root / "legacy.json"),
                account_id="openai-codex",
                cache_path=str(legacy_cache),
            )
            result = manager._fallback({}, {})
        self.assertEqual(result["fallback"], "cli_cache")

    def test_models_manager_default_account_matches_auth_manager(self):
        """P2-B/小问题：None 默认身份统一为主账号，避免凭证与缓存错配。"""
        from utils.api_client.codex import models as models_module

        fake_store = MagicMock()
        fake_store.primary_account_id.return_value = "openai-codex-cccccccc"
        fake_store.get_auth_manager.return_value = _FakeAuth("/tmp/primary.json")
        try:
            with patch(
                "utils.api_client.codex.accounts.get_codex_account_store",
                return_value=fake_store,
            ):
                manager = models_module.get_models_manager(None)
                explicit = models_module.get_models_manager("openai-codex-cccccccc")
            self.assertEqual(manager.account_id, "openai-codex-cccccccc")
            self.assertIs(manager, explicit)
            fake_store.get_auth_manager.assert_called_with("openai-codex-cccccccc")
        finally:
            models_module._MANAGERS.pop("openai-codex-cccccccc", None)

    def test_encrypted_reasoning_isolated_across_accounts(self):
        from core.main_terminal_parts.context.messages import _same_responses_provider

        profiles = {
            "openai-codex/gpt-test": codex_profile("openai-codex", "OpenAI（ChatGPT 订阅）-aaaaaa"),
            "openai-codex-bbbbbbbb/gpt-test": codex_profile(
                "openai-codex-bbbbbbbb", "OpenAI（ChatGPT 订阅）-bbbbbb"
            ),
        }
        with patch(
            "config.model_profiles.get_registered_model_profiles", return_value=profiles
        ):
            self.assertTrue(
                _same_responses_provider("openai-codex/gpt-test", "openai-codex/gpt-test")
            )
            self.assertFalse(
                _same_responses_provider(
                    "openai-codex/gpt-test", "openai-codex-bbbbbbbb/gpt-test"
                )
            )
            self.assertFalse(
                _same_responses_provider(
                    "openai-codex-bbbbbbbb/gpt-test", "openai-codex/gpt-test"
                )
            )


if __name__ == "__main__":
    unittest.main()
