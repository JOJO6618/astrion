"""Offline regressions: temporary files only, no service, real models or credentials."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.crosspet.observer import CrossPetObserver, read_destination
from modules.crosspet.state import SessionState, character_for_model, session_key, tool_pose
from modules.crosspet.settings import save_enabled


class ManualObserver(CrossPetObserver):
    def enqueue(self, item):
        self.queue.append(item)


class StateTests(unittest.TestCase):
    def event(self, state, kind, data=None, task="one", now=100):
        state.apply(kind, data or {}, task_id=task, model="gpt-5", now=now)

    def test_tool_categories(self):
        self.assertEqual(tool_pose("mcp__playwright__browser_snapshot"), "searching")
        self.assertEqual(tool_pose("create_sub_agent"), "delegating")
        self.assertEqual(tool_pose("write_file"), "writing")
        self.assertEqual(tool_pose("read_file"), "reading")
        self.assertEqual(tool_pose("terminal_snapshot"), "running")
        self.assertEqual(tool_pose("ask_user"), "asking")

    def test_models_and_unknown(self):
        for model, role in [("gpt-5", "gpt"), ("claude-sonnet", "claude"), ("deepseek-v3", "deepseek"),
                            ("gemini-pro", "gemini"), ("glm-5", "glm"), ("qwen3", "current")]:
            self.assertEqual(character_for_model(model), role)

    def test_identity_is_private_and_workspace_scoped(self):
        first = session_key("user", "one", "conv_same")
        self.assertNotEqual(first, session_key("user", "two", "conv_same"))
        self.assertNotIn("user", first)
        self.assertEqual(first, session_key("user", "one", "same"))

    def test_wait_survives_unrelated_events(self):
        state = SessionState()
        self.event(state, "tool_approval_required")
        self.event(state, "api_request_start")
        self.event(state, "tool_start", {"name": "read_file", "id": "tool"})
        self.assertEqual(state.pose, "asking")
        self.event(state, "compression_state", {"in_progress": True})
        self.assertEqual(state.pose, "asking")
        self.event(state, "compression_finished")
        self.assertEqual(state.pose, "asking")
        self.event(state, "tool_approval_resolved")
        self.assertEqual(state.pose, "thinking")

    def test_overlapping_waits_resolve_individually(self):
        state = SessionState()
        self.event(state, "tool_approval_required")
        self.event(state, "user_questions_required")
        self.event(state, "tool_approval_resolved")
        self.assertEqual(state.pose, "asking")
        self.event(state, "user_questions_resolved")
        self.assertEqual(state.pose, "thinking")

    def test_compression_start_and_clear(self):
        state = SessionState()
        self.event(state, "compression_state", {"in_progress": True})
        self.assertEqual(state.pose, "compact")
        self.event(state, "compression_state", {"in_progress": False})
        self.assertEqual(state.pose, "thinking")

    def test_tool_failure_can_recover(self):
        state = SessionState()
        self.event(state, "tool_start", {"name": "run_command", "id": "t"})
        self.event(state, "update_action", {"status": "error"})
        self.assertEqual(state.pose, "oops")
        self.event(state, "api_request_start")
        self.assertEqual(state.pose, "thinking")

    def test_completion_is_not_response_boundary(self):
        state = SessionState()
        self.event(state, "task_complete")
        self.assertTrue(state.payload(110)["active"])
        state.activity(True, 111)
        state.finish("succeeded", 112)
        self.assertEqual(state.pose, "delegating")
        state.activity(False, 113)
        self.assertEqual(state.pose, "happy")
        self.assertFalse(state.payload(114)["active"])

    def test_business_error_does_not_celebrate_thread_success(self):
        state = SessionState()
        self.event(state, "task_complete", {"error": True})
        state.finish("succeeded", 101)
        self.assertEqual(state.pose, "oops")
        self.event(state, "crosspet_start", task="next", now=102)
        state.finish("succeeded", 103)
        self.assertEqual(state.pose, "happy")

    def test_failure_and_cancellation_do_not_celebrate(self):
        for status, pose in [("failed", "oops"), ("stopped", "surprised"), ("canceled", "surprised")]:
            state = SessionState()
            state.finish(status, 100)
            self.assertEqual(state.pose, pose)
            self.assertEqual(state.event, "Stop")

    def test_tool_count_is_per_session_and_turn(self):
        first, second = SessionState(), SessionState()
        for i in range(8):
            self.event(first, "tool_start", {"name": "read_file", "id": str(i)})
        self.event(first, "tool_start", {"name": "read_file", "id": "0"})
        first.finish("succeeded", 101)
        second.finish("succeeded", 101)
        self.assertEqual(first.pose, "proud")
        self.assertEqual(second.pose, "happy")
        self.event(first, "crosspet_start", task="two", now=102)
        self.assertEqual(len(first.tools), 0)

    def test_short_tool_is_preserved_without_recounting(self):
        state = SessionState()
        self.event(state, "tool_start", {"name": "read_file", "id": "t"}, now=100)
        self.event(state, "update_action", {"status": "success"}, now=100.01)
        self.assertEqual(state.payload(101)["after"], "reading")
        self.assertEqual(len(state.tools), 1)
        before = state.revision
        self.assertEqual(state.payload(600)["ts"], 600)
        self.assertEqual(state.revision, before)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "test/2026-10-14_CrossPet接入")
        self.base = Path(self.temp.name)
        self.config = self.base / "astrion.json"
        self.control = self.base / "crosspet.json"
        self.out = self.base / "state"
        self.observer = ManualObserver(config_path=self.config, settings_path=self.control,
                                       model_resolver=lambda key: key)
        self.identity = ("private-user", "private-workspace", "conv_private")

    def tearDown(self):
        self.temp.cleanup()

    def enable(self):
        self.config.write_text(json.dumps({"version": 1, "state_dir": str(self.out)}))
        save_enabled(self.control, True)

    def start(self, task="one", status="running", background=False):
        self.observer.bind(self.identity, task, "gpt-5", lambda: (status, background))
        self.observer.event(self.identity, task, "gpt-5", "crosspet_start", {})

    def test_disabled_does_not_create_state_directory(self):
        self.start()
        self.observer.tick()
        self.assertFalse(self.out.exists())

    def test_disabled_skips_probes_and_foreground_history_read(self):
        calls = []
        self.observer.bind(self.identity, "one", "gpt-5", lambda: (calls.append("probe") or "running", False))
        self.observer.focus(self.identity, lambda: calls.append("history") or "claude-sonnet")
        self.observer.tick()
        self.assertEqual(calls, [])
        self.enable()
        self.observer.tick()
        self.assertEqual(calls, ["history", "probe"])
        self.assertEqual(self.observer.foreground["model"], "claude-sonnet")
        self.assertEqual(self.observer.snapshot(100)["foreground"]["character"], "gpt")

    def test_snapshot_limit_preserves_foreground_and_waiting(self):
        for index in range(300):
            state = SessionState(task_id=str(index), ts=float(index))
            self.observer.sessions[str(index)] = state
        self.observer.foreground = {"session": "0"}
        self.observer.sessions["1"].waits.add("user_questions")
        value = self.observer.snapshot(301)
        self.assertEqual(len(value["sessions"]), 256)
        self.assertIn("0", value["sessions"])
        self.assertIn("1", value["sessions"])
        self.assertIn("299", value["sessions"])

    def test_compression_migrates_same_task_and_foreground(self):
        self.start()
        self.observer.focus(self.identity, "gpt-5")
        self.observer.drain(100)
        next_identity = (*self.identity[:2], "conv_after_compression")
        self.observer.event(next_identity, "one", "gpt-5", "compression_finished", {})
        self.observer.drain(101)
        key = session_key(*next_identity)
        self.assertEqual(list(self.observer.sessions), [key])
        self.assertEqual(list(self.observer.bindings), [key])
        self.assertEqual(self.observer.foreground["session"], key)

    def test_conversation_binding_keeps_latest_terminal_probe(self):
        self.enable()
        self.observer.bind(self.identity, "one", "gpt-5", lambda: ("running", None))
        self.observer.event(self.identity, "one", "gpt-5", "crosspet_start", {})
        next_identity = (*self.identity[:2], "conv_created")
        self.observer.bind(next_identity, "one", "claude-sonnet", lambda: ("succeeded", True))
        self.observer.event(next_identity, "one", "claude-sonnet", "crosspet_start", {})
        self.observer.tick()
        state = self.observer.sessions[session_key(*next_identity)]
        self.assertEqual(state.pose, "delegating")
        self.assertEqual(state.model, "claude-sonnet")
        self.assertEqual(len(self.observer.bindings), 1)

    def test_auxiliary_symlink_does_not_write_through(self):
        self.enable()
        self.start()
        self.out.mkdir()
        target = self.base / "unrelated.txt"
        target.write_text("untouched")
        for name in ("astrion-host.json", "seen-astrion"):
            link = self.out / name
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                self.observer.tick()
            self.assertEqual(target.read_text(), "untouched")
            self.assertFalse((self.out / "astrion-state.json").exists())
            link.unlink()

    def test_enabled_snapshot_has_no_content_arguments_or_identity(self):
        self.enable()
        self.start()
        self.observer.event(self.identity, "one", "gpt-5", "tool_start", {
            "name": "read_file", "id": "tool", "arguments": "secret-command", "result": "secret-result"})
        self.observer.tick()
        text = (self.out / "astrion-state.json").read_text()
        for private in ["private-user", "private-workspace", "conv_private", "secret-command", "secret-result"]:
            self.assertNotIn(private, text)
        state = next(iter(json.loads(text)["sessions"].values()))
        self.assertEqual(state["pose"], "reading")

    def test_disable_stops_all_output_writes_and_keeps_other_sources(self):
        self.enable()
        self.start()
        self.observer.tick()
        other = self.out / "gpt-state.json"
        other.write_text('{"pose":"writing"}')
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.out.iterdir()}
        save_enabled(self.control, False)
        with patch("modules.crosspet.observer.atomic_write_json") as write:
            self.observer.tick()
            write.assert_not_called()
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.out.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(json.loads(other.read_text())["pose"], "writing")

    def test_old_task_cannot_overwrite_successor(self):
        self.enable()
        self.start("old")
        self.start("new")
        self.observer.event(self.identity, "old", "gpt-5", "crosspet_finish", {"status": "succeeded"})
        self.observer.tick()
        state = next(iter(self.observer.sessions.values()))
        self.assertEqual(state.task_id, "new")
        self.assertTrue(state.payload(100)["active"])

    def test_final_probe_preserves_background_then_finishes(self):
        self.enable()
        activity = ["succeeded", True]
        self.observer.bind(self.identity, "one", "gpt-5", lambda: tuple(activity))
        self.observer.event(self.identity, "one", "gpt-5", "crosspet_start", {})
        self.observer.tick()
        self.assertEqual(next(iter(self.observer.sessions.values())).pose, "delegating")
        activity[1] = False
        self.observer.tick()
        self.assertEqual(next(iter(self.observer.sessions.values())).pose, "happy")

    def test_unknown_probe_is_not_success(self):
        def unavailable():
            raise RuntimeError("unknown")
        self.observer.bind(self.identity, "one", "gpt-5", unavailable)
        self.observer.event(self.identity, "one", "gpt-5", "crosspet_start", {})
        self.observer.drain(100)
        self.assertTrue(next(iter(self.observer.sessions.values())).payload(100)["active"])

    def test_foreground_explicit_model_does_not_revert_to_finished_task(self):
        self.start(status="succeeded")
        self.observer.focus(self.identity, "claude-sonnet")
        self.observer.drain(100)
        self.assertEqual(self.observer.snapshot(101)["foreground"]["character"], "claude")

    def test_config_relative_path_and_symlink_are_rejected(self):
        self.config.write_text('{"version":1,"enabled":true,"state_dir":"relative"}')
        self.assertIsNone(read_destination(self.config))
        link = self.base / "link.json"
        link.symlink_to(self.config)
        self.assertIsNone(read_destination(link))

    def test_heartbeat_does_not_repeat_finish_or_write_every_poll(self):
        self.enable()
        self.start(status="succeeded")
        self.observer.tick(100)
        revision = next(iter(self.observer.sessions.values())).revision
        sequence = self.observer.sequence
        self.observer.tick(102)
        self.assertEqual(self.observer.sequence, sequence)
        self.observer.tick(111)
        self.assertGreater(self.observer.sequence, sequence)
        self.assertEqual(next(iter(self.observer.sessions.values())).revision, revision)


if __name__ == "__main__":
    unittest.main()
