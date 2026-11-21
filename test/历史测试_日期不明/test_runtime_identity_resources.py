"""Host identity, resource reuse, and administrator policy regression tests."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.context import RuntimeIdentity, get_user_resources
from server.context import resources


class GetUserResourcesIdentityTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = root / "project"
        entry = {"workspace_id": "default", "path": str(self.project), "label": "Default"}
        self.catalog = self.enterContext(patch("server.context.workspace.resolve_host_workspace", return_value=(None, entry)))
        self.enterContext(patch("server.context.workspace.DATA_DIR", str(root / "data")))
        self.enterContext(patch("server.context.workspace.LOGS_DIR", str(root / "logs")))
        self.enterContext(patch("server.context.workspace.UPLOAD_QUARANTINE_SUBDIR", ".quarantine"))
        self.enterContext(patch.object(resources, "_ensure_workspace_skills_synced"))
        self.enterContext(patch.object(resources, "_apply_workspace_personalization_preferences"))
        self.enterContext(patch.dict(resources.state.user_terminals, clear=True))
        constructor = self.enterContext(patch.object(resources, "WebTerminal"))
        self.terminal = constructor.return_value
        self.terminal.project_path = str(self.project)
        self.terminal._reaper_closing = False
        self.policy = self.enterContext(patch("modules.admin_policy_manager.get_effective_policy", return_value={
            "forced_category_states": {"terminal": False},
            "updated_at": "v1",
        }))

    def test_explicit_host_resolves_and_reuses_runtime_without_flask_session(self):
        identity = RuntimeIdentity(host_mode=True, host_workspace_id="default", role="admin")
        with patch.object(resources, "request_identity", side_effect=AssertionError("read request identity")):
            terminal, workspace = get_user_resources(identity=identity, conversation_id="conv_t10")
            reused, _ = get_user_resources(identity=identity, conversation_id="conv_t10")
        self.assertIs(terminal, self.terminal)
        self.assertIs(reused, terminal)
        self.assertEqual(workspace.username, "host")
        self.assertEqual(workspace.project_path, self.project.resolve())

    def test_legacy_identities_are_rejected_before_resolving_storage(self):
        identities = (
            ("host", RuntimeIdentity(role="user")),
            ("host", RuntimeIdentity(host_mode=True, is_api_user=True, role="api")),
            ("web_tester", RuntimeIdentity(host_mode=True, role="user")),
        )
        for username, identity in identities:
            with self.subTest(username=username, identity=identity), self.assertRaisesRegex(ValueError, "Host identity"):
                get_user_resources(username, identity=identity)
        self.catalog.assert_not_called()
        self.assertFalse(self.project.exists())

    def test_browser_host_identity_applies_admin_policy(self):
        app = Flask("host_policy_test")
        app.secret_key = "test"
        with app.test_request_context("/"):
            session.update(username="host", host_mode=True, role="admin")
            terminal, _ = get_user_resources("host", workspace_id="default", conversation_id="conv_policy")
        self.assertIs(terminal, self.terminal)
        self.policy.assert_called_once_with("host", "admin", None)
        terminal.set_admin_policy.assert_called_once_with({}, {"terminal": False}, [])
        self.assertEqual(terminal.admin_policy_version, "v1")


if __name__ == "__main__":
    unittest.main()
