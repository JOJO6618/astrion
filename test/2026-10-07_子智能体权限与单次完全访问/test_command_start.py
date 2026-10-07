from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core.main_terminal_parts.tools_execution import MainTerminalToolsExecutionMixin
from modules.background_command_manager import BackgroundCommandManager
from modules.background_command_start import start_background_command
from modules.execution_scope import bind_execution_scope, command_fingerprint, current_execution_scope
from modules.tool_approval_manager import ToolApprovalManager
from server.tool_execution_authority import approval_execution_scope


class BackgroundStartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.manager = BackgroundCommandManager(str(self.root))
        self.manager._records["command"] = {
            "command_id": "command", "status": "running", "command": "echo ok", "pid": None,
            "created_at": time.time(), "stdout_chunks": [], "stderr_chunks": [], "timeout": 1,
        }

    def test_cancel_before_spawn_prevents_process_creation(self):
        self.manager.cancel_command("command")
        with patch("modules.background_command_manager.subprocess.Popen") as spawn:
            with self.assertRaises(RuntimeError):
                self.manager._spawn_and_register("command", None, ["never-run"])
        spawn.assert_not_called()
        self.assertEqual(self.manager.get_record("command")["status"], "cancelled")

    def test_cancel_during_spawn_observes_registered_process(self):
        spawning = threading.Event()
        release = threading.Event()
        proc = SimpleNamespace(pid=123456, poll=lambda: None)
        def popen(*_, **__):
            spawning.set()
            self.assertTrue(release.wait(2))
            return proc
        with patch("modules.background_command_manager.subprocess.Popen", side_effect=popen), \
             patch.object(self.manager, "_terminate_pid", return_value=True) as terminate, \
             ThreadPoolExecutor(max_workers=2) as pool:
            started = pool.submit(self.manager._spawn_and_register, "command", None, ["never-run"])
            self.assertTrue(spawning.wait(2))
            cancelled = pool.submit(self.manager.cancel_command, "command")
            self.assertFalse(cancelled.done())
            release.set()
            self.assertIs(started.result(timeout=2), proc)
            self.assertEqual(cancelled.result(timeout=2)["status"], "cancelled")
            terminate.assert_called_once_with(proc.pid)

    def test_worker_cannot_overwrite_cancel_intent_before_result_is_saved(self):
        def spawn(*_, **__):
            self.manager._records["command"]["status"] = "cancelled"
            self.manager._records["command"]["result"] = None
            return SimpleNamespace(
                pid=123456, stdout=StringIO("last output\n"), stderr=StringIO(""),
                returncode=0, wait=lambda **kwargs: None,
            )
        with patch.object(self.manager, "_spawn_and_register", side_effect=spawn):
            self.manager._run_command_thread_bound(
                command_id="command", command="never-run", work_path=self.root,
                timeout=1, session=None, python_env={}, host_execution_mode="direct",
            )
        record = self.manager.get_record("command")
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["result"]["status"], "cancelled")
        self.assertFalse(record["result"]["success"])
        self.assertEqual(record["result"]["output"], "last output\n")

    async def test_cancellation_before_id_assignment_cancels_late_creation(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        cancelled_ids = []
        def create(**kwargs):
            entered.set()
            release.wait(2)
            self.assertTrue(kwargs["_cancel_event"].is_set())
            kwargs["_on_created"]("late-command")
            finished.set()
            return {"status": "cancelled"}
        manager = SimpleNamespace(create_background_command=create, cancel_command=cancelled_ids.append)
        task = asyncio.create_task(start_background_command(manager, command="never-run"))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        release.set()
        self.assertTrue(await asyncio.to_thread(finished.wait, 2))
        self.assertEqual(cancelled_ids, ["late-command"])

    async def test_background_creation_propagates_trusted_context(self):
        from modules.execution_scope import ExecutionScope
        scope = ExecutionScope("main", "actor", self.root, "sandbox_write")
        seen = []
        def create(**_):
            seen.append(current_execution_scope())
            return {"success": True}
        with bind_execution_scope(scope):
            result = await start_background_command(SimpleNamespace(create_background_command=create))
        self.assertTrue(result["success"])
        self.assertEqual(seen, [scope])


class CommandDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.terminal = MainTerminalToolsExecutionMixin()
        self.terminal.project_path = str(self.root)
        self.terminal.data_dir = str(self.root)
        self.terminal.context_manager = SimpleNamespace(project_path=self.root, current_conversation_id="c", conversation_metadata={})
        self.terminal.get_permission_mode = lambda: "unrestricted"
        self.terminal.get_work_mode = lambda: "execute"
        self.terminal.get_execution_mode = lambda: "sandbox"
        self.terminal.container_session = SimpleNamespace(mode="host")
        self.terminal.execution_backend = None
        self.terminal.custom_tools_enabled = True
        self.terminal.user_role = "admin"
        self.terminal.custom_tool_registry = SimpleNamespace(reload=Mock(), get_tool=Mock(return_value={"id": "run_command"}))
        self.terminal.custom_tool_executor = SimpleNamespace(run=AsyncMock())
        self.terminal._check_skill_strict_prerequisite = lambda *_: None
        self.terminal.host_network_permission = "restricted"
        self.executed = []
        async def run(command, **kwargs):
            self.executed.append((command, kwargs, current_execution_scope()))
            return {"success": True, "output": "ok", "return_code": 0}
        self.terminal.terminal_ops = SimpleNamespace(run_command=run)
        self.args = {"command": "echo exact-approved-command", "timeout": 10,
                     "request_full_access": True, "full_access_reason": "test"}
        manager = ToolApprovalManager()
        item = manager.create_request(username="u", conversation_id="c", task_id="t", tool_call_id="x",
                                      tool_name="run_command", arguments=self.args, preview={}, approval_type="full_access",
                                      executor_id=id(self.terminal), workspace_root=str(self.root))
        approved = manager.decide(item["approval_id"], "u", "approved")
        self.scope = approval_execution_scope(self.terminal, self.args, approved, task_id="t", tool_call_id="x",
                                              expected_root=self.root, manager=manager, tool_name="run_command",
                                              fingerprint=command_fingerprint(self.args))

    async def test_full_access_calls_native_command_and_cannot_replay(self):
        with patch("modules.command_blocking.validate_command", return_value=(True, "")), bind_execution_scope(self.scope):
            result = json.loads(await self.terminal.handle_tool_call("run_command", self.args))
            repeated = json.loads(await self.terminal.handle_tool_call("run_command", self.args))
        self.assertTrue(result["success"], result)
        self.assertTrue(result["full_access_granted"])
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(self.executed[0][0], self.args["command"])
        self.assertEqual(self.executed[0][2].execution_mode, "direct")
        self.assertFalse(repeated["success"])
        self.terminal.custom_tool_registry.get_tool.assert_not_called()
        self.terminal.custom_tool_executor.run.assert_not_called()
        self.assertEqual(self.terminal.get_execution_mode(), "sandbox")

    async def test_child_tools_keep_independent_fixed_resources_after_parent_switch(self):
        from modules.execution_scope import ExecutionScope
        from modules.sub_agent.execution import execute_child_tool
        self.terminal.get_network_permission = lambda: "restricted"
        self.terminal._apply_execution_mode_to_runtime = Mock()
        manager = SimpleNamespace(terminal=self.terminal, project_path=self.root,
                                  data_dir=self.root, container_session=self.terminal.container_session)
        scope = ExecutionScope("sub_agent", "child", self.root, "workspace_write", "c", "child")
        child = SimpleNamespace(manager=manager, execution_scope=scope)
        modes = []
        async def run(command, **kwargs):
            modes.append(current_execution_scope().access_level)
            return {"success": True, "output": "ok"}
        resources = SimpleNamespace(run_command=run, set_host_execution_mode=Mock(),
                                    attach_terminal_manager=Mock(), _validate_command=lambda _: (True, ""))
        terminals = SimpleNamespace(set_host_execution_mode=Mock())
        with patch("modules.sub_agent.execution.TerminalOperator", return_value=resources), \
             patch("modules.sub_agent.execution.TerminalManager", return_value=terminals), \
             patch("modules.command_blocking.validate_command", return_value=(True, "")):
            first = json.loads(await execute_child_tool(manager, "run_command", {"command": "echo child", "timeout": 1}, child))
            self.terminal.get_permission_mode = lambda: "readonly"
            self.terminal.get_execution_mode = lambda: "direct"
            second = json.loads(await execute_child_tool(manager, "run_command", {"command": "echo child", "timeout": 1}, child))
        self.assertTrue(first["success"], first)
        self.assertTrue(second["success"], second)
        self.assertEqual(modes, ["workspace_write", "workspace_write"])
        self.assertEqual(child._execution_terminal.get_execution_mode(), "sandbox")
        self.assertIsNot(child._execution_terminal.context_manager, self.terminal.context_manager)
        self.assertIsNot(child._execution_terminal.terminal_ops, self.terminal.terminal_ops)
        self.assertFalse(child._execution_terminal.custom_tools_enabled)
        self.terminal._apply_execution_mode_to_runtime.assert_not_called()
        self.assertIsNone(current_execution_scope())

    async def test_unapproved_request_and_model_authority_fields_are_denied(self):
        result = json.loads(await self.terminal.handle_tool_call("run_command", self.args))
        self.assertFalse(result["success"])
        malicious = {"command": "echo bad", "timeout": 1, "_execution_scope": "direct"}
        result = json.loads(await self.terminal.handle_tool_call("run_command", malicious))
        self.assertEqual(result["code"], "untrusted_authority")
        self.assertFalse(self.executed)


if __name__ == "__main__":
    unittest.main()
