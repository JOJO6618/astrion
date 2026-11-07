"""执行侧浅版本（Docker/远端）与 `track_file_edit` 契约测试。

从 ``test_local.py`` 拆出（两文件都保持 <500 行）：
- ``ExecutorFilesystemVersioningTest``：`ShallowVersioningManager(filesystem=...)`
  的读源/写回/删除/失败语义；
- ``BackendTrackFileEditTest``：``LocalExecutionBackend.track_file_edit`` 的行为
  （执行侧取字节、新文件记 None 版本、权限与身份缺失时跳过）。

隔离要求与 ``test_local.py`` 一致：独立进程 + 临时 ASTRION_DATA_ROOT，且在
import 任何业务模块之前注入。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="astrion_versioning_backend_"))

os.environ["ASTRION_IGNORE_DOTENV"] = "1"
os.environ["ASTRION_DATA_ROOT"] = str(_TMP_ROOT / "root")
os.environ["DATA_DIR"] = str(_TMP_ROOT / "root" / "data")
os.environ["LOGS_DIR"] = str(_TMP_ROOT / "root" / "logs")
os.environ["DEPLOY_CONFIG_DIR"] = str(_TMP_ROOT / "root" / "config")
os.environ["TERMINAL_SANDBOX_MODE"] = "host"
for _key in list(os.environ):
    if _key.startswith("HOST_SANDBOX_") and _key != "HOST_SANDBOX_ENABLED":
        os.environ.pop(_key, None)

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from modules.execution_plane.local import LocalExecutionBackend  # noqa: E402
from modules.execution_plane.resources import create_local_resources  # noqa: E402
from modules.shallow_versioning import (  # noqa: E402
    ShallowVersioningError,
    ShallowVersioningManager,
)


def _backup_dir(data_dir: Path, conversation_id: str) -> Path:
    return Path(data_dir) / "save" / conversation_id / "shallow_backups"


class _FakeExecutorFilesystem:
    """模拟 Docker：文件内容只在“执行侧”，控制端宿主路径不存在。

    只实现公共层协议里浅版本用到的面：inspect_path / resolve_path /
    read_binary / write_binary / delete_file。``resolve_path=None`` 可关闭路径
    归一化，用来验证纯词法回退。
    """

    def __init__(self, files=None, *, with_resolve=True):
        self._files = dict(files or {})
        self.reads = []
        self.writes = []
        self.deletes = []
        self._with_resolve = with_resolve

    def inspect_path(self, path, access="read"):
        exists = path in self._files
        return {"success": True, "path": path, "exists": exists, "is_file": exists}

    def resolve_path(self, path, access="read"):
        if not self._with_resolve:
            raise AttributeError("resolve_path disabled in this fake")
        rel = path[2:] if path.startswith("./") else path
        if rel.startswith("/workspace/"):
            # 与容器代理一致：容器内绝对路径归一为工作区相对路径
            rel = rel.split("/workspace/", 1)[1]
        return {
            "success": True,
            "path": rel,
            "relative_path": rel,
            "within_workspace": True,
            "access": access,
        }

    def read_binary(self, path):
        self.reads.append(path)
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    def write_binary(self, path, data):
        self.writes.append((path, bytes(data)))
        self._files[path] = bytes(data)
        return {"success": True, "path": path, "size": len(data)}

    def delete_file(self, path):
        self.deletes.append(path)
        existed = self._files.pop(path, None) is not None
        return {"success": True, "path": path, "deleted": existed}


class _TextOnlyExecutorFilesystem(_FakeExecutorFilesystem):
    """没有 write_binary 的后端：回滚必须显式失败，不能偷偷走文本通道。"""

    write_binary = None


class ExecutorFilesystemVersioningTest(unittest.TestCase):
    """浅版本必须在执行侧读、在控制端存、经执行侧恢复（Docker 语义）。"""

    def setUp(self):
        self._root = Path(tempfile.mkdtemp(prefix="astrion_shallow_exec_"))
        self.project = self._root / "ws"
        self.project.mkdir(parents=True, exist_ok=True)
        self.data = self._root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.fs = _FakeExecutorFilesystem({"a.txt": b"v1-content\n"})

    def _manager(self, conversation_id="conv_fs"):
        return ShallowVersioningManager(
            project_path=self.project,
            data_dir=self.data,
            conversation_id=conversation_id,
            filesystem=self.fs,
        )

    # ---- 读源 ----

    def test_backup_reads_executor_not_controller_disk(self):
        manager = self._manager()
        manager.track_edit_content("a.txt", "m1", self.fs.read_binary("a.txt"))

        backups = list(_backup_dir(self.data, "conv_fs").glob("*@v1"))
        self.assertEqual(len(backups), 1, backups)
        self.assertEqual(backups[0].read_bytes(), b"v1-content\n")
        # 控制端宿主目录里绝不应该出现/被要求存在这个文件
        self.assertFalse((self.project / "a.txt").exists())
        self.assertEqual(self.fs.reads, ["a.txt"])

    def test_read_failure_is_not_treated_as_missing(self):
        """执行侧读失败必须报错，绝不写一个“文件不存在”的假备份。"""

        class _Denied(_FakeExecutorFilesystem):
            def read_binary(self, path):
                raise PermissionError("denied by executor policy")

        self.fs = _Denied({"a.txt": b"x\n"})
        manager = self._manager("conv_denied")
        with self.assertRaises(ShallowVersioningError):
            manager.track_edit("a.txt", "m1")
        self.assertFalse(_backup_dir(self.data, "conv_denied").exists())

    # ---- 写回 ----

    def test_rewind_writes_back_through_executor(self):
        """回滚到 m1 的消息末状态，必须经执行侧写回，不得向宿主镜像复制。"""
        manager = self._manager()
        manager.track_edit_content("a.txt", "m1", self.fs.read_binary("a.txt"))
        self.fs._files["a.txt"] = b"v1-final\n"
        manager.make_snapshot("m1")

        # 后续消息又改了文件（尚未快照），回滚到 m1 应恢复 m1 末状态
        self.fs._files["a.txt"] = b"v2-later\n"

        result = manager.rewind("m1")
        self.assertTrue(result["success"], result)
        self.assertIn("a.txt", result["files_changed"])
        self.assertEqual(self.fs._files["a.txt"], b"v1-final\n")
        self.assertEqual(self.fs.writes, [("a.txt", b"v1-final\n")])
        self.assertFalse((self.project / "a.txt").exists(), "不得向宿主镜像复制")

    def test_rewind_deletes_file_created_by_message(self):
        """消息新建的文件，回滚到之前的消息必须经执行侧删除。"""
        manager = self._manager("conv_fs_new")
        manager.track_edit_content("new.txt", "m1", None)  # 执行侧确认不存在
        manager.make_snapshot("m1")

        self.fs._files["new.txt"] = b"created\n"
        manager.make_snapshot("m2")
        self.assertIn("new.txt", self.fs._files)

        manager.rewind("m1")
        self.assertNotIn("new.txt", self.fs._files)
        self.assertIn("new.txt", self.fs.deletes)

    def test_rewind_fails_loudly_without_binary_writer(self):
        """缺 write_binary：必须显式失败，不得降级成文本写入。"""
        self.fs = _TextOnlyExecutorFilesystem({"a.txt": b"v1\n"})
        manager = self._manager("conv_no_binary")
        manager.track_edit_content("a.txt", "m1", b"v1\n")
        manager.make_snapshot("m1")
        self.fs._files["a.txt"] = b"v2\n"

        with self.assertRaises(ShallowVersioningError):
            manager.rewind("m1")
        # 内容没有被“文本通道”改写，也没有写到宿主镜像
        self.assertEqual(self.fs._files["a.txt"], b"v2\n")
        self.assertFalse((self.project / "a.txt").exists())

    # ---- 路径归一化：不得触碰控制端磁盘 ----

    def test_paths_are_lexical_and_executor_authoritative(self):
        (self.project / "link.txt").symlink_to(self._root / "outside.txt")
        self.fs._files["link.txt"] = b"x\n"
        manager = self._manager("conv_paths")

        # 纯词法拼接：不跟随控制端符号链接，也不要求文件在控制端存在
        lexical = manager._resolve_file_path("link.txt")
        self.assertEqual(lexical, Path(os.path.normpath(str(self.project / "link.txt"))))
        self.assertNotEqual(str(lexical), str((self._root / "outside.txt").resolve()))

        # 执行端绝对路径（容器内 /workspace/...）→ 工作区相对跟踪键
        self.assertEqual(
            manager._normalize_via_filesystem("/workspace/sub/x.txt"), "sub/x.txt"
        )

    def test_normalize_falls_back_to_lexical_without_resolve_path(self):
        self.fs = _FakeExecutorFilesystem({"a.txt": b"x\n"}, with_resolve=False)
        manager = self._manager("conv_lexical")
        self.assertEqual(manager._normalize_path("sub/./a.txt"), "sub/a.txt")
        self.assertEqual(
            manager._normalize_path(str(self.project / "sub" / "a.txt")), "sub/a.txt"
        )

    def test_project_root_is_lexical_when_filesystem_injected(self):
        """filesystem 注入时 project root 不得 resolve（/var→/private/var 类污染）。"""
        real_ws = self._root / "real_ws"
        real_ws.mkdir(exist_ok=True)
        link_ws = self._root / "link_ws"
        link_ws.symlink_to(real_ws, target_is_directory=True)

        manager = ShallowVersioningManager(
            project_path=str(link_ws),
            data_dir=str(self.data),
            conversation_id="conv_lexical_root",
            filesystem=self.fs,
        )
        self.assertEqual(manager.project_path, Path(os.path.abspath(str(link_ws))))
        self.assertNotEqual(
            manager.project_path, link_ws.resolve(), "不得跟随控制端符号链接"
        )
        # 控制端备份目录仍真实 resolve（它是控制器本地存储）
        self.assertTrue(str(manager.save_root).startswith(str(self.data.resolve())))


class BackendTrackFileEditTest(unittest.TestCase):
    """`LocalExecutionBackend.track_file_edit`：执行侧取字节 + 撤销创建。"""

    @classmethod
    def setUpClass(cls):
        cls.workspace = _TMP_ROOT / "ws_track"
        cls.workspace.mkdir(parents=True, exist_ok=True)
        cls.data_dir = _TMP_ROOT / "root" / "data"
        cls.data_dir.mkdir(parents=True, exist_ok=True)
        cls.resources = create_local_resources(
            project_path=str(cls.workspace), data_dir=str(cls.data_dir)
        )
        cls.backend = LocalExecutionBackend(cls.resources)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.resources.close()
        except Exception:
            pass

    def test_track_file_edit_records_shallow_backup(self):
        (self.workspace / "tracked.txt").write_text("before\n", encoding="utf-8")
        conversation_id = "conv_track_smoke"
        message_id = "msg_track_1"
        self.backend.track_file_edit(
            "tracked.txt", conversation_id=conversation_id, message_id=message_id
        )
        state_file = _backup_dir(self.data_dir, conversation_id) / "state.jsonl"
        self.assertTrue(state_file.exists(), f"应在执行端生成浅版本记录: {state_file}")
        payload = state_file.read_text(encoding="utf-8")
        self.assertIn("tracked.txt", payload)
        self.assertIn(message_id, payload)

    def test_track_file_edit_uses_executor_bytes(self):
        """必须经 files.read_binary 取内容（不是控制端 Path 读取）。"""
        from modules.execution_plane.files import LocalFileSystem

        (self.workspace / "d.txt").write_text("orig-bytes\n", encoding="utf-8")
        calls = []
        original = LocalFileSystem.read_binary

        def _spy(self_fs, path):
            calls.append(path)
            return original(self_fs, path)

        with mock.patch.object(LocalFileSystem, "read_binary", _spy):
            self.backend.track_file_edit(
                "d.txt", conversation_id="conv_backend", message_id="m1"
            )

        self.assertEqual(calls, ["d.txt"], "必须经执行侧 files.read_binary 读取原始内容")
        backups = list(_backup_dir(self.data_dir, "conv_backend").glob("*@v1"))
        self.assertEqual(len(backups), 1, f"应产生 1 份快照，实际: {backups}")
        self.assertEqual(backups[0].read_bytes(), b"orig-bytes\n")

    def test_new_file_records_none_backup_and_rewind_deletes_it(self):
        """合法新文件：记 None 版本 → 写入 → snapshot → rewind 删除（撤销创建）。"""
        conversation_id = "conv_new_file"
        message_id = "m_create"
        self.backend.track_file_edit(
            "created.txt", conversation_id=conversation_id, message_id=message_id
        )

        state_file = _backup_dir(self.data_dir, conversation_id) / "state.jsonl"
        self.assertTrue(state_file.exists(), "新文件也必须产生 track 行")
        row = json.loads(state_file.read_text(encoding="utf-8").splitlines()[-1])
        entry = row["tracked_file_backups"]["created.txt"]
        self.assertIsNone(entry["backup_file_name"], "新文件的 v1 必须是 None 版本")

        # 模拟工具真正写出文件，并在后续消息做快照
        (self.workspace / "created.txt").write_text("created\n", encoding="utf-8")
        manager = ShallowVersioningManager(
            project_path=str(self.workspace),
            data_dir=str(self.data_dir),
            conversation_id=conversation_id,
            filesystem=self.backend.files,
        )
        manager.make_snapshot("m_next")
        self.assertTrue((self.workspace / "created.txt").exists())

        # 回滚到创建消息之前 → 文件必须被删除
        result = manager.rewind(message_id)
        self.assertIn("created.txt", result["files_changed"], result)
        self.assertFalse((self.workspace / "created.txt").exists(), "撤销创建应删除文件")

    def test_rewind_restores_binary_bytes_via_write_binary(self):
        """真实文件后端：非 UTF-8 字节也要原样回滚（证明走 bytes，不走文本通道）。"""
        raw = b"\x00\x01\xff\xfe binary tail\n"
        target = self.workspace / "bin.dat"
        target.write_bytes(raw)
        conversation_id = "conv_binary"
        self.backend.track_file_edit(
            "bin.dat", conversation_id=conversation_id, message_id="m1"
        )
        manager = ShallowVersioningManager(
            project_path=str(self.workspace),
            data_dir=str(self.data_dir),
            conversation_id=conversation_id,
            filesystem=self.backend.files,
        )
        manager.make_snapshot("m1")  # v1 = 原始字节
        target.write_bytes(b"later-edit\n")

        result = manager.rewind("m1")
        self.assertIn("bin.dat", result["files_changed"], result)
        self.assertEqual(target.read_bytes(), raw, "回滚必须精确恢复原始字节")

    def test_track_file_edit_skips_without_identity(self):
        (self.workspace / "skip_track.txt").write_text("x\n", encoding="utf-8")
        conversation_id = "conv_track_skipped"
        self.backend.track_file_edit("skip_track.txt", conversation_id=conversation_id)
        self.assertFalse(
            _backup_dir(self.data_dir, conversation_id).exists(),
            "缺少 message_id 时必须 best-effort 跳过",
        )

    def test_track_file_edit_requires_write_permission(self):
        if str(os.environ.get("EXEC_PLANE_TEST_EXECUTION_MODE", "sandbox")) == "direct":
            self.skipTest("direct 执行环境不做路径授权检查")
        self.backend.track_file_edit(
            "/etc/hosts", conversation_id="conv_track_write_denied", message_id="msg_x"
        )
        self.assertFalse(
            _backup_dir(self.data_dir, "conv_track_write_denied").exists(),
            "写权限不足时不得产生备份",
        )

    def test_track_file_edit_respects_read_denylist(self):
        if str(os.environ.get("EXEC_PLANE_TEST_EXECUTION_MODE", "sandbox")) == "direct":
            self.skipTest("direct 执行环境不做路径授权检查")
        (self.workspace / ".env").write_text("SECRET=1\n", encoding="utf-8")
        self.backend.track_file_edit(
            ".env", conversation_id="conv_track_denied", message_id="msg_x"
        )
        self.assertFalse(
            _backup_dir(self.data_dir, "conv_track_denied").exists(),
            "禁读路径不得产生备份",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
