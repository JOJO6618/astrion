"""Isolated Flask adapter tests: no execution resources or real credentials."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flask import Flask
from PIL import Image
from modules.chat_mode.storage import ChatStorage
from server.chat_mode import chat_mode_bp
from server.chat_mode_scope import ChatScope
from server.runtime.context import TrustedPrincipal


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = ChatScope("synthetic", self.root / "chat", self.root)
        self.storage = ChatStorage(self.scope.root)
        self.cid = self.storage.create(model_key="synthetic")
        self.identity = TrustedPrincipal("synthetic", None, conversation_kind="chat")
        self.app = Flask(__name__)
        self.app.secret_key = "synthetic-test-secret"
        self.app.register_blueprint(chat_mode_bp)
        self.client = self.app.test_client()
        for item in [patch("server.auth_helpers.is_logged_in", return_value=True),
                     patch("server.chat_mode.context.principal", return_value=self.identity),
                     patch("server.chat_mode.context.scope_for_principal", return_value=self.scope),
                     patch("server.context.get_user_resources", side_effect=AssertionError("Agent resource provisioned"))]:
            item.start(); self.addCleanup(item.stop)

    def upload(self):
        stream = BytesIO()
        Image.new("RGB", (3, 3)).save(stream, format="PNG")
        return self.client.post("/api/chat/media", data={"media": (BytesIO(stream.getvalue()), "image.png")})

    def test_local_media_uses_stable_refs_and_account_isolation(self):
        result = self.upload()
        self.assertEqual(result.status_code, 200, result.json)
        ref = result.json["data"]
        self.assertNotIn("store_path", ref)
        self.assertNotIn("source_path", ref)
        self.assertEqual(self.client.get(f'/api/chat/media/{ref["media_id"]}').status_code, 200)
        other = ChatScope("other", self.root / "other", self.root / "other-prefs")
        with patch("server.chat_mode.context.scope_for_principal", return_value=other):
            self.assertEqual(self.client.get(f'/api/chat/media/{ref["media_id"]}').status_code, 404)

    def test_regular_file_and_forged_type_are_rejected(self):
        response = self.client.post("/api/chat/media", data={"media": (BytesIO(b"fake PDF"), "image.png", "image/png")})
        self.assertEqual(response.status_code, 400)
        for images in [[{"source_path": "/etc/passwd"}], ["data:image/png;base64,AA=="]]:
            response = self.client.post("/api/chat/tasks", json={"message": "hi", "operation_id": "send", "images": images})
            self.assertIn(response.status_code, {400, 404})

    def test_branch_bootstrap_edit_and_network_have_no_workspace(self):
        turn = self.storage.begin(self.cid, "first", message={"role": "user", "content": "hello", "message_id": "u1"})["value"]["id"]
        self.storage.append(self.cid, turn, {"role": "assistant", "content": "answer", "message_id": "a1"})
        self.storage.finish(self.cid, turn)
        url = f"/api/chat/conversations/{self.cid}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["data"]["document"]["metadata"]["conversation_kind"], "chat")
        self.assertEqual(self.client.post(url + "/edit", json={"operation_id": "edit", "message_id": "u1", "content": "edited"}).status_code, 200)
        self.assertEqual(len(self.storage.read(self.cid)["messages"]), 2)
        self.assertEqual(self.client.post(url + "/network", json={"operation_id": "off", "enabled": False}).status_code, 200)
        self.assertFalse(self.storage.read(self.cid)["metadata"]["network_enabled"])

    def test_draft_cas_and_no_files(self):
        url = "/api/chat/drafts/new"
        response = self.client.put(url, json={"text": "draft", "media": [], "client_id": "window", "sequence": 1, "base_revision": 0})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["data"]["revision"], 1)
        self.assertEqual(self.client.get(url).json["data"]["text"], "draft")
        response = self.client.put(url, json={"text": "stale", "media": [], "client_id": "window2", "sequence": 1, "base_revision": 0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(url).json["data"]["text"], "draft")
        self.assertEqual(self.client.post("/api/chat/tasks", json={"message": "hi", "files": ["a.pdf"]}).status_code, 400)

    def test_unauthenticated_chat_has_no_access(self):
        with patch("server.auth_helpers.is_logged_in", return_value=False):
            self.assertEqual(self.client.get("/api/chat/conversations").status_code, 401)
            self.assertEqual(self.client.get(f"/api/chat/conversations/{self.cid}").status_code, 401)


if __name__ == "__main__":
    unittest.main()
