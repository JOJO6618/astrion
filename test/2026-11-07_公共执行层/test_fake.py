"""FakeExecutionBackend 协议完整性测试（不依赖 pytest，直接 python3 运行）。

覆盖：
1. ``base.ExecutionBackend`` 协议一致性（files/terminals/background/get_environment/命令/写入）；
2. 既有验收的 E1-E4 调用序列与 ``backend.files.get(...)`` 存量断言；
3. 文件协议全方法（读/片段/搜索/多段/二进制/写/替换/建删改/文件夹/列表/信息/检查/解析）；
4. 内存隔离硬约束：把所有本地文件系统入口打成"调用即失败"，文件操作仍全部成功；
5. 终端与后台替身的最小状态机；
6. ``get_environment`` 声明值（is_local=False）与 JSON 可序列化。

运行：python3 test_fake.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import unittest

# 测试文件在 test/<日期>_<主题>/ 下，导入业务包需要仓库根在 sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from modules.execution_plane.base import ExecutionBackend  # noqa: E402
from modules.execution_plane.fake import FakeExecutionBackend  # noqa: E402
from modules.execution_plane.fake_files import FakeFileSystem  # noqa: E402


class LocalAccessGuard:
    """把所有本地 FS 入口替换成"记录并失败"，用于证明替身零本地访问。"""

    def __init__(self) -> None:
        self.hits = []
        self._saved = []

    def _patch(self, target, attr, name):
        original = getattr(target, attr, None)
        if callable(original):
            self._saved.append((target, attr, original))

            def guard(*args, **kwargs):
                self.hits.append(f"{name}:{args[1:] if args else ''}")
                raise AssertionError(f"fake backend touched local filesystem via {name}")

            setattr(target, attr, guard)

    def __enter__(self):
        for attr in ("exists", "is_file", "is_dir", "stat", "read_text", "read_bytes",
                     "open", "resolve", "iterdir", "mkdir", "write_text", "unlink"):
            self._patch(pathlib.Path, attr, f"Path.{attr}")
        for attr in ("open", "stat", "listdir", "scandir", "mkdir", "remove", "rename"):
            self._patch(os, attr, f"os.{attr}")
        return self

    def __exit__(self, exc_type, exc, tb):
        for target, attr, original in reversed(self._saved):
            setattr(target, attr, original)
        return False


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeExecutionBackend()

    def test_protocol_shape(self):
        self.assertIsInstance(self.backend, ExecutionBackend)
        for name in ("files", "terminals", "background"):
            self.assertTrue(hasattr(self.backend, name), name)
        self.assertIsNotNone(self.backend.background)
        # 公共接口：track_file_edit 显式存在，且是“无磁盘 no-op”
        self.assertTrue(hasattr(self.backend, "track_file_edit"))
        self.assertIsNone(
            self.backend.track_file_edit("notes.md", conversation_id="c1", message_id="m1")
        )
        self.assertEqual(len(self.backend.tracked_edits), 1)
        self.assertFalse(self.backend.tracked_edits[0]["backed_up"])

    def test_track_file_edit_does_not_touch_disk(self):
        self.backend.write_file("notes.md", "alpha\n")
        with LocalAccessGuard() as guard:
            self.backend.track_file_edit(
                "notes.md", conversation_id="c1", message_id="m1"
            )
        self.assertEqual(guard.hits, [], f"不应触碰本地文件系统: {guard.hits}")
        self.assertEqual(self.backend.files.paths(), ["notes.md"], "不应产生备份文件")
        self.assertFalse(self.backend.tracked_edits[0]["backed_up"])

    def test_environment_is_declared_not_probed(self):
        env = self.backend.get_environment()
        self.assertEqual(env["backend"], "fake")
        self.assertIs(env["is_local"], False)
        self.assertEqual(env["workspace_root"], "/workspace")
        json.dumps(env)  # 必须可序列化

    def test_e1_e4_call_sequence_preserved(self):
        async def run():
            return await self.backend.run_command(
                "echo hello-fake", timeout=5,
                sandbox_write_access=False, network_permission="none",
            )

        r1 = asyncio.run(run())
        self.assertTrue(r1["success"])
        self.assertIn("[fake-exec]", r1["output"])

        r2 = asyncio.run(self.backend.run_command_background(
            "sleep 1", timeout=60, conversation_id="conv-1", wait_seconds=0,
            network_permission="none", sandbox_write_access=False,
        ))
        self.assertTrue(r2["success"])
        self.assertTrue(r2["command_id"].startswith("fake_bg_"))

        r3 = self.backend.write_file("fake_probe.txt", "line-a\n")
        self.assertTrue(r3["success"])
        self.assertEqual(self.backend.files.get("fake_probe.txt"), "line-a\n")

        r4 = self.backend.edit_file(
            "fake_probe.txt", [{"old_string": "line-a", "new_string": "line-b"}]
        )
        self.assertTrue(r4["success"])
        self.assertEqual(self.backend.files.get("fake_probe.txt"), "line-b\n")

        self.assertEqual(
            [call["op"] for call in self.backend.calls],
            ["run_command", "run_command_background", "write_file", "edit_file"],
        )

    def test_run_command_accepts_working_dir(self):
        async def run():
            return await self.backend.run_command(
                "pwd", timeout=1, sandbox_write_access=False,
                network_permission="none", working_dir="/workspace/sub",
            )
        r = asyncio.run(run())
        self.assertTrue(r["success"])
        self.assertEqual(r["working_dir"], "/workspace/sub")


class FileProtocolTests(unittest.TestCase):
    def setUp(self):
        self.fs = FakeFileSystem(clock=lambda: 1_700_000_000.0)
        self.fs.write_file("notes.md", "alpha\nbeta\ngamma\n")
        self.fs.seed("pkg/sub/data.txt", "x=1\ny=2\n")

    def test_read_and_segment(self):
        r = self.fs.read_file("notes.md")
        self.assertTrue(r["success"])
        self.assertEqual(r["content"], "alpha\nbeta\ngamma\n")
        self.assertEqual(r["size"], len("alpha\nbeta\ngamma\n"))

        seg = self.fs.read_text_segment("notes.md", start_line=2, end_line=3)
        self.assertTrue(seg["success"])
        self.assertEqual(seg["content"], "beta\ngamma\n")
        self.assertEqual(seg["total_lines"], 3)

        bad = self.fs.read_text_segment("notes.md", start_line=9, end_line=10)
        self.assertFalse(bad["success"])

        too_big = self.fs.read_text_segment("notes.md", size_limit=3)
        self.assertFalse(too_big["success"])

    def test_search_and_extract(self):
        found = self.fs.search_text("notes.md", query="beta", max_matches=5,
                                    context_before=1, context_after=1)
        self.assertTrue(found["success"])
        self.assertEqual(found["matches"][0]["hits"], [2])
        self.assertEqual(found["matches"][0]["snippet"], "alpha\nbeta\ngamma\n")

        nothing = self.fs.search_text("notes.md", query="zzz", max_matches=5,
                                      context_before=0, context_after=0)
        self.assertTrue(nothing["success"])
        self.assertEqual(nothing["matches"], [])

        segs = self.fs.extract_segments("notes.md", [
            {"label": "head", "start_line": 1, "end_line": 1},
            {"label": "tail", "start_line": 3, "end_line": 99},
        ])
        self.assertTrue(segs["success"])
        self.assertEqual([s["content"] for s in segs["segments"]], ["alpha\n", "gamma\n"])

    def test_binary_roundtrip(self):
        self.fs.seed("blob.bin", b"\xff\x00\xfe")
        self.assertEqual(self.fs.read_binary("blob.bin"), b"\xff\x00\xfe")
        # 非 UTF-8 内容走文本接口必须明确失败，不能兜底解码
        self.assertFalse(self.fs.read_file("blob.bin")["success"])
        with self.assertRaises(FileNotFoundError):
            self.fs.read_binary("missing.bin")
        with self.assertRaises(PermissionError):
            self.fs.read_binary("../escape.txt")

    def test_write_replace_create_delete_rename(self):
        w = self.fs.write_file("notes.md", " delta\n", mode="a")
        self.assertTrue(w["success"])
        self.assertEqual(w["new_file"], "alpha\nbeta\ngamma\n delta\n")
        self.assertEqual(w["original_file"], "alpha\nbeta\ngamma\n")

        rep = self.fs.replace_many_in_file("notes.md", [
            {"old_string": "alpha", "new_string": "ALPHA"},
            {"old_string": "nope", "new_string": "x"},
        ])
        self.assertTrue(rep["success"])
        self.assertEqual(rep["replacements_applied"], 1)
        self.assertFalse(
            self.fs.replace_many_in_file("notes.md", [{"old_string": "nope", "new_string": "x"}])["success"]
        )

        created = self.fs.create_file("report")
        self.assertTrue(created["success"])
        self.assertEqual(created["path"], "report.txt")

        ren = self.fs.rename_file("report.txt", "report_final.txt")
        self.assertTrue(ren["success"])
        self.assertEqual(ren["action"], "renamed")
        self.assertNotIn("report.txt", self.fs.paths())

        dele = self.fs.delete_file("report_final.txt")
        self.assertTrue(dele["success"])
        self.assertEqual(dele["action"], "deleted")

    def test_folders_list_and_info(self):
        made = self.fs.create_folder("logs")
        self.assertTrue(made["success"])
        self.assertFalse(self.fs.create_folder("logs")["success"])

        listing = self.fs.list_files("")
        self.assertTrue(listing["success"])
        names = [item["name"] for item in listing["files"]]
        self.assertIn("notes.md", names)
        self.assertIn("pkg", [item["name"] for item in listing["folders"]])
        self.assertIn("logs", [item["name"] for item in listing["folders"]])

        info = self.fs.get_file_info("notes.md")
        self.assertTrue(info["success"])
        self.assertEqual(info["type"], "file")
        self.assertEqual(info["extension"], ".md")
        self.assertEqual(info["size"], len("alpha\nbeta\ngamma\n"))

        folder_info = self.fs.get_file_info("pkg")
        self.assertEqual(folder_info["type"], "folder")

        removed = self.fs.delete_folder("pkg")
        self.assertTrue(removed["success"])
        self.assertEqual([p for p in self.fs.paths() if p.startswith("pkg/")], [])
        self.assertFalse(self.fs.delete_folder("")["success"])

    def test_inspect_and_resolve(self):
        ins = self.fs.inspect_path("notes.md")
        self.assertTrue(ins["success"])
        self.assertTrue(ins["exists"] and ins["is_file"])
        self.assertFalse(ins["is_symlink"])
        self.assertEqual(ins["relative_path"], "notes.md")

        missing = self.fs.inspect_path("ghost.txt")
        self.assertTrue(missing["success"])
        self.assertFalse(missing["exists"])

        res = self.fs.resolve_path("/workspace/notes.md", access="write")
        self.assertTrue(res["success"])
        self.assertEqual(res["path"], "notes.md")
        self.assertEqual(res["access"], "write")

        self.assertFalse(self.fs.inspect_path("notes.md", access="execute")["success"])
        self.assertFalse(self.fs.resolve_path("/etc/passwd")["success"])
        self.assertFalse(self.fs.read_file("../outside.txt")["success"])

    def test_dict_view_matches_legacy_assertion(self):
        self.fs.write_file("fake_probe.txt", "line-a\n")
        self.assertEqual(self.fs.get("fake_probe.txt"), "line-a\n")
        self.assertEqual(self.fs["fake_probe.txt"], "line-a\n")
        self.assertIn("fake_probe.txt", dict.keys(self.fs))
        self.assertEqual(sorted(self.fs.paths()), ["fake_probe.txt", "notes.md", "pkg/sub/data.txt"])

    def test_no_local_filesystem_access(self):
        fs = FakeFileSystem()
        with LocalAccessGuard() as guard:
            fs.write_file("a.txt", "one\n")
            fs.read_file("a.txt")
            fs.read_text_segment("a.txt", start_line=1, end_line=1)
            fs.search_text("a.txt", query="one", max_matches=1, context_before=0, context_after=0)
            fs.extract_segments("a.txt", [{"start_line": 1, "end_line": 1}])
            fs.read_binary("a.txt")
            fs.create_file("b")
            fs.create_folder("d")
            fs.list_files("")
            fs.get_file_info("a.txt")
            fs.inspect_path("a.txt", access="write")
            fs.resolve_path("a.txt", access="read")
            fs.rename_file("b.txt", "c.txt")
            fs.delete_file("c.txt")
            fs.delete_folder("d")
        self.assertEqual(guard.hits, [], f"不应触碰本地文件系统: {guard.hits}")


class TerminalAndBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeExecutionBackend(clock=lambda: 1_700_000_000.0)

    def test_terminal_lifecycle(self):
        terms = self.backend.terminals
        opened = terms.open_terminal("t1", working_dir="/workspace")
        self.assertTrue(opened["success"])
        self.assertEqual(opened["shell"], "fake-shell")
        self.assertFalse(terms.open_terminal("t1")["success"])

        sent = terms.send_to_terminal("echo hi", "t1", output_wait=1)
        self.assertTrue(sent["success"])
        self.assertEqual(sent["status"], "completed")
        self.assertIn("echo hi", sent["output"])

        snap = terms.get_terminal_snapshot("t1")
        self.assertTrue(snap["success"])
        self.assertIn("echo hi", snap["output"])
        self.assertEqual(len(terms.get_terminal_list()), 1)
        self.assertTrue(terms.list_terminals()["success"])

        self.assertTrue(terms.close_terminal("t1")["success"])
        self.assertEqual(terms.get_terminal_list(), [])
        terms.close_all()

    def test_background_state_machine(self):
        bg = self.backend.background
        record = bg.create_record("sleep 5", conversation_id="c1")
        command_id = record["command_id"]
        self.assertEqual(bg.get_record(command_id)["status"], "running")
        self.assertTrue(bg.has_pending_for_conversation("c1"))
        self.assertEqual(len(bg.list_waiting_items("c1")), 1)

        waited = bg.wait_for_completion(command_id, timeout_seconds=1)
        self.assertTrue(waited["success"])
        self.assertEqual(waited["status"], "completed")

        updates = bg.poll_updates("c1")
        self.assertEqual(len(updates), 1)
        bg.mark_notified(command_id)
        self.assertEqual(bg.poll_updates("c1"), [])

        other = bg.create_record("sleep 9", conversation_id="c2")
        cancelled = bg.cancel_command(other["command_id"])
        self.assertTrue(cancelled["success"])
        self.assertEqual(bg.get_record(other["command_id"])["status"], "cancelled")
        self.assertFalse(bg.cancel_command(other["command_id"])["success"])

        stale = bg.create_record("sleep 7", conversation_id="c3")
        self.assertEqual(bg.reconcile_stale_records("c3"), 1)
        self.assertEqual(bg.get_record(stale["command_id"])["status"], "failed")

    def test_background_unmark_notified(self):
        bg = self.backend.background
        record = bg.create_record("sleep 1", conversation_id="c1", auto_complete=True)
        command_id = record["command_id"]
        self.assertEqual(len(bg.poll_updates("c1")), 1)

        bg.mark_notified(command_id)
        self.assertEqual(bg.poll_updates("c1"), [])

        # 回退水位后应重新出现在待通知列表
        self.assertIsNone(bg.unmark_notified(command_id))
        self.assertFalse(bg.get_record(command_id)["notified"])
        self.assertEqual([u["command_id"] for u in bg.poll_updates("c1")], [command_id])

        # 未知 id 是安静的 no-op（不抛异常）
        self.assertIsNone(bg.unmark_notified("missing-id"))

    def test_background_mark_all_notified_is_not_truncated(self):
        bg = self.backend.background
        total = 1005  # 明确超过 1000，验证无隐式截断
        ids = [
            bg.create_record("job-%d" % i, conversation_id="c1", auto_complete=True)["command_id"]
            for i in range(total)
        ]
        bg.create_record("job-other", conversation_id="c2", auto_complete=True)
        running = bg.create_record("job-running", conversation_id="c1")  # 非终态不参与

        self.assertEqual(len(bg.poll_updates("c1")), total)
        self.assertEqual(bg.mark_all_notified("c1"), total)
        self.assertEqual(bg.poll_updates("c1"), [])
        self.assertFalse(bg.get_record(running["command_id"])["notified"])
        self.assertTrue(all(bg.get_record(i)["notified"] for i in ids))

        # 其他对话不受影响；再调用一次返回 0（幂等）
        self.assertEqual(len(bg.poll_updates("c2")), 1)
        self.assertEqual(bg.mark_all_notified("c1"), 0)
        self.assertEqual(bg.mark_all_notified("c2"), 1)

    def test_background_no_auto_complete(self):
        backend = FakeExecutionBackend(auto_complete_background=False)
        record = backend.background.create_record("sleep 5", conversation_id="c9")
        waited = backend.background.wait_for_completion(record["command_id"])
        self.assertFalse(waited["success"])
        self.assertEqual(waited["status"], "running")
        self.assertTrue(waited["timeout"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
