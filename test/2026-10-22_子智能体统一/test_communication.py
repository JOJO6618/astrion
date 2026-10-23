"""Question tools return answers and report child-to-child communication."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from modules.multi_agent.state import AgentInstance
import test_rounds as rounds


class CommunicationTests(unittest.IsolatedAsyncioTestCase):
    async def wait_for_question(self, state):
        for _ in range(100):
            if state.has_pending_master_messages():
                return state.drain_master_messages()[0]
            await asyncio.sleep(0.002)
        self.fail("Question did not reach parent queue")

    async def test_ask_master_can_be_answered_while_child_is_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            child, state = rounds.RoundTests.make_agent(self, Path(temporary))
            child.manager._save_state = Mock()
            ask = asyncio.create_task(child._execute_tool("ask_master", {
                "question": "Which source?", "question_id": "q-parent", "timeout_seconds": 1}))
            notice = await self.wait_for_question(state)
            self.assertIn("Which source?", notice)
            self.assertTrue(state.provide_answer("q-parent", "Use the official source"))
            result = await ask
            self.assertEqual(result["answer"], "Use the official source")
            self.assertEqual(state.get_instance(1).status, "running")
            child.manager._save_state.assert_called()

    async def test_ask_other_agent_delivers_answer_and_reports_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            child, state = rounds.RoundTests.make_agent(self, Path(temporary))
            state.register_instance(AgentInstance(agent_id=2, display_name="Reviewer", task_id="reviewer"))
            delivered = []
            def inject(agent_id, text):
                delivered.append((agent_id, text))
                # Exercise the early-answer race, before wait_for_answer registers.
                state.provide_answer("q-peer", "Reviewed successfully")
                return True
            child.manager.inject_message_to_sub_agent = inject
            result = await child._execute_tool("ask_other_agent", {
                "name": "REVIEWER", "question": "Review this result", "question_id": "q-peer"})
            self.assertEqual(result["answer"], "Reviewed successfully")
            self.assertEqual(delivered[0][0], 2)
            self.assertIn("Review this result", state.drain_master_messages()[0])
            child._pending_answer_question_id = "q-final"
            state.provide_answer = Mock(return_value=True)
            child._forward_output_to_master("Final peer answer", is_final=True)
            state.provide_answer.assert_called_with("q-final", "Final peer answer")
            self.assertIn("Final peer answer", state.drain_master_messages()[0])


if __name__ == "__main__":
    unittest.main()
