"""New-page inheritance follows the explicit departed conversation only."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from server.context import new_conversation as service
from utils.conversation_manager import ConversationManager


class InheritanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = ConversationManager(str(self.root / "data"), str(self.root))
        self.workspace = SimpleNamespace(data_dir=str(self.root / "data"), project_path=self.root)
        self.terminal = SimpleNamespace(
            context_manager=SimpleNamespace(conversation_manager=self.manager,
                current_conversation_id=None, conversation_metadata={}),
            user_role="admin", _is_host_mode=lambda: True,
            model_key="synthetic", model_profile={}, api_client=SimpleNamespace(),
        )
        for key in ["work_mode", "permission_mode", "execution_mode", "network_permission"]:
            setattr(self.terminal, f"set_{key}", self.setter(key))
            setattr(self.terminal, f"get_{key}", self.getter(key))
        self.terminal.set_model = lambda key: setattr(self.terminal, "model_key", key)
        self.terminal.set_reasoning_effort = self.setter("reasoning_effort")
        self.terminal.set_run_mode = self.set_run_mode
        self.prefs = {"default_model": "synthetic", "default_run_mode": "thinking",
                      "default_work_mode": "ask", "default_permission_mode": "unrestricted",
                      "versioning_enabled_by_default": False}
        for item in [patch.object(service, "load_personalization_config", return_value=self.prefs),
                     patch.object(service, "get_registered_model_keys", return_value=["synthetic"]),
                     patch.object(service, "get_model_profile", return_value={"supports_thinking": True})]:
            item.start()
            self.addCleanup(item.stop)
        service.remember_workspace_defaults(self.terminal, self.prefs)

    def setter(self, key):
        return lambda value, **kwargs: setattr(self.terminal, key, value)

    def getter(self, key):
        return lambda: getattr(self.terminal, key)

    def set_run_mode(self, mode):
        self.terminal.run_mode = mode
        self.terminal.thinking_mode = mode == "thinking"

    def previous(self, enabled):
        return self.manager.create_conversation(
            project_path=str(self.root), initial_messages=[], model_key="synthetic",
            metadata_overrides={"chatgpt_fast_mode": enabled},
        )

    def test_inherits_both_on_and_off_from_explicit_source(self):
        for enabled in [True, False]:
            settings = service.prepare_draft_settings(self.terminal, self.workspace, self.previous(enabled))
            self.assertEqual(settings["chatgpt_fast_mode"], enabled)

    def test_no_source_does_not_scan_latest_conversation(self):
        self.previous(True)
        settings = service.prepare_draft_settings(self.terminal, self.workspace)
        self.assertFalse(settings["chatgpt_fast_mode"])

    def test_missing_field_is_off_and_does_not_leak_previous_on_state(self):
        service.prepare_draft_settings(self.terminal, self.workspace, self.previous(True))
        previous = self.manager.create_conversation(project_path=str(self.root), initial_messages=[])
        settings = service.prepare_draft_settings(self.terminal, self.workspace, previous)
        self.assertFalse(settings["chatgpt_fast_mode"])

    def test_first_send_persists_frontend_choice(self):
        settings = service.prepare_draft_settings(self.terminal, self.workspace, self.previous(False))
        settings["chatgpt_fast_mode"] = True
        result = service.create_from_settings(self.terminal, self.workspace, settings)
        document = self.manager.read_conversation(result["conversation_id"])
        self.assertTrue(document["metadata"]["chatgpt_fast_mode"])
        self.assertIsNone(self.terminal.context_manager.current_conversation_id)

    def test_invalid_initial_boolean_is_rejected(self):
        settings = service.prepare_draft_settings(self.terminal, self.workspace)
        settings["chatgpt_fast_mode"] = "false"
        with self.assertRaises(ValueError):
            service.validate_creation_settings(settings, host_mode=True, can_manage=True)
