"""Offline projector contract tests; AST loading bypasses server package/config.

Run: python3 -B test/2026-10-07_运行对话快照/projector_tests.py -v
No servers, network, real conversation data, config or runtime files are used.
"""
import ast
from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "server/conversation_view/projector.py"
SOURCE_TEXT = SOURCE.read_text(encoding="utf-8")
SOURCE_AST = ast.parse(SOURCE_TEXT, filename=str(SOURCE))
NAMESPACE = {"__name__": "offline_runtime_projector", "__file__": str(SOURCE)}
exec(compile(SOURCE_AST, str(SOURCE), "exec"), NAMESPACE)
RuntimeProjection = NAMESPACE["RuntimeProjection"]


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.projection = RuntimeProjection("task-one", "conv-one", "codex-model")
        self.idx = 0

    def emit(self, event_type, **data):
        event = {"idx": self.idx, "type": event_type, "data": data, "ts": 1760000000 + self.idx}
        self.idx += 1
        self.projection.apply(event)
        return event

    def view(self):
        return self.projection.snapshot()

    def actions(self):
        return self.view()["messages"][-1]["actions"]

    def start(self):
        self.emit("ai_message_start")
        self.emit("api_request_start", attempt=1)

    def test_only_stdlib_imports_no_server_configuration(self):
        imports = []
        for node in ast.walk(SOURCE_AST):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertEqual(set(imports), {"copy", "functools", "re"})
        self.assertFalse(any(name == "server" or name.startswith("server.") for name in sys.modules))
        self.assertNotIn("config", sys.modules)
        self.assertLessEqual(len(SOURCE_TEXT.splitlines()), 500)

    def test_same_user_content_is_never_deduplicated(self):
        for i in range(5):
            self.emit("user_message", message="same prompt", message_id=f"u-{i}")
        messages = self.view()["messages"]
        self.assertEqual(len(messages), 5)
        self.assertEqual([m["id"] for m in messages], [f"u-{i}" for i in range(5)])
        self.assertEqual([m["message_id"] for m in messages], [f"u-{i}" for i in range(5)])

    def test_missing_user_id_is_task_and_event_stable(self):
        self.emit("user_message", message="same")
        self.emit("user_message", message="same")
        messages = self.view()["messages"]
        self.assertEqual([m["id"] for m in messages], ["task-one:0:user", "task-one:1:user"])
        replay = RuntimeProjection("task-one", "conv-one")
        for idx in range(2):
            replay.apply({"idx": idx, "type": "user_message", "data": {"message": "same"}})
        self.assertEqual([m["id"] for m in replay.snapshot()["messages"]], [m["id"] for m in messages])

    def test_user_metadata_media_and_timestamp_are_preserved(self):
        metadata = {"message_source": "sub_agent", "starts_work": False,
                    "visibility": "chat", "opaque": {"x": [1, {"flag": False}]}}
        self.emit("user_message", content="  verbatim text  ", metadata=metadata,
                  message_id="stable-user", images=["a.png"], videos=["a.mp4"],
                  media_refs=[{"path": "a.png", "custom": True}], timestamp="2026-10-07T12:00:00Z")
        metadata["opaque"]["x"].append(99)
        msg = self.view()["messages"][0]
        self.assertEqual(msg["content"], "  verbatim text  ")
        self.assertEqual(msg["metadata"]["opaque"]["x"], [1, {"flag": False}])
        self.assertEqual(msg["images"], ["a.png"])
        self.assertEqual(msg["videos"], ["a.mp4"])
        self.assertEqual(msg["media_refs"], [{"path": "a.png", "custom": True}])
        self.assertEqual(msg["created_at"], "2026-10-07T12:00:00Z")
        self.assertEqual(msg["timestamp"], msg["created_at"])

    def test_metadata_media_fallback_and_numeric_timestamp(self):
        self.emit("user_message", message="", metadata={"images": ["x.png"], "media_refs": [{"id": 1}]},
                  timestamp=123.5)
        msg = self.view()["messages"][0]
        self.assertEqual(msg["images"], ["x.png"])
        self.assertEqual(msg["media_refs"], [{"id": 1}])
        self.assertEqual(msg["timestamp"], 123.5)
        self.assertEqual(msg["created_at"], 123.5)
        self.assertEqual(msg["content"], "")

    def test_ai_start_always_creates_independent_assistant(self):
        self.emit("ai_message_start")
        self.emit("text_chunk", content="first")
        self.emit("ai_message_start")
        messages = self.view()["messages"]
        self.assertEqual(len(messages), 2)
        self.assertNotEqual(messages[0]["id"], messages[1]["id"])
        self.assertEqual(messages[0]["actions"][0]["content"], "first")
        self.assertFalse(messages[0]["actions"][0]["streaming"])
        self.assertTrue(messages[1]["awaitingFirstContent"])
        self.assertEqual(messages[1]["actions"], [])

    def test_thinking_and_text_full_content_and_fields(self):
        self.start()
        self.assertTrue(self.view()["state"]["api_request_pending"])
        self.emit("thinking_start")
        msg = self.view()["messages"][-1]
        thinking_id = msg["actions"][0]["id"]
        self.assertEqual(msg["activeThinkingId"], thinking_id)
        self.assertEqual(msg["actions"][0]["blockId"], thinking_id)
        self.assertEqual(msg["actions"][0]["modelKey"], "codex-model")
        self.assertEqual(msg["currentStreamingType"], "thinking")
        self.assertFalse(msg["awaitingFirstContent"])
        self.assertFalse(self.view()["state"]["api_request_pending"])
        self.emit("thinking_chunk", content="draft")
        self.assertEqual(self.view()["messages"][-1]["streamingThinking"], "draft")
        self.emit("thinking_end", full_content="corrected reasoning")
        self.emit("text_start")
        self.emit("text_chunk", content="draft answer")
        self.assertEqual(self.view()["messages"][-1]["streamingText"], "draft answer")
        self.emit("text_end", full_content="final answer")
        msg = self.view()["messages"][-1]
        self.assertEqual([a["content"] for a in msg["actions"]], ["corrected reasoning", "final answer"])
        self.assertTrue(all(not a["streaming"] for a in msg["actions"]))
        self.assertEqual(msg["streamingThinking"], "")
        self.assertEqual(msg["streamingText"], "")
        self.assertIsNone(msg["activeThinkingId"])
        self.assertIsNone(msg["currentStreamingType"])
        self.assertTrue(self.view()["state"]["streaming"])

    def test_start_redelivery_does_not_create_duplicate_active_blocks(self):
        self.start()
        self.emit("thinking_start")
        self.emit("thinking_start")
        self.emit("thinking_chunk", content="x")
        self.assertEqual(len(self.actions()), 1)
        self.emit("thinking_end", full_content="x")
        self.emit("text_start")
        self.emit("text_start")
        self.assertEqual(len(self.actions()), 2)
        self.emit("text_end", full_content="done")
        self.emit("text_start")
        self.assertEqual(len(self.actions()), 3)

    def test_chunk_without_start_and_end_without_chunks(self):
        self.emit("thinking_chunk", content="inferred thinking")
        self.emit("thinking_end")
        self.emit("text_chunk", content="inferred text")
        self.emit("text_end", full_content="")
        self.assertEqual([a["content"] for a in self.actions()], ["inferred thinking", "inferred text"])
        self.emit("ai_message_start")
        self.emit("thinking_end", full_content="end only reasoning")
        self.emit("text_end", full_content="end only answer")
        self.assertEqual([a["content"] for a in self.actions()], ["end only reasoning", "end only answer"])
        self.assertTrue(all(not a["streaming"] for a in self.actions()))

    def test_over_20000_chunks_keep_complete_thinking_and_text(self):
        self.start()
        expected = "abc中" * 25007
        for kind in ("thinking", "text"):
            self.emit(f"{kind}_start")
            for _ in range(25007):
                self.emit(f"{kind}_chunk", content="abc中")
            msg = self.view()["messages"][-1]
            field = "streamingThinking" if kind == "thinking" else "streamingText"
            self.assertEqual(msg[field], expected)
            self.assertEqual(msg["actions"][-1]["content"], expected)
            self.emit(f"{kind}_end")
            self.assertEqual(self.actions()[-1]["content"], expected)
        self.assertEqual(len(self.actions()), 2)
        self.assertFalse(hasattr(self.projection, "events"))
        self.assertFalse(hasattr(self.projection, "_events"))

    def test_fragmented_show_html_freezes_then_continues(self):
        self.start()
        self.emit("text_start")
        first = "intro <show_html>one</show_html>"
        for chunk in ("intro <sho", "w_html>one</show_h", "tml>"):
            self.emit("text_chunk", content=chunk)
        frozen = deepcopy(self.actions()[0])
        self.assertFalse(frozen["streaming"])
        self.assertTrue(frozen["frozenByShowHtml"])
        self.assertEqual(frozen["content"], first)
        self.emit("text_chunk", content="next <SHOW_HTML data-x='a'>two</SHOW_HTML>")
        second = deepcopy(self.actions()[1])
        self.assertTrue(second["continuation"])
        self.assertTrue(second["frozenByShowHtml"])
        self.emit("text_chunk", content="last")
        self.assertTrue(self.actions()[2]["continuation"])
        self.emit("text_end", full_content="THIS MUST NOT OVERWRITE FROZEN BLOCKS")
        actions = self.actions()
        self.assertEqual(actions[0], frozen)
        self.assertEqual(actions[1], second)
        self.assertEqual(actions[2]["content"], "last")
        self.assertFalse(actions[2]["streaming"])
        self.emit("text_start")
        self.emit("text_chunk", content="another response")
        self.emit("text_end", full_content="another final")
        self.assertEqual(self.actions()[-1]["content"], "another final")
        self.assertNotIn("continuation", self.actions()[-1])

    def test_html_freezes_after_whole_chunk_like_store(self):
        self.emit("text_chunk", content="<show_html>x</show_html>same chunk suffix")
        self.emit("text_end", full_content="different full response")
        self.assertEqual(len(self.actions()), 1)
        self.assertEqual(self.actions()[0]["content"], "<show_html>x</show_html>same chunk suffix")
        self.assertTrue(self.actions()[0]["frozenByShowHtml"])

    def test_tool_preparing_execution_aliases_and_name_changes(self):
        self.start()
        self.emit("tool_preparing", id="prepare-A", name="old_name", intent="plan")
        action_id = self.actions()[0]["id"]
        self.emit("tool_preparing", id="prepare-B", name="old_name")
        self.emit("tool_start", id="execute-A", preparing_id="prepare-A", name="new_name",
                  arguments={"path": "dir/file.txt", "intent": "read"}, monitor_snapshot={"before": "a"})
        self.emit("tool_intent", execution_id="execute-A", name="unrelated_name", intent="read fully",
                  intent_complete=True)
        self.emit("tool_update_action", id="execute-A", name="new_name", status="completed", result={"text": "ok"})
        actions = self.actions()
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["id"], action_id)
        tool = actions[0]["tool"]
        self.assertEqual(tool["id"], "execute-A")
        self.assertEqual(tool["executionId"], "execute-A")
        self.assertEqual(tool["preparing_id"], "prepare-A")
        self.assertEqual(tool["name"], "new_name")
        self.assertEqual(tool["status"], "completed")
        self.assertEqual(tool["result"], {"text": "ok"})
        self.assertEqual(tool["arguments"]["intent"], "read fully")
        self.assertEqual(tool["argumentSnapshot"], tool["arguments"])
        self.assertEqual(tool["argumentLabel"], "file.txt")
        self.assertEqual(tool["intent_rendered"], "read fully")
        self.assertTrue(tool["intent_complete"])
        self.assertEqual(actions[1]["tool"]["status"], "preparing")
        self.emit("update_action", id="arbitrary-display-id", preparing_id="prepare-A", message="updated",
                  content="detail", awaiting_content=True, monitor_snapshot_after={"after": "b"})
        tool = self.actions()[0]["tool"]
        self.assertEqual(tool["message"], "updated")
        self.assertEqual(tool["content"], "detail")
        self.assertTrue(tool["awaiting_content"])
        self.assertEqual(tool["monitor_snapshot_after"], {"after": "b"})

    def test_tool_start_without_preparing_and_old_preparing_does_not_regress(self):
        self.emit("tool_start", id="exec", name="run_command", arguments={"command": "synthetic"})
        self.emit("tool_preparing", id="exec", name="run_command", intent="late")
        self.assertEqual(len(self.actions()), 1)
        self.assertEqual(self.actions()[0]["tool"]["status"], "running")
        self.assertEqual(self.actions()[0]["tool"]["argumentLabel"], "synthetic")
        self.emit("update_action", execution_id="exec", status="failed", result="failure")
        self.assertEqual(self.actions()[0]["tool"]["status"], "failed")
        self.assertEqual(self.actions()[0]["tool"]["result"], "failure")

    def test_tool_intent_completion_without_text_change(self):
        self.emit("tool_preparing", id="p", name="read_file", intent="read", intent_complete=False)
        self.emit("tool_intent", id="p", intent="read", intent_complete=True)
        self.emit("tool_intent", id="p", intent="read", intent_complete=False)
        self.assertTrue(self.actions()[0]["tool"]["intent_complete"])

    def test_request_batch_and_retry_remove_exact_current_attempt(self):
        self.start()
        self.emit("text_chunk", content="previous iteration")
        self.emit("text_end", full_content="previous iteration")
        self.emit("tool_preparing", id="old-p", name="read_file")
        self.emit("tool_start", id="old-exec", preparing_id="old-p", arguments={"path": "old"})
        self.emit("update_action", id="old-exec", status="completed", result="old result")
        old = deepcopy(self.actions())
        self.emit("api_request_start", attempt=1)
        self.emit("thinking_chunk", content="discard reasoning")
        self.emit("thinking_end", full_content="discard reasoning")
        self.emit("text_chunk", content="<show_html>discard</show_html>")
        self.emit("tool_preparing", id="retry-p", name="read_file")
        self.assertEqual(self.actions()[-1]["toolBatchId"], "request-2")
        self.emit("error", retry=True, message="synthetic interruption")
        # A retry error is not terminal: the frontend keeps the task running.
        self.assertTrue(self.view()["state"]["streaming"])
        self.emit("stream_reset", attempt=2, max_attempts=5)
        self.assertEqual(self.actions(), old)
        self.assertTrue(self.view()["state"]["streaming"])
        self.emit("update_action", id="retry-p", status="completed", result="ghost update")
        self.assertEqual(self.actions(), old)
        self.emit("api_request_start", attempt=2)
        self.emit("text_chunk", content="retry success")
        self.emit("text_end", full_content="retry success")
        self.emit("tool_preparing", id="retry-p", name="read_file")
        self.assertEqual(self.actions()[-1]["toolBatchId"], "request-3")
        state = self.view()["state"]
        self.assertEqual(state["summary_tool_batch_sequence"], 3)
        self.assertEqual(state["summary_tool_batch_id"], "request-3")
        self.assertEqual([a.get("content") for a in self.actions() if a["type"] == "text"],
                         ["previous iteration", "retry success"])

    def test_retry_clears_only_current_request_even_without_tool_barrier(self):
        self.start()
        self.emit("text_chunk", content="completed request")
        self.emit("text_end", full_content="completed request")
        previous = self.actions()[0]
        self.emit("api_request_start")
        self.emit("text_chunk", content="incomplete request")
        self.emit("stream_reset")
        self.assertEqual(self.actions(), [previous])

    def test_retry_empty_placeholder_then_success(self):
        self.start()
        self.emit("thinking_chunk", content="discard")
        self.emit("stream_reset", attempt=2)
        msg = self.view()["messages"][-1]
        self.assertEqual(msg["actions"], [])
        self.assertTrue(msg["awaitingFirstContent"])
        self.assertIsNone(msg["activeThinkingId"])
        self.emit("api_request_start", attempt=2)
        self.emit("text_chunk", content="kept")
        self.emit("task_complete")
        self.assertEqual(self.actions()[0]["content"], "kept")

    def test_terminal_events_close_streaming_but_keep_all_content(self):
        for terminal in ("task_complete", "task_stopped", "error", "quota_exceeded"):
            with self.subTest(terminal=terminal):
                projection = RuntimeProjection("t", "c")
                sequence = [("ai_message_start", {}), ("api_request_start", {}),
                            ("thinking_chunk", {"content": "thinking"}),
                            ("text_chunk", {"content": "text"}),
                            ("tool_start", {"id": "p", "name": "synthetic", "arguments": {}})]
                for idx, (kind, data) in enumerate(sequence):
                    projection.apply({"idx": idx, "type": kind, "data": data})
                before = projection.snapshot()["messages"][0]["actions"]
                projection.apply({"idx": 99, "type": terminal, "data": {"message": "ignored error text"}})
                msg = projection.snapshot()["messages"][0]
                after = msg["actions"]
                self.assertEqual([a["id"] for a in before], [a["id"] for a in after])
                self.assertEqual([a.get("content") for a in before], [a.get("content") for a in after])
                self.assertEqual(after[-1]["tool"]["status"], "cancelled")
                self.assertEqual(after[-1]["tool"]["arguments"], before[-1]["tool"]["arguments"])
                self.assertTrue(all(not a.get("streaming") for a in after))
                self.assertEqual(msg["streamingThinking"], "")
                self.assertEqual(msg["streamingText"], "")
                self.assertIsNone(msg["currentStreamingType"])
                self.assertIsNone(msg["activeThinkingId"])
                self.assertFalse(msg["awaitingFirstContent"])
                self.assertFalse(projection.snapshot()["state"]["streaming"])
                self.assertFalse(projection.snapshot()["state"]["api_request_pending"])

    def test_tool_approval_progress_resolution_and_stale_required(self):
        approval = {"approval_id": "a", "tool_name": "read_file", "arguments": {"path": "x"}}
        self.emit("tool_approval_required", approval=approval)
        self.emit("tool_approval_required", approval={**approval, "reason": "explain"})
        self.emit("auto_approval_progress", approval_id="a", progress={"stage": "start", "message": "review"})
        self.emit("auto_approval_progress", approval_id="a", progress={"decision": "approved"})
        pending = self.view()["state"]["pending_tool_approvals"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["auto_review_status"], "approved")
        self.assertEqual(len(pending[0]["auto_review_progress"]), 2)
        self.emit("tool_approval_resolved", approval_id="a", decision="approved", reason="accepted")
        self.emit("tool_approval_required", approval=approval)
        self.emit("auto_approval_progress", approval_id="a", progress={"stage": "pending"})
        state = self.view()["state"]
        self.assertEqual(state["pending_tool_approvals"], [])
        self.assertEqual(state["resolved_tool_approval_ids"], ["a"])
        self.assertEqual(len(state["approval_review_records"]), 1)
        record = state["approval_review_records"][0]
        self.assertEqual(record["final_decision"], "approved")
        self.assertEqual(len(record["progress"]), 2)
        self.assertEqual(record["approval"]["status"], "approved")

    def test_user_questions_single_plural_and_batch_order(self):
        self.emit("user_questions_required", questions=[
            {"question_id": "q2", "batch_id": "b", "batch_index": 1, "created_at": 1},
            {"question_id": "q1", "batch_id": "b", "batch_index": 0, "created_at": 2}])
        self.emit("user_question_required", question={"question_id": "q2", "batch_id": "b", "batch_index": 1,
                                                      "question": "updated"})
        rows = self.view()["state"]["pending_user_questions"]
        self.assertEqual([r["question_id"] for r in rows], ["q1", "q2"])
        self.assertEqual(rows[1]["question"], "updated")
        self.emit("user_question_resolved", question_id="q1")
        self.assertEqual(len(self.view()["state"]["pending_user_questions"]), 1)
        self.emit("user_questions_resolved", question_ids=["q2"])
        self.assertEqual(self.view()["state"]["pending_user_questions"], [])

    def test_plan_approvals_preserve_snapshots_then_resolve(self):
        self.emit("plan_approval_required", approval={"approval_id": "p2", "created_at": 2, "plan_file": "b.md"})
        self.emit("plan_approval_required", approval={"approval_id": "p1", "created_at": 1, "plan_file": "a.md"})
        rows = self.view()["state"]["pending_plan_approvals"]
        self.assertEqual([r["approval_id"] for r in rows], ["p1", "p2"])
        self.emit("plan_approval_resolved", approval_id="p1", decision="approved")
        self.assertEqual([r["approval_id"] for r in self.view()["state"]["pending_plan_approvals"]], ["p2"])

    def test_goal_progress_completed_stopped_and_user_cancel(self):
        self.emit("goal_progress", goal="goal", turn_count=3, status="RUNNING")
        self.emit("goal_progress", tokens_used=200)
        state = self.view()["state"]
        self.assertTrue(state["goal_running"])
        self.assertEqual(state["goal_progress"]["goal"], "goal")
        self.assertEqual(state["goal_progress"]["tokens_used"], 200)
        self.emit("goal_review_progress", progress={"stage": "start", "message": "review goal"})
        self.assertEqual(self.view()["state"]["approval_review_records"][-1]["kind"], "goal")
        self.emit("goal_completed", goal="goal")
        self.assertFalse(self.view()["state"]["goal_running"])
        self.assertEqual(self.view()["state"]["goal_progress"]["status"], "done")
        self.emit("goal_stopped", stopped_reason="budget")
        self.assertEqual(self.view()["state"]["goal_progress"]["status"], "stopped")
        self.emit("goal_stopped", stopped_reason="user_cancel")
        self.assertIsNone(self.view()["state"]["goal_progress"])

    def test_system_action_and_stable_replay(self):
        events = [self.emit("ai_message_start"), self.emit("system_message", content="[Background run_command finished] result", variant="notice"),
                  self.emit("thinking_chunk", content="reason"), self.emit("thinking_end", full_content="reason"),
                  self.emit("text_chunk", content="answer"), self.emit("text_end", full_content="answer"),
                  self.emit("tool_preparing", id="p", name="synthetic")]
        replay = RuntimeProjection("task-one", "conv-one", "codex-model")
        for event in events:
            replay.apply(event)
        self.assertEqual(replay.snapshot(), self.view())
        self.assertEqual(self.actions()[0]["type"], "system")
        self.assertEqual(self.actions()[0]["content"], "[Background run_command finished] result")
        ids = [a["id"] for a in self.actions()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(value.startswith("task-one:") for value in ids))

    def test_snapshot_and_input_are_deeply_detached(self):
        event = self.emit("tool_start", id="exec", name="synthetic", arguments={"paths": ["a"]})
        event["data"]["arguments"]["paths"].append("mutated input")
        self.emit("tool_approval_required", approval={"approval_id": "a", "arguments": {"nested": [1]}})
        snapshot = self.view()
        snapshot["messages"][0]["actions"][0]["tool"]["arguments"]["paths"].append("mutated snapshot")
        snapshot["state"]["pending_tool_approvals"][0]["arguments"]["nested"].append(2)
        msg = self.view()["messages"][0]
        self.assertEqual(msg["actions"][0]["tool"]["arguments"]["paths"], ["a"])
        self.assertEqual(self.view()["state"]["pending_tool_approvals"][0]["arguments"]["nested"], [1])
        self.emit("update_action", id="exec", status="completed", result={"items": [1]})
        self.assertEqual(self.actions()[0]["tool"]["status"], "completed")

    def test_index_watermark_redelivery_and_cross_task_isolation(self):
        event = self.emit("text_chunk", content="same")
        self.projection.apply(event)
        self.emit("text_chunk", content="same")
        self.assertEqual(self.actions()[0]["content"], "samesame")
        before = self.view()
        self.projection.apply({"idx": 100, "type": "text_chunk", "data": {"content": "other", "task_id": "other"}})
        self.projection.apply({"idx": 100, "type": "text_chunk", "data": {"content": "other", "conversation_id": "other"}})
        self.assertEqual(self.view(), before)
        self.emit("text_chunk", content="next")
        self.assertEqual(self.actions()[0]["content"], "samesamenext")

    def test_unindexed_events_and_unknown_events(self):
        projection = RuntimeProjection("t", "c")
        projection.apply({"type": "user_message", "data": {"message": "same"}})
        projection.apply({"type": "user_message", "data": {"message": "same"}})
        self.assertEqual([m["id"] for m in projection.snapshot()["messages"]], ["t:0:user", "t:1:user"])
        before = projection.snapshot()
        projection.apply({"type": "unsupported", "data": {"opaque": True}})
        projection.apply(None)
        projection.apply({"type": "text_chunk", "data": "invalid"})
        self.assertEqual(projection.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
