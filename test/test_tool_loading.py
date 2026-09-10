"""工具动态加载（core.tool_loading + personalization 字段）单元测试。

设计文档：docs/dynamic_tool_loading_plan.md
运行：.venv/bin/python -m pytest test/test_tool_loading.py -q
无 pytest 时：
  .venv/bin/python -c "import sys; sys.path.insert(0, '.'); import unittest; \\
    s = unittest.TestLoader().discover('test', pattern='test_tool_loading.py'); \\
    r = unittest.TextTestRunner().run(s); sys.exit(0 if r.wasSuccessful() else 1)"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import tool_loading as tl  # noqa: E402


class RegistryIntegrityTest(unittest.TestCase):
    def test_registry_has_expected_shape(self):
        names = tl.deferrable_tool_names()
        self.assertEqual(len(names), 18)
        self.assertEqual(len(set(names)), len(names), "注册表内工具名重复")
        for cat in tl.DEFERRABLE_REGISTRY.values():
            self.assertTrue(cat.get("label"))
            self.assertTrue(cat.get("when_to_use"))
            self.assertTrue(cat.get("tools"))

    def test_every_name_has_category(self):
        for name in tl.deferrable_tool_names():
            self.assertIsNotNone(tl.category_of_tool(name), name)
        self.assertIsNone(tl.category_of_tool("read_file"))
        self.assertIsNone(tl.category_of_tool("no_such_tool"))

    def test_registry_payload(self):
        payload = tl.build_registry_payload()
        self.assertEqual(len(payload), len(tl.DEFERRABLE_REGISTRY))
        total = sum(len(item["tools"]) for item in payload)
        self.assertEqual(total, 18)


class SnapshotTest(unittest.TestCase):
    def test_default_defers_everything(self):
        snap = tl.build_snapshot(None)
        self.assertTrue(snap["enabled"])
        self.assertEqual(snap["deferred_set"], tl.deferrable_tool_names())
        self.assertEqual(snap["loaded"], [])
        self.assertEqual(snap["pending"], snap["deferred_set"])

    def test_partial_and_invalid_names_clamped(self):
        snap = tl.build_snapshot(["activate_workflow", "bogus_tool", "save_workflow"])
        self.assertEqual(snap["deferred_set"], ["activate_workflow", "save_workflow"])

    def test_explicit_empty_defers_nothing(self):
        snap = tl.build_snapshot([])
        self.assertEqual(snap["deferred_set"], [])
        self.assertEqual(snap["pending"], [])

    def test_initial_exposed_recorded_when_built_names_given(self):
        snap = tl.build_snapshot(
            ["activate_workflow"],
            built_tool_names=["read_file", "activate_workflow", "web_search"],
        )
        self.assertNotIn("activate_workflow", snap["initial_exposed"])
        self.assertIn("read_file", snap["initial_exposed"])
        self.assertIn(tl.LOAD_TOOLS_NAME, snap["initial_exposed"])

    def test_overrides_from_prefs(self):
        self.assertEqual(tl.snapshot_overrides_from_prefs({}, multi_agent_mode=True), {})
        self.assertEqual(tl.snapshot_overrides_from_prefs({"tool_loading_enabled": False}), {})
        overrides = tl.snapshot_overrides_from_prefs({})
        self.assertIn(tl.METADATA_KEY, overrides)
        self.assertTrue(overrides[tl.METADATA_KEY]["enabled"])


class StateParsingTest(unittest.TestCase):
    def test_missing_or_disabled_returns_none(self):
        self.assertIsNone(tl.get_tool_loading_state(None))
        self.assertIsNone(tl.get_tool_loading_state({}))
        self.assertIsNone(tl.get_tool_loading_state({"tool_loading": "garbage"}))
        self.assertIsNone(tl.get_tool_loading_state({"tool_loading": {"enabled": False}}))

    def test_normalization_recomputes_pending_and_drops_ghosts(self):
        snap = tl.build_snapshot(None)
        snap["loaded"] = ["activate_workflow", "ghost_tool"]
        snap["pending"] = ["wrong"]
        state = tl.get_tool_loading_state({tl.METADATA_KEY: snap})
        self.assertIsNotNone(state)
        self.assertEqual(state["loaded"], ["activate_workflow"])
        self.assertNotIn("activate_workflow", state["pending"])
        self.assertIn("save_workflow", state["pending"])

    def test_guard_decision(self):
        state = tl.get_tool_loading_state({tl.METADATA_KEY: tl.build_snapshot(None)})
        self.assertTrue(tl.is_deferred_not_loaded(state, "save_workflow"))
        self.assertFalse(tl.is_deferred_not_loaded(state, "read_file"))
        self.assertFalse(tl.is_deferred_not_loaded(None, "save_workflow"))
        loaded = tl.mark_tools_loaded(state, ["save_workflow"])
        self.assertFalse(tl.is_deferred_not_loaded(loaded, "save_workflow"))


class StateTransitionTest(unittest.TestCase):
    def test_mark_loaded_is_idempotent(self):
        state = tl.get_tool_loading_state({tl.METADATA_KEY: tl.build_snapshot(None)})
        once = tl.mark_tools_loaded(state, ["save_workflow"])
        twice = tl.mark_tools_loaded(once, ["save_workflow", "list_workflows"])
        self.assertEqual(twice["loaded"].count("save_workflow"), 1)
        self.assertNotIn("list_workflows", twice["pending"])

    def test_compression_reset(self):
        state = tl.get_tool_loading_state({tl.METADATA_KEY: tl.build_snapshot(None)})
        loaded = tl.mark_tools_loaded(state, ["save_workflow", "list_workflows"])
        reset = tl.reset_state_after_compression(loaded)
        self.assertEqual(reset["loaded"], [])
        self.assertEqual(reset["pending"], reset["deferred_set"])
        self.assertEqual(reset["initial_exposed"], loaded["initial_exposed"])


class CatalogRenderTest(unittest.TestCase):
    def test_subset_and_empty_categories_omitted(self):
        catalog = tl.render_catalog(["activate_workflow", "create_sub_agent"])
        self.assertIn("工作流", catalog)
        self.assertIn("子智能体", catalog)
        self.assertNotIn("对话回顾", catalog)
        self.assertNotIn("彩蛋", catalog)

    def test_unavailable_excluded(self):
        catalog = tl.render_catalog(["activate_workflow"], unavailable={"activate_workflow"})
        self.assertEqual(catalog, "")

    def test_load_tools_definition(self):
        definition = tl.build_load_tools_definition()
        fn = definition["function"]
        self.assertEqual(fn["name"], "load_tools")
        self.assertEqual(fn["parameters"]["required"], ["tool_names"])


class PersonalizationFieldsTest(unittest.TestCase):
    def test_sanitize_defaults(self):
        from modules.personalization_manager import sanitize_personalization_payload

        result = sanitize_personalization_payload({})
        self.assertTrue(result["tool_loading_enabled"])
        self.assertEqual(len(result["tool_loading_deferred"]), 18)

    def test_sanitize_explicit_values(self):
        from modules.personalization_manager import sanitize_personalization_payload

        result = sanitize_personalization_payload({
            "tool_loading_enabled": False,
            "tool_loading_deferred": ["activate_workflow", "bogus", 123],
        })
        self.assertFalse(result["tool_loading_enabled"])
        self.assertEqual(result["tool_loading_deferred"], ["activate_workflow"])

    def test_sanitize_empty_list_means_defer_nothing(self):
        from modules.personalization_manager import sanitize_personalization_payload

        result = sanitize_personalization_payload({"tool_loading_deferred": []})
        self.assertEqual(result["tool_loading_deferred"], [])


if __name__ == "__main__":
    unittest.main()
