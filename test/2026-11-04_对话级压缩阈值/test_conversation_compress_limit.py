"""对话级压缩阈值：快照、读写与回退的回归测试。

运行：PYTHONPATH=. /opt/homebrew/bin/python3.11 -B -m unittest \
        discover -s "test/2026-11-04_对话级压缩阈值" -p "test_*.py"
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.adaptive_context import effective_trigger  # noqa: E402
from modules.conversation_compress_limit import (  # noqa: E402
    LIMIT_KEY,
    SOURCE_KEY,
    configured_for,
    custom_update,
    limit_tokens,
    parse_tokens,
    resolved,
    snapshot_update,
    source,
)
from utils.conversation_manager import ConversationManager  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_rejects_invalid(self):
        for value in (True, False, None, "abc", 999, 2_000_001, -5, 1000.5):
            self.assertIsNone(parse_tokens(value), value)

    def test_accepts_valid(self):
        self.assertEqual(parse_tokens(1000), 1000)
        self.assertEqual(parse_tokens("256000"), 256000)
        self.assertEqual(parse_tokens(2_000_000), 2_000_000)


class ResolveTests(unittest.TestCase):
    def test_conversation_record_wins(self):
        metadata = {LIMIT_KEY: 300_000, SOURCE_KEY: "snapshot"}
        self.assertEqual(resolved(metadata, 250_000), 300_000)

    def test_missing_falls_back_to_configured(self):
        self.assertEqual(resolved({}, 250_000), 250_000)
        self.assertEqual(limit_tokens({}), None)

    def test_invalid_record_falls_back(self):
        self.assertEqual(resolved({LIMIT_KEY: 10}, 250_000), 250_000)
        self.assertEqual(resolved({}, "bad"), 0)

    def test_source_labels(self):
        self.assertEqual(source({}), "missing")
        self.assertEqual(source({LIMIT_KEY: 250_000}), "snapshot")
        self.assertEqual(source({LIMIT_KEY: 250_000, SOURCE_KEY: "custom"}), "custom")

    def test_updates(self):
        self.assertEqual(snapshot_update(250_000), {LIMIT_KEY: 250_000, SOURCE_KEY: "snapshot"})
        self.assertEqual(custom_update(120_000), {LIMIT_KEY: 120_000, SOURCE_KEY: "custom"})
        with self.assertRaises(ValueError):
            custom_update(10)
        with self.assertRaises(ValueError):
            custom_update(None)

    def test_adaptive_adds_baseline_on_top(self):
        metadata = {LIMIT_KEY: 300_000, "context_baseline_tokens": 52_000}
        self.assertEqual(effective_trigger(metadata, resolved(metadata, 250_000)), 352_000)


class ConfiguredAndSnapshotTests(unittest.TestCase):
    def _write_prefs(self, root: Path, payload: dict):
        (root / "personalization.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_configured_for_agent_and_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_prefs(
                root,
                {"deep_compress_trigger_tokens": 300_000, "chat_deep_compress_trigger_tokens": 180_000},
            )
            self.assertEqual(configured_for(root, "agent"), 300_000)
            self.assertEqual(configured_for(root, "chat"), 180_000)

    def test_configured_for_missing_file_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(configured_for(Path(tmp), "agent"), 250_000)
            self.assertEqual(configured_for(Path(tmp), "chat"), 250_000)

    def test_create_conversation_snapshots_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_prefs(root, {"deep_compress_trigger_tokens": 320_000})
            manager = ConversationManager(base_dir=str(root))
            conversation_id = manager.create_conversation(project_path=None, run_mode="fast")
            document = manager.read_conversation(conversation_id)
            metadata = document["metadata"]
            self.assertEqual(metadata[LIMIT_KEY], 320_000)
            self.assertEqual(metadata[SOURCE_KEY], "snapshot")

    def test_chat_conversation_uses_chat_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_prefs(root, {"chat_deep_compress_trigger_tokens": 180_000})
            manager = ConversationManager(base_dir=str(root), conversation_kind="chat")
            conversation_id = manager.create_conversation(
                project_path=None,
                run_mode="fast",
                metadata_overrides={"conversation_kind": "chat"},
            )
            metadata = manager.read_conversation(conversation_id)["metadata"]
            self.assertEqual(metadata[LIMIT_KEY], 180_000)
            self.assertEqual(metadata[SOURCE_KEY], "snapshot")


if __name__ == "__main__":
    unittest.main()
