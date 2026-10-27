"""Offline transfer tests: lossless history, media rebuild and hostile archives."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from modules.conversation_transfer.archive import export_archive, read_archive
from modules.conversation_transfer.assets import collect, mapped_path, tool_path
from modules.conversation_transfer.service import import_document, read_document, retire_source
from utils.conversation_manager import ConversationManager
from utils.media_store import MediaStore


class TransferFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "original"
        self.target = self.root / "new-machine"
        self.source.mkdir()
        self.target.mkdir()
        catalog = self.root / "workspaces.json"
        catalog.write_text(json.dumps({"workspaces": [
            {"workspace_id": "a", "path": str(self.source)},
            {"workspace_id": "b", "path": str(self.target)},
        ]}))
        self.patch = patch("utils.conversation_manager.path_mixin.HOST_WORKSPACES_FILE", str(catalog))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.manager = ConversationManager(str(self.root / "data-a"), str(self.source))
        self.destination = ConversationManager(str(self.root / "data-b"), str(self.target))
        self.media = MediaStore(self.manager.base_dir)
        image = self.media.store_bytes(b"original-image-bytes", kind="image", mime_type="image/png",
                                       source_path=".astrion/user_upload/photo.png")
        video = self.media.store_bytes(b"read-video-bytes", kind="video", mime_type="video/mp4",
                                       source_path="movies/movie.mp4")
        self.prefix = [
            {"role": "user", "message_id": "u1", "timestamp": "original", "content": "inspect", "images": [image["source_path"]],
             "media_refs": [image], "metadata": {"files": [".astrion/user_upload/private.pdf"]}},
            {"role": "assistant", "message_id": "a1", "content": "", "reasoning_content": "原始思考",
             "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "view_video", "arguments": '{"path":"movies/movie.mp4"}'}}],
             "metadata": {"responses_reasoning_items": [{"encrypted_content": "do-not-change"}]}},
            {"role": "tool", "message_id": "t1", "content": "viewed", "tool_call_id": "call-1", "name": "view_video", "media_refs": [video]},
            {"role": "assistant", "message_id": "a2", "content": "done"},
        ]
        self.cid = self.manager.create_conversation(str(self.source), initial_messages=deepcopy(self.prefix),
            model_key="synthetic", metadata_overrides={
                "frozen_main_system_prompt": "original-system", "frozen_workspace_prompt": "old workspace",
                "frozen_permission_prompt": "old permissions", "title_locked": True,
                "permission_mode": "unrestricted", "execution_mode": "direct"})
        self.document = read_document(self.manager, self.cid)
        self.source_info = {"workspace_id": "a", "workspace_name": "Original", "project_path": str(self.source), "environment": "host"}
        self.target_info = {"workspace_id": "b", "workspace_name": "New", "project_path": "/workspace", "environment": "docker"}
        self.modes = {"work_mode": "ask", "permission_mode": "approval", "execution_mode": "sandbox", "network_permission": "restricted"}

    def archive(self):
        return export_archive(self.document, self.manager.base_dir, self.source, self.source_info)

    def import_archive(self, archive=None, operation="import-one"):
        doc, manifest, files = read_archive(archive or self.archive())
        return import_document(doc, manifest, files, self.destination, project=self.target,
            target=self.target_info, modes=self.modes, operation_id=operation)


class TransferTests(TransferFixture):
    def test_export_is_zip_and_preserves_raw_document(self):
        with ZipFile(self.archive()) as archive:
            self.assertIn("conversation.json", archive.namelist())
            self.assertEqual(json.loads(archive.read("conversation.json")), self.document)
            self.assertFalse(any("private.pdf" in name for name in archive.namelist()))
        _, manifest, files = read_archive(self.archive())
        self.assertEqual({r["kind"] for r in manifest["resources"]}, {"image", "video"})
        self.assertEqual(len(files), 2)

    def test_new_machine_restores_media_and_original_prefix(self):
        result = self.import_archive()
        self.assertEqual(result["messages"][:-1], self.prefix)
        self.assertEqual(result["messages"][-1]["role"], "user")
        self.assertFalse(result["messages"][-1]["metadata"]["starts_work"])
        self.assertIn("Original", result["messages"][-1]["content"])
        self.assertIn("New", result["messages"][-1]["content"])
        for name, value in self.document["metadata"].items():
            if name.startswith("frozen_"):
                self.assertEqual(result["metadata"][name], value)
        self.assertEqual(result["metadata"]["project_path"], str(self.target))
        self.assertEqual(result["metadata"]["permission_mode"], "approval")
        self.assertEqual(read_document(self.manager, self.cid), self.document)
        original_store = self.media
        moved_store = MediaStore(self.destination.base_dir)
        from utils.context_manager.media_mixin import MediaMixin
        context = SimpleNamespace(media_store=moved_store, project_path=self.target,
                                  conversation_metadata=result["metadata"])
        for message in self.prefix:
            for ref in message.get("media_refs", []):
                self.assertEqual(original_store.to_data_url(ref), moved_store.to_data_url(ref))
                payload = MediaMixin._build_content_with_images(context, "same text", [], media_refs=[ref])
                self.assertIn(moved_store.to_data_url(ref), str(payload))

    def test_retries_do_not_append_another_notice(self):
        first = self.import_archive()
        second = self.import_archive()
        self.assertEqual(first, second)
        self.assertEqual(len(list(self.destination.conversations_dir.glob("conv_*.json"))), 1)

    def test_shared_media_migration_does_not_read_or_copy_blobs(self):
        same_store_target = ConversationManager(str(self.manager.base_dir), str(self.target))
        with patch.object(MediaStore, "load_bytes_by_media_id", side_effect=AssertionError("unnecessary copy")):
            resources, files = collect(self.document, self.manager.base_dir, self.source, shared_media=True)
        self.assertEqual(resources, [])
        self.assertEqual(files, {})
        result = import_document(self.document, {"source": self.source_info, "resources": [],
            "document_sha256": "same", "shared_media": True}, files, same_store_target,
            project=self.target, target=self.target_info, modes=self.modes, preserve_id=True)
        self.assertEqual(result["id"], self.cid)
        self.assertEqual(result["messages"][:-1], self.prefix)
        self.assertFalse((self.target / ".astrion/conversation_assets").exists())
        retire_source(self.manager, self.document, {"conversation_id": result["id"], "workspace_id": "b"})
        self.assertIsNone(self.manager.read_conversation(self.cid))
        self.assertIsNotNone(same_store_target.read_conversation(self.cid))
        self.assertEqual(len(list((self.manager.base_dir / "conversation_transfer_backups").glob("*.json"))), 1)

    def test_summary_handoff_and_second_export_keep_original_paths(self):
        compact = self.source / ".astrion/compact_result/compact_1.md"
        compact.parent.mkdir(parents=True)
        compact.write_text("summary", encoding="utf-8")
        handoff = self.source / ".astrion/handoff/handoff_one"
        handoff.mkdir(parents=True)
        (handoff / "summary.md").write_text("handoff summary", encoding="utf-8")
        (handoff / "conversation.md").write_text("conversation", encoding="utf-8")
        (handoff / "manifest.json").write_text('{"snapshot":"one"}', encoding="utf-8")
        (handoff / "media").mkdir()
        (handoff / "media/image.png").write_bytes(b"handoff-sibling-image")
        self.document["messages"][-1]["content"] = "read `.astrion/compact_result/compact_1.md`\nand `.astrion/handoff/handoff_one/summary.md`"
        original = deepcopy(self.document["messages"])
        result = self.import_archive()
        metadata = result["metadata"]
        for relative in (".astrion/compact_result/compact_1.md", ".astrion/handoff/handoff_one/summary.md"):
            current = Path(mapped_path(metadata, self.target, relative))
            self.assertTrue(current.is_file())
            self.assertTrue(tool_path(metadata, self.target, relative).startswith(".astrion/conversation_assets/"))
        self.assertEqual(result["messages"][:-1], original)
        again = export_archive(result, self.destination.base_dir, self.target, self.target_info)
        doc, manifest, files = read_archive(again)
        third = self.root / "third"
        third.mkdir()
        third_manager = ConversationManager(str(self.root / "data-c"), str(third))
        imported = import_document(doc, manifest, files, third_manager, project=third,
            target=self.target_info, modes=self.modes)
        self.assertEqual(imported["messages"][:-1], result["messages"])
        self.assertEqual(Path(mapped_path(imported["metadata"], third, ".astrion/compact_result/compact_1.md")).read_text(), "summary")
        for relative in ("manifest.json", "media/image.png", "conversation.md", "summary.md"):
            old = f".astrion/handoff/handoff_one/{relative}"
            restored = Path(mapped_path(imported["metadata"], third, old))
            self.assertEqual(restored.read_bytes(), (handoff / relative).read_bytes())

    def test_legacy_media_works_without_rewriting_paths(self):
        relative = ".astrion/user_upload/legacy.png"
        path = self.source / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b"legacy-image")
        self.document["messages"][0].pop("media_refs")
        self.document["messages"][0]["images"] = [relative]
        result = self.import_archive()
        self.assertEqual(result["messages"][0]["images"], [relative])
        self.assertEqual(Path(mapped_path(result["metadata"], self.target, relative)).read_bytes(), b"legacy-image")

    def test_missing_media_rejects_export(self):
        with patch.object(MediaStore, "load_bytes_by_media_id", return_value=None):
            with self.assertRaises(FileNotFoundError):
                self.archive()

    def test_corrupt_archive_rejected_before_destination_write(self):
        source = self.archive()
        tampered = BytesIO()
        with ZipFile(source) as original, ZipFile(tampered, "w") as changed:
            for name in original.namelist():
                changed.writestr(name, b"tampered" if name.startswith("assets/") else original.read(name))
        tampered.seek(0)
        with self.assertRaises(ValueError):
            self.import_archive(tampered)
        self.assertEqual(list(self.destination.conversations_dir.glob("conv_*.json")), [])

    def test_zip_traversal_and_extra_entries_rejected(self):
        source = self.archive()
        bad = BytesIO()
        with ZipFile(source) as original, ZipFile(bad, "w") as changed:
            for name in original.namelist():
                changed.writestr(name, original.read(name))
            changed.writestr("../outside", b"bad")
        bad.seek(0)
        with self.assertRaises(ValueError):
            read_archive(bad)

    def test_target_symlink_cannot_redirect_asset_write(self):
        (self.target / ".astrion").symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.import_archive()
        self.assertEqual(list(self.destination.conversations_dir.glob("conv_*.json")), [])


if __name__ == "__main__":
    unittest.main()
