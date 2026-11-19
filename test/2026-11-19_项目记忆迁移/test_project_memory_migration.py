from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from modules.container_file_proxy import CONTAINER_FILE_SCRIPT
from modules.execution_plane.files import LocalFileSystem
from modules.execution_plane.local import LocalExecutionBackend
from modules.file_manager import FileManager
from modules.project_memory import NOTICE_NAME, docker_bind_directory, legacy_files, scan_index, tool_directory
from modules.project_memory_migration import WorkspaceMemories, clean_workspace, copy_workspace, verify_workspace
from modules.user_container_manager import ContainerHandle, UserContainerManager


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

    def workspace(self, docker=False):
        return WorkspaceMemories('test', 'default', 'Test project', self.project, self.data, docker,
                                 tuple(legacy_files(self.project)))

    def copy(self, workspace):
        return copy_workspace(workspace, lambda: None)

    def test_host_switch_reads_only_external_topics_and_leaves_notice(self):
        manager = FileManager(str(self.project), data_dir=str(self.data))
        owner = SimpleNamespace(project_path=self.project, data_dir=self.data, file_manager=manager)
        owner.execution_backend = LocalExecutionBackend(owner)
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

    def test_docker_old_path_is_selected_until_verified_cleanup(self):
        workspace = self.workspace(docker=True)
        self.assertIsNone(docker_bind_directory(str(self.project), str(self.data)))
        copies = self.copy(workspace)
        verify_workspace(workspace, copies, lambda: None)
        self.assertIsNone(docker_bind_directory(str(self.project), str(self.data)))
        clean_workspace(workspace, copies)
        self.assertEqual(docker_bind_directory(str(self.project), str(self.data)), workspace.destination)
        self.assertEqual(list(self.legacy.iterdir()), [])
        self.assertEqual((workspace.destination / '.gitignore').read_text(), '*\n')

    def test_real_container_file_script_reads_the_mount_view(self):
        workspace = self.workspace(docker=True)
        clean_workspace(workspace, self.copy(workspace))
        container_view = self.root / 'container-view'
        (container_view / '.astrion').mkdir(parents=True)
        (container_view / '.astrion/memory').mkdir()
        virtual_file = container_view / '.astrion/memory/topic.md'
        os.link(workspace.destination / 'topic.md', virtual_file)
        handle = ContainerHandle('test', 'docker', str(self.project), '/custom/ws', 'sandbox',
                                 topic_memory_directory=str(workspace.destination))
        file_manager = FileManager(str(self.project), container_session=handle, data_dir=str(self.data))
        self.assertEqual(file_manager._quota_file_size(self.source), virtual_file.stat().st_size)
        self.assertGreaterEqual(file_manager._get_project_size(), virtual_file.stat().st_size)
        backend = LocalFileSystem(file_manager)
        namespace = {'__name__': 'test'}
        exec(CONTAINER_FILE_SCRIPT, namespace)

        def invoke(action, payload):
            return namespace['HANDLERS'][action](container_view, payload)

        with patch.object(file_manager, '_container_call', side_effect=invoke):
            relative = '.astrion/memory/topic.md'
            self.assertIn('old data', backend.read_file(relative)['content'])
            self.assertIn('old data', backend.read_text_segment('/custom/ws/' + relative)['content'])
            result = backend.container_project_memory_index('/custom/ws/.astrion/memory', NOTICE_NAME)
            self.assertEqual(result['entries'][0]['description'], 'index text')
            self.assertTrue(backend.write_file(relative, 'new value')['success'])
        self.assertEqual(virtual_file.read_text(), 'new value')
        self.assertFalse(self.source.exists())

    def test_container_mount_uses_configured_workspace_path(self):
        from threading import Lock

        workspace = self.workspace(docker=True)
        clean_workspace(workspace, self.copy(workspace))
        manager = UserContainerManager.__new__(UserContainerManager)
        manager.sandbox_mode = 'docker'
        manager._lock = Lock()
        manager._containers = {}
        manager.mount_path = '/custom/ws'
        manager.sandbox_bin = 'docker'
        manager.max_containers = 0
        manager.image = 'local-image'
        manager.network = 'none'
        manager.cpus = manager.memory = manager.pids_limit = None
        manager.binds = []
        manager.extra_env = {}
        manager._project_label_key = manager._project_label_value = 'test'
        manager._kind_label_key = manager._kind_label_value = 'test'
        with patch.object(manager, '_assert_docker_runtime_ready'), \
                patch.object(manager, '_build_container_name', return_value='sandbox'), \
                patch.object(manager, '_kill_container'), \
                patch('modules.user_container_manager.shutil.which', return_value='docker'), \
                patch('modules.user_container_manager.subprocess.run', return_value=SimpleNamespace(
                    returncode=0, stdout='sandbox-id', stderr='')) as docker:
            handle = manager.ensure_container('test', str(self.project), data_dir=str(self.data))
        self.assertEqual(handle.topic_memory_directory, str(workspace.destination))
        self.assertIn(f'{workspace.destination}:/custom/ws/.astrion/memory', docker.call_args.args[0])
        self.assertIn(f'{workspace.destination.parent}:/custom/ws/.astrion/astrion-memory', docker.call_args.args[0])
