"""持久终端 close 的资源收口回归（2026-11-07）。

背景：主侧原生复跑 ``test_local.py`` 时出现
``terminal_manager.py:881``（即 ``PersistentTerminal.close``）的
``FileIO wb/rb unclosed`` —— 关闭会话只结束了 shell，没有关闭自己 PIPE 的
stdin/stdout。

本文件只验证 close 的资源语义（不改 start/io 的读取逻辑）：

1. close 必须结束并 wait 自己 spawn 的 shell；
2. 先让 reader 读到 EOF 排空尾部输出，再关句柄 —— 不能提前置
   ``is_reading=False`` 让 reader 提前退出循环而丢尾部输出；
3. 只关闭自己 PIPE 持有的句柄，调用方共享的文件对象绝不能被关闭；
4. 幂等重复调用不报错、不重复广播；
5. 孙进程继承 stdout 写端时，close 不能阻塞在 reader 的内部锁上（有期限）。

沙箱内信号可能被拒，所以孙进程用例用短寿命 ``sleep 8``，并且只断言
「close 有期限 + 句柄最终由延迟收口关闭」，不依赖 kill 一定成功。
"""
from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

# 必须先于 config / modules 导入：它负责临时运行根与环境隔离。
from process_cleanup_env import (  # noqa: E402
    TMP_ROOT,
    resource_warnings,
    unclosed_file_warnings,
)

from modules.persistent_terminal import PersistentTerminal  # noqa: E402


class PersistentTerminalCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="term_cleanup_", dir=str(TMP_ROOT)))

    def _terminal(self) -> PersistentTerminal:
        return PersistentTerminal(
            session_name="cleanup",
            working_dir=str(self.tmp),
            shell_command="/bin/sh",
            project_path=str(self.tmp),
        )

    def test_close_reaps_shell_and_closes_own_pipes(self) -> None:
        terminal = self._terminal()
        process = subprocess.Popen(
            ["/bin/sh"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        terminal.process = process
        terminal.is_running = True

        with resource_warnings() as caught:
            self.assertTrue(terminal.close())
            self.assertIsNotNone(process.returncode, "close 必须 wait 到自己的 shell 退出")
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)
            # stderr=STDOUT 时 Popen 不暴露 stderr 句柄：本实例无权也不该关闭它。
            self.assertIsNone(process.stderr)

            # 幂等：再次 close 只做句柄收口，返回 False，且不重复广播。
            self.assertFalse(terminal.close())
            self.assertFalse(terminal.is_running)

            # 断开引用后 GC，让任何漏关的句柄以 ResourceWarning 暴露。
            terminal.process = None
            del terminal, process
        self.assertEqual(unclosed_file_warnings(caught), [])

    def test_close_never_closes_shared_descriptor(self) -> None:
        """调用方传入的共享 stdout 文件对象（非 PIPE）绝不能被 close 关闭。"""
        shared_path = self.tmp / "shared.log"
        shared = open(shared_path, "wb")
        try:
            terminal = self._terminal()
            process = subprocess.Popen(
                ["/bin/sh"],
                stdin=subprocess.PIPE,
                stdout=shared,
                stderr=subprocess.STDOUT,
            )
            terminal.process = process
            terminal.is_running = True

            with resource_warnings() as caught:
                self.assertTrue(terminal.close())
                self.assertFalse(shared.closed, "外部共享描述符被 close 了")
                self.assertTrue(process.stdin.closed, "自己的 stdin PIPE 应关闭")
                self.assertIsNone(process.stdout, "非 PIPE 时 Popen 不暴露 stdout 句柄")
                self.assertFalse(terminal.close())

                terminal.process = None
                del terminal, process
            self.assertEqual(unclosed_file_warnings(caught), [])
        finally:
            shared.close()

    def test_close_waits_reader_and_keeps_tail_output(self) -> None:
        terminal = self._terminal()
        process = subprocess.Popen(
            ["/bin/sh"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        terminal.process = process
        terminal.is_running = True
        terminal.is_reading = True
        reader = threading.Thread(target=terminal._read_output, daemon=True)
        terminal.reader_thread = reader
        reader.start()

        with resource_warnings() as caught:
            process.stdin.write(b"echo marker-cleanup\n")
            process.stdin.flush()
            deadline = time.time() + 10
            while time.time() < deadline:
                if "marker-cleanup" in "".join(terminal.output_buffer):
                    break
                time.sleep(0.02)
            self.assertIn("marker-cleanup", "".join(terminal.output_buffer))

            self.assertTrue(terminal.close())
            # reader 必须自行退出（不因句柄关闭而卡死），且已读输出不丢。
            self.assertFalse(reader.is_alive(), "reader 线程未退出")
            self.assertIn("marker-cleanup", "".join(terminal.output_buffer))
            self.assertTrue(process.stdout.closed)

            terminal.process = None
            del terminal, process
        self.assertEqual(unclosed_file_warnings(caught), [])

    def test_close_is_bounded_when_grandchild_holds_stdout(self) -> None:
        """孙进程继承 stdout 写端时：close 不得阻塞在 reader 的内部锁上。

        ``echo marker-tail; sleep 8 &`` 后 shell 退出，但 sleep 仍持有写端，
        reader 阻塞在 read() 上。旧实现会在本线程 close stdout，卡到 sleep 结束
        （实测 3s 不返回）；修复后 close 有期限返回，句柄由延迟收口线程在
        reader 读到 EOF 后关闭。
        """
        terminal = self._terminal()
        process = subprocess.Popen(
            ["/bin/sh"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        terminal.process = process
        terminal.is_running = True
        terminal.is_reading = True
        reader = threading.Thread(target=terminal._read_output, daemon=True)
        terminal.reader_thread = reader
        reader.start()

        proc_ref = process
        try:
            process.stdin.write(b"echo marker-tail; sleep 8 &\n")
            process.stdin.flush()
            deadline = time.time() + 10
            while time.time() < deadline:
                if "marker-tail" in "".join(terminal.output_buffer):
                    break
                time.sleep(0.02)
            self.assertIn("marker-tail", "".join(terminal.output_buffer))

            with resource_warnings() as caught:
                started = time.time()
                self.assertTrue(terminal.close())
                elapsed = time.time() - started
                self.assertLess(elapsed, 5.0, f"close 被孙进程拖住: {elapsed:.1f}s")
                self.assertIn("marker-tail", "".join(terminal.output_buffer))

                # reader 仍在阻塞：句柄由延迟收口线程在 EOF（孙进程退出）后关闭。
                deadline = time.time() + 15
                while time.time() < deadline and not process.stdout.closed:
                    time.sleep(0.05)
                self.assertTrue(process.stdout.closed, "延迟收口未关闭 stdout")
                self.assertFalse(reader.is_alive(), "reader 线程未退出")

                terminal.process = None
                del terminal, process
            self.assertEqual(unclosed_file_warnings(caught), [])
        finally:
            # 兜底：测试失败时不要让 sleep 子进程与管道残留。
            if proc_ref.poll() is None:
                proc_ref.kill()
                proc_ref.wait(timeout=10)

    def test_close_does_not_drop_buffered_output(self) -> None:
        """close 不能先置 ``is_reading=False`` 就撒手：reader 必须读到 EOF 才停。

        回归：输出量远超单个 1024 字节块时，提前停止循环会只拿到第一块，
        丢掉后续所有行（与“等 reader 排空”矛盾）。
        """
        terminal = self._terminal()
        process = subprocess.Popen(
            ["/bin/sh"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        terminal.process = process
        terminal.is_running = True
        terminal.is_reading = True
        reader = threading.Thread(target=terminal._read_output, daemon=True)
        terminal.reader_thread = reader
        reader.start()

        with resource_warnings() as caught:
            process.stdin.write(
                b"i=0; while [ $i -lt 200 ]; do echo line-$i-padding-padding; i=$((i+1)); done\n"
            )
            process.stdin.flush()
            self.assertTrue(terminal.close())

            joined = "".join(terminal.output_buffer)
            self.assertIn("line-0-padding-padding", joined)
            self.assertIn("line-199-padding-padding", joined, "reader 提前停止导致尾部输出丢失")
            self.assertFalse(reader.is_alive())
            self.assertTrue(process.stdout.closed)

            terminal.process = None
            del terminal, process
        self.assertEqual(unclosed_file_warnings(caught), [])

    def test_close_on_already_dead_process_is_idempotent(self) -> None:
        """进程自然结束后再 close：只收口句柄，不报错、不重复广播。"""
        terminal = self._terminal()
        broadcasts = []
        terminal.broadcast = lambda event, payload: broadcasts.append(event)
        process = subprocess.Popen(
            ["/bin/sh", "-c", "echo done"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        process.wait(timeout=10)
        terminal.process = process
        terminal.is_running = True

        with resource_warnings() as caught:
            self.assertTrue(terminal.close())
            self.assertEqual(broadcasts, ["terminal_closed"])
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)

            self.assertFalse(terminal.close())
            self.assertEqual(broadcasts, ["terminal_closed"])

            terminal.process = None
            del terminal, process
        self.assertEqual(unclosed_file_warnings(caught), [])


if __name__ == "__main__":
    unittest.main()
