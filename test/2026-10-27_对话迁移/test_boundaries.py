"""Portable-file boundaries and the one-click default workspace contract."""
from contextlib import ExitStack
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from flask import Flask
from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.handoff import register, read
from modules.conversation_transfer.archive import read_archive
from server.runtime import TrustedPrincipal
from server.conversation_transfer_support import expire_exports, export_path
from test_transfer import TransferFixture


class TransferBoundaryTests(TransferFixture):
    def test_unbundled_legacy_media_cannot_read_destination_file(self):
        bad = BytesIO()
        with ZipFile(self.archive()) as original, ZipFile(bad, "w") as changed:
            document = json.loads(original.read("conversation.json"))
            document["messages"][0]["images"] = ["/private/destination/image.png"]
            payload = json.dumps(document).encode()
            manifest = json.loads(original.read("manifest.json"))
            manifest["document_sha256"] = hashlib.sha256(payload).hexdigest()
            changed.writestr("conversation.json", payload)
            changed.writestr("manifest.json", json.dumps(manifest))
            for name in original.namelist():
                if name.startswith("assets/"):
                    changed.writestr(name, original.read(name))
        bad.seek(0)
        with self.assertRaisesRegex(ValueError, "legacy image/video"):
            read_archive(bad)

    def test_export_expiry_only_removes_generated_old_files(self):
        directory = self.source / "exports"
        directory.mkdir()
        expired = directory / ("a" * 32 + ".astrion.zip")
        fresh = directory / ("b" * 32 + ".astrion.zip")
        unrelated = directory / "notes.zip"
        link = directory / ("c" * 32 + ".astrion.zip")
        for path in (expired, fresh, unrelated):
            path.write_bytes(b"keep unless expired")
        link.symlink_to(unrelated)
        os.utime(expired, (1, 1))
        os.utime(unrelated, (1, 1))
        expire_exports(directory)
        self.assertFalse(expired.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(link.is_symlink())

    def test_download_rejects_symlinked_export_directory(self):
        (self.source / ".astrion").symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(ValueError):
            export_path(SimpleNamespace(workspace=SimpleNamespace(project_path=self.source)),
                        self.manager, "a" * 32)

    def test_default_handoff_target_is_reused_and_ignores_personal_selection(self):
        from server.chat_mode.handoff_workspace import default_target
        principal = TrustedPrincipal("synthetic", None, host_mode=True, conversation_kind="chat")
        catalog = {}
        def create(path, label):
            catalog.setdefault(path, {"workspace": {"workspace_id": "shared-default"}})
            return catalog[path]
        with patch("server.chat_mode.handoff_workspace.Path.home", return_value=self.root), \
             patch("modules.host_workspace_manager.create_host_workspace", side_effect=create) as create_mock, \
             patch("server.chat_mode.handoff_workspace.resolve_target", side_effect=lambda p, value: value):
            first, second = default_target(principal), default_target(principal)
        self.assertEqual(first, second)
        self.assertEqual(first, {"workspace_id": "shared-default"})
        self.assertEqual(len(catalog), 1)
        self.assertEqual(create_mock.call_args.args[0], str(self.root / "Desktop/Astrion/Default"))

    def test_pending_suggestion_accepts_without_workspace_form(self):
        import server.chat_mode.handoff as handoff
        storage = ChatStorage(self.root / "pending-chat")
        cid = storage.create(model_key="synthetic")
        turn = storage.begin(cid, "send", message={"role": "user", "message_id": "u",
            "content": "make an app"})["value"]["id"]
        storage.append(cid, turn, {"role": "assistant", "message_id": "a",
            "content": '<suggest_agent reason="files" />'})
        storage.finish(cid, turn)
        principal = TrustedPrincipal("synthetic", None, host_mode=True, conversation_kind="chat")
        register(storage, cid, principal, schedule=False)
        app = Flask(__name__)
        app.secret_key = "synthetic"
        with app.test_request_context("/", method="POST", json={
                "decision": "accept", "message_id": "a", "operation_id": "one-click",
                "project_path": "/ignored/frontend/path", "workspace_id": "ignored"}), ExitStack() as stack:
            stack.enter_context(patch("server.auth_helpers.is_logged_in", return_value=True))
            stack.enter_context(patch.object(handoff, "resources", return_value=(principal,
                SimpleNamespace(preferences_dir=self.root), storage)))
            target = stack.enter_context(patch.object(handoff, "default_target", return_value=(
                SimpleNamespace(workspace_id="shared-default"), principal)))
            start = stack.enter_context(patch.object(handoff, "start"))
            stack.enter_context(patch.object(handoff, "is_running", return_value=True))
            response = handoff.handoff_api(cid)
        self.assertTrue(response.json["success"])
        target.assert_called_once_with(principal)
        start.assert_called_once()
        self.assertEqual(read(storage, cid, "a")["target_workspace_id"], "shared-default")
