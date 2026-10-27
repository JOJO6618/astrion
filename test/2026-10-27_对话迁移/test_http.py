"""Exercise authenticated transfer routes with isolated synthetic workspaces."""
from contextlib import ExitStack
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask
from server.conversation_transfer import conversation_transfer_bp
from server.runtime import TrustedPrincipal
from test_transfer import TransferFixture


class TransferHttpTests(TransferFixture):
    # Reuse only the fixture, not its test methods.
    def setUp(self):
        super().setUp()
        self.app = Flask(__name__)
        self.app.secret_key = "synthetic-transfer-test"
        self.app.register_blueprint(conversation_transfer_bp)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session.update(username="synthetic", host_mode=True, role="admin")
        self.identity = SimpleNamespace(host_mode=True)
        self.source_workspace = SimpleNamespace(project_path=self.source,
            data_dir=self.manager.base_dir, workspace_id="a", label="Original")
        self.target_workspace = SimpleNamespace(project_path=self.target,
            data_dir=self.destination.base_dir, workspace_id="b", label="New")
        self.source_data = SimpleNamespace(username="synthetic", workspace_id="a",
            identity=self.identity, workspace=self.source_workspace)
        self.target_data = SimpleNamespace(username="synthetic", workspace_id="b",
            identity=self.identity, workspace=self.target_workspace)
        self.principal = TrustedPrincipal("synthetic", "a", host_mode=True, role="admin")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("server.auth_helpers.is_logged_in", return_value=True))
        self.stack.enter_context(patch("server.conversation_transfer._scope", return_value=(
            self.principal, self.source_data, self.manager, self.source_info)))
        self.stack.enter_context(patch("server.context.background.background_status", return_value={}))
        self.stack.enter_context(patch("server.context.resources.find_runtime", return_value=None))
        self.list_tasks = self.stack.enter_context(patch("server.tasks.task_manager.list_tasks", return_value=[]))
        self.stack.enter_context(patch("config.model_profiles.get_registered_model_profiles", return_value={"synthetic": {}}))
        self.stack.enter_context(patch("server.conversation_transfer._modes", return_value=self.modes))
        from modules.desktop_navigation import DesktopNavigation
        self.navigation = DesktopNavigation(str(self.root / "tabs.json"))
        self.stack.enter_context(patch("server.conversation_tabs.navigation", self.navigation))

    def test_authentication_is_required(self):
        with patch("server.auth_helpers.is_logged_in", return_value=False):
            response = self.client.post(f"/api/conversations/{self.cid}/export")
        self.assertEqual(response.status_code, 401)

    def test_export_rejects_active_task(self):
        self.list_tasks.return_value = [SimpleNamespace(conversation_id=self.cid, status="running", principal=self.principal)]
        response = self.client.post(f"/api/conversations/{self.cid}/export")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.manager.read_conversation(self.cid)["messages"], self.prefix)

    def test_export_download_is_zip(self):
        response = self.client.post(f"/api/conversations/{self.cid}/export")
        self.assertEqual(response.status_code, 200, response.json)
        file = response.json["data"]
        self.assertTrue(file["filename"].endswith(".zip"))
        downloaded = self.client.get(file["url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertTrue(downloaded.data.startswith(b"PK"))
        self.assertEqual(downloaded.headers["Cache-Control"], "no-store")

    def test_import_publishes_only_valid_package(self):
        with patch("server.conversation_transfer._scope", return_value=(self.principal,
            self.target_data, self.destination, self.target_info)):
            response = self.client.post("/api/conversations/import", data={
                "file": (self.archive(), "conversation.zip"), "operation_id": "valid-one"})
            self.assertEqual(response.status_code, 200, response.json)
            imported = self.destination.read_conversation(response.json["data"]["conversation_id"])
            self.assertEqual(imported["messages"][:-1], self.prefix)
            invalid = self.client.post("/api/conversations/import", data={
                "file": (BytesIO(b"not a zip"), "bad.zip"), "operation_id": "invalid-one"})
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(len(list(self.destination.conversations_dir.glob("conv_*.json"))), 1)

    def test_missing_model_is_not_silently_replaced(self):
        with patch("config.model_profiles.get_registered_model_profiles", return_value={}):
            response = self.client.post("/api/conversations/import", data={
                "file": (self.archive(), "conversation.zip"), "operation_id": "missing-model"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Model is unavailable", response.json["error"])

    def test_model_override_obeys_archived_protocol_when_source_model_is_missing(self):
        self.document["metadata"]["model_key"] = "missing-original"
        self.source_info["api_protocol"] = "responses"
        with patch("server.conversation_transfer._scope", return_value=(self.principal,
                self.target_data, self.destination, self.target_info)):
            rejected = self.client.post("/api/conversations/import", data={
                "file": (self.archive(), "conversation.zip"), "operation_id": "wrong-protocol",
                "model_key": "synthetic"})
            self.assertEqual(rejected.status_code, 400, rejected.json)
            with patch("config.model_profiles.get_registered_model_profiles", return_value={
                    "synthetic": {"api_protocol": "responses"}}):
                accepted = self.client.post("/api/conversations/import", data={
                    "file": (self.archive(), "conversation.zip"), "operation_id": "right-protocol",
                    "model_key": "synthetic"})
            self.assertEqual(accepted.status_code, 200, accepted.json)

    def test_migration_does_not_build_zip_and_preserves_identity(self):
        from server.context.identity import RuntimeIdentity
        self.source_data.identity = RuntimeIdentity(host_mode=True, host_workspace_id="a", role="admin")
        self.target_data.identity = RuntimeIdentity(host_mode=True, host_workspace_id="b", role="admin")
        with patch("server.conversation_transfer.get_workspace_data", return_value=self.target_data), \
             patch("server.conversation_transfer.export_archive", side_effect=AssertionError("internal migration must not zip")):
            initial = self.navigation.command("synthetic", "open-original", "open", {
                "workspace_id": "a", "conversation_id": self.cid})["state"]
            response = self.client.post(f"/api/conversations/{self.cid}/migrate", json={
                "target_workspace_id": "b", "operation_id": "move-one"})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json["data"]["conversation_id"], self.cid)
            self.assertIsNone(self.manager.read_conversation(self.cid))
            moved = self.destination.read_conversation(self.cid)
            self.assertEqual(moved["messages"][:-1], self.prefix)
            repeated = self.client.post(f"/api/conversations/{self.cid}/migrate", json={
                "target_workspace_id": "b", "operation_id": "move-one"})
            self.assertEqual(repeated.json, response.json)
            final = self.navigation.snapshot("synthetic")
            self.assertEqual(len(final["tabs"]), 1)
            self.assertEqual(final["tabs"][0]["key"], initial["tabs"][0]["key"])
            self.assertEqual(final["tabs"][0]["workspace_id"], "b")
            self.assertNotEqual(final["navigation"]["id"], initial["navigation"]["id"])

    def _retry_interrupted_migration(self, failure):
        from server.context.identity import RuntimeIdentity
        self.source_data.identity = RuntimeIdentity(host_mode=True, host_workspace_id="a", role="admin")
        payload = {"target_workspace_id": "b", "operation_id": "interrupted"}
        with patch("server.conversation_transfer.get_workspace_data", return_value=self.target_data):
            with failure:
                failed = self.client.post(f"/api/conversations/{self.cid}/migrate", json=payload)
            self.assertEqual(failed.status_code, 500, failed.json)
            self.assertEqual(self.manager.read_conversation(self.cid)["messages"], self.prefix)
            retry = self.client.post(f"/api/conversations/{self.cid}/migrate", json=payload)
            self.assertEqual(retry.status_code, 200, retry.json)
            self.assertIsNone(self.manager.read_conversation(self.cid))
            moved = self.destination.read_conversation(self.cid)
            self.assertEqual(moved["messages"][:-1], self.prefix)
            self.assertEqual(sum(bool(m.get("metadata", {}).get("conversation_migration"))
                                 for m in moved["messages"]), 1)

    def test_retry_finishes_source_retirement(self):
        self._retry_interrupted_migration(patch(
            "modules.conversation_transfer.service.retire_source", side_effect=OSError("interrupted retirement")))

    def _interrupted_then_changed(self, manager, change):
        from server.context.identity import RuntimeIdentity
        self.source_data.identity = RuntimeIdentity(host_mode=True, host_workspace_id="a", role="admin")
        payload = {"target_workspace_id": "b", "operation_id": "changed-destination"}
        with patch("server.conversation_transfer.get_workspace_data", return_value=self.target_data):
            with patch("modules.conversation_transfer.service.retire_source", side_effect=OSError("interrupted")):
                response = self.client.post(f"/api/conversations/{self.cid}/migrate", json=payload)
                self.assertEqual(response.status_code, 500)
            manager.mutate_conversation(self.cid, change)
            retry = self.client.post(f"/api/conversations/{self.cid}/migrate", json=payload)
            self.assertEqual(retry.status_code, 409, retry.json)
            self.assertIsNotNone(self.manager.read_conversation(self.cid))
            self.assertIsNotNone(self.destination.read_conversation(self.cid))

    def test_retry_rejects_replaced_destination(self):
        self._interrupted_then_changed(self.destination,
            lambda doc: doc["metadata"].pop("conversation_transfer"))

    def test_retry_rejects_edited_destination_prefix(self):
        self._interrupted_then_changed(self.destination,
            lambda doc: doc["messages"][0].update(content="different target history"))

    def test_retry_preserves_new_source_progress(self):
        self._interrupted_then_changed(self.manager,
            lambda doc: doc["messages"].append({"role": "user", "content": "new source progress", "message_id": "new"}))

    def test_retry_after_receipt_write_failure_reuses_destination(self):
        atomic = self.manager._atomic_write_json
        def write(path, value):
            if path.parent.name == "conversation_migrations":
                raise OSError("interrupted receipt")
            return atomic(path, value)
        self._retry_interrupted_migration(patch.object(self.manager, "_atomic_write_json", side_effect=write))

    def test_source_runtime_is_detached_without_saving(self):
        from server import state
        from server.context.identity import RuntimeIdentity
        from unittest.mock import Mock
        self.source_data.identity = RuntimeIdentity(host_mode=True, host_workspace_id="a", role="admin")
        context = SimpleNamespace(current_conversation_id=self.cid,
            conversation_history=list(self.prefix), auto_save_enabled=True, todo_list={})
        runtime = SimpleNamespace(context_manager=context, terminal_manager=Mock(), mcp_client_manager=Mock())
        key = f"synthetic::a::{self.cid}"
        with patch.dict(state.user_terminals, {key: runtime}, clear=True), \
             patch("server.conversation_transfer.get_workspace_data", return_value=self.target_data):
            response = self.client.post(f"/api/conversations/{self.cid}/migrate", json={
                "target_workspace_id": "b", "operation_id": "detach-source"})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertNotIn(key, state.user_terminals)
        self.assertIsNone(context.current_conversation_id)
        self.assertFalse(context.auto_save_enabled)
        self.assertEqual(context.conversation_history, [])
        runtime.terminal_manager.close_all.assert_called_once()
        runtime.mcp_client_manager.close_all_clients.assert_called_once()

    def test_migration_failure_keeps_source(self):
        from server.context.identity import RuntimeIdentity
        self.source_data.identity = RuntimeIdentity(host_mode=True, host_workspace_id="a", role="admin")
        with patch("server.conversation_transfer.get_workspace_data", return_value=self.target_data), \
             patch("server.conversation_transfer.import_document", side_effect=OSError("synthetic disk error")):
            response = self.client.post(f"/api/conversations/{self.cid}/migrate", json={
                "target_workspace_id": "b", "operation_id": "move-fail"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.manager.read_conversation(self.cid)["messages"], self.prefix)


if __name__ == "__main__":
    unittest.main()
