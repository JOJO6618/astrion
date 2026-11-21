from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from modules.execution_plane.local import LocalExecutionBackend
from modules.file_manager import FileManager
from modules.project_memory import NOTICE_NAME, legacy_files, scan_index, tool_directory
from modules.project_memory_migration import WorkspaceMemories, clean_workspace, copy_workspace, verify_workspace
from modules.workspace_memory_context import owner_location


class MigrationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / 'project'
        self.data = self.root / 'data'
        self.legacy = self.project / '.astrion/memory'
        self.legacy.mkdir(parents=True)
        self.source = self.legacy / 'topic.md'
        self.source.write_bytes(b'---\nname: topic\ndescription: index text\n---\nold data\n')

    def workspace(self):
        return WorkspaceMemories('host', 'default', 'Test project', self.project, self.data,
                                 tuple(legacy_files(self.project)))

    def copy(self, workspace):
        return copy_workspace(workspace, lambda: None)

    def owner(self, container_session=None):
        manager = FileManager(str(self.project), data_dir=str(self.data))
        owner = SimpleNamespace(project_path=self.project, data_dir=self.data,
                                file_manager=manager, container_session=None)
        owner.execution_backend = LocalExecutionBackend(owner)
        # A legacy identity is attached only after the Host backend is bound, so
        # the test exercises the memory/tool guard rather than backend creation.
        owner.container_session = container_session
        return owner

    def test_host_switch_reads_only_external_topics_and_leaves_notice(self):
        owner = self.owner()
        workspace = self.workspace()
        self.assertEqual(Path(tool_directory(owner)), workspace.destination)
        self.assertEqual(scan_index(owner), [])
        os.utime(self.source, (1642435200, 1642435200))
        unrelated = self.legacy / 'keep.txt'
        unrelated.write_text('keep me')

        copies = self.copy(workspace)
        self.assertTrue(self.source.exists())
        verified = []
        verify_workspace(workspace, copies, lambda: verified.append(True))
        clean_workspace(workspace, copies)

        self.assertEqual(verified, [True])
        self.assertFalse(self.source.exists())
        self.assertEqual(copies[0].destination.stat().st_mtime, 1642435200)
        self.assertEqual(unrelated.read_text(), 'keep me')
        self.assertIn(str(workspace.destination), (self.legacy / NOTICE_NAME).read_text())
        self.assertEqual(legacy_files(self.project), [])
        self.assertEqual(scan_index(owner)[0]['description'], 'index text')

    def test_existing_filename_conflict_and_retries_preserve_both_copies(self):
        workspace = self.workspace()
        workspace.destination.mkdir(parents=True)
        current = workspace.destination / self.source.name
        current.write_text('keep the new topic')
        first = self.copy(workspace)
        second = self.copy(workspace)
        self.assertEqual(first, second)
        clean_workspace(workspace, second)
        self.assertEqual(current.read_text(), 'keep the new topic')
        self.assertEqual(len(list(workspace.destination.glob('*.md'))), 2)
        self.assertIn(b'old data', second[0].destination.read_bytes())

    def test_corrupt_destination_leaves_entire_workspace_for_retry(self):
        other = self.legacy / 'second.md'
        other.write_text('second')
        workspace = self.workspace()
        copies = self.copy(workspace)
        copies[-1].destination.write_text('changed')
        with self.assertRaisesRegex(OSError, '核验失败'):
            clean_workspace(workspace, copies)
        self.assertTrue(other.exists())
        self.assertTrue(self.source.exists())
        self.assertFalse((self.legacy / NOTICE_NAME).exists())

    def test_modified_source_and_added_source_are_preserved(self):
        workspace = self.workspace()
        copies = self.copy(workspace)
        self.source.write_text('updated')
        with self.assertRaisesRegex(OSError, '发生变化'):
            clean_workspace(workspace, copies)
        copies = self.copy(workspace)
        (self.legacy / 'later.md').write_text('new source')
        with self.assertRaisesRegex(OSError, '文件列表'):
            clean_workspace(workspace, copies)
        self.assertEqual(self.source.read_text(), 'updated')

    def test_link_in_source_cannot_read_or_delete_external_file(self):
        external = self.root / 'outside.md'
        external.write_text('outside secret')
        (self.legacy / 'link.md').symlink_to(external)
        with self.assertRaises(OSError):
            self.copy(self.workspace())
        self.assertEqual(external.read_text(), 'outside secret')
        self.assertTrue((self.legacy / 'link.md').is_symlink())

    def test_host_memory_never_reuses_legacy_in_repository_directory(self):
        workspace = self.workspace()
        self.assertEqual(workspace.destination.parent.parent, self.data / 'memory')
        self.assertNotEqual(workspace.destination.parent, self.legacy.parent)

    def test_legacy_container_identity_is_rejected_instead_of_served_host_path(self):
        owner = self.owner(SimpleNamespace(mode='docker'))
        with self.assertRaises(PermissionError):
            tool_directory(owner)
        with self.assertRaises(PermissionError):
            scan_index(owner)
        with self.assertRaises(RuntimeError):
            owner_location(owner)
        self.assertFalse((self.data / 'memory').exists())


if __name__ == '__main__':
    unittest.main()
