"""「自适应压缩阈值」开关的回归测试：默认关闭，关闭时阈值/分母都是裸配置值。

运行：PYTHONPATH=. /opt/homebrew/bin/python3.11 -B -m unittest \
        discover -s "test/2026-11-04_自适应压缩开关" -p "test_*.py"
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.adaptive_context import (  # noqa: E402
    effective_trigger,
    is_adaptive_enabled,
)
from modules.personalization_manager import (  # noqa: E402
    DEFAULT_PERSONALIZATION_CONFIG,
    sanitize_personalization_payload,
)
from server.runtime.service import RuntimeService  # noqa: E402

BASELINE = 52_000
CONFIGURED = 256_000


class AdaptiveSwitchTests(unittest.TestCase):
    def test_default_is_off(self):
        self.assertFalse(DEFAULT_PERSONALIZATION_CONFIG["adaptive_compress_enabled"])

    def test_missing_key_means_off(self):
        self.assertFalse(is_adaptive_enabled({}))
        self.assertFalse(is_adaptive_enabled(None))

    def test_enabled_flag(self):
        self.assertTrue(is_adaptive_enabled({"adaptive_compress_enabled": True}))
        self.assertFalse(is_adaptive_enabled({"adaptive_compress_enabled": False}))

    def test_trigger_follows_switch(self):
        metadata = {"context_baseline_tokens": BASELINE}
        configured = CONFIGURED
        off = configured if not is_adaptive_enabled({}) else effective_trigger(metadata, configured)
        on = (
            effective_trigger(metadata, configured)
            if is_adaptive_enabled({"adaptive_compress_enabled": True})
            else configured
        )
        self.assertEqual(off, CONFIGURED)
        self.assertEqual(on, CONFIGURED + BASELINE)

    def test_sanitize_keeps_flag_and_defaults_to_off(self):
        kept = sanitize_personalization_payload({"adaptive_compress_enabled": True})
        self.assertTrue(kept["adaptive_compress_enabled"])
        dropped = sanitize_personalization_payload({"auto_deep_compress_enabled": True})
        self.assertFalse(dropped["adaptive_compress_enabled"])

    def test_context_limit_ignores_baseline_when_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "personalization.json").write_text(
                json.dumps(
                    {
                        "auto_deep_compress_enabled": True,
                        "adaptive_compress_enabled": False,
                        "deep_compress_trigger_tokens": CONFIGURED,
                    }
                ),
                encoding="utf-8",
            )
            workspace = SimpleNamespace(data_dir=tmp)
            metadata = {"context_baseline_tokens": BASELINE}
            limit, source = RuntimeService._context_limit(workspace, 400_000, metadata)
            self.assertEqual(source, "deep_compress")
            self.assertEqual(limit, CONFIGURED)

    def test_context_limit_includes_baseline_when_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "personalization.json").write_text(
                json.dumps(
                    {
                        "auto_deep_compress_enabled": True,
                        "adaptive_compress_enabled": True,
                        "deep_compress_trigger_tokens": CONFIGURED,
                    }
                ),
                encoding="utf-8",
            )
            workspace = SimpleNamespace(data_dir=tmp)
            metadata = {"context_baseline_tokens": BASELINE}
            limit, source = RuntimeService._context_limit(workspace, 400_000, metadata)
            self.assertEqual(source, "deep_compress")
            self.assertEqual(limit, CONFIGURED + BASELINE)

    def test_context_limit_falls_back_when_deep_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "personalization.json").write_text(
                json.dumps({"auto_deep_compress_enabled": False, "adaptive_compress_enabled": True}),
                encoding="utf-8",
            )
            workspace = SimpleNamespace(data_dir=tmp)
            limit, source = RuntimeService._context_limit(workspace, 400_000, {})
            self.assertEqual(source, "model_window")
            self.assertEqual(limit, 400_000)


if __name__ == "__main__":
    unittest.main()
