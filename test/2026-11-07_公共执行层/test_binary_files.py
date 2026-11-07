"""公共文件层 write_binary 回归（2026-11-07）。

覆盖：host 精确字节 roundtrip、非 bytes 拒绝、scope 越界拒绝、scoped
硬链接拒绝；容器脚本 write_binary 的 base64/no-follow/nlink 行为，以及
LocalFileSystem 在 Docker 模式经容器动作写入而不回退宿主。
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_ENV_KEYS = (
    "ASTRION_IGNORE_DOTENV",
    "ASTRION_DATA_ROOT",
    "DATA_DIR",
    "LOGS_DIR",
    "USER_SPACE_DIR",
    "API_USER_SPACE_DIR",
    "TERMINAL_SANDBOX_MODE",
    "WEB_SECRET_KEY",
    "ASTRION_TRUSTED_PROXIES",
)
_ENV_BEFORE = {key: os.environ.get(key) for key in _ENV_KEYS}

_TEST_HOME = Path(tempfile.mkdtemp(prefix="astrion_binary_test_")).resolve()
os.environ["ASTRION_IGNORE_DOTENV"] = "1"
os.environ["ASTRION_DATA_ROOT"] = str(_TEST_HOME / "runtime")
os.environ["DATA_DIR"] = str(_TEST_HOME / "runtime" / "data")
os.environ["LOGS_DIR"] = str(_TEST_HOME / "runtime" / "logs")
os.environ["USER_SPACE_DIR"] = str(_TEST_HOME / "runtime" / "users")
os.environ["API_USER_SPACE_DIR"] = str(_TEST_HOME / "runtime" / "api_users")
os.environ["TERMINAL_SANDBOX_MODE"] = "host"
os.environ["WEB_SECRET_KEY"] = "test-only-not-a-real-secret"
os.environ.pop("ASTRION_TRUSTED_PROXIES", None)

atexit.register(lambda: shutil.rmtree(_TEST_HOME, ignore_errors=True))

from modules.container_file_proxy import CONTAINER_FILE_SCRIPT  # noqa: E402
from modules.execution_plane.files import LocalFileSystem  # noqa: E402
from modules.execution_scope import ExecutionScope, bind_execution_scope  # noqa: E402
from modules.file_manager import FileManager  # noqa: E402
from modules.user_container_manager import ContainerHandle  # noqa: E402

# 导入完成后恢复 env，避免同进程 discover 时污染 test_local 的 fresh-interpreter 断言。
for _key, _value in _ENV_BEFORE.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value


def _run_container_action(root: Path, action: str, payload: dict) -> dict:
    request = {"action": action, "root": str(root), "payload": payload}
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", CONTAINER_FILE_SCRIPT],
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"container script rc={completed.returncode}: {completed.stderr}"
        )
    return json.loads((completed.stdout or "").strip())


class HostBinaryWriteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = Path(tempfile.mkdtemp(prefix="astrion_bin_ws_")).resolve()
        # 默认 tmp 在 macOS 可能落在 /private/var（系统最小可读区域），
        # 不能作为“未授权路径”证据；放到测试目录下。
        cls.outside = Path(
            tempfile.mkdtemp(
                prefix="astrion_bin_outside_", dir=Path(__file__).resolve().parent
            )
        ).resolve()
        cls.file_manager = FileManager(
            str(cls.workspace), data_dir=str(cls.workspace / ".test_data")
        )
        cls.fs = LocalFileSystem(cls.file_manager)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.workspace, ignore_errors=True)
        shutil.rmtree(cls.outside, ignore_errors=True)

    def test_roundtrip_exact_bytes(self) -> None:
        data = b"\x00\xff\xfe\x80hello\r\n\x1a"
        written = self.fs.write_binary("bin/blob.bin", data)
        self.assertTrue(written["success"], written)
        self.assertEqual(written["size"], len(data))
        self.assertEqual(self.fs.read_binary("bin/blob.bin"), data)

        # 不是文本工具：二进制内容不应被 text decode 成功。
        text_read = self.fs.read_file("bin/blob.bin")
        self.assertFalse(text_read["success"], text_read)

    def test_overwrite_truncates_exactly(self) -> None:
        self.assertTrue(self.fs.write_binary("bin/trunc.bin", b"abcdefgh")["success"])
        self.assertTrue(self.fs.write_binary("bin/trunc.bin", b"xy")["success"])
        self.assertEqual(self.fs.read_binary("bin/trunc.bin"), b"xy")

    def test_rejects_non_bytes_without_writing(self) -> None:
        result = self.fs.write_binary("bin/text-fallback.bin", "not-bytes")  # type: ignore[arg-type]
        self.assertFalse(result["success"], result)
        self.assertFalse((self.workspace / "bin" / "text-fallback.bin").exists())

    def test_scope_denies_outside_write(self) -> None:
        child = self.workspace / "agent_bin"
        child.mkdir(exist_ok=True)
        target = self.outside / "denied.bin"
        scope = ExecutionScope(
            "sub_agent", "bin-child", self.workspace, "workspace_write", working_dir=child
        )
        with bind_execution_scope(scope):
            result = self.fs.write_binary(str(target), b"must-not-write")
        self.assertFalse(result["success"], result)
        self.assertFalse(target.exists())

    def test_scope_rejects_multiply_linked_target(self) -> None:
        child = self.workspace / "agent_link"
        child.mkdir(exist_ok=True)
        original = child / "original.bin"
        original.write_bytes(b"keep")
        alias = child / "alias.bin"
        os.link(original, alias)
        scope = ExecutionScope(
            "sub_agent", "link-child", self.workspace, "workspace_write", working_dir=child
        )
        with bind_execution_scope(scope):
            result = self.fs.write_binary("alias.bin", b"overwrite")
        self.assertFalse(result["success"], result)
        self.assertEqual(original.read_bytes(), b"keep")


class ContainerBinaryWriteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="astrion_bin_container_")).resolve()
        cls.outside = Path(
            tempfile.mkdtemp(
                prefix="astrion_bin_container_out_", dir=Path(__file__).resolve().parent
            )
        ).resolve()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)
        shutil.rmtree(cls.outside, ignore_errors=True)

    def action(self, name: str, payload: dict) -> dict:
        return _run_container_action(self.root, name, payload)

    def test_roundtrip_exact_bytes(self) -> None:
        data = b"\x00\xff\xfe\x80container\r\n"
        result = self.action(
            "write_binary",
            {"path": "sub/blob.bin", "b64": base64.b64encode(data).decode("ascii")},
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["size"], len(data))
        read = self.action("read_binary", {"path": "sub/blob.bin"})
        self.assertTrue(read["success"], read)
        self.assertEqual(base64.b64decode(read["b64"]), data)

    def test_rejects_hardlink_without_truncating(self) -> None:
        original = self.root / "hard.bin"
        original.write_bytes(b"keep")
        alias = self.root / "hard-alias.bin"
        os.link(original, alias)
        result = self.action(
            "write_binary",
            {"path": "hard-alias.bin", "b64": base64.b64encode(b"overwrite").decode()},
        )
        self.assertFalse(result["success"], result)
        self.assertEqual(original.read_bytes(), b"keep")

    def test_rejects_invalid_base64_and_escape_path(self) -> None:
        bad = self.action("write_binary", {"path": "bad.bin", "b64": "%%%"})
        self.assertFalse(bad["success"], bad)
        self.assertFalse((self.root / "bad.bin").exists())

        escape = self.action(
            "write_binary",
            {"path": "../outside.bin", "b64": base64.b64encode(b"x").decode()},
        )
        self.assertFalse(escape["success"], escape)
        self.assertFalse((self.outside / "outside.bin").exists())


class ContainerModeAdapterBinaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.host_root = Path(tempfile.mkdtemp(prefix="astrion_bin_docker_host_")).resolve()
        cls.container_root = Path(tempfile.mkdtemp(prefix="astrion_bin_docker_container_")).resolve()
        cls.handle = ContainerHandle(
            username="test-user",
            mode="docker",
            workspace_path=str(cls.host_root),
            mount_path="/workspace",
            container_name="fake-container-not-started",
        )
        cls.file_manager = FileManager(
            str(cls.host_root),
            container_session=cls.handle,
            data_dir=str(cls.host_root / ".test_data"),
        )
        cls.fs = LocalFileSystem(cls.file_manager)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.host_root, ignore_errors=True)
        shutil.rmtree(cls.container_root, ignore_errors=True)

    def test_write_binary_uses_container_action_not_host(self) -> None:
        calls: list = []
        original = self.file_manager._container_call

        def call(action: str, payload: dict) -> dict:
            calls.append((action, payload))
            return _run_container_action(self.container_root, action, payload)

        self.file_manager._container_call = call
        self.addCleanup(lambda: setattr(self.file_manager, "_container_call", original))
        data = b"\x00\xffcontainer-bytes"
        result = self.fs.write_binary("probe.bin", data)
        self.assertTrue(result["success"], result)
        self.assertEqual(calls[0][0], "write_binary")
        self.assertEqual(calls[0][1]["path"], "probe.bin")
        self.assertEqual((self.container_root / "probe.bin").read_bytes(), data)
        self.assertFalse((self.host_root / "probe.bin").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
