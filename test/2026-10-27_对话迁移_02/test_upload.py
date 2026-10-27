"""Reproduce legacy tempfile upload streams through Flask multipart parsing."""
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "2026-10-27_对话迁移"))
from test_transfer import TransferFixture
from server.conversation_transfer import conversation_transfer_bp


class LegacyUploadStream:
    """Python 3.9 SpooledTemporaryFile has read/seek/tell but no seekable."""
    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        if name == "seekable":
            raise AttributeError("'SpooledTemporaryFile' object has no attribute 'seekable'")
        return getattr(self.stream, name)


class LegacyUploadRequest(Request):
    def _get_file_stream(self, *args, **kwargs):
        return LegacyUploadStream(super()._get_file_stream(*args, **kwargs))


class UploadStreamTests(TransferFixture):
    def test_legacy_spooled_multipart_upload_imports_original_history(self):
        app = Flask(__name__)
        app.secret_key = "synthetic-upload-regression"
        app.request_class = LegacyUploadRequest
        app.register_blueprint(conversation_transfer_bp)
        data = SimpleNamespace(workspace=SimpleNamespace(project_path=self.target),
                               identity=SimpleNamespace(host_mode=True), workspace_id="target-menu")
        with ExitStack() as stack:
            stack.enter_context(patch("server.auth_helpers.is_logged_in", return_value=True))
            stack.enter_context(patch("server.conversation_transfer._scope", return_value=(
                None, data, self.destination, self.target_info)))
            stack.enter_context(patch("server.conversation_transfer._modes", return_value=self.modes))
            stack.enter_context(patch("config.model_profiles.get_registered_model_profiles",
                                     return_value={"synthetic": {}}))
            with app.test_client() as client:
                response = client.post("/api/conversations/import?workspace_id=target-menu", data={
                    "file": (self.archive(), "conversation.astrion.zip"),
                    "operation_id": "legacy-spooled-upload"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["data"]["workspace_id"], "target-menu")
        imported = self.destination.read_conversation(response.json["data"]["conversation_id"])
        self.assertEqual(imported["messages"][:-1], self.prefix)
