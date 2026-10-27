"""Small, isolated lifecycle regressions; no model or real workspace requests."""
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "2026-10-25_Chat模式"))
import test_http as http_fixture
from modules.chat_mode import handoff
from modules.chat_mode.handoff_records import complete, detach, sync, tags
from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.duplicate import duplicate
from server.runtime import TrustedPrincipal


class StateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.storage = ChatStorage(Path(temporary.name))
        self.cid = self.storage.create(model_key="synthetic")
        self.principal = TrustedPrincipal("synthetic", None, conversation_kind="chat")
        handoff.presentation(self.storage, self.cid, "view")
        turn = self.storage.begin(self.cid, "first", message={
            "role": "user", "message_id": "u1", "content": "Build"})["value"]["id"]
        self.storage.append(self.cid, turn, {"role": "assistant", "message_id": "a1",
            "content": '<suggest_agent reason="one" />\n<suggest_agent reason="two" />'})
        self.storage.finish(self.cid, turn)
        handoff.register(self.storage, self.cid, self.principal, turn_id=turn, presentation_id="view")
        self.addCleanup(handoff.cancel_deadlines, self.storage, self.cid)

    def states(self):
        return sorted(self.storage.read(self.cid)["metadata"]["chat"]["handoffs"].values(),
                      key=lambda state: state["sequence"])

    def observe(self, state):
        with patch.object(handoff, "schedule_deadline"):
            return handoff.observe(self.storage, self.cid, self.principal, state["id"], "view")

    def test_independent_ids_count_and_repeat_registration(self):
        before = self.states()
        self.assertEqual(len({s["id"] for s in before}), 2)
        self.assertEqual([s["tag_index"] for s in before], [0, 1])
        handoff.register(self.storage, self.cid, self.principal, presentation_id="view")
        self.assertEqual(before, self.states())
        self.assertEqual(self.storage.read(self.cid)["metadata"]["chat"]["handoff_count"], 2)
        with self.assertRaises(FileNotFoundError):
            handoff.read(self.storage, self.cid, "a1")

    def test_reload_keeps_pending_but_permanently_cancels_timeout(self):
        state = self.states()[0]
        self.observe(state)
        handoff.presentation(self.storage, self.cid, "reloaded")
        after = handoff.read(self.storage, self.cid, state["id"])
        self.assertEqual(after["status"], "pending")
        self.assertIsNone(after["deadline"])
        self.assertFalse(after["countdown_enabled"])
        self.observe(state)  # A stale page heartbeat cannot re-arm it.
        with patch("server.runtime.runtime_service.create_task") as create:
            handoff.decline(self.storage, self.cid, self.principal, state["id"], expired=True)
            create.assert_not_called()
        handoff.select(self.storage, self.cid, state["id"])
        self.assertEqual(handoff.read(self.storage, self.cid, state["id"])["status"], "selecting")

    def test_live_timeout_and_double_click_are_idempotent(self):
        state = self.observe(self.states()[0])
        with patch.object(handoff.time, "time", return_value=state["deadline"] + 1), \
             patch("server.runtime.runtime_service.create_task", return_value=SimpleNamespace(task_id="continued")) as create:
            self.observe(state)
            handoff.decline(self.storage, self.cid, self.principal, state["id"], expired=True)
            handoff.decline(self.storage, self.cid, self.principal, state["id"])
            self.assertEqual(create.call_count, 1)
        decided = handoff.read(self.storage, self.cid, state["id"])
        self.assertEqual(decided["status"], "declined")
        self.assertEqual(decided["decision_source"], "timeout")

    def test_lost_heartbeat_or_restart_cannot_auto_continue(self):
        state = self.observe(self.states()[0])
        with patch.object(handoff.time, "time", return_value=state["deadline"] + 1), \
             patch.object(handoff, "_RUNTIME_ID", "restarted"), \
             patch("server.runtime.runtime_service.create_task") as create:
            result = handoff.decline(self.storage, self.cid, self.principal, state["id"], expired=True)
            create.assert_not_called()
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["countdown_enabled"])

    def test_predeadline_heartbeat_cannot_authorize_offline_continuation(self):
        state = self.observe(self.states()[0])
        with patch.object(handoff.time, "time", return_value=state["deadline"] + 1), \
             patch("server.runtime.runtime_service.create_task") as create:
            result = handoff.decline(self.storage, self.cid, self.principal, state["id"], expired=True)
            create.assert_not_called()
        self.assertEqual(result["status"], "pending")

    def test_pause_does_not_change_decision(self):
        state = self.observe(self.states()[0])
        handoff.observe(self.storage, self.cid, self.principal, state["id"], "view", suspend=True)
        self.assertEqual(self.states()[0]["status"], "pending")
        self.assertFalse(self.states()[0]["countdown_enabled"])

    def test_direct_send_is_atomic_and_failed_send_keeps_pending(self):
        with self.assertRaises(ValueError):
            self.storage.begin(self.cid, "bad", parent_id="missing", message={
                "role": "user", "message_id": "bad-user", "content": "Continue"})
        self.assertTrue(all(s["status"] == "pending" for s in self.states()))
        self.storage.begin(self.cid, "continue", parent_id="a1", message={
            "role": "user", "message_id": "u2", "content": "Continue"})
        self.assertTrue(all(s["status"] == "declined" for s in self.states()))
        self.assertTrue(all(not s["countdown_enabled"] for s in self.states()))

    def test_completion_closes_siblings_without_reviving_on_reload(self):
        state = self.states()[0]
        handoff.update(self.storage, self.cid, state["id"],
                       lambda current, document: complete(current, document, agent_conversation_id="agent"))
        handoff.presentation(self.storage, self.cid, "reload")
        self.assertEqual([s["status"] for s in self.states()], ["completed", "superseded"])
        self.assertEqual(self.states()[0]["agent_conversation_id"], "agent")

    def test_failed_switch_is_retryable_but_preparing_blocks_other_decisions(self):
        first, second = self.states()
        handoff.update(self.storage, self.cid, first["id"], lambda current, _: current.update(status="failed"))
        handoff.select(self.storage, self.cid, first["id"])
        handoff.update(self.storage, self.cid, first["id"], lambda current, _: current.update(status="preparing"))
        with self.assertRaises(RuntimeError):
            handoff.select(self.storage, self.cid, second["id"])
        with self.assertRaises(ValueError):
            self.storage.begin(self.cid, "during-handoff", parent_id="a1", message={
                "role": "user", "message_id": "u2", "content": "Continue"})

    def test_edit_removes_one_label_and_preserves_the_other_identity(self):
        first, second = self.states()
        self.storage.edit(self.cid, "remove-first", "a1", '<suggest_agent reason="two" />')
        states = {s["id"]: s for s in self.states()}
        self.assertFalse(states[first["id"]]["present"])
        self.assertEqual(states[first["id"]]["status"], "superseded")
        self.assertEqual(states[second["id"]]["tag_index"], 0)
        self.assertEqual(states[second["id"]]["status"], "pending")
        self.assertEqual(self.storage.read(self.cid)["metadata"]["chat"]["handoff_count"], 2)

    def test_removed_processed_label_cannot_be_revived_by_editing(self):
        first, second = self.states()
        handoff.update(self.storage, self.cid, first["id"], lambda current, _: current.update(status="completed"))
        self.storage.edit(self.cid, "remove", "a1", '<suggest_agent reason="two" />')
        self.storage.edit(self.cid, "restore", "a1", '<suggest_agent reason="one" />\n<suggest_agent reason="two" />')
        states = {state["id"]: state for state in self.states()}
        self.assertEqual(states[first["id"]]["status"], "completed")
        self.assertTrue(states[first["id"]]["present"])
        self.assertEqual(len(states), 2)
        self.assertEqual(states[second["id"]]["status"], "pending")

    def test_regeneration_creates_new_id_without_recounting_shared_ancestors(self):
        document = self.storage.read(self.cid)
        turn = document["metadata"]["chat"]["turns"][0]["id"]
        new_turn = self.storage.begin(self.cid, "regenerate", regenerate_turn_id=turn)["value"]["id"]
        self.storage.append(self.cid, new_turn, {"role": "assistant", "message_id": "a2",
            "content": '<suggest_agent reason="new version" />'})
        self.storage.finish(self.cid, new_turn)
        handoff.register(self.storage, self.cid, self.principal, turn_id=new_turn, presentation_id="view")
        self.assertEqual(len(self.states()), 3)
        self.assertEqual([s["status"] for s in self.states()], ["superseded", "superseded", "pending"])

    def test_examples_in_code_and_incomplete_tags_are_not_actions(self):
        tag = '<suggest_agent reason="example" />'
        self.assertEqual(tags(f'`{tag}`\n```html\n{tag}\n```\n{tag}'), [tag])
        self.assertEqual(tags(f'```html\n{tag}'), [])
        self.assertEqual(tags('<suggest_agent reason="unfinished"'), [])

    def test_copy_keeps_count_and_decisions_without_sharing_live_job_identity(self):
        first = self.states()[0]
        handoff.update(self.storage, self.cid, first["id"], lambda current, _: current.update(status="declined"))
        copied = duplicate(self.storage, self.cid, "copy")
        chat = self.storage.read(copied)["metadata"]["chat"]
        states = sorted(chat["handoffs"].values(), key=lambda s: s["sequence"])
        self.assertEqual(chat["handoff_count"], 2)
        self.assertEqual([s["status"] for s in states], ["declined", "pending"])
        self.assertTrue(all(not s["countdown_enabled"] for s in states))
        self.assertTrue(all(s["id"] != s["source_handoff_id"] for s in states))

    def test_legacy_records_are_upgraded_without_reissuing_processed_tags(self):
        document = self.storage.read(self.cid)
        document["metadata"]["chat"]["handoffs"] = {"a1": {
            "id": "legacy", "message_id": "a1", "status": "completed", "deadline": 1}}
        entries = sync(document)
        self.assertEqual(entries["legacy"]["status"], "completed")
        self.assertEqual(len(entries), 2)
        self.assertEqual(document["metadata"]["chat"]["handoff_count"], 3)
        self.assertTrue(all(not s["countdown_enabled"] for s in entries.values()))
        original = deepcopy(document)
        detach(document)
        self.assertEqual(document["messages"], original["messages"])


