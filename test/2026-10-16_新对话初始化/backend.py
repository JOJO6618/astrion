"""Isolated defaults, inheritance and file-creation contract regressions."""
from pathlib import Path
import inspect
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="isolated-")
RUNTIME = Path(TEMP.name)
os.environ.update(ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=str(RUNTIME),
                  DATA_DIR=str(RUNTIME / "data"), LOGS_DIR=str(RUNTIME / "logs"),
                  DEPLOY_CONFIG_DIR=str(RUNTIME / "deploy"), TERMINAL_SANDBOX_MODE="host",
                  USER_SPACE_DIR=str(RUNTIME / "users"), API_USER_SPACE_DIR=str(RUNTIME / "api"))

from flask import Flask
from core.main_terminal import MainTerminal
from core.web_terminal import WebTerminal
from core.main_terminal_parts.tools_policy import MainTerminalToolsPolicyMixin
from core.main_terminal_parts.commands import MainTerminalCommandMixin
from server.conversation_bootstrap import prepare_new_conversation_settings
from server.context import new_conversation as service
from server.conversation import create_conversation
from server.multi_agent import create_multi_agent_conversation
from utils.conversation_manager import ConversationManager


class Terminal(MainTerminalToolsPolicyMixin):
    get_execution_mode = MainTerminal.get_execution_mode
    set_execution_mode = MainTerminal.set_execution_mode
    get_execution_mode_state = MainTerminal.get_execution_mode_state
    get_network_permission = MainTerminal.get_network_permission
    set_network_permission = MainTerminal.set_network_permission
    set_run_mode = MainTerminalCommandMixin.set_run_mode
    set_reasoning_effort = MainTerminalCommandMixin.set_reasoning_effort

    def __init__(self, normal, multi):
        self.context_manager = SimpleNamespace(conversation_manager=normal,
            multi_agent_conversation_manager=multi, current_conversation_id=None,
            conversation_metadata={}, has_images=False, has_videos=False)
        self.api_client = SimpleNamespace()
        self.model_key = "old-model"
        self.model_profile = {}
        self.run_mode = "fast"
        self.thinking_mode = False
        self.reasoning_effort = "high"
        self.current_work_mode = "plan"
        self.current_permission_mode = "readonly"
        self.host_execution_mode = "sandbox"
        self.host_network_permission = "full"
        self.user_role = "admin"
        self.container_session = None

    def set_model(self, key):
        self.model_key = key
        self.model_profile = service.get_model_profile(key)

    def _is_host_mode(self):
        return True

    def _apply_execution_mode_to_runtime(self):
        pass


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.case = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.addCleanup(self.case.cleanup)
        path = Path(self.case.name)
        project = path / "project"
        project.mkdir()
        self.workspace = SimpleNamespace(project_path=project, data_dir=path / "data")
        self.normal = ConversationManager(str(path / "normal"), str(project))
        self.multi = ConversationManager(str(path / "multi"), str(project))
        self.terminal = Terminal(self.normal, self.multi)
        self.prefs = {"default_model": "selected", "default_run_mode": "thinking",
            "default_reasoning_effort": None, "default_work_mode": "ask",
            "default_permission_mode": "unrestricted", "versioning_enabled_by_default": False}
        for target, value in (("load_personalization_config", self.prefs),
                              ("get_model_profile", {}),
                              ("get_registered_model_keys", ["selected"])):
            mock = patch.object(service, target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        # A nonempty profile represents an available, configured model.
        service.get_model_profile.return_value = {"supports_thinking": True}
        service.remember_workspace_defaults(self.terminal, self.prefs)

    def previous(self, manager=None):
        return (manager or self.normal).create_conversation(
            project_path=str(self.workspace.project_path), initial_messages=[],
            metadata_overrides={"execution_mode": "direct", "network_permission": "full",
                                "work_mode": "execute", "reasoning_effort": "high"})

    def test_defaults_and_null_effort_never_inherit_old_runtime(self):
        before = list(self.normal.conversations_dir.glob("conv_*.json"))
        value = service.prepare_draft_settings(self.terminal, self.workspace)
        self.assertEqual(value["work_mode"], "ask")
        self.assertEqual(value["model_key"], "selected")
        self.assertEqual(value["run_mode"], "thinking")
        self.assertIsNone(value["reasoning_effort"])
        self.assertEqual(value["execution_mode"], "sandbox")
        self.assertEqual(value["network_permission"], "restricted")
        self.assertEqual(list(self.normal.conversations_dir.glob("conv_*.json")), before)

    def test_explicit_departed_conversation_inherits_only_nondefault_fields(self):
        for manager in (self.normal, self.multi):
            cid = self.previous(manager)
            value = service.prepare_draft_settings(self.terminal, self.workspace, cid)
            self.assertEqual(value["execution_mode"], "direct")
            self.assertEqual(value["network_permission"], "full")
            self.assertEqual(value["work_mode"], "ask")
            self.assertIsNone(value["reasoning_effort"])

    def test_no_source_never_scans_history_and_missing_source_is_safe(self):
        self.previous()
        with patch.object(self.normal, "get_conversation_list", side_effect=AssertionError("history scan")):
            for source in (None, "conv_missing"):
                value = service.prepare_draft_settings(self.terminal, self.workspace, source)
                self.assertEqual(value["execution_mode"], "sandbox")
                self.assertEqual(value["network_permission"], "restricted")

    def test_plan_and_restricted_modes_lock_sandbox_with_restoration(self):
        cid = self.previous()
        for work, permission in (("plan", "unrestricted"), ("ask", "approval"),
                                 ("plan", "approval")):
            service.remember_workspace_defaults(self.terminal, {**self.prefs,
                "default_work_mode": work, "default_permission_mode": permission})
            value = service.prepare_draft_settings(self.terminal, self.workspace, cid)
            self.assertEqual(value["execution_mode"], "sandbox")
            if work == "plan":
                self.assertEqual(value["permission_mode"], "readonly")
                self.assertEqual(value["restore_modes"]["pre_plan_permission_mode"], permission)
            else:
                self.assertEqual(value["restore_modes"]["pre_readonly_execution_mode"], "direct")

    def test_changed_defaults_replace_cached_defaults_not_current_selection(self):
        value = service.prepare_draft_settings(self.terminal, self.workspace)
        service.remember_workspace_defaults(self.terminal, {**self.prefs, "default_work_mode": "execute"})
        self.assertEqual(value["work_mode"], "ask")
        self.assertEqual(service.prepare_draft_settings(self.terminal, self.workspace)["work_mode"], "execute")

    def test_both_creation_routes_persist_full_snapshot_without_changing_focus(self):
        settings = service.prepare_draft_settings(self.terminal, self.workspace)
        settings.update(work_mode="execute", execution_mode="direct", network_permission="full")
        app = Flask("new-conversation-test")
        for route, manager, is_multi in ((create_conversation, self.normal, False),
                                        (create_multi_agent_conversation, self.multi, True)):
            old = self.previous(manager)
            with app.test_request_context("/", method="POST", json={"settings": settings}):
                response, code = inspect.unwrap(route)(self.terminal, self.workspace, "host")
            self.assertEqual(code, 201)
            cid = response.get_json()["conversation_id"]
            meta = manager.read_conversation(cid)["metadata"]
            for key in service.SETTING_KEYS:
                self.assertEqual(meta[key], settings[key], key)
            self.assertEqual(meta["multi_agent_mode"], is_multi)
            self.assertEqual(manager.current_conversation_id, old)
            self.assertIsNone(self.terminal.context_manager.current_conversation_id)

    def test_non_host_preparation_and_creation_keep_environment_controls_disabled(self):
        cid = self.previous()
        self.terminal.user_role = "user"
        with patch.object(self.terminal, "_is_host_mode", return_value=False):
            settings = service.prepare_draft_settings(self.terminal, self.workspace, cid)
        self.assertEqual(settings["execution_mode"], "sandbox")
        self.assertEqual(settings["network_permission"], "restricted")
        with self.assertRaises(PermissionError):
            service.validate_creation_settings({**settings, "network_permission": "full"},
                                               host_mode=False, can_manage=False)

    def test_preparation_endpoint_allocates_no_conversation_and_keeps_explicit_identity(self):
        self.workspace.username = "host"
        self.workspace.workspace_id = "synthetic"
        data = SimpleNamespace(workspace_id="synthetic", identity=SimpleNamespace(host_mode=True, role="admin"))
        app = Flask("new-preparation")
        before = set(Path(self.case.name).rglob("*"))
        with app.test_request_context("/", method="POST", json={}), \
             patch("server.context.resources.get_user_resources", return_value=(self.terminal, self.workspace)) as acquire:
            result = inspect.unwrap(prepare_new_conversation_settings)(data, self.workspace, "host").get_json()
        acquire.assert_called_once_with("host", workspace_id="synthetic", update_session=False)
        self.assertTrue(result["success"])
        self.assertIsNone(result["data"]["settings"]["reasoning_effort"])
        self.assertEqual(set(Path(self.case.name).rglob("*")), before)

    def test_workspace_terminal_does_not_automatically_load_or_create_a_file(self):
        terminal = SimpleNamespace(context_manager=SimpleNamespace(current_conversation_id=None),
            load_conversation=lambda *a, **k: self.fail("loaded recent history"),
            create_new_conversation=lambda *a, **k: self.fail("created a file"))
        WebTerminal._ensure_conversation(terminal)
        self.assertIsNone(terminal.context_manager.current_conversation_id)
        calls = []
        terminal._bound_conversation_id = "bound"
        terminal.load_conversation = lambda *a, **k: calls.append((a, k)) or {"success": True}
        WebTerminal._ensure_conversation(terminal)
        self.assertEqual(calls, [(("conv_bound",), {"restore_model": True})])

    def test_invalid_snapshots_rejected_instead_of_silently_overridden(self):
        valid = service.prepare_draft_settings(self.terminal, self.workspace)
        invalid = [{}, {**valid, "thinking_mode": False},
                   {**valid, "model_key": "missing"},
                   {**valid, "work_mode": "plan", "permission_mode": "unrestricted"},
                   {**valid, "permission_mode": "approval", "execution_mode": "direct"},
                   {**valid, "reasoning_effort": "unknown"}]
        for settings in invalid:
            with self.assertRaises(ValueError):
                service.validate_creation_settings(settings, host_mode=True, can_manage=True)
        with self.assertRaises(PermissionError):
            service.validate_creation_settings({**valid, "execution_mode": "direct"},
                                               host_mode=True, can_manage=False)
        service.get_model_profile.return_value = {"thinking_only": True}
        with self.assertRaises(ValueError):
            service.validate_creation_settings({**valid, "run_mode": "fast", "thinking_mode": False},
                                               host_mode=True, can_manage=True)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        TEMP.cleanup()
