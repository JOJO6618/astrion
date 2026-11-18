"""回归：markdown 文件链接用了宿主绝对路径时的解析回退。

背景：前端把不以 ``//`` 开头、也没有 scheme 的 href 当作工作区相对路径并剥掉
开头的 ``/``（static/src/composables/useMarkdownRenderer.ts 的 extractWorkspacePath），
宿主绝对路径到达 /api/system/open-file 时已变成 ``Users/xxx/proj/a.html``。
本用例锁定 _resolve_workspace_file_for_open 的三类结果：
相对解释优先、剥斜杠后回退到绝对解释、工作区外一律拒绝。

运行：python3 -B -m unittest discover -s test/2026-11-18_文件链接绝对路径 -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.status.file_open import _resolve_workspace_file_for_open  # noqa: E402


class _FakeFileManager:
    """模拟 host 模式 _validate_path：相对路径拼工作区根，绝对路径直接 resolve。"""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def _validate_path(self, path: str):
        normalized = (path or "").strip()
        if not normalized:
            return True, "", self.workspace_root
        candidate = Path(normalized).expanduser()
        if candidate.is_absolute():
            return True, "", candidate.resolve()
        return True, "", (self.workspace_root / normalized).resolve()


class _FakeTerminal:
    def __init__(self, workspace_root: Path, project_root: Path | None = None) -> None:
        self.project_path = str(project_root or workspace_root)
        self.file_manager = _FakeFileManager(workspace_root)


class ResolveWorkspaceFileForOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        target = self.root / "cache" / "3" / "pelican-bike.html"
        target.parent.mkdir(parents=True)
        target.write_text("<html></html>", encoding="utf-8")
        self.target = target
        self.terminal = _FakeTerminal(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_relative_path_still_resolves_first(self) -> None:
        status, error, path = _resolve_workspace_file_for_open(
            self.terminal, "cache/3/pelican-bike.html"
        )
        self.assertEqual(status, "ok")
        self.assertEqual(error, "")
        self.assertEqual(path, self.target)

    def test_absolute_path_passed_through(self) -> None:
        status, _, path = _resolve_workspace_file_for_open(self.terminal, str(self.target))
        self.assertEqual(status, "ok")
        self.assertEqual(path, self.target)

    def test_stripped_absolute_path_falls_back(self) -> None:
        """前端剥掉前导 '/' 后的形式必须能回退到绝对解释。"""
        stripped = str(self.target).lstrip("/")
        self.assertFalse(stripped.startswith("/"))
        status, error, path = _resolve_workspace_file_for_open(self.terminal, stripped)
        self.assertEqual(status, "ok")
        self.assertEqual(error, "")
        self.assertEqual(path, self.target)

    def test_stripped_path_outside_workspace_is_rejected(self) -> None:
        outside = Path("/etc/hosts")
        status, _, _ = _resolve_workspace_file_for_open(self.terminal, str(outside).lstrip("/"))
        self.assertEqual(status, "invalid")

    def test_missing_relative_file_is_not_found(self) -> None:
        status, _, _ = _resolve_workspace_file_for_open(self.terminal, "cache/missing.html")
        self.assertEqual(status, "not_found")

    def test_parent_traversal_is_rejected(self) -> None:
        status, _, _ = _resolve_workspace_file_for_open(self.terminal, "../outside.txt")
        self.assertEqual(status, "invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
