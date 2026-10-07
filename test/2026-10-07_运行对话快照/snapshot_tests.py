"""Offline integration of real snapshot, projection and persistence methods.

No service/config imports or real conversations. Temporary IO stays in this test folder.
"""
import ast
from collections import deque
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_source(relative, namespace=None, drop_import=None, class_only=None):
    path = ROOT / relative
    tree = ast.parse(path.read_text(), filename=str(path))
    if drop_import:
        tree.body = [node for node in tree.body if not
                     (isinstance(node, ast.ImportFrom) and node.module == drop_import)]
    if class_only:
        tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef)
                     and node.name == class_only]
    scope = {"__name__": "offline_snapshot", "__file__": str(path), **(namespace or {})}
    exec(compile(tree, str(path), "exec"), scope)
    return scope


Projection = load_source("server/conversation_view/projector.py")["RuntimeProjection"]
locking = load_source("utils/conversation_manager/locking.py")
view = load_source("server/conversation_view/snapshots.py", {"RuntimeProjection": Projection},
                   drop_import="projector")
Crud = load_source("utils/conversation_manager/crud_mixin.py", {
    "locked_write": locking["locked_write"], "Optional": Optional,
    "Dict": Dict, "List": List, "Any": Any, "Path": Path,
    "json": json, "datetime": datetime, "_KEEP": object(),
}, class_only="CrudMixin")["CrudMixin"]


class Disk(Crud):
    def __init__(self, directory):
        self.directory = Path(directory)
        self._io_lock = locking["directory_lock"](self.directory)
        self.index_writes = 0

    def _get_conversation_file_path(self, conversation_id):
        return self.directory / f"{conversation_id}.json"

    def _count_tools_in_messages(self, messages):
        return sum(len(m.get("tool_calls") or []) for m in messages)

    def _initialize_token_statistics(self):
        return {"total_tokens": 0}

    def _validate_token_statistics(self, data):
        return data

    def _extract_title_from_messages(self, messages):
        return "Test"

    def _save_conversation_file(self, conversation_id, data):
        self._get_conversation_file_path(conversation_id).write_text(json.dumps(data))

    def _update_index(self, conversation_id, data):
        self.index_writes += 1

    def seed(self, messages, **fields):
        self._save_conversation_file("conv-a", {"title": "Test", "metadata": {},
                                               "messages": messages, **fields})


class Manager(view["TaskViewMixin"]):
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks = {}

    @view["view_transaction"]
    def _append_event(self, rec, event_type, data):
        with self._lock:
            event = {"idx": rec.next_event_idx, "type": event_type,
                     "data": {**data, "task_id": rec.task_id}, "ts": 1000}
            self.publish_display(rec, event)
            rec.events.append(event)
            rec.next_event_idx += 1
            rec.updated_at = rec.next_event_idx


