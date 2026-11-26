"""schema.py 回归：严格 YAML（重复键/别名/未知字段/大小）、字段校验、版本与默认值。"""
from __future__ import annotations

import json
import unittest

import schedule_fixtures as fx
from modules.scheduled_tasks import clock, schema

BASE = {
    "schema_version": 1,
    "name": "巡检",
    "message": "检查项目状态",
    "enabled": False,
    "schedule": {"type": "daily", "timezone": "UTC", "times": ["09:00"]},
    "target": {"workspace_id": "ws-1", "conversation": {"mode": "new"}},
    "configuration": {"mode": "inherit"},
    "goal": {"mode": "off"},
    "policies": {"busy": "skip", "missed": "skip"},
    "notifications": {"enabled": True},
}

DOC_EXAMPLE = """
schema_version: 1
name: 每日项目巡检
enabled: false
schedule:
  type: daily
  timezone: Asia/Shanghai
  times: ['09:00']
target:
  workspace_id: workspace_id_here
  conversation:
    mode: new
message: 检查项目状态，汇报需要处理的问题。
configuration:
  mode: inherit
goal:
  mode: off
policies:
  busy: skip
  missed: skip
notifications:
  enabled: true
"""


def definition(**overrides) -> dict:
    import copy

    value = copy.deepcopy(BASE)
    value.update(copy.deepcopy(overrides))
    return value


