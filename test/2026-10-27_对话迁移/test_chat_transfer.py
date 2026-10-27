"""Full Chat trees and completed handoff materials survive portable archives."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.tree import MessageTree
from modules.conversation_transfer.archive import export_archive, read_archive
from modules.conversation_transfer.assets import mapped_path
from modules.conversation_transfer.service import import_document
from server.conversation_transfer_support import chat_materials
from server.runtime import TrustedPrincipal
from test_transfer import TransferFixture
from utils.media_store import MediaStore


class ChatTransferTests(TransferFixture):
    def make_chat(self):
        storage = ChatStorage(self.root / "chat-source")
        cid = storage.create(model_key="synthetic")
        image = MediaStore(storage.root).store_bytes(b"chat-image", kind="image", mime_type="image/png")
        turn = storage.begin(cid, "first", message={"role": "user", "message_id": "u1",
            "content": "first", "media_refs": [image]})["value"]["id"]
        storage.append(cid, turn, {"role": "assistant", "message_id": "a1", "content": "first branch"})
        storage.finish(cid, turn)
        turn = storage.begin(cid, "branch", parent_id="u1", message={"role": "user",
            "message_id": "u2", "content": "second branch"})["value"]["id"]
        storage.append(cid, turn, {"role": "assistant", "message_id": "a2", "content": "latest"})
        storage.finish(cid, turn)
        return storage, cid

    def test_tree_branches_media_and_handoff_reexport(self):
        storage, cid = self.make_chat()
        directory = self.source / ".astrion/handoff/handoff_chat"
        (directory / "media").mkdir(parents=True)
        for name, data in {"summary.md": b"summary", "conversation.md": b"[image](media/pic.png)",
                           "manifest.json": b"{}", "media/pic.png": b"picture"}.items():
            (directory / name).write_bytes(data)
        def materials(document):
            document["metadata"]["chat"]["handoffs"] = {"a2": {"status": "completed",
                "workspace_id": "a", "materials": ".astrion/handoff/handoff_chat"}}
        storage.manager.mutate_conversation(cid, materials)
        document = storage.read(cid)
        before = deepcopy(document)
        principal = TrustedPrincipal("synthetic", None, host_mode=True, conversation_kind="chat")
        with patch("server.chat_mode.handoff_workspace.projects", return_value=[{"id": "a"}]), \
             patch("server.context.workspace.resolve_workspace", return_value=SimpleNamespace(project_path=self.source)) as resolve:
            rows, payloads = chat_materials(document, principal, storage.root)
        self.assertFalse(resolve.call_args.kwargs["prepare"])
        archive = export_archive(document, storage.root, None, {"environment": "chat"},
                                 extra_resources=rows, extra_files=payloads)
        doc, manifest, files = read_archive(archive)
        target = ChatStorage(self.root / "chat-target")
        imported = import_document(doc, manifest, files, target.manager, project=None,
                                   target={"environment": "chat"}, modes={}, operation_id="chat-import")
        self.assertEqual(document, before)
        self.assertEqual(imported["messages"][:-1], before["messages"])
        tree = MessageTree(imported)
        self.assertEqual(tree.path("a1", latest=False), MessageTree(before).path("a1", latest=False))
        self.assertEqual(tree.path()[-1]["metadata"]["chat"]["parent_id"], "a2")
        states = list(tree.state["handoffs"].values())
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["status"], "completed")
        self.assertFalse(states[0]["countdown_enabled"])
        ref = imported["messages"][0]["media_refs"][0]
        self.assertEqual(MediaStore(target.root).load_bytes(ref), b"chat-image")
        for name in ("summary.md", "conversation.md", "manifest.json", "media/pic.png"):
            old = f".astrion/handoff/handoff_chat/{name}"
            self.assertEqual(Path(mapped_path(imported["metadata"], target.root, old)).read_bytes(),
                             (directory / name).read_bytes())
        # Decisions remain historical; live worker ownership is removed. Materials
        # are discoverable through aliases when this imported Chat is exported again.
        second = export_archive(imported, target.root, None, {"environment": "chat"})
        _, again, _ = read_archive(second)
        self.assertTrue(any(".astrion/handoff/handoff_chat/media/pic.png" in row["paths"]
                            for row in again["resources"]))

    def test_missing_handoff_workspace_is_explicit(self):
        storage, cid = self.make_chat()
        document = storage.read(cid)
        document["metadata"]["chat"]["handoffs"] = {"a2": {
            "materials": ".astrion/handoff/handoff_chat", "workspace_id": "missing"}}
        with patch("server.chat_mode.handoff_workspace.projects", return_value=[]):
            with self.assertRaises(FileNotFoundError):
                chat_materials(document, TrustedPrincipal("synthetic", None, conversation_kind="chat"), storage.root)
