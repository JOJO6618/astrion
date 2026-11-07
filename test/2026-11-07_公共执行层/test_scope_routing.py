"""公共执行层 × 子实例权限边界（离线回归，不启动服务/模型/真实终端）。

覆盖本次改动：
1. 单次完全访问只认「本地可信后端」：本地 LocalExecutionBackend 保留原能力，
   Fake/远端/伪造 is_local 对象/Docker 一律拒绝；
2. 子实例独立装配 LocalExecutionBackend(child) 包住自己专属资源，scope 不扩大；
3. 父对话注入非本地后端时子实例 fail-closed，不回退本机工具执行；
4. 媒体读取走 backend.files.inspect_path + read_binary，不依赖本地 exists/resolve。

运行：
    python /opt/homebrew/bin/python3.11 test/2026-11-07_公共执行层/test_scope_routing.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.background_command_manager import BackgroundCommandManager
from modules.execution_plane import (
    FakeExecutionBackend,
    LocalExecutionBackend,
    is_local_backend,
)
from modules.execution_scope import (
    ExecutionScope,
    bind_execution_scope,
    is_trusted_local_backend,
    validate_full_access_request,
)
from modules.file_manager import FileManager
from modules.sub_agent import execution as child_execution
from modules.sub_agent.tools import handle_read_mediafile
from modules.terminal_manager import TerminalManager
from modules.terminal_ops import TerminalOperator

FULL_ACCESS_ARGS = {
    "command": "pwd",
    "timeout": 30,
    "request_full_access": True,
    "full_access_reason": "需要脱离沙箱执行一次命令",
}


def _resource_owner(root: Path, *, container_mode: str | None = "host") -> SimpleNamespace:
    """最小资源 owner：LocalExecutionBackend 只要求资源属性存在。"""
    session = SimpleNamespace(mode=container_mode) if container_mode else None
    return SimpleNamespace(
        project_path=str(root),
        container_session=session,
        file_manager=object(),
        terminal_ops=object(),
        terminal_manager=object(),
        background_command_manager=object(),
    )


def _validation_terminal(*, backend=None, container_mode: str | None = "host") -> SimpleNamespace:
    """只满足 validate_full_access_request 读取面的终端替身。"""
    session = SimpleNamespace(mode=container_mode) if container_mode else None
    return SimpleNamespace(
        execution_backend=backend,
        container_session=session,
        get_permission_mode=lambda: "unrestricted",
        get_work_mode=lambda: "execute",
        get_execution_mode=lambda: "sandbox",
        get_network_permission=lambda: "restricted",
    )


class FullAccessBackendValidationTests(unittest.TestCase):
    """单次完全访问：本地后端保留，非本地/伪造/Docker 一律拒绝。"""

    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_local_backend_keeps_one_time_full_access(self):
        terminal = _validation_terminal()
        terminal.execution_backend = LocalExecutionBackend(terminal)
        self.assertTrue(is_local_backend(terminal))
        self.assertTrue(is_trusted_local_backend(terminal))
        self.assertIs(validate_full_access_request(terminal, FULL_ACCESS_ARGS), True)
        terminal.get_execution_mode = lambda: "direct"
        self.assertIs(validate_full_access_request(terminal, FULL_ACCESS_ARGS), False)

    def test_unbound_backend_is_legacy_local_chain(self):
        terminal = _validation_terminal(backend=None)
        self.assertTrue(is_trusted_local_backend(terminal))
        self.assertIs(validate_full_access_request(terminal, FULL_ACCESS_ARGS), True)

    def test_fake_backend_is_rejected(self):
        terminal = _validation_terminal(backend=FakeExecutionBackend())
        self.assertFalse(is_local_backend(terminal))
        with self.assertRaises(ValueError):
            validate_full_access_request(terminal, FULL_ACCESS_ARGS)

    def test_spoofed_is_local_object_is_rejected(self):
        class Spoof:
            is_local = True
            supports_full_access = True

            def run_command(self, *args, **kwargs):  # pragma: no cover - 不应被调用
                raise AssertionError("伪造后端不得被执行")

        terminal = _validation_terminal(backend=Spoof())
        self.assertFalse(is_trusted_local_backend(terminal))
        with self.assertRaises(ValueError):
            validate_full_access_request(terminal, FULL_ACCESS_ARGS)

    def test_fake_backend_cannot_grant_by_declaring_capabilities(self):
        backend = FakeExecutionBackend()
        backend.is_local = True  # 运行期伪造，仍不得被信任
        backend.supports_full_access = True
        terminal = _validation_terminal(backend=backend)
        with self.assertRaises(ValueError):
            validate_full_access_request(terminal, FULL_ACCESS_ARGS)

    def test_trusted_class_subclass_is_fail_closed(self):
        class Derived(LocalExecutionBackend):
            pass

        terminal = _validation_terminal(backend=Derived(_resource_owner(self.root)))
        with self.assertRaises(ValueError):
            validate_full_access_request(terminal, FULL_ACCESS_ARGS)

    def test_local_backend_without_full_access_capability_is_rejected(self):
        terminal = _validation_terminal()
        backend = LocalExecutionBackend(terminal)
        backend.supports_full_access = False
        terminal.execution_backend = backend
        with self.assertRaises(ValueError):
            validate_full_access_request(terminal, FULL_ACCESS_ARGS)

    def test_backend_owned_by_another_terminal_cannot_use_this_terminal_grant(self):
        backend = LocalExecutionBackend(_resource_owner(self.root))
        terminal = _validation_terminal(backend=backend)
        self.assertFalse(is_local_backend(terminal))
        with self.assertRaises(ValueError):
            validate_full_access_request(terminal, FULL_ACCESS_ARGS)

    def test_docker_is_rejected_even_with_local_backend(self):
        backend = LocalExecutionBackend(_resource_owner(self.root))
        terminal = _validation_terminal(backend=backend, container_mode="docker")
        with self.assertRaises(ValueError):
            validate_full_access_request(terminal, FULL_ACCESS_ARGS)

    def test_docker_backend_session_is_rejected_without_terminal_session(self):
        backend = LocalExecutionBackend(_resource_owner(self.root, container_mode="docker"))
        terminal = _validation_terminal(backend=backend, container_mode=None)
        self.assertFalse(is_trusted_local_backend(terminal))
        with self.assertRaises(ValueError):
            validate_full_access_request(terminal, FULL_ACCESS_ARGS)


class _ParentTerminal:
    """父对话终端替身：具备子实例装配所需的资源面。"""

    def __init__(self, root: Path, data_dir: Path, *, backend=None) -> None:
        self.project_path = str(root)
        self.data_dir = str(data_dir)
        self.container_session = SimpleNamespace(mode="host")
        self.file_manager = FileManager(
            str(root), container_session=self.container_session, data_dir=str(data_dir)
        )
        self.terminal_ops = TerminalOperator(
            str(root), container_session=self.container_session, data_dir=str(data_dir)
        )
        self.terminal_manager = TerminalManager(
            project_path=str(root),
            container_session=self.container_session,
            network_permission_getter=self.get_network_permission,
            terminal_readonly_getter=lambda: False,
            command_validator=self.terminal_ops._validate_command,
        )
        self.background_command_manager = BackgroundCommandManager(project_path=str(root))
        self.context_manager = SimpleNamespace(
            project_path=root, current_conversation_id="conv-parent", main_terminal=self
        )
        self.execution_backend = backend
        self.current_permission_mode = "unrestricted"
        self.host_execution_mode = "sandbox"

    def get_network_permission(self) -> str:
        return "restricted"

    def get_permission_mode(self) -> str:
        return "unrestricted"

    def get_execution_mode(self) -> str:
        return "sandbox"


class _Manager:
    def __init__(self, terminal: _ParentTerminal) -> None:
        self.terminal = terminal
        self.container_session = terminal.container_session
        self.data_dir = terminal.data_dir


class ChildExecutionAssemblyTests(unittest.TestCase):
    """子实例装配自己的本地后端，且不扩大 scope / 不共享父资源。"""

    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.data_dir = self.base / "data"
        self.data_dir.mkdir()
        self.work = self.root / "agent_1"
        self.work.mkdir()

    def _record(self, access_level: str) -> dict:
        return {
            "task_id": "child-1",
            "access_level": access_level,
            "workspace_root": str(self.root),
            "working_dir": str(self.work),
        }

    def test_child_backend_wraps_own_resources(self):
        parent = _ParentTerminal(self.root, self.data_dir)
        manager = _Manager(parent)
        child = child_execution.build_child_terminal(manager, self._record("workspace_write"))

        backend = child.execution_backend
        self.assertIsInstance(backend, LocalExecutionBackend)
        self.assertIs(backend.owner, child)
        self.assertTrue(is_local_backend(child))
        self.assertTrue(backend.is_local)
        self.assertIsNot(backend, parent.execution_backend)

        # 资源全部是子实例自己的对象，不复用父对象
        self.assertIsNot(child.file_manager, parent.file_manager)
        self.assertIsNot(child.terminal_ops, parent.terminal_ops)
        self.assertIsNot(child.terminal_manager, parent.terminal_manager)
        self.assertIsNot(child.background_command_manager, parent.background_command_manager)
        self.assertEqual(child.project_path, str(self.root))

    def test_child_scope_does_not_widen_workspace_write(self):
        parent = _ParentTerminal(self.root, self.data_dir)
        manager = _Manager(parent)
        child = child_execution.build_child_terminal(manager, self._record("workspace_write"))
        scope = ExecutionScope(
            "sub_agent", "child-1", self.root, "workspace_write", working_dir=self.work
        )
        with bind_execution_scope(scope):
            inside = child.execution_backend.files.write_file("inside.txt", "ok")
            escaped = child.execution_backend.files.write_file(
                str(self.base / "escape.txt"), "no"
            )
        self.assertTrue(inside["success"], inside)
        self.assertFalse(escaped["success"], escaped)
        self.assertFalse((self.base / "escape.txt").exists())

    def test_child_scope_does_not_widen_working_dir_write(self):
        parent = _ParentTerminal(self.root, self.data_dir)
        manager = _Manager(parent)
        child = child_execution.build_child_terminal(manager, self._record("working_dir_write"))
        scope = ExecutionScope(
            "sub_agent", "child-1", self.root, "working_dir_write", working_dir=self.work
        )
        with bind_execution_scope(scope):
            inside = child.execution_backend.files.write_file("inside.txt", "ok")
            sibling = child.execution_backend.files.write_file(
                str(self.root / "sibling.txt"), "no"
            )
        self.assertTrue(inside["success"], inside)
        self.assertFalse(sibling["success"], sibling)
        self.assertFalse((self.root / "sibling.txt").exists())

    def test_nonlocal_parent_backend_fails_closed(self):
        fake = FakeExecutionBackend()
        parent = _ParentTerminal(self.root, self.data_dir, backend=fake)
        manager = _Manager(parent)
        parent_file_manager = parent.file_manager
        parent_terminal_manager = parent.terminal_manager
        with self.assertRaises(ValueError) as caught:
            child_execution.build_child_terminal(manager, self._record("workspace_write"))
        message = str(caught.exception)
        self.assertIn("fork_child", message)
        # 没有静默重建本地工具，也没有改动父资源
        self.assertIs(parent.execution_backend, fake)
        self.assertIs(parent.file_manager, parent_file_manager)
        self.assertIs(parent.terminal_manager, parent_terminal_manager)
        self.assertFalse((self.work / "inside.txt").exists())


class _StubFiles:
    def __init__(self, info: dict, data: bytes) -> None:
        self._info = info
        self._data = data
        self.calls: list = []

    def inspect_path(self, path: str) -> dict:
        self.calls.append(("inspect_path", path))
        return dict(self._info)

    def read_binary(self, path: str) -> bytes:
        self.calls.append(("read_binary", path))
        return self._data


class _StubBackend:
    def __init__(self, files: _StubFiles) -> None:
        self.files = files


class MediaReadRoutingTests(unittest.TestCase):
    """媒体读取只经 backend.files 元数据 + 二进制，不触本地 exists/resolve。"""

    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.data_dir = self.base / "data"
        self.data_dir.mkdir()
        self.work = self.root / "agent_1"
        self.work.mkdir()

    def test_media_uses_backend_metadata_even_when_path_is_virtual(self):
        files = _StubFiles(
            {
                "success": True,
                "path": "ghost.png",
                "relative_path": "ghost.png",
                "exists": True,
                "is_file": True,
            },
            b"virtual-bytes",
        )
        result = asyncio.run(
            handle_read_mediafile(self.root, {"path": "ghost.png"}, backend=_StubBackend(files))
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(base64.b64decode(result["b64"]), b"virtual-bytes")
        self.assertEqual(result["mime"], "image/png")
        self.assertEqual([call[0] for call in files.calls], ["inspect_path", "read_binary"])
        # 磁盘上确实不存在：证明没有走本地 exists/resolve 判定
        self.assertFalse((self.root / "ghost.png").exists())

    def test_container_metadata_unsupported_still_reads_binary(self):
        files = _StubFiles(
            {"success": True, "code": "metadata_unsupported_in_container"}, b"container-bytes"
        )
        result = asyncio.run(
            handle_read_mediafile(self.root, {"path": "clip.mp4"}, backend=_StubBackend(files))
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["type"], "video")
        self.assertEqual(base64.b64decode(result["b64"]), b"container-bytes")

    def test_backend_denial_is_reported_without_local_fallback(self):
        files = _StubFiles({"success": False, "error": "denied-by-backend"}, b"")
        result = asyncio.run(
            handle_read_mediafile(self.root, {"path": "secret.png"}, backend=_StubBackend(files))
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "denied-by-backend")
        self.assertEqual([call[0] for call in files.calls], ["inspect_path"])

    def test_child_media_read_goes_through_child_backend(self):
        parent = _ParentTerminal(self.root, self.data_dir)
        manager = _Manager(parent)
        scope = ExecutionScope(
            "sub_agent", "child-1", self.root, "workspace_write", working_dir=self.work
        )
        (self.work / "image.png").write_bytes(b"sample-image")
        instance = SimpleNamespace(manager=manager, task_id="child-1", execution_scope=scope)

        payload = asyncio.run(
            child_execution.execute_child_tool(
                manager, "read_mediafile", {"path": "image.png"}, instance
            )
        )
        result = json.loads(payload)
        self.assertTrue(result["success"], result)
        self.assertEqual(base64.b64decode(result["b64"]), b"sample-image")
        child = instance._execution_terminal
        self.assertIsInstance(child.execution_backend, LocalExecutionBackend)
        self.assertIs(child.execution_backend.owner, child)

        # 越界符号链接仍被拒绝（沿用固定 scope + no-follow 读取语义）
        outside = self.base / "outside.png"
        outside.write_bytes(b"private")
        (self.work / "link.png").symlink_to(outside)
        denied = json.loads(
            asyncio.run(
                child_execution.execute_child_tool(
                    manager, "read_mediafile", {"path": "link.png"}, instance
                )
            )
        )
        self.assertFalse(denied["success"], denied)

    def test_media_without_backend_fails_closed(self):
        result = asyncio.run(handle_read_mediafile(self.root, {"path": "image.png"}))
        self.assertFalse(result["success"])
        self.assertIn("backend", result["error"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
