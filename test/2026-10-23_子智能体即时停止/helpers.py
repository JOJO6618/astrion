"""Isolated child runtime with real persistence and no external model requests."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from modules.multi_agent.state import AgentInstance, MultiAgentState
from modules.sub_agent.manager import SubAgentManager
from modules.sub_agent.named import handle_named_tool
from modules.sub_agent.task import SubAgentTask


class StopCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.state = MultiAgentState("parent")
        self.record = dict(task_id="child", agent_id=1, name="Research", summary="Task",
                           access_level="working_dir_write", workspace_root=str(self.root),
                           working_dir=str(self.root), task_root=str(self.root),
                           conversation_id="parent", timeout_seconds=None, status="running", round_id=1)
        self.record.update({key: str(self.root / (key + ".json")) for key in
                            ("output_file", "stats_file", "progress_file", "conversation_file")})
        self.state.register_instance(AgentInstance(agent_id=1, display_name="Research", task_id="child"))
        self.manager = SimpleNamespace(terminal=SimpleNamespace(mcp_tools_enabled=False),
            tasks={"child": self.record}, _save_state=Mock(), _sub_agent_instances={},
            get_multi_agent_state=lambda _conversation_id: self.state)
        self.manager.stop_sub_agent = lambda **kw: SubAgentManager.stop_sub_agent(self.manager, **kw)
        self.manager.terminate_sub_agent = lambda **kw: SubAgentManager.terminate_sub_agent(self.manager, **kw)
        self.parent = SimpleNamespace(sub_agent_manager=self.manager,
                                     context_manager=SimpleNamespace(current_conversation_id="parent"))
        self.agent = SubAgentTask(self.manager, self.record, "Original request", "System", "fake", "thinking",
                                  multi_agent_state=self.state, display_name="Research")
        self.agent._build_client = lambda: (None, "fake")
        self.agent._build_runtime_tools = AsyncMock(return_value=[])
        self.agent._apply_usage = lambda *_: None
        self.manager._sub_agent_instances[1] = self.agent
        self.capture = patch("modules.api_pricing.capture_price", return_value=None)
        self.capture.start()

    async def asyncTearDown(self):
        self.agent._cancelled = True
        task = self.agent._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.capture.stop()
        self.temporary.cleanup()

    def start(self):
        self.agent._task = asyncio.create_task(self.agent.run())

    async def wait_until(self, predicate):
        for _ in range(400):
            if predicate():
                return
            await asyncio.sleep(0.005)
        self.fail("Child did not reach expected state")

    async def stop_from_master(self):
        result = await handle_named_tool(self.parent, "stop_sub_agent", {"name": "Research"})
        self.assertTrue(result["success"], result)
        await self.wait_until(lambda: self.record.get("end_reason") == "stopped" and self.agent._idle)
        self.assertFalse(self.agent._task.done())
        self.assertFalse(self.agent._cancelled)
        self.assertEqual(self.record["status"], "idle")
        self.assertIn("被主智能体取消", self.record["final_result"])

    def assert_persisted_pairs(self):
        saved = json.loads(self.agent.conversation_file.read_text())
        expected = [call["id"] for message in saved["messages"]
                    for call in message.get("tool_calls", [])]
        results = [message["tool_call_id"] for message in saved["messages"] if message["role"] == "tool"]
        self.assertEqual(results, expected)
        self.assertEqual(saved["status"], "idle")
        self.assertEqual(saved["end_reason"], "stopped")
        return saved

    @staticmethod
    def call(call_id, name="read_file", args=None):
        return {"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(args or {})}}

    @staticmethod
    def response(text="", calls=None):
        return text, "", calls or [], None, []
