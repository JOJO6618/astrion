"""Offline tests: complete tool batches, final notices and resumable context."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.multi_agent.state import MultiAgentState, AgentInstance
from modules.sub_agent.task import SubAgentTask
from modules.sub_agent.definitions import build_master_tools, build_sub_tools
from modules.sub_agent.named import validate_name, validate_timeout, find_named


class RoundTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, root: Path, timeout=None):
        root = root.resolve()
        state = MultiAgentState("round-test")
        state.register_instance(AgentInstance(agent_id=1, display_name="Research", task_id="child"))
        manager = SimpleNamespace(terminal=SimpleNamespace(mcp_tools_enabled=False),
                                  _save_state=lambda: None)
        record = dict(task_id="child", agent_id=1, access_level="working_dir_write",
                      workspace_root=str(root), working_dir=str(root), deliverables_dir=str(root),
                      task_root=str(root), conversation_id="round-test", timeout_seconds=timeout,
                      **{key: str(root / (key + ".json")) for key in
                         ("output_file", "stats_file", "progress_file", "conversation_file")})
        agent = SubAgentTask(manager, record, "Read sources", "System", "fake", "thinking",
                             multi_agent_state=state, display_name="Research")
        agent._build_client = lambda: (None, "fake")
        agent._apply_usage = lambda usage: None
        agent._continue_event = asyncio.Event()
        return agent, state

    async def wait_until(self, condition):
        for _ in range(300):
            if condition():
                return
            await asyncio.sleep(0.005)
        self.fail("Agent did not reach the expected safe boundary")

    async def test_timeout_executes_entire_batch_then_resumes_original_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent, state = self.make_agent(Path(temporary), timeout=1)
            calls, executed = [], []

            async def model(*args):
                calls.append(list(agent.messages))
                if len(calls) == 1:
                    return "Checking sources", "", [
                        {"id": key, "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
                        for key in ("first", "second", "third")], None, []
                return "Final answer with remembered sources", "", [], None, []

            async def execute(name, args):
                executed.append(name)
                agent._round_started = time.monotonic() - 2
                return {"success": True, "type": "read", "content": "source " + str(len(executed))}

            agent._call_model = model
            agent._execute_tool = execute
            with patch("modules.api_pricing.capture_price", return_value=None):
                loop = asyncio.create_task(agent._run_loop())
                agent._task = loop
                try:
                    await self.wait_until(lambda: agent._idle)
                    self.assertEqual(len(executed), 3)
                    self.assertEqual(len(calls), 1)
                    results = [item for item in agent.messages if item["role"] == "tool"]
                    self.assertEqual([item["tool_call_id"] for item in results], ["first", "second", "third"])
                    self.assertTrue(all("source" in item["content"] for item in results))
                    self.assertEqual(agent.task_record["end_reason"], "timeout")
                    self.assertEqual(len(state.drain_master_messages()), 1)
                    agent.enqueue_work("Continue using these three sources")
                    await self.wait_until(lambda: len(calls) == 2 and agent._idle)
                    self.assertIsNone(agent.timeout_seconds)
                    self.assertEqual(agent._round_id, 2)
                    self.assertEqual(len([m for m in calls[-1] if m["role"] == "tool"]), 3)
                    notices = state.drain_master_messages()
                    self.assertEqual(len(notices), 1)
                    self.assertIn("Final answer with remembered sources", notices[0])
                    self.assertNotIn("Checking sources", notices[0])
                    saved = json.loads(agent.conversation_file.read_text())
                    self.assertEqual(saved["round_id"], 2)
                    self.assertEqual(saved["status"], "idle")
                finally:
                    agent._cancelled = True
                    agent._continue_event.set()
                    await loop

    async def test_natural_end_has_no_finish_tool_or_required_output_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent, state = self.make_agent(Path(temporary))
            async def model(*args):
                return "Analysis only", "", [], None, []
            agent._call_model = model
            with patch("modules.api_pricing.capture_price", return_value=None):
                loop = asyncio.create_task(agent._run_loop())
                try:
                    await self.wait_until(lambda: agent._idle)
                    self.assertEqual(agent.task_record["end_reason"], "completed")
                    self.assertIn("Analysis only", state.drain_master_messages()[0])
                    self.assertFalse((Path(temporary) / "output.json").exists())
                finally:
                    agent._cancelled = True
                    agent._continue_event.set()
                    await loop


class PublicContractTests(unittest.TestCase):
    def test_schema_uses_names_and_background_only(self):
        definitions = {item["function"]["name"]: item["function"] for item in build_master_tools()}
        fields = definitions["create_sub_agent"]["parameters"]["properties"]
        for removed in ("agent_id", "role_id", "run_in_background", "deliverables_dir"):
            self.assertNotIn(removed, fields)
        self.assertNotIn("timeout_seconds", definitions["create_sub_agent"]["parameters"]["required"])
        self.assertIn("working_dir_write", fields["access_level"]["enum"])
        self.assertNotIn("finish_task", [item["function"]["name"] for item in build_sub_tools()])

    def test_name_and_timeout_validation(self):
        self.assertEqual(validate_name("  Research  "), "Research")
        self.assertIsNone(validate_timeout(None))
        for value in (0, -1, True, 1.5, "10"):
            with self.assertRaises(ValueError):
                validate_timeout(value)
        manager = SimpleNamespace(tasks={"a": dict(name="Research", conversation_id="a", created_at=1),
                                         "b": dict(name="Research", conversation_id="b", created_at=2)})
        self.assertEqual(find_named(manager, "a", "RESEARCH")["conversation_id"], "a")

    def test_pending_notices_survive_snapshot_and_failed_admission(self):
        state = MultiAgentState("queue-test")
        state.push_master_message("first")
        pending = state.drain_master_messages()
        state.push_master_message("second")
        state.restore_master_messages(pending)
        restored = MultiAgentState.from_snapshot(state.to_snapshot())
        self.assertEqual(restored.drain_master_messages(), ["first", "second"])


if __name__ == "__main__":
    unittest.main()