class EndpointTests(unittest.TestCase):
    def setUp(self):
        http_fixture.HttpTests.setUp(self)
        handoff.presentation(self.storage, self.cid, "view")
        turn = self.storage.begin(self.cid, "first", message={
            "role": "user", "message_id": "u1", "content": "Build"})["value"]["id"]
        self.storage.append(self.cid, turn, {"role": "assistant", "message_id": "a1",
            "content": '<suggest_agent reason="one" />'})
        self.storage.finish(self.cid, turn)
        self.state = handoff.register(self.storage, self.cid, self.identity, presentation_id="view")
        self.url = f"/api/chat/conversations/{self.cid}"
        self.addCleanup(handoff.cancel_deadlines, self.storage, self.cid)

    def test_presentation_reload_returns_pending_without_deadline(self):
        response = self.client.post(self.url + "/presentation", json={"presentation_id": "reload"})
        self.assertEqual(response.status_code, 200, response.json)
        chat = response.json["data"]["document"]["metadata"]["chat"]
        state = chat["handoffs"][self.state["id"]]
        self.assertEqual(chat["handoff_count"], 1)
        self.assertEqual(state["status"], "pending")
        self.assertFalse(state["countdown_enabled"])
        self.assertNotIn("tag", state)

    def test_late_release_cannot_retire_newer_page(self):
        self.client.post(self.url + "/presentation", json={"presentation_id": "new"})
        response = self.client.post(self.url + "/presentation", json={"presentation_id": "view", "release": True})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.storage.read(self.cid)["metadata"]["chat"]["handoff_presentation_id"], "new")

    def test_suggestion_id_endpoint_declines_once(self):
        with patch("server.runtime.runtime_service.create_task", return_value=SimpleNamespace(task_id="continued")) as create:
            for _ in range(2):
                response = self.client.post(self.url + "/handoff", json={
                    "handoff_id": self.state["id"], "operation_id": "same", "decision": "decline"})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json["data"]["status"], "declined")
            self.assertEqual(create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
