"""Admission only transports notices; history belongs to the gated task."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from server.chat_flow_task_main import _dispatch_multi_agent_idle_messages_sync


class AdmissionTests(unittest.TestCase):
    def test_failed_admission_never_writes_or_emits_notices(self):
        terminal = SimpleNamespace(data_dir="synthetic", context_manager=Mock())
        sender = Mock()
        with patch("modules.goal_policy.goal_blocks_background", return_value=False), \
             patch("server.chat_flow_notice_idle.RuntimeContext.from_terminal", return_value=object()) as build, \
             patch("server.chat_flow_notice_idle.runtime_service.create_task", side_effect=RuntimeError("busy")):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                _dispatch_multi_agent_idle_messages_sync(web_terminal=terminal,
                    workspace=SimpleNamespace(workspace_id="workspace"), sender=sender,
                    client_sid="client", username="host", conversation_id="parent",
                    messages=["first result", "last result"], gate_token="owned-token")
            self.assertFalse(terminal.context_manager.add_conversation.called)
            sender.assert_not_called()
            directives = build.call_args.kwargs["directives"]
            self.assertEqual(directives.main_task_gate_token, "owned-token")
            self.assertEqual(directives.preceding_user_notices[0]["message"], "first result")

    def test_successful_admission_waits_for_gated_message_persistence(self):
        terminal = SimpleNamespace(data_dir="synthetic", context_manager=Mock())
        sender = Mock()
        with patch("modules.goal_policy.goal_blocks_background", return_value=False), \
             patch("server.chat_flow_notice_idle.RuntimeContext.from_terminal", return_value=object()), \
             patch("server.chat_flow_notice_idle.runtime_service.create_task",
                   return_value=SimpleNamespace(task_id="notice")):
            _dispatch_multi_agent_idle_messages_sync(web_terminal=terminal,
                workspace=SimpleNamespace(workspace_id="workspace"), sender=sender,
                client_sid="client", username="host", conversation_id="parent",
                messages=["result"], gate_token="owned-token")
        self.assertFalse(terminal.context_manager.add_conversation.called)
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
