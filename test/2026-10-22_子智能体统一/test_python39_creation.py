"""Exercise public creation under the application's Python, including 3.9."""
import asyncio
from contextlib import ExitStack
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from typing import get_type_hints
import unittest
from unittest.mock import patch

from modules.sub_agent.intelligence import resolve_intelligence_model
from modules.sub_agent.manager import SubAgentManager
from modules.sub_agent.named import handle_named_tool
from modules.sub_agent.task import SubAgentTask


class PublicCreationTests(unittest.TestCase):
    def test_model_selection_annotations_and_configured_levels(self):
        # Import and runtime evaluation both need to work on Python 3.9.
        self.assertIn("current_model", get_type_hints(resolve_intelligence_model))
        with patch("modules.aux_model_resolver.resolve_locked_model_profile") as resolve:
            self.assertEqual(resolve_intelligence_model("same", {}, "parent-model"), "parent-model")
            self.assertEqual(resolve_intelligence_model("high", {"sub_agent_model_high": "high-model"}, None), "high-model")
            self.assertEqual(resolve.call_count, 2)
            with self.assertRaises(ValueError):
                resolve_intelligence_model("medium", {}, "parent-model")

    def test_public_create_runs_in_background_and_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            data = root / "data"
            data.mkdir()
            manager = SubAgentManager(str(root), str(data), owner_conversation_id="parent")
            terminal = SimpleNamespace(
                sub_agent_manager=manager, data_dir=data,
                context_manager=SimpleNamespace(current_conversation_id="parent"),
                api_client=SimpleNamespace(model_key="fake", reasoning_effort="high"),
                get_permission_mode=lambda: "unrestricted",
                get_work_mode=lambda: "execute",
                get_execution_mode=lambda: "sandbox",
            )
            requests = []

            async def tools(_agent):
                return []

            async def model(agent, *_args):
                requests.append([dict(item) for item in agent.messages])
                return "answer " + str(len(requests)), "", [], None, []

            def wait_idle(round_id):
                for _ in range(300):
                    if record.get("round_id") == round_id and record["status"] == "idle":
                        return
                    time.sleep(0.01)
                self.fail("Publicly created child did not become idle")

            with ExitStack() as stack:
                stack.enter_context(patch("modules.personalization_manager.load_personalization_config", return_value={}))
                stack.enter_context(patch("modules.aux_model_resolver.resolve_locked_model_profile", return_value={}))
                stack.enter_context(patch.object(SubAgentTask, "_build_client", return_value=(None, "fake")))
                stack.enter_context(patch.object(SubAgentTask, "_build_runtime_tools", tools))
                stack.enter_context(patch.object(SubAgentTask, "_call_model", model))
                stack.enter_context(patch.object(SubAgentTask, "_apply_usage", lambda *a: None))
                created = asyncio.run(handle_named_tool(terminal, "create_sub_agent", {
                    "name": "Research", "summary": "Research", "task": "Remember this request",
                    "intelligence": "same", "access_level": "working_dir_write",
                }))
                self.assertTrue(created["success"], created)
                self.assertNotIn("task_id", created)
                self.assertNotIn("conversation_id", created)
                record = next(item for item in manager.tasks.values() if item["name"] == created["name"])
                try:
                    wait_idle(1)
                    self.assertEqual(record["model_key"], "fake")
                    self.assertEqual(record["reasoning_effort"], "high")
                    self.assertTrue(Path(created["working_dir"]).is_dir())
                    self.assertIsNone(record["timeout_seconds"])
                    sent = asyncio.run(handle_named_tool(terminal, "send_message_to_sub_agent", {
                        "name": "research", "message": "Continue with your previous answer",
                    }))
                    self.assertTrue(sent["success"], sent)
                    wait_idle(2)
                    self.assertTrue(any(item.get("content") == "answer 1" for item in requests[-1]))
                    notices = manager.get_multi_agent_state("parent").drain_master_messages()
                    self.assertEqual(len(notices), 2)
                finally:
                    instance = manager._sub_agent_instances[record["agent_id"]]
                    stopped = asyncio.run(handle_named_tool(terminal, "terminate_sub_agent", {"name": "Research"}))
                    self.assertTrue(stopped["success"], stopped)

                    async def settle():
                        await asyncio.gather(instance._task, return_exceptions=True)
                        await asyncio.sleep(0)

                    asyncio.run_coroutine_threadsafe(settle(), instance._task.get_loop()).result(5)


if __name__ == "__main__":
    unittest.main()
