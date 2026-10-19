import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import RUNTIME
from modules.goal_state_manager import GoalStateManager
from modules.goal_policy import enforce_goal_modes, goal_blocks_background, interaction_denied, validate_limits


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.addCleanup(self.tmp.cleanup)
        self.gsm = GoalStateManager(self.tmp.name, "conv_A")
        self.gsm.start(goal="Deliver the report", task_id="first")

    def test_stop_resume_persists_identity_and_counters(self):
        run = self.gsm.state["run_id"]
        self.gsm.record_usage("r1", 100, run_id=run)
        self.gsm.increment_turn()
        self.gsm.pause("user_cancel")
        restored = GoalStateManager(self.tmp.name, "conv_A")
        self.assertEqual(restored.state["status"], "paused")
        restored.resume(task_id="second", limits={"max_tokens": 50, "max_turns": None})
        self.assertEqual(restored.state["run_id"], run)
        self.assertEqual(restored.tokens_used(), 100)
        self.assertFalse(restored.reached_max_tokens())
        restored.record_usage("r2", 50, run_id=run)
        self.assertTrue(restored.reached_max_tokens())
        self.assertEqual(restored.get_goal(), "Deliver the report")

    def test_restart_pauses_without_resuming_or_resetting(self):
        self.gsm.update(process_id="previous-process", status="retrying", next_retry_at=42)
        loaded = GoalStateManager(self.tmp.name, "conv_A")
        self.assertEqual(loaded.state["status"], "paused")
        self.assertEqual(loaded.state["stopped_reason"], "process_restarted")
        self.assertIsNone(loaded.state["next_retry_at"])
        disk = json.loads(loaded._path().read_text())
        self.assertEqual(disk["status"], "paused")
        self.assertFalse(disk["active"])

    def test_close_rejects_stale_review_and_save(self):
        stale = GoalStateManager(self.tmp.name, "conv_A")
        old_run = stale.state["run_id"]
        self.gsm.mark_stopped("user_closed")
        with self.assertRaises(RuntimeError):
            stale.save()
        self.assertFalse(stale.mark_done("late completion", run_id=old_run))
        self.gsm.start(goal="New goal", task_id="next")
        self.assertFalse(stale.mark_done("old goal", run_id=old_run))
        self.assertEqual(self.gsm.load()["goal"], "New goal")

    def test_usage_deduplicated_and_conversation_isolated(self):
        run = self.gsm.state["run_id"]
        self.gsm.record_usage("r1", 20, run_id=run)
        self.gsm.record_usage("r1", 20, run_id=run)
        other = GoalStateManager(self.tmp.name, "conv_B")
        other.start(goal="Other", task_id="other")
        other.record_usage("r2", 900, run_id=other.state["run_id"])
        self.assertEqual(self.gsm.load()["tokens_used"], 20)
        self.assertIs(self.gsm.lock, GoalStateManager(self.tmp.name, "conv_A").lock)

    def test_unlimited_by_default_and_optional_limits(self):
        for _ in range(10):
            self.gsm.increment_turn()
        self.assertFalse(self.gsm.reached_max_turns())
        self.assertEqual(validate_limits({}), {"max_turns": None, "max_tokens": None})
        for bad in (True, -1, 0, "5"):
            with self.assertRaises(ValueError):
                validate_limits({"max_turns": bad})

    def test_blocked_can_resume_without_probing_provider(self):
        self.gsm.update(status="blocked", stopped_reason="quota_exhausted")
        self.assertTrue(goal_blocks_background(self.tmp.name, "conv_A"))
        self.gsm.resume(task_id="manual")
        self.assertEqual(self.gsm.state["status"], "running")
        self.assertFalse(goal_blocks_background(self.tmp.name, "conv_A"))

    def test_human_tools_blocked_but_internal_agent_tools_allowed(self):
        terminal = SimpleNamespace(data_dir=self.tmp.name,
            context_manager=SimpleNamespace(current_conversation_id="conv_A"))
        self.assertTrue(interaction_denied(terminal, "ask_user"))
        self.assertTrue(interaction_denied(terminal, "submit_plan"))
        self.assertTrue(interaction_denied(terminal, "run_command", {"request_full_access": True}))
        self.assertFalse(interaction_denied(terminal, "ask_master"))
        self.assertFalse(interaction_denied(terminal, "run_command", {"request_full_access": False}))

    def test_mode_transition_order_and_preserved_permissions(self):
        for permission in ("readonly", "approval", "auto_approval", "unrestricted"):
            calls = []
            terminal = SimpleNamespace(
                set_work_mode=lambda *a, **k: calls.append(("work", a[0])),
                get_permission_mode=lambda: permission,
                set_permission_mode=lambda *a, **k: calls.append(("permission", a[0])))
            enforce_goal_modes(terminal, "conv_A")
            expected = [("work", "execute")]
            if permission not in {"auto_approval", "unrestricted"}:
                expected.append(("permission", "auto_approval"))
            self.assertEqual(calls, expected)

    def test_real_mode_setters_leave_plan_and_reject_interactive_queue(self):
        from core.main_terminal import MainTerminal
        from core.main_terminal_parts.tools_policy import MainTerminalToolsPolicyMixin

        class Terminal(MainTerminalToolsPolicyMixin):
            queue_permission_mode_change = MainTerminal.queue_permission_mode_change

            def get_work_mode(self):
                return self.current_work_mode

            def get_permission_mode(self):
                return self.current_permission_mode

            def _apply_restricted_execution_mode_link(self, *args, **kwargs):
                pass

            def _persist_runtime_mode_metadata(self, updates):
                pass

        terminal = Terminal()
        terminal.data_dir = self.tmp.name
        terminal.current_work_mode = "plan"
        terminal.current_permission_mode = "readonly"
        terminal.context_manager = SimpleNamespace(current_conversation_id="conv_A",
            conversation_metadata={}, _get_conversation_manager_for_id=lambda cid:
                SimpleNamespace(update_conversation_metadata=lambda *a: None))
        from modules.conversation_modes import restore_conversation_modes
        terminal.default_work_mode = "plan"
        terminal.default_permission_mode = "readonly"
        terminal.get_execution_mode = lambda: "sandbox"
        terminal.get_network_permission = lambda: "full"
        terminal.set_execution_mode = lambda value: None
        terminal.set_network_permission = lambda value: None
        terminal._is_host_mode = lambda: True
        restored = restore_conversation_modes(terminal, {
            "work_mode": "plan", "permission_mode": "readonly", "execution_mode": "sandbox"})
        self.assertEqual(restored["work_mode"], "execute")
        self.assertEqual(restored["permission_mode"], "auto_approval")
        terminal.pending_permission_mode = "approval"
        terminal.pending_execution_mode = "direct"
        enforce_goal_modes(terminal, "conv_A")
        self.assertIsNone(terminal.pending_permission_mode)
        self.assertIsNone(terminal.pending_execution_mode)
        self.assertEqual(terminal.get_work_mode(), "execute")
        self.assertEqual(terminal.get_permission_mode(), "auto_approval")
        self.assertEqual(terminal.context_manager.conversation_metadata["work_mode"], "execute")
        for permission in ("approval", "readonly"):
            with self.assertRaises(ValueError):
                terminal.set_permission_mode(permission)
            with self.assertRaises(ValueError):
                terminal.queue_permission_mode_change(permission)
        with self.assertRaises(ValueError):
            terminal.set_work_mode("ask")


if __name__ == "__main__":
    unittest.main()
