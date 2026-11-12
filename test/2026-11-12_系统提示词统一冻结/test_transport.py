from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from support import Owner, isolated_builders
from modules.system_prompt_snapshot import SYSTEM_PROMPT_KEY, SYSTEM_PROMPT_MARKER
from utils.api_client.message_mixin import APIClientMessageMixin
from utils.api_client.responses.mixin import APIClientResponsesMixin
from utils.api_client.responses.translate import build_responses_body
from utils.context_manager.compression_mixin import CompressionMixin


class CapturedRequest(Exception):
    pass


class TransportAndCompressionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="system_transport_case_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        stack = isolated_builders()
        self.addCleanup(stack.close)

    def _capture_responses(self, messages):
        client = SimpleNamespace(
            _is_codex_oauth=lambda: False,
            get_current_thinking_mode=lambda: True,
            _select_api_config=lambda _: {"model_id": "test-model", "base_url": "http://127.0.0.1:9"},
            thinking_max_tokens=None, fast_max_tokens=None,
            max_context_tokens=0, default_context_window=0,
            _resolve_responses_effort=lambda _: None,
            _build_responses_instructions=Mock(return_value="fresh provider instructions"),
        )
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            raise CapturedRequest()

        async def consume():
            async for _ in APIClientResponsesMixin.chat_responses(client, messages):
                pass

        with patch("utils.api_client.responses.mixin.build_responses_body", side_effect=capture):
            with self.assertRaises(CapturedRequest):
                asyncio.run(consume())
        return captured, client

    def test_responses_does_not_rebuild_or_duplicate_provider_instructions(self):
        messages = [{"role": "system", "content": "saved provider\n\nsaved static", SYSTEM_PROMPT_MARKER: True},
                    {"role": "user", "content": "question"}]
        captured, client = self._capture_responses(messages)
        self.assertEqual(captured["instructions"], "")
        client._build_responses_instructions.assert_not_called()
        body = build_responses_body(**captured)
        self.assertEqual(body["instructions"], messages[0]["content"])
        self.assertNotIn(SYSTEM_PROMPT_MARKER, str(body))

    def test_chat_and_auxiliary_responses_keep_their_existing_instruction_path(self):
        captured, client = self._capture_responses([
            {"role": "system", "content": "auxiliary system"},
            {"role": "user", "content": "question"},
        ])
        self.assertEqual(captured["instructions"], "fresh provider instructions")
        client._build_responses_instructions.assert_called_once_with("test-model")

    def test_chat_completion_never_sends_the_local_frozen_marker(self):
        client = SimpleNamespace(_debug_log=lambda _: None)
        cleaned = APIClientMessageMixin._sanitize_message_fields_for_api(client, [
            {"role": "system", "content": "saved complete system", SYSTEM_PROMPT_MARKER: True}
        ])
        self.assertEqual(cleaned, [{"role": "system", "content": "saved complete system"}])

    def test_runtime_notifications_still_follow_the_frozen_responses_prefix(self):
        body = build_responses_body(
            messages=[{"role": "system", "content": "saved system", SYSTEM_PROMPT_MARKER: True},
                      {"role": "user", "content": "question"},
                      {"role": "system", "content": "existing runtime notice"}],
            instructions="", model_id="test-model",
        )
        self.assertEqual(body["instructions"], "saved system\n\nexisting runtime notice")

    def test_real_shallow_compression_invalidates_whole_snapshot_only_when_work_is_done(self):
        owner = Owner(self.root)
        frozen = owner.build_messages(owner.context(), "q")[0]["content"]
        cm = owner.context_manager
        cm.conversation_history = []
        cm._shallow_compact_round = 0
        self.assertEqual(CompressionMixin._run_auto_shallow_compression(cm, keep_recent_tools=0, keep_user_turn_tools=0), 0)
        self.assertEqual(cm.conversation_metadata[SYSTEM_PROMPT_KEY], frozen)
        cm.conversation_history = [{"role": "tool", "name": "read_file", "content": "old output"}]
        self.assertEqual(CompressionMixin._run_auto_shallow_compression(cm, keep_recent_tools=0, keep_user_turn_tools=0), 1)
        self.assertIsNone(cm.conversation_metadata[SYSTEM_PROMPT_KEY])
        self.assertIsNone(owner.saved()["metadata"][SYSTEM_PROMPT_KEY])

    def test_private_memory_exemption_accepts_the_combined_snapshot(self):
        from modules.workspace_memory_tools import injected_memory
        from modules.execution_plane.fake import FakeExecutionBackend

        owner = Owner(self.root)
        owner.context_manager.conversation_metadata.update({
            SYSTEM_PROMPT_KEY: "saved full system including memory",
            "workspace_memory_snapshot": {"path": "/memory", "version": "synthetic"},
        })
        files = SimpleNamespace(resolve_path=lambda path, **_: {"success": True, "path": path})
        with patch("modules.workspace_memory_tools.get_execution_backend", return_value=SimpleNamespace(files=files)):
            self.assertEqual(injected_memory(owner, "/memory")["version"], "synthetic")
            self.assertIsNone(injected_memory(owner, "/other"))


if __name__ == "__main__":
    unittest.main()
