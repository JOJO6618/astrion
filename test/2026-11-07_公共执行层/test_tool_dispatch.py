"""工具分发执行层路由回归（2026-11-07）。

覆盖范围（全部走真实 ``handle_tool_call`` 分发，不检查源码字符串）：

1. 读：read_file 的 read / search / extract 三模式 → ``backend.files``；
2. 写：create_file / delete_file / rename_file / create_folder → ``backend.files``；
3. 编辑守门：write_file 覆盖已存在文件与 edit_file 必须先 read_file；
   元数据探测被拒**不能**被当成"新文件"放行覆盖；
4. 终端：terminal_session(open) / terminal_input / terminal_snapshot →
   ``backend.terminals``，working_dir 必须原样透传；
5. 命令：run_command 前台/后台 → ``backend.run_command`` /
   ``backend.run_command_background``，working_dir 透传；sleep(wait_runcommand_id)
   → ``backend.background``；
6. 媒体：view_image 经 ``backend.files.inspect_path`` 校验后再挂 pending；
7. MCP：list_mcp_servers / mcp__ alias → ``backend.list_mcp_servers`` /
   ``backend.execute_mcp_tool``；
8. 自定义工具 → ``backend.execute_custom_tool``。

所有用例的控制端 ``file_manager`` / ``terminal_manager`` / ``terminal_ops`` /
``background_command_manager`` 都被毒化：任何访问都会抛 ``PoisonedAccessError``
（BaseException），不会被 ``except Exception`` 吞掉。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_TEST_DIR = Path(__file__).resolve().parent
if str(_TEST_DIR) not in sys.path:
    sys.path.insert(0, str(_TEST_DIR))

# 复用夹具：内部已先隔离运行态再导入 config/modules。
from tool_dispatch_harness import (  # noqa: E402
    DenyInspectBackend,
    PoisonedAccessError,
    RegistryStub,
    SpyBackend,
    ToolDispatchTerminal,
    call_tool,
    make_seeded_backend,
)


class ReadRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_search_extract_route_to_backend(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        read = await call_tool(terminal, "read_file", {"path": "notes.md"})
        self.assertTrue(read["success"], read)
        self.assertEqual(read["path"], "notes.md")
        self.assertEqual(read["content"], "line-a\nline-b\nline-c\n")
        self.assertEqual(read["total_lines"], 3)
        # 整文件读取已持有原文，交缓存时不能再让 ContextManager 读一次。
        self.assertEqual(terminal.context_manager.load_file_calls, [("notes.md", "line-a\nline-b\nline-c\n")])

        search = await call_tool(
            terminal, "read_file", {"path": "notes.md", "type": "search", "query": "line-b"}
        )
        self.assertTrue(search["success"], search)
        self.assertEqual(search["actual_matches"], 1)
        self.assertIn("line-b", search["matches"][0]["snippet"])

        extract = await call_tool(
            terminal,
            "read_file",
            {"path": "notes.md", "type": "extract", "segments": [{"start_line": 1, "end_line": 2}]},
        )
        self.assertTrue(extract["success"], extract)
        self.assertEqual(extract["segment_count"], 1)
        self.assertIn("line-a", extract["segments"][0]["content"])

        # 读侧没有产生任何写调用，且内容仍由内存后端持有。
        self.assertEqual(terminal.ops("write_file"), [])
        self.assertEqual(backend.files.get("notes.md"), "line-a\nline-b\nline-c\n")


class WriteRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_delete_rename_mkdir_route_to_backend(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        created = await call_tool(
            terminal, "create_file", {"path": "new_note", "file_type": "txt"}
        )
        self.assertTrue(created["success"], created)
        self.assertEqual(created["path"], "new_note.txt")
        self.assertIn("new_note.txt", backend.files.paths())

        folder = await call_tool(terminal, "create_folder", {"path": "sub"})
        self.assertTrue(folder["success"], folder)
        self.assertIn("sub", backend.files.folder_paths())

        renamed = await call_tool(
            terminal, "rename_file", {"old_path": "new_note.txt", "new_path": "sub/moved.txt"}
        )
        self.assertTrue(renamed["success"], renamed)
        self.assertEqual(renamed["old_path"], "new_note.txt")
        self.assertEqual(renamed["new_path"], "sub/moved.txt")
        self.assertNotIn("new_note.txt", backend.files.paths())

        deleted = await call_tool(terminal, "delete_file", {"path": "sub/moved.txt"})
        self.assertTrue(deleted["success"], deleted)
        self.assertEqual(deleted["action"], "deleted")
        self.assertNotIn("sub/moved.txt", backend.files.paths())

    async def test_write_new_file_is_allowed_without_prior_read(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        result = await call_tool(
            terminal, "write_file", {"file_path": "fresh.md", "content": "fresh\n"}
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(backend.files.get("fresh.md"), "fresh\n")
        writes = terminal.ops("write_file")
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["mode"], "w")

    async def test_write_existing_file_requires_prior_read(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        blocked = await call_tool(
            terminal, "write_file", {"file_path": "notes.md", "content": "overwritten\n"}
        )
        self.assertFalse(blocked["success"], blocked)
        self.assertEqual(blocked["enforcement"], "read_before_edit_required")
        self.assertEqual(blocked["required_path"], "notes.md")
        self.assertEqual(terminal.ops("write_file"), [])
        self.assertEqual(backend.files.get("notes.md"), "line-a\nline-b\nline-c\n")

    async def test_write_after_read_is_allowed(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        read = await call_tool(terminal, "read_file", {"path": "notes.md"})
        self.assertTrue(read["success"], read)

        written = await call_tool(
            terminal, "write_file", {"file_path": "notes.md", "content": "rewritten\n"}
        )
        self.assertTrue(written["success"], written)
        self.assertEqual(backend.files.get("notes.md"), "rewritten\n")
        self.assertEqual(len(terminal.ops("write_file")), 1)

    async def test_metadata_denial_is_not_treated_as_new_file(self) -> None:
        """元数据探测失败 ≠ 文件不存在：不允许绕过先读守门直接覆盖。"""
        backend = DenyInspectBackend({"secret.md"})
        backend.files.seed("secret.md", "classified\n")
        terminal = ToolDispatchTerminal(backend)

        result = await call_tool(
            terminal, "write_file", {"file_path": "secret.md", "content": "leaked\n"}
        )
        self.assertFalse(result["success"], result)
        self.assertIn("permission denied", result.get("error", ""))
        # 拒绝必须表现为失败，而不是"新建文件"通过。
        self.assertEqual(terminal.ops("write_file"), [])
        self.assertEqual(backend.files.read_file("secret.md")["content"], "classified\n")

    async def test_edit_requires_read_then_applies_through_backend(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)
        replacements = [{"old_string": "hello world", "new_string": "hello backend"}]

        blocked = await call_tool(
            terminal, "edit_file", {"file_path": "docs/readme.txt", "replacements": replacements}
        )
        self.assertFalse(blocked["success"], blocked)
        self.assertEqual(blocked["enforcement"], "read_before_edit_required")
        self.assertEqual(terminal.ops("edit_file"), [])

        read = await call_tool(terminal, "read_file", {"path": "docs/readme.txt"})
        self.assertTrue(read["success"], read)

        edited = await call_tool(
            terminal, "edit_file", {"file_path": "docs/readme.txt", "replacements": replacements}
        )
        self.assertTrue(edited["success"], edited)
        self.assertEqual(backend.files.get("docs/readme.txt"), "hello backend\n")
        self.assertEqual(len(terminal.ops("edit_file")), 1)


class TerminalRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_open_input_snapshot_route_to_backend(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        opened = await call_tool(
            terminal,
            "terminal_session",
            {"action": "open", "session_name": "s1", "working_dir": "sub/dir"},
        )
        self.assertTrue(opened["success"], opened)
        self.assertEqual(opened["action"], "open")
        # working_dir 必须原样透传到后端会话，而不是被控制侧改写。
        self.assertEqual(backend.terminals.sessions["s1"]["working_dir"], "sub/dir")

        typed = await call_tool(
            terminal,
            "terminal_input",
            {"command": "echo routed", "session_name": "s1", "output_wait": 1},
        )
        self.assertTrue(typed["success"], typed)
        self.assertEqual(typed["output"], "[fake-terminal] echo routed\n")

        snapshot = await call_tool(
            terminal, "terminal_snapshot", {"session_name": "s1", "lines": 10}
        )
        self.assertTrue(snapshot["success"], snapshot)
        self.assertIn("echo routed", snapshot["output"])
        self.assertIn("send_to_terminal", [c["op"] for c in backend.terminals.calls])

    async def test_foreground_run_command_passes_working_dir(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        result = await call_tool(
            terminal, "run_command", {"command": "ls -la", "timeout": 10, "working_dir": "src"}
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["output"], "[fake-exec] ls -la")
        calls = terminal.ops("run_command")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["working_dir"], "src")
        self.assertEqual(calls[0]["command"], "ls -la")

    async def test_background_run_and_sleep_wait_route_to_backend(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        started = await call_tool(
            terminal,
            "run_command",
            {
                "command": "sleep 1 && echo done",
                "timeout": 30,
                "run_in_background": True,
                "working_dir": "jobs",
            },
        )
        self.assertTrue(started["success"], started)
        self.assertEqual(started["status"], "running_background")
        command_id = started["command_id"]
        bg_calls = terminal.ops("run_command_background")
        self.assertEqual(len(bg_calls), 1)
        self.assertEqual(bg_calls[0]["working_dir"], "jobs")
        self.assertEqual(bg_calls[0]["conversation_id"], "conv-dispatch")

        waited = await call_tool(terminal, "sleep", {"wait_runcommand_id": command_id})
        self.assertTrue(waited["success"], waited)
        self.assertEqual(waited["mode"], "wait_runcommand_id")
        self.assertEqual(waited["command_id"], command_id)
        record = backend.background.records[command_id]
        self.assertEqual(record["status"], "completed")
        self.assertTrue(record["claimed"])

    async def test_sleep_wait_unknown_id_reports_not_found(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        result = await call_tool(terminal, "sleep", {"wait_runcommand_id": "missing-id"})
        self.assertFalse(result["success"], result)
        self.assertIn("missing-id", result["error"])


class MediaRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_view_image_validates_through_backend(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        attached = await call_tool(terminal, "view_image", {"path": "assets/pic.png"})
        self.assertTrue(attached["success"], attached)
        self.assertEqual(attached["path"], "assets/pic.png")
        self.assertEqual(terminal.pending_image_view, {"path": "assets/pic.png"})

        missing = await call_tool(terminal, "view_image", {"path": "assets/nope.png"})
        self.assertFalse(missing["success"], missing)
        # 失败不能留下可被后续附加的 pending 记录。
        self.assertEqual(terminal.pending_image_view, {"path": "assets/pic.png"})

        unsupported = await call_tool(terminal, "view_image", {"path": "plain.txt"})
        self.assertFalse(unsupported["success"], unsupported)
        self.assertIn(".txt", unsupported["error"])


class IntegrationRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_mcp_servers_routes_to_backend(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        result = await call_tool(terminal, "list_mcp_servers", {})
        # Fake 后端没有插件客户端：必须显式返回空列表，而不是伪造控制端列表。
        self.assertTrue(result["success"], result)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["servers"], [])

    async def test_mcp_alias_routes_to_backend_executor(self) -> None:
        backend = SpyBackend()
        terminal = ToolDispatchTerminal(backend)
        terminal.mcp_tools_enabled = True

        result = await call_tool(terminal, "mcp__demo__echo", {"text": "hi", "limit": 3})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["served_by"], "spy-backend")
        self.assertEqual(len(backend.mcp_calls), 1)
        self.assertEqual(backend.mcp_calls[0]["tool_name"], "mcp__demo__echo")
        self.assertEqual(backend.mcp_calls[0]["arguments"], {"text": "hi", "limit": 3})

    async def test_mcp_alias_requires_enabled_flag_without_backend_call(self) -> None:
        backend = SpyBackend()
        terminal = ToolDispatchTerminal(backend)
        terminal.mcp_tools_enabled = False

        result = await call_tool(terminal, "mcp__demo__echo", {"text": "hi"})
        self.assertFalse(result["success"], result)
        self.assertEqual(backend.mcp_calls, [])

    async def test_custom_tool_routes_to_backend_executor(self) -> None:
        backend = SpyBackend()
        terminal = ToolDispatchTerminal(backend)
        terminal.custom_tools_enabled = True
        terminal.user_role = "admin"
        terminal.custom_tool_registry = RegistryStub({"my_tool": {"name": "my_tool"}})

        result = await call_tool(terminal, "my_tool", {"query": "abc"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["served_by"], "spy-backend")
        self.assertEqual(backend.custom_calls, [{"tool_name": "my_tool", "arguments": {"query": "abc"}}])
        self.assertGreaterEqual(terminal.custom_tool_registry.reloads, 1)


class CrossToolPoisonSweepTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_supported_tools_survive_poisoned_controller_resources(self) -> None:
        """一次性扫过代表性子集：任一工具回退控制端资源都会抛 PoisonedAccessError。"""
        backend = SpyBackend()
        backend.files.seed("notes.md", "alpha\n")
        backend.files.seed("assets/pic.png", b"\x89PNG\r\n\x1a\n")
        terminal = ToolDispatchTerminal(backend)
        terminal.mcp_tools_enabled = True
        terminal.custom_tools_enabled = True
        terminal.user_role = "admin"
        terminal.custom_tool_registry = RegistryStub({"my_tool": {"name": "my_tool"}})

        cases = [
            ("read_file", {"path": "notes.md"}),
            ("write_file", {"file_path": "sweep.txt", "content": "x\n"}),
            ("create_folder", {"path": "sweep_dir"}),
            ("terminal_session", {"action": "open", "session_name": "sweep"}),
            ("terminal_input", {"command": "pwd", "session_name": "sweep", "output_wait": 0}),
            ("terminal_snapshot", {"session_name": "sweep"}),
            ("run_command", {"command": "echo ok", "timeout": 5, "working_dir": "sweep_dir"}),
            ("view_image", {"path": "assets/pic.png"}),
            ("list_mcp_servers", {}),
            ("mcp__demo__echo", {"text": "x"}),
            ("my_tool", {"query": "x"}),
        ]
        for name, arguments in cases:
            with self.subTest(tool=name):
                result = await call_tool(terminal, name, arguments)
                self.assertTrue(result.get("success"), f"{name} 未成功: {result}")


class HarnessSanityTest(unittest.IsolatedAsyncioTestCase):
    """证明夹具本身有牙齿，避免"毒化没生效、测试假通过"。"""

    async def test_poisoned_resource_raises_base_exception(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        with self.assertRaises(PoisonedAccessError):
            terminal.file_manager.read_file("notes.md")

        # 关键：不能被 `except Exception` 静默吞掉（分发代码里到处是这种兜底）。
        try:
            try:
                terminal.terminal_manager.list_terminals()
            except Exception:  # noqa: BLE001 - 故意验证兜底挡不住
                self.fail("毒化异常被 except Exception 吞掉了")
        except PoisonedAccessError:
            pass

    async def test_dispatch_keeps_explicit_backend_and_never_builds_local_one(self) -> None:
        backend = make_seeded_backend()
        terminal = ToolDispatchTerminal(backend)

        await call_tool(terminal, "read_file", {"path": "notes.md"})
        self.assertIs(terminal.execution_backend, backend)
        self.assertEqual(type(terminal.execution_backend).__name__, "FakeExecutionBackend")


if __name__ == "__main__":
    unittest.main()
