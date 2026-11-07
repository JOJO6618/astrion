"""LocalExecutionBackend 验收：真实命令 / working_dir / 取消 / 后台 / 环境快照。

运行（必须独立进程，且在 import config 之前注入隔离环境）::

    /opt/homebrew/bin/python3.11 -B test/2026-11-07_公共执行层/test_local.py

沙箱无法应用时（例如在子智能体沙箱内嵌套运行，sandbox-exec 会 EPERM）按
fail-closed 断言并跳过「成功执行」类用例。隔离：不读仓库 .env，DATA_ROOT/
LOGS/DEPLOY_CONFIG 全指临时目录，TERMINAL_SANDBOX_MODE=host，且清除
HOST_SANDBOX_* 避免借用真实机器的路径授权。

浅版本/快照用例在 test_versioning_backend.py，import 隔离在 test_import_isolation.py。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="astrion_exec_plane_"))

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
from modules.execution_plane.resources import (  # noqa: E402
    ExecutionResources,
    create_local_resources,
)


def _decode(result):
    return json.loads(result) if isinstance(result, str) else result


def _sandbox_disabled_by_env() -> bool:
    raw = str(os.environ.get("HOST_SANDBOX_ENABLED", "1")).strip().lower()
    return raw in {"0", "false", "no", "off"}


def _looks_like_sandbox_unavailable(result) -> bool:
    text = " ".join(
        str(result.get(key) or "") for key in ("error", "message", "output")
    ).lower()
    return "sandbox" in text or "operation not permitted" in text


class LocalBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = _TMP_ROOT / "ws"
        cls.workspace.mkdir(parents=True, exist_ok=True)
        (cls.workspace / "sub").mkdir(exist_ok=True)
        cls.data_dir = _TMP_ROOT / "root" / "data"
        cls.data_dir.mkdir(parents=True, exist_ok=True)
        cls.execution_mode = str(
            os.environ.get("EXEC_PLANE_TEST_EXECUTION_MODE", "sandbox")
        ).strip().lower()
        cls.resources: ExecutionResources = create_local_resources(
            project_path=str(cls.workspace),
            data_dir=str(cls.data_dir),
            execution_mode=cls.execution_mode,
            network_permission="restricted",
            readonly=True,
        )
        cls.backend = LocalExecutionBackend(cls.resources)

        # 一次性探测：本机能否真正起进程 / 能否应用 OS 沙箱。
        probe = asyncio.run(
            cls.backend.run_command("echo __exec_probe__", timeout=20, sandbox_write_access=True)
        )
        cls.exec_available = bool(probe.get("success"))
        cls.sandbox_unavailable = (not cls.exec_available) and _looks_like_sandbox_unavailable(probe)
        cls.exec_probe = probe

    @classmethod
    def tearDownClass(cls):
        try:
            cls.resources.close()
        except Exception:
            pass

    def require_exec(self):
        if not self.exec_available:
            self.skipTest(
                "本机无法执行真实命令（沙箱不可应用或命令被拒）: "
                f"{self.exec_probe.get('error') or self.exec_probe.get('message')}"
            )

    # ---- 契约标记与资源视图 ----

    def test_backend_flags_and_resources(self):
        self.assertTrue(self.backend.is_local)
        self.assertTrue(self.backend.supports_full_access)
        self.assertEqual(self.backend.backend_name, "local")
        self.assertIs(
            self.resources.terminal_ops._terminal_manager, self.resources.terminal_manager
        )
        self.assertEqual(self.backend.execution_mode, self.execution_mode)
        self.assertEqual(self.backend.network_permission, "restricted")

    def test_views_are_live(self):
        self.assertIs(self.backend.terminals.manager, self.resources.terminal_manager)
        self.assertIs(self.backend.background.manager, self.resources.background_command_manager)
        try:
            first = self.backend.files
        except RuntimeError:
            self.skipTest("modules.execution_plane.files 尚未就位")
        self.assertIsNot(first, self.backend.files)

    # ---- E1 真实命令 ----

    def test_run_command_real(self):
        self.require_exec()
        result = asyncio.run(
            self.backend.run_command("echo exec-plane-ok", timeout=30, sandbox_write_access=True)
        )
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result.get("status"), "completed", result)
        self.assertIn("exec-plane-ok", result.get("output") or "")
        self.assertEqual(result.get("return_code"), 0)

    @staticmethod
    def _marked_path(output: str, marker: str = "__CWD__"):
        """从可能混有 stderr 的输出里取出标记行后的路径。

        `TerminalOperator` 把 stdout+stderr 合并：宿主机 bash 启动时可能先往
        stderr 吐一行 `.profile: Operation not permitted`，直接 `strip()` 比较就会
        把无关噪声当成 cwd。这里只认带前缀标记的那一行，不改 shell 配置、也不
        屏蔽生产 stderr。
        """
        for line in (output or "").splitlines():
            if marker in line:
                return line.split(marker, 1)[1].strip()
        return None

    def test_run_command_default_cwd_is_workspace(self):
        self.require_exec()
        result = asyncio.run(
            self.backend.run_command(
                "printf '__CWD__';pwd", timeout=30, sandbox_write_access=True
            )
        )
        self.assertTrue(result.get("success"), result)
        marked = self._marked_path(result.get("output") or "")
        self.assertIsNotNone(marked, f"输出里应有 __CWD__ 标记行: {result.get('output')!r}")
        self.assertEqual(
            os.path.realpath(marked), os.path.realpath(str(self.workspace)),
            f"默认 cwd 应为工作区: {marked!r}",
        )

    def test_working_dir_relative(self):
        self.require_exec()
        result = asyncio.run(
            self.backend.run_command(
                "printf '__CWD__';pwd", timeout=30, sandbox_write_access=True, working_dir="sub"
            )
        )
        self.assertTrue(result.get("success"), result)
        marked = self._marked_path(result.get("output") or "")
        self.assertIsNotNone(marked, f"输出里应有 __CWD__ 标记行: {result.get('output')!r}")
        self.assertEqual(
            os.path.realpath(marked), os.path.realpath(str(self.workspace / "sub"))
        )

    def test_working_dir_outside_workspace_rejected(self):
        # 越界在起进程之前就会被拒绝，不依赖沙箱可用性
        result = asyncio.run(
            self.backend.run_command("pwd", timeout=30, sandbox_write_access=True, working_dir="..")
        )
        self.assertFalse(result.get("success"), result)
        self.assertEqual(result.get("return_code"), -1, result)

    def test_timeout_none_keeps_old_error(self):
        result = asyncio.run(self.backend.run_command("echo x"))
        self.assertFalse(result.get("success"), result)
        self.assertEqual(result.get("return_code"), -1, result)

    # ---- 沙箱授权不可绕过 ----

    def test_readonly_never_writes(self):
        if _sandbox_disabled_by_env():
            # 显式关闭沙箱时不存在只读强制，不能拿裸执行当作“写被拒”。
            self.skipTest("HOST_SANDBOX_ENABLED=0：只读强制不在本次覆盖范围")
        if getattr(self, "execution_mode", "sandbox") == "direct":
            # direct 下没有沙箱强制，只读语义不在本用例范围。
            self.skipTest("direct 执行环境下不存在 OS 沙箱只读强制")
        target = self.workspace / "should_not_exist.txt"
        if target.exists():
            target.unlink()
        result = asyncio.run(
            self.backend.run_command(
                "touch should_not_exist.txt", timeout=30, sandbox_write_access=False
            )
        )
        self.assertFalse(result.get("success"), f"只读执行不应允许写入: {result}")
        self.assertFalse(target.exists(), "写入未获授权时文件不得被创建（fail-closed）")
        if self.sandbox_unavailable:
            return  # 沙箱不可应用 => 命令被拒且零副作用，符合 fail-closed
        self.assertIn(
            "perm",
            str(result.get("error") or result.get("output") or "").lower(),
            f"应体现 OS 层权限拒绝: {result}",
        )

    def test_write_access_allows_workspace_write(self):
        self.require_exec()
        target = self.workspace / "allowed.txt"
        if target.exists():
            target.unlink()
        result = asyncio.run(
            self.backend.run_command("printf ok > allowed.txt", timeout=30, sandbox_write_access=True)
        )
        self.assertTrue(result.get("success"), result)
        self.assertTrue(target.exists())
        target.unlink()

    # ---- 进程回收断言工具 ----

    def _pid_alive(self, pid: int) -> bool:
        """真实探测进程是否还在（复用管理器的 _is_pid_alive，非纯状态标记）。"""
        checker = getattr(self.resources.background_command_manager, "_is_pid_alive", None)
        if callable(checker):
            return bool(checker(pid))
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _wait_pid_gone(self, pid: int, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(0.1)
        return False

    # ---- 异步取消（保留 kill 行为） ----

    def test_async_cancel_kills_command(self):
        self.require_exec()
        pid_file = self.workspace / "cancel_probe.pid"
        if pid_file.exists():
            pid_file.unlink()

        async def scenario():
            task = asyncio.create_task(
                self.backend.run_command(
                    "echo $$ > cancel_probe.pid; sleep 30",
                    timeout=60,
                    sandbox_write_access=True,
                )
            )
            deadline = time.time() + 15
            while time.time() < deadline and not pid_file.exists():
                await asyncio.sleep(0.1)
            self.assertTrue(pid_file.exists(), "命令应已启动并把 pid 写入工作区")
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                return await task
            except asyncio.CancelledError:
                return {"status": "cancelled", "reaped_by": "caller"}

        started = time.time()
        result = asyncio.run(scenario())
        elapsed = time.time() - started
        self.assertEqual(result.get("status"), "cancelled", result)
        self.assertLess(elapsed, 15, f"取消应迅速返回，实际 {elapsed:.1f}s")

        # 不只是状态：必须证明真实子进程已消失（pid 已回收）
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        self.assertTrue(pid > 0, "应拿到真实 shell pid")
        self.assertTrue(self._wait_pid_gone(pid, 15), f"取消后进程 {pid} 仍存活")
        self.assertFalse(self._pid_alive(pid))

    # ---- E2 后台命令 ----

    def test_background_completes(self):
        self.require_exec()

        async def scenario():
            created = await self.backend.run_command_background(
                "echo bg-exec-ok", timeout=60, conversation_id="conv_smoke"
            )
            self.assertTrue(created.get("success"), created)
            command_id = created.get("command_id")
            self.assertTrue(command_id, created)
            done = await asyncio.to_thread(
                self.backend.background.wait_for_completion, command_id, 20, False
            )
            return done

        done = asyncio.run(scenario())
        self.assertEqual(done.get("status"), "completed", done)
        self.assertIn("bg-exec-ok", done.get("output") or "")

    def test_background_cancel(self):
        self.require_exec()

        async def scenario():
            created = await self.backend.run_command_background(
                "sleep 30", timeout=120, conversation_id="conv_smoke"
            )
            self.assertTrue(created.get("success"), created)
            command_id = created.get("command_id")
            await asyncio.sleep(0.8)
            cancelled = await asyncio.to_thread(self.backend.cancel_background, command_id)
            return command_id, cancelled

        command_id, cancelled = asyncio.run(scenario())
        self.assertEqual(cancelled.get("status"), "cancelled", cancelled)
        record = self.backend.background.get_record(command_id)
        self.assertIsNotNone(record, "取消后记录仍应可查询")
        self.assertEqual(record.get("status"), "cancelled")

        # 真实回收断言：记录里的 pid 必须真的消失（不是只改状态字）
        pid = record.get("pid")
        self.assertTrue(pid, f"后台记录应带真实 pid: {record}")
        self.assertTrue(
            self._wait_pid_gone(int(pid), 15), f"取消后后台进程 {pid} 仍存活"
        )
        self.assertFalse(self._pid_alive(int(pid)))

    def test_background_cancel_before_creation_is_handshaked(self):
        """取消发生在 command_id 分配前：start_background_command 必须补发取消。"""

        async def scenario():
            task = asyncio.create_task(
                self.backend.run_command_background(
                    "sleep 30", timeout=120, conversation_id="conv_smoke"
                )
            )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return True
            return False

        self.assertTrue(asyncio.run(scenario()))

    # ---- E3/E4 文件（依赖文件子协议，未就位则跳过） ----

    def test_write_and_edit_via_backend(self):
        try:
            self.backend.files
        except RuntimeError:
            self.skipTest("modules.execution_plane.files 尚未就位（write/edit 无回退路径）")
        write_result = _decode(self.backend.write_file("note.txt", "line-1\nline-2\nline-3\nline-4\n"))
        self.assertTrue(write_result.get("success"), write_result)
        self.assertEqual(
            (self.workspace / "note.txt").read_text(encoding="utf-8"),
            "line-1\nline-2\nline-3\nline-4\n",
        )
        edit_result = _decode(
            self.backend.edit_file(
                "note.txt",
                [{"old_string": "line-2\nline-3\nline-4", "new_string": "line-2\nCHANGED\nline-4"}],
            )
        )
        self.assertTrue(edit_result.get("success"), edit_result)
        self.assertIn("CHANGED", (self.workspace / "note.txt").read_text(encoding="utf-8"))

    # ---- 终端视图 ----

    def test_terminal_open_send_close(self):
        self.require_exec()
        open_result = self.backend.terminals.open("smoke", make_active=True)
        if not open_result.get("success"):
            self.skipTest(f"持久终端不可用: {open_result.get('error')}")
        try:
            sent = self.backend.terminals.send("echo term-ok", "smoke", 5)
            # 交互式终端按 output_wait 收尾；输出已拿到即为成功（status 可能为 timeout）。
            self.assertIn("term-ok", sent.get("output") or "", sent)
        finally:
            self.backend.terminals.close_all()

    # ---- 环境快照 ----

    def test_get_environment(self):
        env = self.backend.get_environment()
        json.dumps(env)  # 必须可序列化
        self.assertTrue(env["is_local"])
        self.assertTrue(env["supports_full_access"])
        self.assertEqual(env["execution_target"], "host")
        self.assertEqual(env["execution_mode"], self.execution_mode)
        self.assertEqual(env["network_permission"], "restricted")
        self.assertEqual(env["workspace_root"], str(self.workspace.resolve()))
        self.assertEqual(env["os_scope"], "host")
        self.assertTrue(env["host"]["system"])
        self.assertEqual(env["platform"], sys.platform)
        # 工作区路径带字母，应给出确定的大小写敏感性结论
        self.assertIsInstance(env["case_sensitive"], bool)
        self.assertEqual(env["case_sensitive_source"], "workspace-probe")
        self.assertIsNone(env["container"])
        # capabilities 是实测结果，不是类级声明副本
        caps = env["capabilities"]
        self.assertTrue(caps["files"])
        self.assertTrue(caps["binary_reads"])
        self.assertTrue(caps["run_command"])
        self.assertEqual(env["declared_capabilities"]["run_command"], True)

    def test_get_environment_does_not_claim_missing_capabilities(self):
        """精简 owner（缺 file_manager/project_path）时不得声称文件能力可用。"""
        mini = SimpleNamespace(terminal_ops=self.resources.terminal_ops)
        env = LocalExecutionBackend(mini).get_environment()
        json.dumps(env)  # 必须可序列化
        self.assertFalse(env["capabilities"]["files"])
        self.assertFalse(env["capabilities"]["binary_reads"])
        self.assertFalse(env["capabilities"]["background_commands"])
        self.assertIsNone(env["workspace_root"])
        self.assertIsNone(env["case_sensitive"])
        self.assertEqual(env["case_sensitive_source"], "unknown")
        self.assertTrue(env["workspace_error"], "缺 workspace 也要报出原因")


if __name__ == "__main__":
    unittest.main(verbosity=2)