def record(task_id="T", workspace="W", created=1, preceding=None):
    return SimpleNamespace(task_id=task_id, username="U", workspace_id=workspace,
        conversation_id="conv-a", model_key="model", task_type="chat",
        task_params=SimpleNamespace(goal_mode=False), goal_progress=None,
        status="running", created_at=created, updated_at=created, error=None,
        display_base=None, display_context=None, display_projection=Projection(task_id, "conv-a"),
        display_revision=task_id, display_valid=True, trigger_message_id="input-" + task_id,
        directives=SimpleNamespace(preceding_user_notices=preceding or []),
        events=deque(maxlen=4), next_event_idx=0)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.disk = Disk(self.temp.name)
        self.disk.seed([{"role": "user", "message_id": "old", "content": "old input"}])
        self.manager = Manager()
        self.rec = record()
        self.manager._tasks[self.rec.task_id] = self.rec
        self.context = SimpleNamespace(_get_conversation_manager_for_id=lambda _: self.disk)
        self.manager.bind_display(self.rec, self.context)

    def emit(self, event_type, **data):
        self.manager._append_event(self.rec, event_type, data)

    def snapshot(self, workspace="W"):
        return self.manager.capture_display("U", "conv-a", self.disk, workspace_id=workspace)

    def test_memory_output_and_cursor_are_one_snapshot(self):
        self.emit("user_message", message="new", message_id="new")
        self.emit("ai_message_start")
        self.emit("text_start")
        for _ in range(20007):
            self.emit("text_chunk", content="x")
        snap = self.snapshot()
        self.assertEqual(len(self.rec.events), 4)
        self.assertEqual(snap["display"]["next_event_idx"], 20010)
        self.assertEqual(len(snap["display"]["messages"][-1]["actions"][0]["content"]), 20007)
        self.assertEqual([m["message_id"] for m in snap["messages"]], ["old"])

    def test_disk_partial_output_never_appends_second_copy(self):
        self.emit("user_message", message="new", message_id="new")
        self.emit("text_chunk", content="visible")
        self.disk.seed([{"role": "user", "message_id": "old", "content": "old input"},
            {"role": "user", "message_id": "new", "content": "new", "metadata": {"work_timer": {"status": "working"}}},
            {"role": "assistant", "message_id": "saved-assistant", "content": "visible"}])
        snap = self.snapshot()
        self.assertEqual(len(snap["messages"]), 1)
        self.assertEqual(len(snap["display"]["messages"]), 2)
        self.assertEqual(snap["display"]["messages"][0]["metadata"]["work_timer"]["status"], "working")

    def test_same_id_upserts_without_breaking_assistant(self):
        self.emit("user_message", message_id="input", message="repeat")
        self.emit("text_chunk", content="live")
        self.emit("user_message", message_id="input", message="repeat", metadata={"later": True})
        self.emit("text_chunk", content=" tail")
        live = self.snapshot()["display"]["messages"]
        self.assertEqual(len(live), 2)
        self.assertEqual(live[-1]["actions"][0]["content"], "live tail")
        self.emit("user_message", message_id="different", message="repeat")
        self.assertEqual(len(self.snapshot()["display"]["messages"]), 3)

    def test_workspace_collision_cannot_select_another_task(self):
        other = record("OTHER", "other", 999)
        self.manager._tasks[other.task_id] = other
        self.manager._append_event(other, "text_chunk", {"content": "wrong workspace"})
        self.emit("text_chunk", content="right")
        self.assertEqual(self.snapshot()["display"]["task"]["task_id"], "T")
        self.assertEqual(self.snapshot()["display"]["messages"][-1]["actions"][0]["content"], "right")

    def test_preceding_notice_moves_from_prefix_by_identity(self):
        self.disk.seed([{"role": "user", "message_id": "notice", "content": "same"},
                        {"role": "user", "message_id": "other", "content": "same"}])
        self.rec.directives.preceding_user_notices = [{"payload": {"message_id": "notice"}}]
        self.manager.bind_display(self.rec, self.context)
        self.emit("user_message", message_id="notice", message="same")
        snap = self.snapshot()
        self.assertEqual([m["message_id"] for m in snap["messages"]], ["other"])
        self.assertEqual([m["message_id"] for m in snap["display"]["messages"]], ["notice"])

    def test_completion_preserves_output_and_citations(self):
        self.emit("text_chunk", content="final")
        self.emit("task_complete", citations=[{"id": "src-one", "url": "https://example.com"}])
        self.manager.finish_display_task(self.rec, "succeeded")
        snap = self.snapshot()
        self.assertEqual(snap["display"]["task"]["status"], "succeeded")
        self.assertFalse(snap["display"]["state"]["streaming"])
        self.assertEqual(snap["display"]["messages"][-1]["metadata"]["citations"][0]["id"], "src-one")

    def test_pending_successor_retains_completed_view_until_binding(self):
        self.emit("text_chunk", content="previous")
        self.manager.finish_display_task(self.rec, "succeeded")
        next_rec = record("NEXT", created=2)
        self.manager._tasks[next_rec.task_id] = next_rec
        snap = self.snapshot()
        self.assertEqual(snap["display"]["task"]["task_id"], "NEXT")
        self.assertEqual(snap["display"]["messages"][-1]["actions"][0]["content"], "previous")
        self.disk.seed([{"role": "assistant", "message_id": "persisted", "content": "previous"}])
        self.manager.bind_display(next_rec, self.context)
        self.assertIsNone(self.rec.display_projection)
        self.assertIsNone(self.rec.display_base)
        self.assertFalse(self.rec.display_valid)
        self.assertEqual(self.snapshot()["messages"][0]["content"], "previous")

    def test_compression_rebases_revision_and_continues_at_absolute_cursor(self):
        self.emit("text_chunk", content="before")
        before = self.snapshot()["display"]
        self.disk.seed([{"role": "user", "message_id": "summary", "content": "compressed"}])
        self.manager.rebase_display(self.rec)
        self.emit("text_chunk", content="after")
        after = self.snapshot()
        self.assertNotEqual(after["display"]["revision"], before["revision"])
        self.assertEqual(after["display"]["next_event_idx"], 2)
        self.assertEqual(after["messages"][0]["content"], "compressed")
        self.assertEqual(after["display"]["messages"][-1]["actions"][0]["content"], "after")

    def test_checkpoint_invalidates_only_requested_workspace(self):
        other = record("other", "other", 2)
        self.manager._tasks[other.task_id] = other
        self.emit("text_chunk", content="obsolete")
        self.manager.invalidate_display("conv-a", username="U", workspace_id="W")
        self.assertFalse(self.rec.display_valid)
        self.assertTrue(other.display_valid)
        self.assertEqual(self.snapshot()["display"]["messages"], [])

    def test_retry_and_request_boundary_preserve_prior_completed_round(self):
        self.emit("ai_message_start")
        self.emit("api_request_start")
        self.emit("text_chunk", content="finished round")
        self.emit("text_end", full_content="finished round")
        self.emit("api_request_start")
        self.emit("error", retry=True)
        msg = self.snapshot()["display"]["messages"][-1]
        self.assertEqual(msg["streamAttemptStart"], 1)
        self.assertEqual(msg["streamAttemptBatchId"], "request-2")
        self.assertTrue(self.snapshot()["display"]["state"]["streaming"])
        self.emit("stream_reset")
        self.assertEqual(self.snapshot()["display"]["messages"][-1]["actions"][0]["content"], "finished round")

    def test_read_does_not_migrate_or_write_anything(self):
        target = self.disk._get_conversation_file_path("conv-a")
        contents, mtime = target.read_bytes(), target.stat().st_mtime_ns
        for _ in range(3):
            result = self.disk.read_conversation("conv-a")
            self.assertIn("run_mode", result["metadata"])
            result["messages"].clear()
        self.assertEqual(target.read_bytes(), contents)
        self.assertEqual(target.stat().st_mtime_ns, mtime)
        self.assertEqual(self.disk.index_writes, 0)

    def test_distinct_managers_serialize_full_read_merge_write(self):
        other = Disk(self.temp.name)
        self.assertIs(self.disk._io_lock, other._io_lock)
        gate = threading.Barrier(12)
        results = []
        def save(index):
            gate.wait(timeout=3)
            manager = self.disk if index % 2 else other
            results.append(manager.save_conversation("conv-a", [
                {"role": "user", "message_id": f"thread-{index}", "content": str(index)}]))
        threads = [threading.Thread(target=save, args=(i,)) for i in range(12)]
        with redirect_stdout(io.StringIO()):
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=5)
        self.assertEqual(results, [True] * 12)
        messages = self.disk.read_conversation("conv-a")["messages"]
        self.assertEqual(len(messages), 13)
        self.assertEqual({m["message_id"] for m in messages}, {"old", *[f"thread-{i}" for i in range(12)]})


if __name__ == "__main__":
    unittest.main()
