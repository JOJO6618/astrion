"""Current-format child creation, shared records, restart and terminal state."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from modules.sub_agent.manager import SubAgentManager
from modules.sub_agent.persistence import _REGISTRIES
from modules.sub_agent.task import SubAgentTask


class ManagerTests(unittest.TestCase):
    def wait_for(self, predicate):
        for _ in range(200):
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Child did not reach expected state")

    def test_current_format_resumes_context_and_never_reads_legacy_tasks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            data = root / "data"
            data.mkdir()
            # An intentionally invalid legacy record must not enter the new registry.
            (data / "sub_agents.json").write_text('{"tasks":{"legacy":{"status":"running"}}}')
            manager = SubAgentManager(str(root), str(data), owner_conversation_id="parent")
            calls = []

            async def tools(agent):
                return []

            async def model(agent, *_args):
                calls.append([dict(item) for item in agent.messages])
                return "remembered answer " + str(len(calls)), "", [], None, []

            with patch("modules.aux_model_resolver.resolve_locked_model_profile", return_value={}), \
                 patch.object(SubAgentTask, "_build_client", return_value=(None, "fake")), \
                 patch.object(SubAgentTask, "_build_runtime_tools", tools), \
                 patch.object(SubAgentTask, "_call_model", model), \
                 patch.object(SubAgentTask, "_apply_usage", lambda *a: None):
                result = manager.create_sub_agent(agent_id=1, summary="Research", task="Original task",
                    conversation_id="parent", access_level="working_dir_write", model_key="fake",
                    intelligence="same", reasoning_effort="high", thinking_mode="thinking", display_name="Research",
                    working_dir=None, system_prompt="system", task_message="Original request")
                self.assertTrue(result["success"], result)
                record = manager.tasks[result["task_id"]]
                self.wait_for(lambda: record["status"] == "idle")
                self.assertEqual(len(calls), 1)
                other = SubAgentManager(str(root), str(data), owner_conversation_id="parent")
                self.assertIs(other.tasks[result["task_id"]], record)
                self.assertNotIn("legacy", manager.tasks)
                instance = manager._find_or_revive_sub_agent_task(1)
                other._load_state()
                self.assertIs(other._find_or_revive_sub_agent_task(1), instance)
                self.assertEqual(record["reasoning_effort"], "high")
                # Simulate process exit without terminating the persisted logical instance.
                async def cancel():
                    instance._task.cancel()
                    await asyncio.gather(instance._task, return_exceptions=True)
                asyncio.run_coroutine_threadsafe(cancel(), instance._task.get_loop()).result(5)
                record["status"] = "idle"
                manager.get_multi_agent_state("parent").mark_status(1, "idle")
                manager._save_state()
                # The history saved by cancellation retains the complete messages.
                _REGISTRIES.pop(str(data))
                restarted = SubAgentManager(str(root), str(data), owner_conversation_id="parent")
                restored = restarted._find_or_revive_sub_agent_task(1)
                self.assertIsNotNone(restored)
                self.assertEqual(restarted.tasks[record["task_id"]]["working_dir"], record["working_dir"])
                restored.enqueue_work("Continue from the previous answer")
                self.wait_for(lambda: len(calls) == 2 and restored.task_record["status"] == "idle")
                self.assertTrue(any(message.get("content") == "remembered answer 1" for message in calls[-1]))
                self.assertEqual(restored.task_record["round_id"], 2)
                restarted.terminate_sub_agent(agent_id=1)
                self.wait_for(lambda: restored._task.done())
                self.assertIsNone(restarted._find_or_revive_sub_agent_task(1))
                saved = json.loads((data / "sub_agent_runtime.json").read_text())
                self.assertEqual(saved["tasks"][record["task_id"]]["status"], "terminated")


if __name__ == "__main__":
    unittest.main()
