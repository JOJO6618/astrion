"""Exercise real legacy-layout methods on synthetic workspace files only."""
import ast
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_methods():
    tree = ast.parse((ROOT / 'modules/user_manager.py').read_text(encoding='utf-8'))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == 'UserManager')
    names = {'_migrate_legacy_docker_workspace_layout', '_move_dir_contents',
             '_looks_like_project_workspace_dir'}
    methods = [node for node in owner.body if isinstance(node, ast.FunctionDef)
               and node.name in names]
    for node in methods:
        node.decorator_list = []
    unit = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
    namespace = {'Path': Path, 'shutil': shutil}
    exec(compile(unit, 'modules/user_manager.py', 'exec'), namespace)
    return namespace


class LegacyLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='layout-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'synthetic'
        self.root.mkdir()
        methods = load_methods()
        self.manager = SimpleNamespace(workspace_root=Path(self.temp.name),
            RESERVED_WORKSPACE_IDS={'default', 'personal', 'shared'},
            _project_container_root=lambda username: self.root / 'projects',
            _personal_root=lambda username: self.root / 'personal',
            _looks_like_project_workspace_dir=methods['_looks_like_project_workspace_dir'])
        self.manager._move_dir_contents = lambda src, dst: methods['_move_dir_contents'](
            self.manager, src, dst)
        self.migrate = lambda: methods['_migrate_legacy_docker_workspace_layout'](
            self.manager, 'synthetic')
        self.marker = self.root / 'projects/default/.migrated_from_legacy'

    def mark_prepared(self):
        self.marker.parent.mkdir(parents=True)
        self.marker.write_text('ok', encoding='utf-8')

    def test_prepared_layout_does_not_scan_or_rewrite_directories(self):
        self.mark_prepared()
        with patch.object(Path, 'iterdir', side_effect=AssertionError('warm layout scanned')), \
             patch.object(Path, 'mkdir', side_effect=AssertionError('warm layout wrote')):
            self.migrate()
            self.migrate()
        self.assertEqual(self.marker.read_text(), 'ok')

    def test_new_legacy_data_is_not_hidden_by_an_existing_marker(self):
        self.mark_prepared()
        legacy = self.root / 'data'
        legacy.mkdir()
        (legacy / 'synthetic.json').write_text('preserve me', encoding='utf-8')
        self.migrate()
        self.assertEqual((self.root / 'projects/default/data/synthetic.json').read_text(), 'preserve me')
        self.assertFalse(legacy.exists())

    def test_first_migration_preserves_legacy_named_workspaces(self):
        legacy = self.root / 'project/other/project'
        legacy.mkdir(parents=True)
        (legacy / 'synthetic.txt').write_text('named workspace', encoding='utf-8')
        self.migrate()
        self.assertEqual((self.root / 'projects/other/project/synthetic.txt').read_text(), 'named workspace')
        self.assertTrue(self.marker.is_file())


if __name__ == '__main__':
    unittest.main()
