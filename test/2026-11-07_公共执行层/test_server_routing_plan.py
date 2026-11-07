"""计划审批读取 + 检查点浅备份恢复的执行出口接线回归（离线）。

- ``submit_plan`` 的路径校验/内容读取必须经 ``backend.files``，不改批准流程；
- 检查点恢复构造 ``ShallowVersioningManager`` 时必须传入目标执行环境的 files，
  不能默认控制端本机磁盘（Docker/远端会写宿主镜像）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from server.chat_flow_tool_loop import _handle_submit_plan  # noqa: E402
from server.conversation import _restore_checkpoint_to_conversation  # noqa: E402

from server_routing_fixtures import (  # noqa: E402
    ROOT,
    SpyBackend,
    SpyFiles,
    SpyShallowVersioningManager,
    make_terminal,
)


class PlanFlowRoutingTests(unittest.TestCase):
    """submit_plan 的路径校验/内容读取必须经 backend.files，不改批准流程。"""

    def _run(self, *, relative_path: str = ".astrion/plan/plan.md", content: str = "# plan"):
        files = SpyFiles(
            content=content,
            inspected={
                "success": True,
                "path": relative_path,
                "relative_path": relative_path,
                "exists": True,
                "is_file": True,
                "is_dir": False,
                "within_workspace": True,
                "size": len(content.encode("utf-8")),
                "suffix": ".md" if relative_path.endswith(".md") else ".txt",
            },
        )
        terminal = make_terminal(SpyBackend(files=files))
        terminal.get_work_mode = lambda: "plan"
        approval_manager = SimpleNamespace(
            create_request=Mock(return_value={"approval_id": "ap_1", "plan_content": content}),
        )
        events: List[Any] = []

        async def wait_plan(**kwargs):
            return {"status": "rejected"}

        with patch("server.chat_flow_tool_loop.plan_approval_manager", approval_manager), \
                patch("server.chat_flow_tool_loop._wait_for_plan_approval", wait_plan):
            raw = asyncio.run(_handle_submit_plan(
                web_terminal=terminal,
                arguments={"plan_file": relative_path, "summary": "synthetic"},
                sender=lambda *a, **k: events.append((a, k)),
                username="synthetic", conversation_id="conv_synthetic",
                tool_call_id="tc_1",
            ))
        return files, terminal, approval_manager, events, json.loads(raw)

    def test_plan_content_reads_through_backend_files(self):
        files, terminal, approval_manager, _events, payload = self._run()

        self.assertEqual([
            ("resolve_path", ".astrion/plan/plan.md", "read"),
            ("inspect_path", ".astrion/plan/plan.md", "read"),
            ("read_binary", ".astrion/plan/plan.md"),
        ], files.calls)
        self.assertFalse(payload["success"])
        self.assertEqual("rejected", payload["status"])
        self.assertEqual("# plan", approval_manager.create_request.call_args.kwargs["plan_content"])
        self.assertEqual([], terminal.file_manager.accessed)

    def test_plan_outside_plan_dir_is_rejected_without_read(self):
        files, terminal, approval_manager, _events, payload = self._run(relative_path="docs/plan.md")

        self.assertEqual("invalid_plan_file", payload["status"])
        self.assertEqual([("resolve_path", "docs/plan.md", "read")], files.calls)
        approval_manager.create_request.assert_not_called()
        self.assertEqual([], terminal.file_manager.accessed)

    def test_plan_non_markdown_is_rejected_without_read(self):
        files, terminal, approval_manager, _events, payload = self._run(relative_path=".astrion/plan/plan.txt")

        self.assertEqual("invalid_plan_file", payload["status"])
        self.assertNotIn(("read_binary", ".astrion/plan/plan.txt"), files.calls)
        approval_manager.create_request.assert_not_called()
        self.assertEqual([], terminal.file_manager.accessed)


class CheckpointRestoreRoutingTests(unittest.TestCase):
    """检查点恢复的浅备份 rewind 必须拿目标执行环境的 files。"""

    def setUp(self):
        SpyShallowVersioningManager.instances = []

    def test_shallow_rewind_receives_backend_files(self):
        files = SpyFiles(content="synthetic")
        terminal = make_terminal(SpyBackend(files=files))
        cm = SimpleNamespace(
            load_conversation=Mock(return_value={"messages": [{"role": "user", "content": "x"}], "metadata": {}}),
            save_conversation=Mock(return_value=True),
            update_conversation_metadata=Mock(),
        )
        terminal.context_manager.conversation_manager = cm
        workspace = SimpleNamespace(project_path=str(ROOT), data_dir=tempfile.mkdtemp(prefix="restore-test-"))

        source_manager = Mock()
        source_manager.get_checkpoint.return_value = {
            "seq": 1, "shallow_message_id": "msg_1", "tree_hash": "hash_1", "message_index": 0,
        }
        source_manager.get_checkpoint_snapshot.return_value = None
        target_manager = Mock()
        target_manager.prune_checkpoints_after.return_value = {"last_tree_hash": "hash_1", "max_seq": 1}
        target_manager.load_meta.return_value = {}

        def manager_factory(_workspace, conversation_id):
            return source_manager if conversation_id == "conv_src" else target_manager

        with patch.multiple(
            "server.conversation",
            _get_conv_versioning_manager=manager_factory,
            ShallowVersioningManager=SpyShallowVersioningManager,
            _update_conversation_versioning_meta=Mock(),
        ):
            result = _restore_checkpoint_to_conversation(
                terminal, workspace, "conv_src", "conv_dst", 1,
                "conversation_only", False,
            )

        self.assertEqual(1, len(SpyShallowVersioningManager.instances))
        manager = SpyShallowVersioningManager.instances[0]
        self.assertIs(files, manager.kwargs.get("filesystem"))
        self.assertIs(terminal.execution_backend.files, manager.kwargs.get("filesystem"))
        self.assertEqual("conv_src", manager.kwargs.get("conversation_id"))
        self.assertEqual(["msg_1"], manager.rewound)
        self.assertEqual(1, result["restored_seq"])
        self.assertEqual([], terminal.file_manager.accessed)


class PlanFlowSourceTests(unittest.TestCase):
    """源码门禁：计划流不得回退本地 file_manager / 控制端 project_path。"""

    def test_plan_flow_uses_file_backend(self):
        source = inspect.getsource(_handle_submit_plan)
        self.assertNotIn("file_manager", source)
        self.assertNotIn("context_manager.project_path", source)
        self.assertIn("resolve_path", source)
        self.assertIn("inspect_path", source)
        self.assertIn("read_binary", source)


if __name__ == "__main__":
    unittest.main()
