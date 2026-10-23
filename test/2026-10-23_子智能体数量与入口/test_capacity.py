"""Retained-instance capacity, per-conversation isolation and public resumption."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modules.sub_agent.manager import SubAgentManager
from modules.sub_agent.named import handle_named_tool
from modules.sub_agent.persistence import _REGISTRIES


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.data = self.root / "data"
        self.data.mkdir()
        self.manager = SubAgentManager(str(self.root), str(self.data),
                                       owner_conversation_id="parent")
        self.addCleanup(lambda: _REGISTRIES.pop(str(self.data), None))
        self.stack.enter_context(patch(
            "modules.aux_model_resolver.resolve_locked_model_profile", return_value={}))
        self.stack.enter_context(patch.object(self.manager, "_launch", self.launch))
        self.terminal = SimpleNamespace(
            sub_agent_manager=self.manager,
            context_manager=SimpleNamespace(current_conversation_id="parent"))

    def launch(self, instance):
        # Keep real instances, records and persistence; omit only execution workers.
        loop = SimpleNamespace(call_soon_threadsafe=lambda callback: callback())
        instance._task = SimpleNamespace(done=lambda: False, get_loop=lambda: loop,
                                         cancel=lambda: None)
        self.manager._sub_agent_instances[instance.agent_id] = instance

    def create(self, agent_id, conversation_id="parent"):
        return self.manager.create_sub_agent(
            agent_id=agent_id, summary="Research", task="Remember this request",
            conversation_id=conversation_id, access_level="working_dir_write",
            model_key="fake", intelligence="same", reasoning_effort=None,
            thinking_mode="thinking", display_name=f"Agent {agent_id}",
            working_dir=None, system_prompt="system", task_message="Original request")

    def fill(self, status="idle", count=20):
        for agent_id in range(1, count + 1):
            created = self.create(agent_id)
            self.assertTrue(created["success"], created)
            self.manager.tasks[created["task_id"]]["status"] = status
            self.manager.get_multi_agent_state("parent").mark_status(agent_id, status)

    def test_twenty_idle_instances_block_creation_before_directory_allocation(self):
        self.fill()
        before = set(self.manager.base_dir.iterdir())
        with patch.object(self.manager, "_resolve_working_dir") as resolve:
            result = self.create(21)
        self.assertFalse(result["success"])
        self.assertIn("20", result["error"])
        resolve.assert_not_called()
        self.assertEqual(set(self.manager.base_dir.iterdir()), before)

    def test_twenty_running_instances_are_allowed(self):
        self.fill(status="running")
        self.assertEqual(sum(record["status"] == "running"
                             for record in self.manager.tasks.values()), 20)
        self.assertFalse(self.create(21)["success"])

    def test_only_terminated_releases_a_slot(self):
        self.fill()
        for status in ("failed", "completed", "timeout", "cancelled", "idle", "running"):
            with self.subTest(status=status):
                for record in self.manager.tasks.values():
                    record["status"] = status
                self.assertFalse(self.create(21)["success"])
        terminated = self.manager.terminate_sub_agent(agent_id=1)
        self.assertTrue(terminated["success"])
        self.assertTrue(self.create(21)["success"])
        self.assertFalse(self.create(22)["success"])
        self.assertEqual(len(self.manager.tasks), 21)  # History is retained.

    def test_other_conversations_do_not_consume_slots(self):
        self.fill()
        self.assertTrue(self.create(21, conversation_id="another-parent")["success"])
        self.assertFalse(self.create(22)["success"])

    def test_twenty_idle_instances_can_all_resume_through_public_tool(self):
        self.fill()
        for record in self.manager.tasks.values():
            result = asyncio.run(handle_named_tool(self.terminal, "send_message_to_sub_agent", {
                "name": record["name"], "message": "Continue with the retained context"}))
            self.assertTrue(result["success"], result)
        self.assertEqual(sum(record["status"] == "running"
                             for record in self.manager.tasks.values()), 20)
        self.assertEqual(len(self.manager.tasks), 20)
        self.assertFalse(self.create(21)["success"])

    def test_concurrent_creates_cannot_both_claim_the_last_slot(self):
        self.fill(count=19)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.create, (20, 21)))
        self.assertEqual(sum(result["success"] for result in results), 1)
        self.assertEqual(len(self.manager.tasks), 20)


if __name__ == "__main__":
    unittest.main()
