"""Delivery retries preserve order; received work survives an idle restart."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from modules.multi_agent.state import AgentInstance, MultiAgentState
from modules.sub_agent.task import SubAgentTask
from server.chat_flow_task_support import process_multi_agent_master_messages


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def make_parent(self, fail_on=None):
        state = MultiAgentState("parent")
        saved = []
        attempts = 0

        def add(role, text, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == fail_on:
                raise OSError("injected storage failure")
            saved.append(text)
            return {"message_id": kwargs["message_id"]}

        manager = SimpleNamespace(get_multi_agent_state=lambda _id: state, _save_state=Mock())
        parent = SimpleNamespace(sub_agent_manager=manager,
            context_manager=SimpleNamespace(current_conversation_id="parent", add_conversation=add))
        return state, saved, parent

    async def test_partial_delivery_requeues_only_unsaved_messages_in_order(self):
        state, saved, parent = self.make_parent(fail_on=2)
        for text in ("first", "second", "third"):
            state.push_master_message(text)
        messages = [{"role": "tool", "tool_call_id": "last", "content": "result"}]
        with self.assertRaises(OSError):
            await process_multi_agent_master_messages(web_terminal=parent, messages=messages,
                sender=None, debug_log=lambda _: None, after_tool_call_id="last")
        self.assertEqual(saved, ["first"])
        self.assertEqual(state.pending_master_messages, ["second", "third"])
        count = await process_multi_agent_master_messages(web_terminal=parent, messages=messages,
            sender=None, debug_log=lambda _: None, after_tool_call_id="last")
        self.assertEqual(count, 2)
        self.assertEqual(saved, ["first", "second", "third"])
        self.assertEqual([m["content"] for m in messages], ["result", "first", "second", "third"])
        self.assertFalse(state.has_pending_master_messages())
        self.assertEqual(parent.sub_agent_manager._save_state.call_count, 2)

    async def test_saved_work_is_consumed_once_after_reconstruction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state = MultiAgentState("parent")
            state.register_instance(AgentInstance(agent_id=1, display_name="Research", task_id="child"))
            manager = SimpleNamespace(_save_state=Mock())
            record = dict(task_id="child", agent_id=1, name="Research", round_id=1,
                conversation_id="parent", access_level="working_dir_write",
                workspace_root=str(root), working_dir=str(root), task_root=str(root),
                **{key: str(root / (key + ".json")) for key in
                   ("output_file", "stats_file", "progress_file", "conversation_file")})
            child = SubAgentTask(manager, record, "Task", "System", "fake", "thinking",
                multi_agent_state=state, display_name="Research")
            child._task = asyncio.current_task()
            child._continue_event = asyncio.Event()
            child.enqueue_work("Resume with remembered context", timeout=35)
            self.assertEqual(record["pending_work"][0]["timeout"], 35)
            # Reconstruct from the persisted record without touching the original instance queue.
            restored_record = json.loads(json.dumps(record))
            restored = SubAgentTask(manager, restored_record, "Task", "System", "fake", "thinking",
                multi_agent_state=state, display_name="Research")
            restored._continue_event = asyncio.Event()
            restored._accept_work(new_round=True)
            restored._accept_work(new_round=False)
            self.assertEqual(sum(m.get("content") == "Resume with remembered context"
                                 for m in restored.messages), 1)
            self.assertEqual(restored.timeout_seconds, 35)
            self.assertEqual(restored_record["pending_work"], [])
            self.assertEqual(restored_record["round_id"], 2)


if __name__ == "__main__":
    unittest.main()
