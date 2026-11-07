"""容器元数据协议回归（2026-11-07）。

真实执行 ``CONTAINER_FILE_SCRIPT``（临时目录当作容器根，不要求 Docker），
并验证 ``LocalFileSystem`` 的容器路由、路径换算与失败不回退。
"""
from __future__ import annotations

import atexit
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

# 必须在导入 config / modules.file_manager 之前隔离运行态。
_TEST_HOME = Path(tempfile.mkdtemp(prefix="astrion_container_test_")).resolve()
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
    """在临时目录（当作容器根）里真实执行 CONTAINER_FILE_SCRIPT。"""
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
    output = (completed.stdout or "").strip()
    if not output:
        raise AssertionError("container script returned no output")
    return json.loads(output)


def _assert_json_safe(testcase: unittest.TestCase, payload: object) -> None:
    """递归确认结果里没有 Path/bytes 等非 JSON 类型。"""

    def walk(value: object) -> None:
        testcase.assertNotIsInstance(value, Path)
        testcase.assertNotIsInstance(value, bytes)
        if isinstance(value, dict):
            for key, item in value.items():
                testcase.assertIsInstance(key, str)
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(payload)
    json.dumps(payload, ensure_ascii=False)


class ContainerScriptOfflineTest(unittest.TestCase):
    """真实容器代理脚本（CONTAINER_FILE_SCRIPT）在临时根内执行。

    不需要 Docker：直接把临时目录当作容器工作区根，通过 stdin 传请求、
    读取 stdout JSON，验证 inspect/get_file_info/list/resolve 的策略。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="astrion_container_root_")).resolve()
        cls.outside = Path(
            tempfile.mkdtemp(
                prefix="astrion_container_outside_", dir=Path(__file__).resolve().parent
            )
        ).resolve()
        (cls.outside / "secret.txt").write_text("outside-secret", encoding="utf-8")
        (cls.root / "real.txt").write_text("12345", encoding="utf-8")
        (cls.root / "sub").mkdir()
        (cls.root / "sub" / "a.txt").write_text("abc", encoding="utf-8")
        (cls.root / ".hidden").write_text("hidden", encoding="utf-8")
        os.symlink(cls.root / "real.txt", cls.root / "link.txt")
        os.symlink(cls.outside / "secret.txt", cls.root / "escape.txt")
        os.symlink(cls.root / "missing-target", cls.root / "dangling.txt")
        os.symlink(cls.root / "sub", cls.root / "dirlink")
        os.symlink(cls.outside, cls.root / "escape_dir")
        os.symlink(cls.root / "loop_b", cls.root / "loop_a")
        os.symlink(cls.root / "loop_a", cls.root / "loop_b")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)
        shutil.rmtree(cls.outside, ignore_errors=True)

    def action(self, name: str, payload: dict) -> dict:
        return _run_container_action(self.root, name, payload)

    def test_inspect_path_file_missing_dir_root_and_symlink(self) -> None:
        file_info = self.action("inspect_path", {"path": "real.txt"})
        self.assertTrue(file_info["success"], file_info)
        self.assertEqual(
            set(file_info),
            {
                "success",
                "path",
                "relative_path",
                "exists",
                "is_file",
                "is_dir",
                "is_symlink",
                "within_workspace",
                "suffix",
                "size",
                "mtime",
            },
        )
        self.assertEqual(file_info["path"], "real.txt")
        self.assertEqual(file_info["relative_path"], "real.txt")
        self.assertTrue(file_info["exists"])
        self.assertTrue(file_info["is_file"])
        self.assertFalse(file_info["is_symlink"])
        self.assertTrue(file_info["within_workspace"])
        self.assertEqual(file_info["size"], 5)
        self.assertEqual(file_info["suffix"], ".txt")
        self.assertIsInstance(file_info["mtime"], float)
        _assert_json_safe(self, file_info)

        missing = self.action("inspect_path", {"path": "missing.txt"})
        self.assertTrue(missing["success"], missing)
        self.assertFalse(missing["exists"])
        self.assertIsNone(missing["size"])
        self.assertIsNone(missing["mtime"])
        self.assertEqual(missing["suffix"], ".txt")

        folder = self.action("inspect_path", {"path": "sub"})
        self.assertTrue(folder["is_dir"], folder)
        self.assertFalse(folder["is_file"])
        self.assertIsNone(folder["size"])

        root = self.action("inspect_path", {"path": "."})
        self.assertTrue(root["is_dir"], root)
        self.assertEqual(root["path"], ".")

        link = self.action("inspect_path", {"path": "link.txt"})
        self.assertTrue(link["success"], link)
        self.assertTrue(link["is_symlink"])
        self.assertTrue(link["exists"])
        self.assertTrue(link["is_file"])
        self.assertEqual(link["size"], 5)
        self.assertEqual(link["suffix"], ".txt")
        self.assertEqual(link["path"], "real.txt")  # 跟随后的目标路径

        dangling = self.action("inspect_path", {"path": "dangling.txt"})
        self.assertTrue(dangling["success"], dangling)
        self.assertFalse(dangling["exists"])
        self.assertTrue(dangling["is_symlink"])
        self.assertIsInstance(dangling["mtime"], float)  # 链接自身 mtime

        escape = self.action("inspect_path", {"path": "escape.txt"})
        self.assertFalse(escape["success"], escape)
        self.assertNotIn("exists", escape)
        self.assertNotIn("size", escape)

        loop = self.action("inspect_path", {"path": "loop_a"})
        self.assertFalse(loop["success"], loop)
        self.assertNotIn("exists", loop)

        bad_access = self.action("inspect_path", {"path": "real.txt", "access": "execute"})
        self.assertFalse(bad_access["success"])

    def test_directory_symlinks_and_nested_children_are_authorized(self) -> None:
        inside = self.action("inspect_path", {"path": "dirlink"})
        self.assertTrue(inside["success"], inside)
        self.assertTrue(inside["is_symlink"])
        self.assertTrue(inside["is_dir"])
        self.assertEqual(inside["path"], "sub")

        nested = self.action("list_files", {"path": "dirlink"})
        self.assertTrue(nested["success"], nested)
        self.assertEqual(nested["path"], "sub")
        self.assertEqual([item["path"] for item in nested["files"]], ["sub/a.txt"])

        # 指向工作区外的目录链接：元数据/列举/子项均拒绝，且不返回 exists/size。
        escape_dir = self.action("inspect_path", {"path": "escape_dir"})
        self.assertFalse(escape_dir["success"], escape_dir)
        self.assertNotIn("exists", escape_dir)
        self.assertNotIn("is_dir", escape_dir)

        escape_list = self.action("list_files", {"path": "escape_dir"})
        self.assertFalse(escape_list["success"], escape_list)
        self.assertNotIn("files", escape_list)

        escape_child = self.action("inspect_path", {"path": "escape_dir/secret.txt"})
        self.assertFalse(escape_child["success"], escape_child)
        self.assertNotIn("exists", escape_child)
        self.assertFalse(
            self.action("get_file_info", {"path": "escape_dir/secret.txt"})["success"]
        )

    def test_get_file_info_contract(self) -> None:
        info = self.action("get_file_info", {"path": "real.txt"})
        self.assertTrue(info["success"], info)
        self.assertEqual(info["type"], "file")
        self.assertEqual(info["size"], 5)
        self.assertEqual(info["extension"], ".txt")
        self.assertEqual(info["path"], "real.txt")
        self.assertIn("T", info["created"])
        self.assertIn("T", info["modified"])
        _assert_json_safe(self, info)

        link = self.action("get_file_info", {"path": "link.txt"})
        self.assertTrue(link["success"], link)
        self.assertEqual(link["name"], "real.txt")
        self.assertEqual(link["size"], 5)

        folder = self.action("get_file_info", {"path": "sub"})
        self.assertTrue(folder["success"], folder)
        self.assertEqual(folder["type"], "folder")
        self.assertIsNone(folder["extension"])

        self.assertFalse(self.action("get_file_info", {"path": "missing.txt"})["success"])
        escape = self.action("get_file_info", {"path": "escape.txt"})
        self.assertFalse(escape["success"], escape)
        self.assertNotIn("size", escape)

    def test_list_files_skips_hidden_and_escaping_children(self) -> None:
        listed = self.action("list_files", {"path": "."})
        self.assertTrue(listed["success"], listed)
        self.assertEqual(listed["path"], ".")
        file_names = [item["name"] for item in listed["files"]]
        folder_names = [item["name"] for item in listed["folders"]]
        self.assertEqual(file_names, ["link.txt", "real.txt"])
        self.assertEqual(folder_names, ["dirlink", "sub"])
        self.assertNotIn(".hidden", file_names)
        self.assertNotIn("escape.txt", file_names)
        self.assertNotIn("escape_dir", folder_names)
        self.assertNotIn("dangling.txt", file_names)
        self.assertNotIn("loop_a", file_names)
        self.assertNotIn("loop_b", file_names)
        link_entry = next(item for item in listed["files"] if item["name"] == "link.txt")
        self.assertEqual(link_entry["size"], 5)
        self.assertEqual(link_entry["path"], "link.txt")
        _assert_json_safe(self, listed)

        nested = self.action("list_files", {"path": "sub"})
        self.assertTrue(nested["success"], nested)
        self.assertEqual(nested["path"], "sub")
        self.assertEqual([item["path"] for item in nested["files"]], ["sub/a.txt"])

        not_dir = self.action("list_files", {"path": "real.txt"})
        self.assertFalse(not_dir["success"])
        self.assertFalse(self.action("list_files", {"path": "missing_dir"})["success"])
        escape = self.action("list_files", {"path": "escape.txt"})
        self.assertFalse(escape["success"], escape)

    def test_resolve_path_is_path_only(self) -> None:
        resolved = self.action("resolve_path", {"path": "new.txt", "access": "write"})
        self.assertTrue(resolved["success"], resolved)
        self.assertEqual(resolved["path"], "new.txt")
        self.assertEqual(resolved["access"], "write")
        self.assertNotIn("exists", resolved)
        self.assertFalse((self.root / "new.txt").exists())

        self.assertFalse(self.action("resolve_path", {"path": "../outside.txt"})["success"])
        self.assertFalse(
            self.action("resolve_path", {"path": "new.txt", "access": "execute"})["success"]
        )


class LocalFileSystemContainerModeTest(unittest.TestCase):
    """适配器 → 真实容器脚本（临时根）→ JSON，全程不碰宿主镜像元数据。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.host_root = Path(tempfile.mkdtemp(prefix="astrion_ws_docker_host_")).resolve()
        cls.container_root = Path(tempfile.mkdtemp(prefix="astrion_ws_docker_container_")).resolve()
        # 宿主镜像故意与容器根文件大小不同，用来证明没有走宿主 stat。
        (cls.host_root / "probe.txt").write_text("host-mirror-must-not-be-used", encoding="utf-8")
        (cls.container_root / "probe.txt").write_text("12345", encoding="utf-8")
        (cls.container_root / "sub").mkdir()
        (cls.container_root / "sub" / "a.txt").write_text("abc", encoding="utf-8")
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

    def _install_script_backend(self) -> list:
        calls: list = []
        original = self.file_manager._container_call

        def call(action: str, payload: dict) -> dict:
            calls.append((action, payload))
            return _run_container_action(self.container_root, action, payload)

        self.file_manager._container_call = call
        self.addCleanup(lambda: setattr(self.file_manager, "_container_call", original))
        return calls

    def test_metadata_comes_from_container_script_not_host(self) -> None:
        calls = self._install_script_backend()

        host_size = (self.host_root / "probe.txt").stat().st_size
        info = self.fs.inspect_path(str(self.host_root / "probe.txt"))
        self.assertTrue(info["success"], info)
        self.assertEqual(info["size"], 5)
        self.assertNotEqual(info["size"], host_size)
        self.assertEqual(info["path"], "probe.txt")
        self.assertEqual(calls[0][0], "inspect_path")
        self.assertEqual(calls[0][1]["path"], "probe.txt")
        _assert_json_safe(self, info)

        # /workspace 形式也只转相对路径，不触发宿主 stat。
        workspace_form = self.fs.inspect_path("/workspace/probe.txt")
        self.assertTrue(workspace_form["success"], workspace_form)
        self.assertEqual(workspace_form["size"], 5)
        self.assertEqual(calls[-1][1]["path"], "probe.txt")

        file_info = self.fs.get_file_info("probe.txt")
        self.assertTrue(file_info["success"], file_info)
        self.assertEqual(file_info["size"], 5)

        listed = self.fs.list_files("")
        self.assertTrue(listed["success"], listed)
        self.assertEqual([item["name"] for item in listed["files"]], ["probe.txt"])
        self.assertEqual([item["name"] for item in listed["folders"]], ["sub"])

        resolved = self.fs.resolve_path("new.txt", access="write")
        self.assertTrue(resolved["success"], resolved)
        self.assertEqual(resolved["access"], "write")
        self.assertNotIn("exists", resolved)
        self.assertEqual(
            [name for name, _payload in calls],
            ["inspect_path", "inspect_path", "get_file_info", "list_files", "resolve_path"],
        )

    def test_invalid_access_does_not_call_container(self) -> None:
        calls = self._install_script_backend()
        result = self.fs.inspect_path("probe.txt", access="execute")
        self.assertFalse(result["success"])
        self.assertEqual(calls, [])

    def test_relative_paths_resolve_against_subagent_working_dir(self) -> None:
        calls = self._install_script_backend()
        child = self.host_root / "agent_1"
        child.mkdir(exist_ok=True)
        scope = ExecutionScope(
            "sub_agent",
            "child",
            self.host_root,
            "workspace_write",
            working_dir=child,
        )
        with bind_execution_scope(scope):
            self.fs.inspect_path("probe.txt")
            self.fs.list_files("")
            self.fs.get_file_info("probe.txt")
        self.assertEqual(calls[0][1]["path"], "agent_1/probe.txt")
        self.assertEqual(calls[1][1]["path"], "agent_1")
        self.assertEqual(calls[2][1]["path"], "agent_1/probe.txt")

    def test_container_failure_is_propagated_without_host_fallback(self) -> None:
        def boom(action: str, payload: dict) -> dict:
            return {"success": False, "error": "box-down"}

        self.file_manager._container_call = boom
        self.addCleanup(lambda: delattr(self.file_manager, "_container_call"))
        for result in (
            self.fs.inspect_path("probe.txt"),
            self.fs.get_file_info("probe.txt"),
            self.fs.list_files(""),
            self.fs.resolve_path("probe.txt"),
        ):
            self.assertFalse(result["success"], result)
            self.assertEqual(result.get("error"), "box-down")
            self.assertNotIn("size", result)
            self.assertNotIn("exists", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
