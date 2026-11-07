"""LocalFileSystem 公共文件协议回归（2026-11-07）。

只使用临时目录与合成配置：不读真实 .env/settings.json，不启动服务，
不访问真实运行态数据目录。
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
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
_TEST_HOME = Path(tempfile.mkdtemp(prefix="astrion_files_test_")).resolve()
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

from modules.execution_plane.files import FileSystemBackend, LocalFileSystem  # noqa: E402
from modules.execution_scope import ExecutionScope, bind_execution_scope  # noqa: E402
from modules.file_manager import FileManager  # noqa: E402

# 测试模块导入期间隔离 config；导入完成后恢复，避免污染同进程 discover 的
# 其他测试（尤其是 test_local 的 fresh-interpreter 断言）。
for _key, _value in _ENV_BEFORE.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value


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


class LocalFileSystemHostTest(unittest.TestCase):
    """host 模式：转发语义、元数据授权顺序与 JSON 契约。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = Path(tempfile.mkdtemp(prefix="astrion_ws_")).resolve()
        # outside 必须放在测试目录下：macOS 的 /var、/private/var 属于
        # MACOS_MINIMAL_READABLE_PATHS，用默认 tmp 目录并不能证明“未授权”。
        cls.outside = Path(
            tempfile.mkdtemp(
                prefix="astrion_outside_", dir=Path(__file__).resolve().parent
            )
        ).resolve()
        cls.file_manager = FileManager(
            str(cls.workspace),
            data_dir=str(cls.workspace / ".test_data"),
        )
        cls.fs = LocalFileSystem(cls.file_manager)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.workspace, ignore_errors=True)
        shutil.rmtree(cls.outside, ignore_errors=True)

    # -- 协议面 ---------------------------------------------------------
    def test_protocol_is_implemented_with_explicit_methods(self) -> None:
        self.assertIsInstance(self.fs, FileSystemBackend)
        expected = [
            "read_file",
            "read_text_segment",
            "search_text",
            "extract_segments",
            "read_binary",
            "write_binary",
            "write_file",
            "replace_many_in_file",
            "create_file",
            "delete_file",
            "rename_file",
            "create_folder",
            "delete_folder",
            "list_files",
            "get_file_info",
            "inspect_path",
            "resolve_path",
        ]
        for name in expected:
            self.assertTrue(callable(getattr(self.fs, name, None)), name)
        # 禁止万能代理：未知属性必须直接失败，而不是被转发给 FileManager。
        self.assertNotIn("__getattr__", LocalFileSystem.__dict__)
        with self.assertRaises(AttributeError):
            getattr(self.fs, "definitely_not_a_file_method")

    # -- 读 -------------------------------------------------------------
    def test_read_file_segments_search_extract(self) -> None:
        content = "l1\nl2\nl3\nl4\nl5\nl6\n"
        created = self.fs.create_file("read_case.txt")
        self.assertTrue(created["success"], created)
        written = self.fs.write_file("read_case.txt", content)
        self.assertTrue(written["success"], written)
        self.assertEqual(written["original_file"], "")
        self.assertEqual(written["new_file"], content)

        full = self.fs.read_file("read_case.txt")
        self.assertTrue(full["success"], full)
        self.assertEqual(full["content"], content)
        self.assertEqual(full["path"], "read_case.txt")

        segment = self.fs.read_text_segment("read_case.txt", start_line=2, end_line=4)
        self.assertTrue(segment["success"], segment)
        self.assertEqual(segment["content"], "l2\nl3\nl4\n")
        self.assertEqual((segment["line_start"], segment["line_end"]), (2, 4))
        self.assertEqual(segment["total_lines"], 6)

        found = self.fs.search_text(
            "read_case.txt",
            query="l3",
            max_matches=2,
            context_before=1,
            context_after=1,
        )
        self.assertTrue(found["success"], found)
        self.assertEqual(found["matches"][0]["hits"], [3])
        self.assertIn("l3", found["matches"][0]["snippet"])

        extracted = self.fs.extract_segments(
            "read_case.txt",
            [
                {"start_line": 1, "end_line": 2, "label": "head"},
                {"start_line": 5, "end_line": 6, "label": "tail"},
            ],
        )
        self.assertTrue(extracted["success"], extracted)
        self.assertEqual([item["label"] for item in extracted["segments"]], ["head", "tail"])
        self.assertEqual(extracted["segments"][1]["content"], "l5\nl6\n")

        for payload in (full, segment, found, extracted):
            _assert_json_safe(self, payload)

    def test_read_binary_returns_bytes(self) -> None:
        target = self.workspace / "blob.bin"
        target.write_bytes(b"\x00\x01\x02hello")
        # 相对路径：适配器用 FileManager 的 _validate_path 解析后转交。
        raw = self.fs.read_binary("blob.bin")
        self.assertIsInstance(raw, bytes)
        self.assertEqual(raw, b"\x00\x01\x02hello")
        # 绝对路径：保持 FileManager 原有调用方式。
        self.assertEqual(self.fs.read_binary(str(target)), b"\x00\x01\x02hello")
        with self.assertRaises(PermissionError):
            self.fs.read_binary(str(self.outside / "missing.bin"))

    def test_read_file_missing_keeps_failure_shape(self) -> None:
        result = self.fs.read_file("no_such_file.txt")
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    # -- 写 -------------------------------------------------------------
    def test_write_append_and_replace_many(self) -> None:
        self.assertTrue(self.fs.create_file("edit.txt")["success"])
        self.assertTrue(self.fs.write_file("edit.txt", "alpha\nbeta\ngamma\ndelta\n")["success"])

        appended = self.fs.write_file("edit.txt", "epsilon\n", mode="a")
        self.assertTrue(appended["success"], appended)
        self.assertEqual(appended["new_file"], "alpha\nbeta\ngamma\ndelta\nepsilon\n")
        self.assertEqual(appended["original_file"], "alpha\nbeta\ngamma\ndelta\n")

        replaced = self.fs.replace_many_in_file(
            "edit.txt",
            [
                {
                    "old_string": "alpha\nbeta\ngamma",
                    "new_string": "alpha\nBETA\ngamma",
                    "replace_all": False,
                }
            ],
        )
        self.assertTrue(replaced["success"], replaced)
        self.assertTrue(replaced["write_performed"])
        self.assertEqual(replaced["replacements"], 1)
        self.assertIn("BETA", self.fs.read_file("edit.txt")["content"])
        _assert_json_safe(self, replaced)

    def test_create_rename_delete_and_folder_semantics(self) -> None:
        self.assertTrue(self.fs.create_folder("d1")["success"])
        self.assertFalse(self.fs.create_folder("d1")["success"])  # 已存在
        self.assertTrue(self.fs.create_file("d1/x.txt")["success"])

        renamed = self.fs.rename_file("d1/x.txt", "d1/y.txt")
        self.assertTrue(renamed["success"], renamed)
        self.assertEqual(renamed["new_path"], "d1/y.txt")

        self.assertFalse(self.fs.rename_file("d1/y.txt", "d1/y.txt")["success"])
        self.assertTrue(self.fs.delete_file("d1/y.txt")["success"])
        self.assertTrue(self.fs.delete_folder("d1")["success"])

    def test_delete_workspace_root_is_still_refused(self) -> None:
        result = self.fs.delete_folder("")
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertTrue(self.workspace.exists())

    # -- 元数据 ---------------------------------------------------------
    def test_list_files_and_get_file_info(self) -> None:
        self.assertTrue(self.fs.create_folder("listing/sub")["success"])
        self.assertTrue(self.fs.create_file("listing/a.txt")["success"])
        (self.workspace / "listing" / "a.txt").write_text("abc", encoding="utf-8")
        (self.workspace / "listing" / ".hidden").write_text("hidden", encoding="utf-8")

        listed = self.fs.list_files("listing")
        self.assertTrue(listed["success"], listed)
        self.assertEqual([item["name"] for item in listed["files"]], ["a.txt"])
        self.assertEqual([item["name"] for item in listed["folders"]], ["sub"])
        self.assertEqual(listed["path"], "listing")
        _assert_json_safe(self, listed)

        info = self.fs.get_file_info("listing/a.txt")
        self.assertTrue(info["success"], info)
        self.assertEqual(info["type"], "file")
        self.assertEqual(info["size"], 3)
        self.assertEqual(info["extension"], ".txt")
        self.assertEqual(info["path"], "listing/a.txt")
        self.assertIn("T", info["modified"])  # ISO 字符串，保持旧结构
        _assert_json_safe(self, info)

        self.assertFalse(self.fs.get_file_info("listing/missing.txt")["success"])

    def test_inspect_path_file_missing_dir_and_root(self) -> None:
        (self.workspace / "inspect.txt").write_text("12345", encoding="utf-8")

        file_info = self.fs.inspect_path("inspect.txt")
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
        self.assertTrue(file_info["exists"])
        self.assertTrue(file_info["is_file"])
        self.assertFalse(file_info["is_dir"])
        self.assertFalse(file_info["is_symlink"])
        self.assertTrue(file_info["within_workspace"])
        self.assertEqual(file_info["path"], "inspect.txt")
        self.assertEqual(file_info["relative_path"], "inspect.txt")
        self.assertEqual(file_info["suffix"], ".txt")
        self.assertEqual(file_info["size"], 5)
        self.assertIsInstance(file_info["mtime"], float)
        _assert_json_safe(self, file_info)

        missing = self.fs.inspect_path("missing.txt")
        self.assertTrue(missing["success"], missing)
        self.assertFalse(missing["exists"])
        self.assertFalse(missing["is_file"])
        self.assertIsNone(missing["size"])
        self.assertIsNone(missing["mtime"])
        self.assertEqual(missing["suffix"], ".txt")

        (self.workspace / "inspect_dir").mkdir(exist_ok=True)
        folder_info = self.fs.inspect_path("inspect_dir")
        self.assertTrue(folder_info["success"], folder_info)
        self.assertTrue(folder_info["is_dir"])
        self.assertFalse(folder_info["is_file"])
        self.assertEqual(folder_info["suffix"], "")
        self.assertIsNone(folder_info["size"])

        root_info = self.fs.inspect_path("")
        self.assertTrue(root_info["success"], root_info)
        self.assertTrue(root_info["is_dir"])
        self.assertEqual(root_info["relative_path"], ".")

        write_probe = self.fs.inspect_path("not_created_yet.txt", access="write")
        self.assertTrue(write_probe["success"], write_probe)
        self.assertFalse(write_probe["exists"])

        bad_access = self.fs.inspect_path("inspect.txt", access="execute")
        self.assertFalse(bad_access["success"])

    def test_inspect_path_detects_symlink_without_following_link_metadata(self) -> None:
        target = self.workspace / "real.dat"
        target.write_text("12345", encoding="utf-8")
        link = self.workspace / "link.txt"
        os.symlink(target, link)

        info = self.fs.inspect_path("link.txt")
        self.assertTrue(info["success"], info)
        self.assertTrue(info["is_symlink"])
        self.assertTrue(info["exists"])
        self.assertTrue(info["is_file"])
        self.assertEqual(info["size"], 5)
        self.assertEqual(info["suffix"], ".txt")
        self.assertTrue(info["within_workspace"])

        # 指向工作区外的链接：授权阶段必须拒绝，不能返回目标元数据。
        outside_target = self.outside / "outside-secret.txt"
        outside_target.write_text("outside", encoding="utf-8")
        escape_link = self.workspace / "escape.link"
        os.symlink(outside_target, escape_link)
        denied = self.fs.inspect_path("escape.link")
        self.assertFalse(denied["success"], denied)
        self.assertNotIn("exists", denied)

    def test_resolve_path_reports_access_without_stat(self) -> None:
        resolved = self.fs.resolve_path("resolve_me.txt", access="write")
        self.assertTrue(resolved["success"], resolved)
        self.assertEqual(resolved["access"], "write")
        self.assertEqual(resolved["path"], "resolve_me.txt")
        self.assertTrue(resolved["within_workspace"])
        self.assertFalse((self.workspace / "resolve_me.txt").exists())

        denied = self.fs.resolve_path(str(self.outside / "nope.txt"), access="write")
        self.assertFalse(denied["success"])

    def test_scoped_hardlink_rules_are_preserved(self) -> None:
        """子智能体作用域下，多硬链接仍由 scoped_io 拒绝（适配器不绕过）。"""
        original = self.workspace / "hardlink_original.txt"
        original.write_text("secret", encoding="utf-8")
        alias = self.workspace / "hardlink_alias.txt"
        os.link(original, alias)
        scope = ExecutionScope("sub_agent", "child", self.workspace, "workspace_write")

        with bind_execution_scope(scope):
            self.assertFalse(self.fs.read_file("hardlink_alias.txt")["success"])
            self.assertFalse(self.fs.write_file("hardlink_alias.txt", "changed")["success"])
        self.assertEqual(original.read_text(encoding="utf-8"), "secret")

    def test_get_file_info_authorizes_before_exists(self) -> None:
        """未授权路径不得先 exists() 探测；授权失败时直接返回拒绝。"""
        probe = self.workspace / "auth_probe.txt"
        probe.write_text("exists", encoding="utf-8")
        with mock.patch.object(
            self.file_manager, "_ensure_host_access", return_value=(False, "denied")
        ), mock.patch(
            "pathlib.Path.exists", side_effect=AssertionError("exists before auth")
        ):
            result = self.file_manager.get_file_info("auth_probe.txt")
        self.assertFalse(result["success"], result)
        self.assertEqual(result["error"], "denied")

    # -- 权限边界未被适配器绕过 -----------------------------------------
    def test_outside_workspace_read_write_metadata_are_denied(self) -> None:
        secret = self.outside / "secret.txt"
        secret.write_text("outside", encoding="utf-8")

        self.assertFalse(self.fs.read_file(str(secret))["success"])
        self.assertFalse(self.fs.get_file_info(str(secret))["success"])
        self.assertFalse(self.fs.list_files(str(self.outside))["success"])
        self.assertFalse(self.fs.inspect_path(str(secret))["success"])

        blocked = self.outside / "blocked.txt"
        write_result = self.fs.write_file(str(blocked), "nope")
        self.assertFalse(write_result["success"], write_result)
        self.assertFalse(blocked.exists())
        self.assertFalse(self.fs.create_file(str(self.outside / "new.txt"))["success"])
        self.assertFalse(self.fs.delete_file(str(secret))["success"])
        self.assertTrue(secret.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
