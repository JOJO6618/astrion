"""Chat 历史构建：Responses 加密 reasoning 只在「同上游连接」时回插。

覆盖 P1-B：Chat 允许同协议切换模型/账号/连接，旧账号的加密块不能带进新账号。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.chat_mode.context import ChatContext  # noqa: E402


def profile(provider_id: str, account_id: str = "") -> dict:
    return {
        "provider_id": provider_id,
        "responses_account_id": account_id or provider_id,
        "api_protocol": "responses",
    }


PROFILES = {
    "openai-codex/gpt-a": profile("openai-codex"),
    "openai-codex/gpt-b": profile("openai-codex"),
    "openai-codex-aaaaaaaa/gpt-a": profile("openai-codex-aaaaaaaa"),
    "deepseek-aaaa/gpt": profile("deepseek-aaaa"),
    "deepseek-bbbb/gpt": profile("deepseek-bbbb"),
}


def assistant_message(model_key: str, items=None) -> dict:
    return {
        "message_id": "m1",
        "role": "assistant",
        "content": "旧回复",
        "metadata": {
            "model_key": model_key,
            "responses_reasoning_items": items if items is not None else [{"type": "reasoning", "encrypted_content": "SECRET"}],
        },
    }


def build(history, current_model_key):
    ctx = object.__new__(ChatContext)
    ctx.conversation_history = history
    ctx.conversation_metadata = {"model_key": current_model_key}
    ctx.main_terminal = SimpleNamespace(model_key=current_model_key)
    return ctx.build_api_history()


class ChatReasoningIsolationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "config.model_profiles.get_registered_model_profiles", return_value=PROFILES
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_account_keeps_encrypted_reasoning(self):
        history = [assistant_message("openai-codex/gpt-a")]
        # 同账号切到另一个模型：仍可回放
        out = build(history, "openai-codex/gpt-b")
        self.assertIn("responses_reasoning_items", out[0])
        self.assertEqual(
            out[0]["responses_reasoning_items"][0]["encrypted_content"], "SECRET"
        )

    def test_cross_codex_account_strips_encrypted_reasoning(self):
        history = [assistant_message("openai-codex/gpt-a")]
        out = build(history, "openai-codex-aaaaaaaa/gpt-a")
        self.assertNotIn("responses_reasoning_items", out[0])
        # 原存储不得被输出引用/修改
        self.assertIn(
            "responses_reasoning_items", history[0]["metadata"], "不得改写权威历史"
        )

    def test_cross_provider_connection_strips_encrypted_reasoning(self):
        history = [assistant_message("deepseek-aaaa/gpt")]
        out = build(history, "deepseek-bbbb/gpt")
        self.assertNotIn("responses_reasoning_items", out[0])

    def test_same_provider_connection_keeps(self):
        history = [assistant_message("deepseek-aaaa/gpt")]
        out = build(history, "deepseek-aaaa/gpt")
        self.assertIn("responses_reasoning_items", out[0])

    def test_unknown_or_missing_current_model_strips(self):
        history = [assistant_message("openai-codex/gpt-a")]
        self.assertNotIn("responses_reasoning_items", build(history, "not-a-model")[0])
        self.assertNotIn("responses_reasoning_items", build(history, "")[0])

    def test_message_without_source_model_key_is_not_replayed(self):
        message = assistant_message("openai-codex/gpt-a")
        message["metadata"].pop("model_key")
        out = build([message], "openai-codex/gpt-a")
        self.assertNotIn("responses_reasoning_items", out[0])

    def test_output_items_are_deep_copied(self):
        history = [assistant_message("openai-codex/gpt-a")]
        out = build(history, "openai-codex/gpt-a")
        out[0]["responses_reasoning_items"][0]["encrypted_content"] = "CHANGED"
        self.assertEqual(
            history[0]["metadata"]["responses_reasoning_items"][0]["encrypted_content"],
            "SECRET",
        )


if __name__ == "__main__":
    unittest.main()
