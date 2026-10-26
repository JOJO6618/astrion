"""Real Chat APIClient headers, account opt-out, and authoritative tree storage."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modules.chat_mode.runtime import ChatRuntime
from modules.chat_mode.storage import ChatStorage
from modules.external_session import resolve_context_headers


class SessionHeadersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = ChatStorage(self.root / "chat")
        self.cid = self.storage.create(model_key="synthetic")
        self.turn = self.storage.begin(self.cid, "send", message={
            "role": "user", "message_id": "user", "content": "hello",
        })["value"]["id"]
        profile = {
            "key": "synthetic", "api_protocol": "chat_completions",
            "fast": {"base_url": "https://opencode.ai/zen/go/v1",
                     "model_id": "synthetic", "api_key": "synthetic"},
            "headers": {"X-Test-Provider": "static"},
        }
        with patch("modules.chat_mode.runtime.get_model_profile", return_value=profile):
            self.runtime = ChatRuntime(self.storage, self.cid, self.turn,
                                       preferences_dir=self.root, username="synthetic")

    def test_shared_header_is_stable_and_preserves_provider_headers_and_tree(self):
        before = self.storage.read(self.cid)
        with patch("modules.personalization_manager.load_personalization_config",
                   return_value={"external_session_header": True}) as preferences:
            headers = self.runtime.api_client._build_headers(
                "synthetic", "https://opencode.ai/zen/go/v1")
            shared = resolve_context_headers("https://opencode.ai/zen/go/v1",
                                             self.runtime.context_manager, self.root)
            self.assertEqual(headers["x-opencode-session"], shared["x-opencode-session"])
            self.assertEqual(headers["X-Test-Provider"], "static")
            preferences.assert_called_with(self.root)
        saved = self.storage.read(self.cid)
        self.assertEqual(saved["metadata"]["external_session_id"], headers["x-opencode-session"])
        self.assertNotEqual(headers["x-opencode-session"], self.cid)
        self.assertEqual(saved["messages"], before["messages"])
        self.assertEqual(saved["metadata"]["chat"], before["metadata"]["chat"])
        self.assertFalse(hasattr(self.runtime, "terminal_manager"))

    def test_account_opt_out_and_other_providers_do_not_receive_header(self):
        with patch("modules.personalization_manager.load_personalization_config",
                   return_value={"external_session_header": False}):
            self.assertNotIn("x-opencode-session", self.runtime.api_client._build_headers(
                "synthetic", "https://opencode.ai/zen/go/v1"))
        with patch("modules.personalization_manager.load_personalization_config",
                   return_value={"external_session_header": True}):
            self.assertNotIn("x-opencode-session", self.runtime.api_client._build_headers(
                "synthetic", "https://example.test/v1"))
        self.assertNotIn("external_session_id", self.storage.read(self.cid)["metadata"])

    def test_new_runtime_restores_same_session_id(self):
        with patch("modules.personalization_manager.load_personalization_config",
                   return_value={"external_session_header": True}):
            first = resolve_context_headers("https://opencode.ai/zen/go/v1",
                                            self.runtime.context_manager, self.root)
            self.runtime.context_manager.refresh()
            self.assertEqual(first, resolve_context_headers("https://opencode.ai/zen/go/v1",
                                                           self.runtime.context_manager, self.root))


if __name__ == "__main__":
    unittest.main()