class NormalizeTest(unittest.TestCase):
    def test_minimal_definition_defaults(self):
        value = {
            "schema_version": 1,
            "name": "  巡检  ",
            "message": "检查",
            "schedule": {"type": "daily", "timezone": "UTC", "times": ["09:00"]},
        }
        out = schema.normalize_definition(value, "ws-default")
        self.assertEqual(out["name"], "巡检")
        self.assertIs(out["enabled"], False)
        self.assertEqual(out["target"], {"workspace_id": "ws-default", "conversation": {"mode": "new"}})
        self.assertEqual(out["configuration"], {"mode": "inherit"})
        self.assertEqual(out["goal"], {"mode": "off"})
        self.assertEqual(
            out["policies"], {"busy": "skip", "missed": "skip", "approval_timeout_seconds": 3600}
        )
        self.assertEqual(out["notifications"], {"enabled": True})
        self.assertNotIn("id", out)
        self.assertNotIn("revision", out)

    def test_workspace_must_be_resolvable(self):
        with self.assertRaises(ValueError):
            schema.normalize_definition(definition(target={"conversation": {"mode": "new"}}), None)
        out = schema.normalize_definition(definition(), "ws-default")
        self.assertEqual(out["target"]["workspace_id"], "ws-1")

    def test_unknown_fields_are_rejected_everywhere(self):
        cases = [
            {**BASE, "extra": 1},
            definition(schedule={"type": "daily", "times": ["09:00"], "extra": 1}),
            definition(target={"workspace_id": "ws-1", "conversation": {"mode": "new"}, "extra": 1}),
            definition(target={"workspace_id": "ws-1", "conversation": {"mode": "new", "extra": 1}}),
            definition(configuration={"mode": "inherit", "model_key": "m"}),
            definition(policies={"busy": "skip", "extra": 1}),
            definition(goal={"mode": "off", "extra": 1}),
            definition(notifications={"enabled": True, "extra": 1}),
        ]
        for value in cases:
            with self.assertRaises(ValueError, msg=json.dumps(value, ensure_ascii=False)):
                schema.normalize_definition(value, "ws-1")

    def test_schema_version_is_strict(self):
        for bad in (2, "1", True, None, 1.0):
            value = {**BASE, "schema_version": bad}
            with self.assertRaises(ValueError, msg=repr(bad)):
                schema.normalize_definition(value, "ws-1")

    def test_name_and_message_bounds(self):
        self.assertEqual(schema.normalize_definition(definition(name="x" * 80), "ws-1")["name"], "x" * 80)
        for bad in ("", "   ", "x" * 81, 5, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                schema.normalize_definition(definition(name=bad), "ws-1")
        for bad in ("", "  \n ", 12, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                schema.normalize_definition(definition(message=bad), "ws-1")
        with self.assertRaises(ValueError):
            schema.normalize_definition(definition(message="x" * 100001), "ws-1")

    def test_enabled_is_strict_bool(self):
        self.assertIs(schema.normalize_definition(definition(enabled=True), "ws-1")["enabled"], True)
        for bad in (1, 0, "true", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                schema.normalize_definition(definition(enabled=bad), "ws-1")

    def test_conversation_modes(self):
        existing = schema.normalize_definition(
            definition(target={"workspace_id": "ws-1", "conversation": {"mode": "existing", "id": "conv_1"}}),
            "ws-1",
        )
        self.assertEqual(existing["target"]["conversation"], {"mode": "existing", "id": "conv_1"})
        with self.assertRaises(ValueError):
            schema.normalize_definition(
                definition(target={"workspace_id": "ws-1", "conversation": {"mode": "existing"}}), "ws-1"
            )
        with self.assertRaises(ValueError):
            schema.normalize_definition(
                definition(target={"workspace_id": "ws-1", "conversation": {"mode": "new", "id": "conv_1"}}),
                "ws-1",
            )
        with self.assertRaises(ValueError):
            schema.normalize_definition(
                definition(target={"workspace_id": "ws-1", "conversation": {"mode": "chat"}}), "ws-1"
            )

    def test_custom_configuration_roundtrip(self):
        custom = {
            "mode": "custom",
            "model_key": "model-a",
            "thinking": True,
            "reasoning_effort": "high",
            "work_mode": "execute",
            "permission_mode": "approval",
            "execution_mode": "sandbox",
            "network_enabled": False,
            "ask_user": True,
            "worktree": {"branch_name": "feature/x"},
        }
        out = schema.normalize_definition(definition(configuration=custom), "ws-1")
        self.assertEqual(out["configuration"]["model_key"], "model-a")
        self.assertEqual(out["configuration"]["worktree"], {"branch_name": "feature/x"})
        self.assertIs(out["configuration"]["thinking"], True)
        self.assertIs(out["configuration"]["ask_user"], True)
        for effort in ("low", "medium", "high", "xhigh", "max"):
            configuration = {"mode": "custom", "reasoning_effort": effort}
            out = schema.normalize_definition(definition(configuration=configuration), "ws-1")
            self.assertEqual(out["configuration"]["reasoning_effort"], effort)
        self.assertEqual(schema.normalize_definition(definition(configuration={"mode": "custom"}), "ws-1")["configuration"], {"mode": "custom"})

    def test_reasoning_effort_levels_match_runtime(self):
        from config.limits import REASONING_EFFORT_LEVELS
        self.assertEqual(schema.REASONING_EFFORT_LEVELS, REASONING_EFFORT_LEVELS)

    def test_invalid_configuration_combinations(self):
        bad = [
            {"mode": "custom", "work_mode": "plan", "permission_mode": "auto_approval"},
            {"mode": "custom", "permission_mode": "readonly", "execution_mode": "direct"},
            {"mode": "custom", "work_mode": "run"},
            {"mode": "custom", "permission_mode": "root"},
            {"mode": "custom", "execution_mode": "host"},
            {"mode": "custom", "reasoning_effort": "ultra"},
            {"mode": "custom", "reasoning_effort": "minimal"},
            {"mode": "custom", "chatgpt_fast_mode": True},
            {"mode": "inherit", "chatgpt_fast_mode": False},
            {"mode": "custom", "model_key": ""},
            {"mode": "custom", "thinking": "yes"},
            {"mode": "custom", "ask_user": "yes"},
            {"mode": "inherit", "ask_user": True},
            {"mode": "custom", "worktree": "feature/x"},
            {"mode": "custom", "worktree": {"branch_name": ""}},
            {"mode": "inherit", "network_enabled": True},
        ]
        for configuration in bad:
            with self.assertRaises(ValueError, msg=json.dumps(configuration, ensure_ascii=False)):
                schema.normalize_definition(definition(configuration=configuration), "ws-1")

    def test_goal_rules(self):
        started = schema.normalize_definition(
            definition(goal={"mode": "start", "max_turns": 3, "max_tokens": 2000}), "ws-1"
        )
        self.assertEqual(started["goal"], {"mode": "start", "max_turns": 3, "max_tokens": 2000})
        with self.assertRaises(ValueError):
            schema.normalize_definition(
                definition(
                    configuration={"mode": "custom", "work_mode": "ask"}, goal={"mode": "start"}
                ),
                "ws-1",
            )
        with self.assertRaises(ValueError):
            schema.normalize_definition(definition(goal={"mode": "continue"}), "ws-1")
        continued = schema.normalize_definition(
            definition(
                target={"workspace_id": "ws-1", "conversation": {"mode": "existing", "id": "conv_1"}},
                goal={"mode": "continue"},
            ),
            "ws-1",
        )
        self.assertEqual(continued["goal"], {"mode": "continue"})
        for bad in (0, -1, True, "3", 1.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                schema.normalize_definition(definition(goal={"mode": "start", "max_turns": bad}), "ws-1")
        with self.assertRaises(ValueError):
            schema.normalize_definition(definition(goal={"mode": "later"}), "ws-1")

    def test_policies_defaults_and_validation(self):
        out = schema.normalize_definition(definition(), "ws-1")
        self.assertEqual(out["policies"]["busy"], "skip")
        self.assertEqual(out["policies"]["missed"], "skip")
        out = schema.normalize_definition(
            definition(policies={"busy": "merge", "missed": "latest", "approval_timeout_seconds": 60}), "ws-1"
        )
        self.assertEqual(out["policies"]["busy"], "merge")
        self.assertEqual(out["policies"]["missed"], "latest")
        self.assertEqual(out["policies"]["approval_timeout_seconds"], 60)
        for bad in ({"busy": "queue"}, {"missed": "all"}, {"approval_timeout_seconds": 0},
                    {"approval_timeout_seconds": 86401}, {"approval_timeout_seconds": True}):
            with self.assertRaises(ValueError, msg=json.dumps(bad)):
                schema.normalize_definition(definition(policies=bad), "ws-1")

    def test_notifications_strict(self):
        self.assertEqual(schema.normalize_definition(definition(), "ws-1")["notifications"], {"enabled": True})
        for bad in ("true", 1, None):
            with self.assertRaises(ValueError):
                schema.normalize_definition(definition(notifications={"enabled": bad}), "ws-1")

    def test_id_and_revision(self):
        out = schema.normalize_definition(definition(id="plan_1", revision=3), "ws-1")
        self.assertEqual(out["id"], "plan_1")
        self.assertEqual(out["revision"], 3)
        for bad in ("a b", "a/b", "", "x" * 161, 5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                schema.normalize_definition(definition(id=bad), "ws-1")
        for bad in (0, -1, True, "2"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                schema.normalize_definition(definition(revision=bad), "ws-1")

    def test_interval_anchor_preserved_through_previous(self):
        first = schema.normalize_definition(
            definition(
                schedule={"type": "interval", "timezone": "UTC", "every": 10, "unit": "minutes",
                          "anchor": "2026-01-01T00:00:00Z"}
            ),
            "ws-1",
        )
        kept = schema.normalize_definition(
            definition(schedule={"type": "interval", "timezone": "UTC", "every": 10, "unit": "minutes"}),
            "ws-1",
            previous=first,
        )
        self.assertEqual(kept["schedule"]["anchor"], "2026-01-01T00:00:00.000000Z")
        changed = schema.normalize_definition(
            definition(schedule={"type": "interval", "timezone": "UTC", "every": 20, "unit": "minutes"}),
            "ws-1",
            previous=first,
        )
        self.assertNotEqual(changed["schedule"]["anchor"], "2026-01-01T00:00:00.000000Z")

    def test_dump_and_parse_roundtrip(self):
        normalized = schema.normalize_definition(definition(), "ws-1")
        text = schema.dump_definition(normalized)
        self.assertIn("schema_version: 1", text)
        self.assertEqual(schema.normalize_definition(schema.parse_definition(text), "ws-1"), normalized)


class StrictYamlTest(unittest.TestCase):
    def test_documented_example_is_accepted(self):
        """docs/scheduled_tasks.md 中的示例必须可以直接验证通过。"""
        out = schema.normalize_definition(schema.parse_definition(DOC_EXAMPLE), "ws-doc")
        self.assertEqual(out["goal"]["mode"], "off")
        self.assertEqual(out["schedule"]["timezone"], "Asia/Shanghai")

    def test_quoted_off_example_is_accepted(self):
        text = DOC_EXAMPLE.replace("  mode: off", "  mode: 'off'")
        out = schema.normalize_definition(schema.parse_definition(text), "ws-doc")
        self.assertEqual(out["goal"]["mode"], "off")

    def test_yaml12_boolean_semantics(self):
        parsed = schema.parse_definition("enabled: off\nflag: yes\nvalue: false\ntruth: true\n")
        self.assertEqual(parsed, {"enabled": "off", "flag": "yes", "value": False, "truth": True})
        with self.assertRaises(ValueError):
            schema.normalize_definition({**BASE, "enabled": "off"}, "ws-1")
        with self.assertRaises(ValueError):
            schema.normalize_definition({**BASE, "enabled": "yes"}, "ws-1")
        self.assertIs(schema.normalize_definition({**BASE, "enabled": True}, "ws-1")["enabled"], True)
        self.assertIs(schema.normalize_definition({**BASE, "enabled": False}, "ws-1")["enabled"], False)

    def test_duplicate_keys_are_rejected(self):
        text = "schema_version: 1\nname: a\nname: b\nmessage: c\n"
        with self.assertRaises(ValueError):
            schema.parse_definition(text)
        nested = (
            "schema_version: 1\nname: a\nmessage: m\nschedule:\n  type: daily\n"
            "  times: ['09:00']\n  times: ['10:00']\n"
        )
        with self.assertRaises(ValueError):
            schema.parse_definition(nested)

    def test_yaml_alias_is_rejected(self):
        text = "schema_version: 1\nname: &n foo\nmessage: *n\n"
        with self.assertRaises(ValueError):
            schema.parse_definition(text)

    def test_non_string_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            schema.parse_definition("schema_version: 1\n1: 2\n")

    def test_root_must_be_object(self):
        with self.assertRaises(ValueError):
            schema.parse_definition("- 1\n- 2\n")
        with self.assertRaises(ValueError):
            schema.parse_definition("")
        with self.assertRaises(ValueError):
            schema.parse_definition(None)  # type: ignore[arg-type]

    def test_malformed_yaml(self):
        with self.assertRaises(ValueError):
            schema.parse_definition("schema_version: 1\nname: [\n")
        with self.assertRaises(ValueError):
            schema.parse_definition("schema_version: 1\n  bad indent: 2\n")

    def test_oversized_config_is_rejected(self):
        text = "schema_version: 1\nname: a\nmessage: " + "x" * (256 * 1024) + "\n"
        with self.assertRaises(ValueError):
            schema.parse_definition(text)

    def test_json_document_is_accepted(self):
        out = schema.normalize_definition(schema.parse_definition(json.dumps(BASE)), "ws-1")
        self.assertEqual(out["name"], "巡检")

    def test_parse_does_not_mutate_definition(self):
        value = definition(
            configuration={"mode": "custom", "worktree": {"branch_name": "feature/x"}},
            id="plan_1",
            revision=2,
        )
        snapshot = json.dumps(value, sort_keys=True, ensure_ascii=False)
        out = schema.normalize_definition(value, "ws-1")
        out["name"] = "changed"
        out["configuration"]["worktree"]["branch_name"] = "other"
        out["target"]["workspace_id"] = "changed"
        self.assertEqual(json.dumps(value, sort_keys=True, ensure_ascii=False), snapshot)


if __name__ == "__main__":
    unittest.main()
