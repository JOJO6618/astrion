"""Server 侧后台/审批执行出口接线回归（离线，不启动服务、不碰真实对话）。

目标：后台观察/查询/取消/通知，以及编辑审批预览的文件存在性/内容读取，
统一经 ``modules.execution_plane.get_execution_backend(terminal)`` 的
``background`` / ``files`` 视图；显式注入替身后端时不得再触本机
``background_command_manager`` / ``file_manager``。

本文件保留后台/审批/端点/真实管理器/源码门禁用例；计划流与检查点恢复见
``test_server_routing_plan.py``，公共夹具见 ``server_routing_fixtures.py``。
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import chat_flow_notice_state, chat_flow_task_support, work_timer  # noqa: E402
from server.chat_flow_tool_loop import _build_tool_approval_preview  # noqa: E402
from server.tasks.models import TaskManager  # noqa: E402
from modules.background_command_manager import BackgroundCommandManager  # noqa: E402

from server_routing_fixtures import (  # noqa: E402
    SpyBackend,
    SpyBackground,
    SpyFiles,
    make_terminal,
)


class BackgroundRoutingTests(unittest.TestCase):
    def test_none_terminal_keeps_original_defense(self):
        self.assertIsNone(chat_flow_notice_state._background_processes(None))
        self.assertIsNone(work_timer._background_processes(None))

    def test_collect_notices_uses_backend_background(self):
        background = SpyBackground(updates=[{
            "command_id": "bg_1",
            "command": "sleep 1",
            "output": "done",
            "return_code": 0,
            "status": "completed",
            "updated_at": 1.0,
        }])
        terminal = make_terminal(SpyBackend(background=background))

        notices = chat_flow_notice_state._collect_pending_completion_notices(
            web_terminal=terminal, conversation_id="conv_synthetic",
        )

        self.assertEqual(("poll_updates", "conv_synthetic"), background.calls[0])
        self.assertIn(("mark_notified", "bg_1"), background.calls)
        self.assertEqual(1, len(notices))
        self.assertEqual("background_command", notices[0]["kind"])
        self.assertIn("[系统通知|background_command]", notices[0]["message"])
        self.assertEqual([], terminal.background_command_manager.accessed)

    def test_has_pending_and_work_timer_use_backend(self):
        background = SpyBackground(pending=True)
        terminal = make_terminal(SpyBackend(background=background))

        self.assertTrue(chat_flow_notice_state._has_pending_completion_work(
            web_terminal=terminal, conversation_id="conv_synthetic",
        ))
        self.assertTrue(work_timer.has_running_background_work(terminal, "conv_synthetic"))
        self.assertIn(("has_pending_for_conversation", "conv_synthetic"), background.calls)
        self.assertIn(("reconcile_stale_records", "conv_synthetic"), background.calls)
        self.assertEqual([], terminal.background_command_manager.accessed)

    def test_rollback_uses_contract_unmark(self):
        background = SpyBackground()
        terminal = make_terminal(SpyBackend(background=background))
        notices = [{
            "kind": "background_command",
            "payload": {"command_id": "bg_1"},
            "message": "x",
            "sort_key": 1.0,
        }]

        chat_flow_notice_state._rollback_completion_notice_marks(
            web_terminal=terminal, notices=notices,
        )

        self.assertEqual([("unmark_notified", "bg_1")], background.calls)
        self.assertEqual([], terminal.background_command_manager.accessed)
        self.assertFalse(hasattr(background, "manager"))

    def test_task_support_poll_uses_backend_background(self):
        import asyncio

        background = SpyBackground(updates=[{
            "command_id": "bg_2",
            "output": "synthetic output",
            "status": "completed",
        }])
        terminal = make_terminal(SpyBackend(background=background))
        messages: List[Dict[str, Any]] = []

        asyncio.run(chat_flow_task_support.process_background_command_updates(
            messages=messages, inline=True, web_terminal=terminal,
            sender=lambda *a, **k: None, debug_log=lambda *a, **k: None,
        ))

        self.assertIn(("poll_updates", "conv_synthetic"), background.calls)
        self.assertIn(("mark_notified", "bg_2"), background.calls)
        self.assertEqual(1, len(messages))
        self.assertIn("[系统通知|background_command]", messages[0]["content"])
        self.assertEqual([], terminal.background_command_manager.accessed)

    def test_task_cleanup_and_status_use_backend(self):
        background = SpyBackground(
            waiting=[{"command_id": "bg_3", "command": "sleep", "status": "running"}],
            records=[
                {"command_id": "bg_3", "status": "running", "notified": False},
                {"command_id": "bg_4", "status": "completed", "notified": False},
                {"command_id": "bg_5", "status": "completed", "notified": True},
            ],
        )
        terminal = make_terminal(SpyBackend(background=background))
        manager = TaskManager()
        record = SimpleNamespace(conversation_id="conv_synthetic")

        self.assertTrue(manager._cleanup_background_tasks(record, terminal))
        self.assertIn(("cancel_command", "bg_3"), background.calls)
        self.assertIn(("mark_all_notified", "conv_synthetic"), background.calls)
        self.assertFalse(any(call[0] == "list_records" for call in background.calls))
        self.assertFalse(any(call[0] == "mark_notified" for call in background.calls))

        status = manager.get_conversation_running_status(terminal, "conv_synthetic")
        self.assertTrue(status["has_running_background_commands"])
        self.assertEqual([], terminal.background_command_manager.accessed)


class ApprovalPreviewRoutingTests(unittest.TestCase):
    def _preview(self, files: SpyFiles):
        terminal = make_terminal(SpyBackend(background=SpyBackground(), files=files))
        preview = _build_tool_approval_preview(terminal, "edit_file", {
            "file_path": "demo.py",
            "replacements": [{"old_string": "old", "new_string": "new"}],
        })
        return terminal, preview

    def test_edit_preview_reads_through_backend_files(self):
        files = SpyFiles(content="line1\nold\nline3\n")
        terminal, preview = self._preview(files)

        self.assertIn(("inspect_path", "demo.py", "read"), files.calls)
        self.assertIn(("read_binary", "demo.py"), files.calls)
        self.assertEqual("demo.py", preview["resolved_path"])
        self.assertIn("edit_context", preview)
        self.assertEqual(2, preview["edit_context"]["old_start_line"])
        self.assertEqual([], terminal.file_manager.accessed)

    def test_preview_keeps_best_effort_decoding(self):
        files = SpyFiles(content=b"line1\nold\xff\nline3\n")
        terminal, preview = self._preview(files)

        self.assertIn("edit_context", preview)
        self.assertEqual(2, preview["edit_context"]["old_start_line"])
        self.assertEqual([], terminal.file_manager.accessed)

    def test_missing_file_uses_inspect_result(self):
        files = SpyFiles(inspected={"success": True, "exists": False, "is_file": False})
        terminal, preview = self._preview(files)

        self.assertNotIn(("read_binary", "demo.py"), files.calls)
        self.assertTrue(preview["summary"])
        self.assertNotIn("edit_context", preview)
        self.assertEqual([], terminal.file_manager.accessed)

    def test_invalid_path_uses_inspect_error(self):
        files = SpyFiles(inspected={"success": False, "error": "synthetic-outside"})
        terminal, preview = self._preview(files)

        self.assertEqual("synthetic-outside", preview["summary"])
        self.assertEqual([], terminal.file_manager.accessed)


class ConversationEndpointRoutingTests(unittest.TestCase):
    """REST 后台命令查询/取消/列表端点必须走 backend.background。"""

    def setUp(self):
        from flask import Flask
        from server import conversation

        self.conversation = conversation
        self.app = Flask(__name__)

    def _call(self, func, url: str, *, pass_terminal: bool = False, **kwargs):
        view = inspect.unwrap(func)
        terminal = kwargs.get("terminal")
        if not pass_terminal:
            kwargs.pop("terminal", None)
        with self.app.test_request_context(url):
            with patch.object(self.conversation, "find_runtime", return_value=terminal):
                return view(**kwargs)

    def test_list_endpoint(self):
        background = SpyBackground(records=[{
            "command_id": "bg_10",
            "status": "completed",
            "command": "sleep",
            "conversation_id": "conv_synthetic",
            "created_at": 1.0,
            "updated_at": 2.0,
            "finished_at": 2.0,
            "timeout": 10,
            "result": {"return_code": 0},
        }])
        terminal = make_terminal(SpyBackend(background=background))

        response = self._call(
            self.conversation.list_background_commands,
            "/api/background_commands?conversation_id=conv_synthetic",
            data_context=None, workspace=SimpleNamespace(), username="synthetic",
            terminal=terminal,
        )
        payload = response.get_json()

        self.assertTrue(payload["success"])
        self.assertEqual("bg_10", payload["data"][0]["command_id"])
        self.assertIn(("list_records", "conv_synthetic", 200), background.calls)
        self.assertEqual([], terminal.background_command_manager.accessed)

    def test_detail_and_cancel_endpoints(self):
        records = [{
            "command_id": "bg_11",
            "status": "running",
            "command": "sleep",
            "conversation_id": "conv_synthetic",
            "result": {"return_code": None},
        }]
        background = SpyBackground(records=records)
        terminal = make_terminal(SpyBackend(background=background))

        detail = self._call(
            self.conversation.get_background_command_detail,
            "/api/background_commands/bg_11",
            pass_terminal=True,
            command_id="bg_11", terminal=terminal,
            workspace=SimpleNamespace(), username="synthetic",
        ).get_json()
        self.assertTrue(detail["success"])
        self.assertEqual("synthetic output", detail["data"]["output"])

        cancel = self._call(
            self.conversation.cancel_background_command,
            "/api/background_commands/bg_11/cancel",
            pass_terminal=True,
            command_id="bg_11", terminal=terminal,
            workspace=SimpleNamespace(), username="synthetic",
        ).get_json()
        self.assertTrue(cancel["success"])
        self.assertIn(("cancel_command", "bg_11"), background.calls)
        self.assertEqual([], terminal.background_command_manager.accessed)


class RealManagerNotifyTests(unittest.TestCase):
    """用真实 BackgroundCommandManager 验证通知水位契约。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="routing-bg-")
        self.addCleanup(self._tmp.cleanup)
        self.manager = BackgroundCommandManager(self._tmp.name)

    def _add(
        self,
        command_id: str,
        *,
        conversation_id: str = "conv_synthetic",
        status: str = "completed",
        notified: bool = False,
        output: str = "synthetic output",
    ) -> None:
        now = time.time()
        terminal_status = status in {"completed", "failed", "timeout", "cancelled"}
        with self.manager._lock:
            self.manager._records[command_id] = {
                "command_id": command_id,
                "conversation_id": conversation_id,
                "status": status,
                "command": "echo synthetic",
                "timeout": 10,
                "created_at": now,
                "updated_at": now,
                "finished_at": now if terminal_status else None,
                "stdout_chunks": [output] if output else [],
                "stderr_chunks": [],
                "truncated": False,
                "result": {
                    "success": status == "completed",
                    "status": status,
                    "command_id": command_id,
                    "conversation_id": conversation_id,
                    "command": "echo synthetic",
                    "output": output,
                    "return_code": 0 if status == "completed" else -1,
                    "updated_at": now,
                    "run_in_background": True,
                } if terminal_status else None,
                "notified": notified,
                "claimed_by_sleep": False,
                "pid": os.getpid() if status == "running" else None,
            }

    def test_cleanup_marks_more_than_1000_records_without_truncation(self):
        for index in range(1005):
            self._add(f"cmd_{index:04d}")
        terminal = make_terminal(SpyBackend(background=self.manager))

        TaskManager()._cleanup_background_tasks(
            SimpleNamespace(conversation_id="conv_synthetic"), terminal,
        )

        remaining = [
            record["command_id"] for record in self.manager._records.values()
            if record["conversation_id"] == "conv_synthetic" and not record["notified"]
        ]
        self.assertEqual([], remaining)
        self.assertEqual([], terminal.background_command_manager.accessed)

    def test_mark_all_notified_scopes_conversation_and_terminal_status(self):
        self._add("done", notified=False)
        self._add("already", notified=True)
        self._add("running", status="running")
        self._add("other", conversation_id="conv_other")

        marked = self.manager.mark_all_notified("conv_synthetic")

        self.assertEqual(1, marked)
        self.assertTrue(self.manager._records["done"]["notified"])
        self.assertFalse(self.manager._records["running"]["notified"])
        self.assertFalse(self.manager._records["other"]["notified"])

    def test_dispatch_failure_rollback_allows_renotify(self):
        self._add("cmd_retry")
        terminal = make_terminal(SpyBackend(background=self.manager))

        first = chat_flow_notice_state._collect_pending_completion_notices(
            web_terminal=terminal, conversation_id="conv_synthetic",
        )
        self.assertEqual(1, len(first))
        self.assertTrue(self.manager._records["cmd_retry"]["notified"])

        chat_flow_notice_state._rollback_completion_notice_marks(
            web_terminal=terminal, notices=first,
        )
        self.assertFalse(self.manager._records["cmd_retry"]["notified"])

        second = chat_flow_notice_state._collect_pending_completion_notices(
            web_terminal=terminal, conversation_id="conv_synthetic",
        )
        self.assertEqual(1, len(second))
        self.assertEqual("cmd_retry", second[0]["payload"]["command_id"])
        self.assertTrue(self.manager._records["cmd_retry"]["notified"])
        self.assertEqual([], terminal.background_command_manager.accessed)


class WiringSourceTests(unittest.TestCase):
    """难以运行到全链路的文件（chat_flow 取消分支等）用源码门禁确保接线。"""

    FILES = (
        "server/chat_flow_task_main.py",
        "server/chat_flow_notice_poll.py",
        "server/chat_flow_notice_state.py",
        "server/chat_flow_task_support.py",
        "server/chat_flow.py",
        "server/work_timer.py",
        "server/conversation.py",
        "server/tasks/models.py",
    )

    def test_server_modules_do_not_read_local_manager_attribute(self):
        for relative in self.FILES:
            with self.subTest(file=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn('"background_command_manager"', source)
                self.assertNotIn("'background_command_manager'", source)
                self.assertIn("get_execution_backend", source)

    def test_approval_preview_does_not_touch_local_file_manager(self):
        source = inspect.getsource(_build_tool_approval_preview)
        self.assertNotIn("file_manager", source)
        self.assertIn("inspect_path", source)
        self.assertIn("read_binary", source)


if __name__ == "__main__":
    unittest.main()
