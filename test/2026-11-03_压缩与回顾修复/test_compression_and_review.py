"""压缩混合模式、自适应阈值基线与导出容错的回归测试。

运行：PYTHONPATH=. /opt/homebrew/bin/python3.11 -B -m unittest \
        discover -s "test/2026-11-03_压缩与回顾修复" -p "test_*.py"
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.adaptive_context import (  # noqa: E402
    baseline_tokens,
    effective_trigger,
    effective_trigger_for,
    invalidated_metadata,
    pending_metadata,
    record_baseline,
)
from modules.conversation_transfer.assets import collect  # noqa: E402
from server.deep_compression import _build_hybrid_guide_message  # noqa: E402


class AdaptiveContextTests(unittest.TestCase):
    def test_trigger_includes_baseline(self):
        metadata = {"context_baseline_pending": True, "model_key": "p/x"}
        self.assertEqual(effective_trigger(metadata, 250000), 250000)
        updates = record_baseline(metadata, 52000)
        self.assertEqual(updates["context_baseline_tokens"], 52000)
        self.assertFalse(updates["context_baseline_pending"])
        metadata.update(updates)
        self.assertEqual(effective_trigger(metadata, 250000), 302000)
        self.assertEqual(baseline_tokens(metadata), 52000)

    def test_baseline_recorded_only_while_pending(self):
        metadata = {"context_baseline_pending": False}
        self.assertIsNone(record_baseline(metadata, 48000))
        self.assertIsNone(record_baseline({"context_baseline_pending": True}, 0))

    def test_model_invalidation_and_pending_keep_old_value(self):
        metadata = {"context_baseline_tokens": 52000, "context_baseline_pending": False}
        metadata.update(pending_metadata())
        # 压缩后到重测之前沿用旧基线，阈值不会瞬间回落
        self.assertEqual(effective_trigger(metadata, 250000), 302000)
        invalidated = invalidated_metadata()
        metadata.update(invalidated)
        self.assertEqual(effective_trigger(metadata, 250000), 250000)

    def test_effective_trigger_for_other_conversation_uses_configured(self):
        class FakeManager:
            current_conversation_id = "conv_now"
            conversation_metadata = {"context_baseline_tokens": 50000}

        self.assertEqual(effective_trigger_for(FakeManager(), "conv_now", 250000), 300000)
        self.assertEqual(effective_trigger_for(FakeManager(), "conv_other", 250000), 250000)
        self.assertEqual(effective_trigger_for(None, "conv_now", 250000), 250000)


class HybridGuideMessageTests(unittest.TestCase):
    def test_summary_inline_and_records_point_to_file(self):
        text = _build_hybrid_guide_message(
            compression_index=2,
            compact_file=".astrion/compact_result/compact_1.md",
            summary_text="用户正在修导出逻辑。",
            runtime_state_lines=["- 持久终端：当前没有终端会话。"],
        )
        self.assertIn("第2次压缩", text)
        self.assertIn(".astrion/compact_result/compact_1.md", text)
        self.assertIn("用户正在修导出逻辑。", text)
        self.assertIn("当前运行时状态", text)
        self.assertIn("不建议完整阅读之前的压缩记录", text)
        self.assertNotIn("推荐用子智能体", text)

    def test_sub_agent_hint_only_from_fourth_compression(self):
        text = _build_hybrid_guide_message(
            compression_index=4,
            compact_file=".astrion/compact_result/compact_4.md",
            summary_text="摘要",
        )
        self.assertIn("推荐用子智能体", text)
        self.assertIn("搜索功能", text)


class ExportToleranceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _document(self, content):
        return {
            "id": "conv_test",
            "metadata": {},
            "messages": [{"role": "user", "content": content}],
        }

    def test_missing_compact_record_does_not_block_export(self):
        document = self._document(
            "read `.astrion/compact_result/compact_missing.md` then continue"
        )
        resources, files = collect(document, self.project, self.project)
        self.assertEqual(resources, [])
        self.assertEqual(files, {})

    def test_existing_compact_record_is_collected(self):
        relative = ".astrion/compact_result/compact_1.md"
        target = self.project / relative
        target.parent.mkdir(parents=True)
        target.write_text("summary", encoding="utf-8")
        document = self._document(f"see `{relative}`")
        resources, files = collect(document, self.project, self.project)
        self.assertEqual(len(resources), 1)
        self.assertEqual(next(iter(files.values())), b"summary")

    def test_missing_handoff_material_still_fails(self):
        document = self._document(
            "read `.astrion/handoff/handoff_one/summary.md`"
        )
        with self.assertRaises(FileNotFoundError):
            collect(document, self.project, self.project)


class ReviewPreviewTests(unittest.TestCase):
    """with_terminal 返回对话级 terminal 时，回顾接口不得再拦截“当前对话”。"""

    def test_bound_conversation_can_still_be_reviewed(self):
        from flask import Flask
        import server.conversation as conversation_module

        class FakeManager:
            def load_conversation(self, conversation_id):
                return {"messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ]}

        class FakeContextManager:
            # 对话级 terminal：绑定对话即 URL 里的对话
            current_conversation_id = "conv_same"

            def _get_conversation_manager_for_id(self, conversation_id):
                return FakeManager()

        class FakeTerminal:
            context_manager = FakeContextManager()

        app = Flask(__name__)
        with app.test_request_context('/?limit=5&content_mode=dialogue'):
            original_policy = conversation_module.resolve_admin_policy
            original_record = conversation_module.get_current_user_record
            conversation_module.resolve_admin_policy = lambda record: {}
            conversation_module.get_current_user_record = lambda: None
            try:
                view = conversation_module.review_conversation_preview
                while hasattr(view, "__wrapped__"):
                    view = view.__wrapped__
                response = view(
                    "conv_same", terminal=FakeTerminal(), workspace=None, username="u"
                )
            finally:
                conversation_module.resolve_admin_policy = original_policy
                conversation_module.get_current_user_record = original_record
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["count"], 2)


if __name__ == "__main__":
    unittest.main()
