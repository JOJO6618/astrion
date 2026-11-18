"""Responses 请求：system 文本作为 input 首条 developer 消息（2026-11-19）。

背景：gpt-6.2 系起后端不缓存 ``instructions`` 字段，缓存缺口恒等于 system
prompt 大小。规避方式与 opencode PR #64542 一致：把 system 文本改成 input
里的 leading developer 消息，且**完全不写** ``instructions`` 字段。

运行：``.venv/bin/python -B -m unittest discover -s "test/2026-11-19_instructions改走input前缀" -v``
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from modules.system_prompt_snapshot import SYSTEM_PROMPT_MARKER  # noqa: E402
from utils.api_client.responses.translate import (  # noqa: E402
    build_responses_body,
    should_send_prompt_cache_key,
    should_use_developer_prompt,
)

CODEX = dict(
    base_url="https://chatgpt.com/backend-api/codex",
    provider_id="openai-codex",
    responses_auth="codex_oauth",
)
OCGO = dict(
    base_url="https://opencode.ai/zen/go/v1",
    provider_id="ocgo",
    responses_auth=None,
)
OPENAI_API = dict(
    base_url="https://api.openai.com/v1",
    provider_id="openai-api",
    responses_auth=None,
)
XAI = dict(
    base_url="https://api.x.ai/v1",
    provider_id="xai",
    responses_auth=None,
)


def frozen_system(text="FROZEN-SYSTEM"):
    return {"role": "system", "content": text, SYSTEM_PROMPT_MARKER: True}


class DeveloperPromptTest(unittest.TestCase):
    def _body(self, profile, messages, **overrides):
        use_dev = should_use_developer_prompt(
            model_id=overrides.get("model_id"), **profile
        )
        send_key = should_send_prompt_cache_key(**profile)
        return build_responses_body(
            messages=messages,
            instructions=overrides.get("instructions", ""),
            model_id=overrides.get("model_id") or "gpt-6.2-sol",
            session_id="sid" if send_key else None,
            use_developer_prompt=use_dev,
        )

    # ----------------------------------------------------------- 启用分支

    def test_codex_oauth_moves_system_into_leading_developer_message(self):
        body = self._body(CODEX, [frozen_system(), {"role": "user", "content": "hi"}],
                          model_id="gpt-6.2-sol")
        self.assertNotIn("instructions", body)
        self.assertEqual(body["input"][0], {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "FROZEN-SYSTEM"}],
        })
        self.assertEqual(body["input"][1]["role"], "user")
        self.assertEqual(body["prompt_cache_key"], "sid")

    def test_third_party_openai_model_also_moves(self):
        """ocgo 上的 gpt 模型同样受 6.2 缓存回归影响，按模型名判定。"""
        body = self._body(OCGO, [frozen_system(), {"role": "user", "content": "hi"}],
                          model_id="gpt-6.1-sol")
        self.assertNotIn("instructions", body)
        self.assertEqual(body["input"][0]["role"], "developer")

    def test_openai_api_moves_and_keeps_cache_key(self):
        body = self._body(OPENAI_API, [frozen_system(), {"role": "user", "content": "hi"}],
                          model_id="gpt-5.1-codex")
        self.assertNotIn("instructions", body)
        self.assertEqual(body["input"][0]["role"], "developer")
        self.assertEqual(body["prompt_cache_key"], "sid")

    def test_o_series_models_are_recognised(self):
        for model in ("o3", "o4-mini", "o1-preview", "chatgpt-4o-latest", "gpt-6.2-astra"):
            self.assertTrue(
                should_use_developer_prompt(model_id=model, **OCGO), model
            )

    # ----------------------------------------------------------- 保持原样

    def test_non_openai_models_keep_instructions(self):
        for model, profile in (
            ("grok-4", OCGO), ("grok-4", XAI),
            ("deepseek-v4", OCGO), ("claude-sonnet-4-5", OCGO),
        ):
            body = self._body(profile, [frozen_system(), {"role": "user", "content": "hi"}],
                              model_id=model)
            self.assertEqual(body["instructions"], "FROZEN-SYSTEM", model)
            self.assertEqual(body["input"][0]["role"], "user", model)
            self.assertNotIn("prompt_cache_key", body, model)

    def test_open_source_gpt_oss_is_excluded(self):
        self.assertFalse(should_use_developer_prompt(model_id="gpt-oss-120b", **OCGO))

    # ----------------------------------------------------------- 拼接与兼容

    def test_multiple_system_messages_are_merged_into_developer_message(self):
        messages = [
            frozen_system("saved system"),
            {"role": "user", "content": "question"},
            {"role": "system", "content": "existing runtime notice"},
        ]
        body = self._body(CODEX, messages)
        self.assertEqual(
            body["input"][0]["content"][0]["text"],
            "saved system\n\nexisting runtime notice",
        )

    def test_default_keeps_legacy_instructions_shape(self):
        """use_developer_prompt 默认 False —— 既有调用方与测试行为不变。"""
        body = build_responses_body(
            messages=[frozen_system(), {"role": "user", "content": "hi"}],
            instructions="", model_id="gpt-6.2-sol",
        )
        self.assertEqual(body["instructions"], "FROZEN-SYSTEM")
        self.assertEqual(body["input"][0]["role"], "user")

    def test_hard_constraints_survive_developer_mode(self):
        body = self._body(CODEX, [frozen_system(), {"role": "user", "content": "hi"}])
        self.assertIs(body["store"], False)
        self.assertIs(body["stream"], True)
        self.assertEqual(body["include"], ["reasoning.encrypted_content"])
        self.assertNotIn("temperature", body)
        self.assertNotIn("top_p", body)

    def test_unfrozen_system_prefixes_builder_instructions(self):
        """无冻结标记时保留 instructions 前缀拼接（原行为），只是换了承载位置。"""
        body = self._body(
            CODEX,
            [{"role": "system", "content": "plain system"}, {"role": "user", "content": "hi"}],
            instructions="BASE",
        )
        self.assertEqual(body["input"][0]["content"][0]["text"], "BASE\n\nplain system")


if __name__ == "__main__":
    unittest.main(verbosity=2)
