"""User-scoped capacity persistence, HTTP validation and live manager behavior."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask

from modules.personalization_manager import (
    load_personalization_config, save_personalization_config,
    sanitize_personalization_payload,
)
from modules.sub_agent.manager import SubAgentManager
from modules.sub_agent.named import handle_named_tool
from modules.sub_agent.persistence import _REGISTRIES
from server.multi_agent import sub_agent_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.data = self.root / "data"
        self.data.mkdir()
        self.manager = SubAgentManager(str(self.root), str(self.data),
                                       owner_conversation_id="parent")
        self.addCleanup(lambda: _REGISTRIES.pop(str(self.data), None))
        self.stack.enter_context(patch(
            "modules.aux_model_resolver.resolve_locked_model_profile", return_value={}))
        self.stack.enter_context(patch.object(self.manager, "_launch", self.launch))
        # Auth and workspace resolution are unchanged; isolate the real HTTP body.
        view = sub_agent_settings.__wrapped__.__wrapped__
        app = Flask(__name__)
        app.add_url_rule("/api/sub_agents/settings", methods=["GET", "PUT"],
                         view_func=lambda: view(data_context=None,
                             workspace=SimpleNamespace(data_dir=str(self.data)), username="user"))
        self.client = app.test_client()

    def launch(self, instance):
        loop = SimpleNamespace(call_soon_threadsafe=lambda callback: callback())
        instance._task = SimpleNamespace(done=lambda: False, get_loop=lambda: loop,
                                         cancel=lambda: None)
        self.manager._sub_agent_instances[instance.agent_id] = instance

    def set_limit(self, value):
        return self.client.put("/api/sub_agents/settings", json={"settings": {
            "sub_agent_max_instances": value,
            "sub_agent_compress_threshold_tokens": 300000,
        }})

    def create(self, agent_id, conversation_id="parent"):
        return self.manager.create_sub_agent(
            agent_id=agent_id, summary="Research", task="Research",
            conversation_id=conversation_id, access_level="working_dir_write",
            model_key="fake", intelligence="same", reasoning_effort=None,
            thinking_mode="thinking", display_name=f"Agent {agent_id}",
            working_dir=None, system_prompt="system", task_message="Original request")

    def test_default_twenty_and_save_get_reload(self):
        self.assertEqual(self.client.get("/api/sub_agents/settings").json["settings"][
            "sub_agent_max_instances"], 20)
        response = self.set_limit(7)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["settings"]["sub_agent_max_instances"], 7)
        self.assertEqual(response.json["settings"]["sub_agent_compress_threshold_tokens"], 300000)
        self.assertEqual(self.client.get("/api/sub_agents/settings").json["settings"][
            "sub_agent_max_instances"], 7)
        self.assertEqual(load_personalization_config(self.data)["sub_agent_max_instances"], 7)
        persisted = json.loads((self.data / "personalization.json").read_text())
        self.assertEqual(persisted["sub_agent_max_instances"], 7)
        # An unrelated personalization update must not drop the new field.
        saved = save_personalization_config(self.data, {"show_status_avatar": False})
        self.assertEqual(saved["sub_agent_max_instances"], 7)

    def test_invalid_values_are_rejected_without_changing_saved_settings(self):
        self.assertEqual(self.set_limit(7).status_code, 200)
        before = (self.data / "personalization.json").read_bytes()
        for value in (True, False, 0, -1, 1.5, "4", None, [], 2 ** 53):
            with self.subTest(value=value):
                response = self.set_limit(value)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json["success"])
                self.assertEqual((self.data / "personalization.json").read_bytes(), before)
        for settings in (None, [], {}, {"sub_agent_max_instances": 5,
                                        "sub_agent_compress_threshold_tokens": True}):
            self.assertEqual(self.client.put("/api/sub_agents/settings", json={
                "settings": settings}).status_code, 400)
        self.assertEqual((self.data / "personalization.json").read_bytes(), before)

    def test_missing_or_invalid_persisted_capacity_uses_default(self):
        for value in (None, True, 0, -5, "broken", 2.3):
            with self.subTest(value=value):
                self.assertEqual(sanitize_personalization_payload({
                    "sub_agent_max_instances": value})["sub_agent_max_instances"], 20)
        self.assertEqual(sanitize_personalization_payload({})["sub_agent_max_instances"], 20)

    def test_lowering_preserves_instances_and_resumption_raising_applies_immediately(self):
        self.assertEqual(self.set_limit(2).status_code, 200)
        self.assertTrue(self.create(1)["success"])
        self.assertTrue(self.create(2)["success"])
        self.assertEqual(self.set_limit(1).status_code, 200)
        records = list(self.manager.tasks.values())
        self.assertEqual(len(records), 2)
        for record in records:
            record["status"] = "idle"
        terminal = SimpleNamespace(sub_agent_manager=self.manager,
            context_manager=SimpleNamespace(current_conversation_id="parent"))
        resumed = asyncio.run(handle_named_tool(terminal, "send_message_to_sub_agent", {
            "name": "Agent 1", "message": "Continue"}))
        self.assertTrue(resumed["success"], resumed)
        denied = self.create(3)
        self.assertFalse(denied["success"])
        self.assertIn("1 个", denied["error"])
        self.assertEqual(self.set_limit(3).status_code, 200)
        self.assertTrue(self.create(3)["success"])
        self.assertEqual(len(self.manager.tasks), 3)
        self.assertFalse(self.create(4)["success"])

    def test_limit_above_twenty_is_not_capped_and_conversations_are_independent(self):
        self.assertEqual(self.set_limit(21).status_code, 200)
        for agent_id in range(1, 22):
            self.assertTrue(self.create(agent_id)["success"])
        self.assertFalse(self.create(22)["success"])
        self.assertTrue(self.create(23, conversation_id="another-parent")["success"])

    def test_one_slot_remains_atomic_with_a_live_custom_setting(self):
        self.assertEqual(self.set_limit(1).status_code, 200)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.create, (1, 2)))
        self.assertEqual(sum(result["success"] for result in results), 1)
        self.assertEqual(len(self.manager.tasks), 1)

    def test_shared_user_preference_symlink_is_preserved(self):
        shared = self.root / "personal"
        shared.mkdir()
        save_personalization_config(shared, {"sub_agent_max_instances": 2})
        preference_path = self.data / "personalization.json"
        preference_path.symlink_to(shared / "personalization.json")
        self.assertEqual(self.set_limit(1).status_code, 200)
        self.assertTrue(preference_path.is_symlink())
        self.assertEqual(load_personalization_config(shared)["sub_agent_max_instances"], 1)
        self.assertTrue(self.create(1)["success"])
        self.assertFalse(self.create(2)["success"])


if __name__ == "__main__":
    unittest.main()
