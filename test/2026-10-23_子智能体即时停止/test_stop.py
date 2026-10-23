import asyncio
import json
from types import SimpleNamespace

from helpers import StopCase
from modules.multi_agent.state import AgentInstance
from modules.sub_agent.manager import SubAgentManager
from modules.sub_agent.task_common import SubAgentModelCallError


class ImmediateStopTests(StopCase):
    async def test_streamed_partial_tool_is_discarded_and_stream_closed_before_resume(self):
        started, closed = asyncio.Event(), asyncio.Event()
        requests = []

        async def stream(messages, **_kwargs):
            requests.append(list(messages))
            if len(requests) == 1:
                try:
                    yield {"choices": [{"delta": {"content": "Partial reply", "tool_calls": [{
                        "index": 0, "id": "partial", "function": {"name": "read_file", "arguments": '{"path":'}}]}}]}
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            else:
                yield {"choices": [{"delta": {"content": "Resumed final answer"}}]}

        self.agent._build_client = lambda: (SimpleNamespace(chat=stream), "fake")
        self.start()
        await asyncio.wait_for(started.wait(), 1)
        await self.stop_from_master()
        self.assertTrue(closed.is_set())
        self.assertEqual([item["role"] for item in self.agent.messages], ["system", "user"])
        entries = [json.loads(line) for line in self.agent._stdout_lines]
        cancelled = [item for item in entries if item.get("id") == "partial" and item.get("status") == "cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["message"], "被主智能体取消")
        self.assert_persisted_pairs()
        self.assertEqual(len(self.state.drain_master_messages()), 1)
        self.agent.enqueue_work("Continue with original context")
        await self.wait_until(lambda: self.agent._idle and self.record["round_id"] == 2)
        self.assertEqual(len(requests), 2)
        self.assertTrue(any(item.get("content") == "Original request" for item in requests[1]))
        self.assertFalse(any(item.get("tool_calls") for item in requests[1]))
        self.assertEqual(self.record["end_reason"], "completed")

    async def test_running_batch_cancels_remaining_tools_preserves_results_and_queued_work(self):
        started, interrupted = asyncio.Event(), asyncio.Event()
        executed, requests = [], []

        async def model(*_args):
            requests.append(list(self.agent.messages))
            if len(requests) == 1:
                return self.response("Checking", [self.call(name) for name in ("done", "running", "not-started")])
            return self.response("Finished after explicit continuation")

        async def execute(_name, _args):
            executed.append(len(executed) + 1)
            if len(executed) == 1:
                return {"success": True, "type": "read", "content": "Real completed result"}
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                interrupted.set()

        self.agent._call_model, self.agent._execute_tool = model, execute
        self.start()
        await asyncio.wait_for(started.wait(), 1)
        self.agent.enqueue_work("Queued before stopping")
        await self.stop_from_master()
        self.assertTrue(interrupted.is_set())
        self.assertEqual(executed, [1, 2])
        saved = self.assert_persisted_pairs()
        results = [item["content"] for item in saved["messages"] if item["role"] == "tool"]
        self.assertIn("Real completed result", results[0])
        self.assertEqual(results[1:], ["被主智能体取消", "被主智能体取消"])
        self.assertEqual(self.record["pending_work"][0]["message"], "Queued before stopping")
        self.assertTrue(self.record["work_paused"])
        self.assertEqual(len(self.state.drain_master_messages()), 1)
        await asyncio.sleep(0.03)
        self.assertEqual(len(requests), 1)
        # Idle stop is a no-op, including a duplicate stop after completion.
        self.agent.request_stop(stopped_by="master")
        await asyncio.sleep(0.01)
        self.assertEqual(self.state.drain_master_messages(), [])
        self.agent.enqueue_work("Explicitly continue")
        await self.wait_until(lambda: self.agent._idle and self.record["round_id"] == 2)
        self.assertEqual(len(requests), 2)
        self.assertFalse(self.record["work_paused"])
        self.assertEqual(self.record["pending_work"], [])
        self.assertEqual([item["content"] for item in requests[1] if item["role"] == "tool"], results)
        self.assertTrue(any(item.get("content") == "Queued before stopping" for item in requests[1]))

    async def test_ui_stop_uses_user_cancellation_and_repeated_requests_do_not_interrupt_cleanup(self):
        started, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.agent._call_model = lambda *_: self.return_response([self.call("running")])

        async def execute(*_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await release.wait()

        self.agent._execute_tool = execute
        self.start()
        await asyncio.wait_for(started.wait(), 1)
        self.assertEqual(SubAgentManager.soft_stop_all_agents(self.manager, "parent"), 1)
        await asyncio.wait_for(cleanup.wait(), 1)
        for _ in range(3):
            self.assertTrue(self.agent.request_stop(stopped_by="user"))
        await asyncio.sleep(0)
        self.assertFalse(self.agent._execution_task.done())
        release.set()
        await self.wait_until(lambda: self.agent._idle)
        results = [item["content"] for item in self.agent.messages if item["role"] == "tool"]
        self.assertEqual(results, ["被用户取消"])
        self.assertIn("被用户取消", self.record["final_result"])
        self.assertEqual(len(self.state.drain_master_messages()), 1)
        self.assert_persisted_pairs()

    async def return_response(self, calls):
        return self.response(calls=calls)

    async def check_question_stop(self, tool_name):
        arguments = {"question": "Need guidance", "question_id": "q-stop"}
        if tool_name == "ask_other_agent":
            arguments["name"] = "Reviewer"
            self.state.register_instance(AgentInstance(agent_id=2, display_name="Reviewer", task_id="peer"))
            self.manager.inject_message_to_sub_agent = lambda *_: True
        async def model(*_):
            return self.response(calls=[self.call("question", tool_name, arguments), self.call("never-started")])
        self.agent._call_model = model
        self.start()
        await self.wait_until(lambda: self.state.is_agent_blocking(1))
        await self.stop_from_master()
        self.assertFalse(self.state.is_agent_blocking(1))
        self.assertNotIn("q-stop", self.state.pending_questions)
        self.assertEqual([item["content"] for item in self.agent.messages if item["role"] == "tool"],
                         ["被主智能体取消", "被主智能体取消"])
        self.assert_persisted_pairs()

    async def test_stop_interrupts_ask_master_wait(self):
        await self.check_question_stop("ask_master")

    async def test_stop_interrupts_ask_other_agent_wait(self):
        await self.check_question_stop("ask_other_agent")

    async def test_stop_interrupts_retry_delay_without_another_model_request(self):
        requests = []
        async def model(*_):
            requests.append(1)
            raise SubAgentModelCallError("Synthetic network failure")
        self.agent._call_model = model
        self.start()
        await self.wait_until(lambda: len(requests) == 1)
        await self.stop_from_master()
        self.assertEqual(requests, [1])
        self.assert_persisted_pairs()

    async def test_terminate_wins_over_pending_stop_and_remains_absorbing(self):
        started, cleanup = asyncio.Event(), asyncio.Event()
        self.agent._call_model = lambda *_: self.return_response([self.call("running")])
        async def execute(*_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await asyncio.Event().wait()
        self.agent._execute_tool = execute
        self.start()
        await asyncio.wait_for(started.wait(), 1)
        self.agent.request_stop(stopped_by="master")
        await asyncio.wait_for(cleanup.wait(), 1)
        self.manager.terminate_sub_agent(task_id="child")
        await self.wait_until(lambda: self.agent._task.done())
        self.assertEqual(self.record["status"], "terminated")
        self.assertEqual(self.state.get_instance(1).status, "terminated")
        self.assertFalse(self.agent.request_stop(stopped_by="master"))
        self.assertEqual(self.state.drain_master_messages(), [])

    async def test_native_cancel_result_is_relabelled_as_master_cancellation(self):
        started = asyncio.Event()
        self.agent._call_model = lambda *_: self.return_response([
            self.call("command", "run_command"), self.call("never-started")])
        executed = []

        async def execute(name, _args):
            executed.append(name)
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {"success": False, "status": "cancelled", "message": "被用户取消"}

        self.agent._execute_tool = execute
        self.start()
        await asyncio.wait_for(started.wait(), 1)
        await self.stop_from_master()
        self.assertEqual(executed, ["run_command"])
        saved = self.assert_persisted_pairs()
        self.assertEqual([item["content"] for item in saved["messages"] if item["role"] == "tool"],
                         ["被主智能体取消", "被主智能体取消"])
