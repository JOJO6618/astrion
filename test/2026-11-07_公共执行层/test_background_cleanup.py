"""后台命令资源收口回归（2026-11-07）。

背景：主侧原生复跑 ``test_local.py`` 时取消 PID 已真实通过，但
``cache/execution_local_native.log`` 仍出现 ResourceWarning：
``background_command_manager.py`` 的后台 stdout/stderr TextIOWrapper 未 close。

本文件只验证 run 线程的"收口"这一层，不触碰取消/通知状态机：自然结束、
超时、取消、spawn 被拒（异常）、孙进程继承 PIPE 写端五条路径都必须
wait 自己 spawn 的进程、关闭自己 PIPE 持有的句柄，且不能因为提前/阻塞式
close 丢输出或拖死 run 线程。

沙箱内信号可能被拒，因此取消/超时/孙进程用例按"终态属于可容忍集合 + 有期限"
断言，进程一律用短寿命命令，保证即使信号被拒也会自然结束。
"""
from __future__ import annotations

import gc
import tempfile
import threading
import time
import unittest
from pathlib import Path

# 必须先于 config / modules 导入：它负责临时运行根与环境隔离。
from process_cleanup_env import (  # noqa: E402
    TMP_ROOT,
    TerminalOpsStub,
    make_bg_record,
    resource_warnings,
    unclosed_file_warnings,
)

from modules.background_command_manager import BackgroundCommandManager  # noqa: E402


class BackgroundCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bg_cleanup_", dir=str(TMP_ROOT)))
        self.manager = BackgroundCommandManager(str(self.tmp))
        self.ops = TerminalOpsStub(self.tmp)

    def tearDown(self) -> None:
        # 兜底：任何残留进程都清掉，避免污染后续用例。
        with self.manager._lock:
            leftovers = list(self.manager._processes.items())
        for _, process in leftovers:
            try:
                process.kill()
            except Exception:
                pass

    def _wait_closed(self, process, deadline_seconds: float = 20.0) -> bool:
        deadline = time.time() + deadline_seconds
        while time.time() < deadline:
            if process.stdout is not None and process.stdout.closed:
                return True
            time.sleep(0.05)
        return False

    def test_completion_closes_pipes_and_keeps_output(self) -> None:
        with resource_warnings() as caught:
            created = self.manager.create_background_command(
                terminal_ops=self.ops,
                command="printf 'bg-out\\n'; printf 'bg-err\\n' 1>&2; sleep 0.4",
                timeout=30,
                conversation_id="conv-cleanup",
                wait_seconds=0.1,
            )
            self.assertTrue(created.get("success"), created)
            command_id = created["command_id"]
            process = self.manager._processes.get(command_id)
            self.assertIsNotNone(process, "后台命令应已真正 spawn")

            done = self.manager.wait_for_completion(command_id, 30)
            self.assertEqual(done.get("status"), "completed", done)
            # 先关后 join 会丢输出：两路输出都必须完整。
            self.assertIn("bg-out", done.get("output") or "")
            self.assertIn("bg-err", done.get("output") or "")

            # 自己 spawn 的进程必须被 wait（回收），PIPE 必须被关闭。
            self.assertIsNotNone(process.returncode)
            self.assertTrue(process.stdout.closed)
            self.assertTrue(process.stderr.closed)
            self.assertIsNone(process.stdin)  # 未请求 stdin PIPE，不应凭空出现

            del process
        self.assertEqual(unclosed_file_warnings(caught), [])

    def test_timeout_reaps_process_and_closes_pipes(self) -> None:
        with resource_warnings() as caught:
            created = self.manager.create_background_command(
                terminal_ops=self.ops,
                command="sleep 2",
                timeout=1,
                conversation_id="conv-cleanup",
                wait_seconds=0.1,
            )
            self.assertTrue(created.get("success"), created)
            command_id = created["command_id"]
            process = self.manager._processes.get(command_id)
            self.assertIsNotNone(process)

            done = self.manager.wait_for_completion(command_id, 30)
            # 沙箱内 SIGINT/SIGKILL 可能被拒：允许命令自然结束，但状态必须是终态。
            self.assertIn(done.get("status"), {"timeout", "completed"}, done)
            deadline = time.time() + 20
            while time.time() < deadline and process.returncode is None:
                time.sleep(0.05)
            self.assertIsNotNone(process.returncode, "超时路径也必须 wait 到自己 spawn 的进程")
            self.assertTrue(self._wait_closed(process))
            self.assertTrue(process.stderr.closed)

            del process
        self.assertEqual(unclosed_file_warnings(caught), [])

    def test_cancel_closes_pipes_without_touching_state_machine(self) -> None:
        with resource_warnings() as caught:
            created = self.manager.create_background_command(
                terminal_ops=self.ops,
                command="sleep 3",
                timeout=60,
                conversation_id="conv-cleanup",
                wait_seconds=0.2,
            )
            self.assertTrue(created.get("success"), created)
            command_id = created["command_id"]
            process = self.manager._processes.get(command_id)
            self.assertIsNotNone(process)

            cancelled = self.manager.cancel_command(command_id)
            self.assertEqual(cancelled.get("status"), "cancelled", cancelled)
            record = self.manager.get_record(command_id)
            self.assertEqual(record.get("status"), "cancelled")

            # 取消只改状态字不算数：run 线程仍必须回收进程并关闭自己的管道。
            deadline = time.time() + 25
            while time.time() < deadline and (process.returncode is None or not process.stdout.closed):
                time.sleep(0.05)
            self.assertIsNotNone(process.returncode, "取消后 run 线程必须 wait 到自己 spawn 的进程")
            self.assertTrue(process.stdout.closed)
            self.assertTrue(process.stderr.closed)

            del process
        self.assertEqual(unclosed_file_warnings(caught), [])

    def test_grandchild_holding_pipe_does_not_block_cleanup(self) -> None:
        """孙进程继承 PIPE 写端：EOF 永不到，收口必须有期限且不阻塞在 stream.close。

        回归：``sh -c 'printf ...; sleep 8 &'`` 的 shell 已退出，但 sleep 仍持有
        stdout/stderr 写端。旧实现在 reader 仍阻塞时从本线程 ``stream.close()``，
        会卡在 reader 持有的内部锁上（实测 3s 不返回、按孙进程寿命可能永久）；
        修复后按「有期限 join → SIGKILL 本任务自己的进程组 → 再 join → 最后才 close」
        收敛，总耗时远小于孙进程寿命。
        """
        with resource_warnings() as caught:
            created = self.manager.create_background_command(
                terminal_ops=self.ops,
                command="printf 'tail-out\\n'; sleep 8 &",
                timeout=30,
                conversation_id="conv-cleanup",
                wait_seconds=0.1,
            )
            self.assertTrue(created.get("success"), created)
            command_id = created["command_id"]
            process = self.manager._processes.get(command_id)
            self.assertIsNotNone(process)

            # 不用 wait_for_completion：它会先跑 reconcile_stale_records，
            # 而主 shell 先于 reader 收口退出，会被当成“进程已退出”提前判失败。
            # 这里直接等 run 线程写完 result，衡量收口本身有没有期限。
            started = time.time()
            record = {}
            deadline = time.time() + 20
            while time.time() < deadline:
                with self.manager._lock:
                    record = dict(self.manager._records.get(command_id) or {})
                if record.get("result") is not None:
                    break
                time.sleep(0.05)
            elapsed = time.time() - started
            result = record.get("result") or {}

            self.assertLess(elapsed, 6.0, f"收口被孙进程拖住: {elapsed:.1f}s")
            self.assertEqual(record.get("status"), "completed", record)
            self.assertEqual(result.get("status"), "completed", result)
            # 命令本身的输出在进程结束前已排空，不能因为收口而丢。
            self.assertIn("tail-out", result.get("output") or "")
            self.assertIsNotNone(process.returncode)
            # 沙箱可能拒绝 killpg：此时只能等孙进程自然结束，reader 读到 EOF
            # 后自行关闭句柄。两种情况都必须是“有期限”的，不能无限阻塞。
            deadline = time.time() + 15
            while time.time() < deadline and not process.stdout.closed:
                time.sleep(0.05)
            self.assertTrue(process.stdout.closed, "reader 收口超期")
            self.assertTrue(process.stderr.closed)

            del process
        self.assertEqual(unclosed_file_warnings(caught), [])

    def test_spawn_refused_fails_without_leaking(self) -> None:
        """异常路径（取消发生在 spawn 前）：不得抛穿 run 线程，也不留句柄。"""
        command_id = "cmd_cleanup_aborted"
        self.manager._records[command_id] = make_bg_record(command_id)
        cancel_event = threading.Event()
        cancel_event.set()

        with resource_warnings() as caught:
            self.manager._run_command_thread_bound(
                command_id=command_id,
                command="echo never-runs",
                work_path=self.tmp,
                timeout=10,
                session=None,
                python_env={},
                host_execution_mode="direct",
                network_permission=None,
                sandbox_write_access=True,
                cancel_event=cancel_event,
            )
            record = self.manager.get_record(command_id)
            self.assertIsNotNone(record.get("result"))
            self.assertEqual(record.get("status"), "failed")
            self.assertIn("取消", str(record["result"].get("message") or ""))
            self.assertIsNone(record.get("pid"))
            gc.collect()
        self.assertEqual(unclosed_file_warnings(caught), [])


if __name__ == "__main__":
    unittest.main()
