"""离线迁移：预览、原始备份、冲突与失败回滚。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/migrate_multi_agent_conversations.py"
spec = importlib.util.spec_from_file_location("conversation_merge", SCRIPT)
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "old"
        self.target = self.root / "main"
        self.backup = self.root / "backup"
        self.original = self.add(self.source, "workspace", "conv_old", True)
        self.existing = self.add(self.target, "workspace", "conv_main", False)

    def add(self, tree, workspace, identifier, mode):
        path = tree / workspace / (identifier + ".json")
        data = {"id": identifier, "title": identifier, "messages": [{"role": "user", "content": "原文"}],
                "metadata": {"multi_agent_mode": mode, "model_key": "test", "frozen_main_system_prompt_multi_agent": "old"}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False))
        index = path.parent / "index.json"
        entries = json.loads(index.read_text()) if index.exists() else {}
        entries[identifier] = {"title": identifier}
        index.write_text(json.dumps(entries))
        return path.read_bytes()

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for tree in (self.source, self.target)
                for p in tree.rglob("*.json")}

    def test_preview_is_read_only(self):
        before = self.snapshot()
        result = merge.migrate(self.source, self.target)
        self.assertEqual(result["conversation_count"], 1)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())

    def test_apply_preserves_history_and_backup(self):
        result = merge.migrate(self.source, self.target, apply=True, backup_dir=self.backup)
        self.assertEqual(result["conversation_count"], 1)
        target = self.target / "workspace/conv_old.json"
        data = json.loads(target.read_text())
        self.assertEqual(data["messages"], json.loads(self.original)["messages"])
        self.assertNotIn("multi_agent_mode", data["metadata"])
        self.assertNotIn("frozen_main_system_prompt_multi_agent", data["metadata"])
        self.assertEqual((self.backup / "conversations/workspace/conv_old.json").read_bytes(), self.original)
        self.assertEqual((self.target / "workspace/conv_main.json").read_bytes(), self.existing)
        self.assertEqual(set(json.loads((target.parent / "index.json").read_text())), {"conv_old", "conv_main"})
        self.assertEqual(json.loads((self.source / "workspace/index.json").read_text()), {})

    def test_collision_in_other_workspace_rejects_whole_batch(self):
        self.add(self.target, "other", "conv_old", False)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            merge.migrate(self.source, self.target, apply=True, backup_dir=self.backup)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())

    def test_failure_restores_files_and_indexes(self):
        before = self.snapshot()
        real = merge.atomic_write
        raised = False

        def fail_index(path, raw):
            nonlocal raised
            if path == self.target / "workspace/index.json" and not raised:
                raised = True
                raise OSError("injected index failure")
            real(path, raw)

        with patch.object(merge, "atomic_write", side_effect=fail_index):
            with self.assertRaises(OSError):
                merge.migrate(self.source, self.target, apply=True, backup_dir=self.backup)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue((self.backup / "manifest.json").exists())

    def test_symlink_rejected(self):
        (self.source / "link").symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(ValueError):
            merge.migrate(self.source, self.target)


if __name__ == "__main__":
    unittest.main()
