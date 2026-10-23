import asyncio
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
import unittest

from helpers import StopCase
from modules.terminal_ops import TerminalOperator


class NativeStopTests(StopCase):
    @unittest.skipUnless(os.environ.get("ASTRION_NATIVE_STOP_TEST") == "1",
                         "显式实机测试：需要允许向本次测试子进程发送信号")
    async def test_stop_kills_real_foreground_command_and_records_master_cancellation(self):
        ready = self.root / "ready.pid"
        operator = TerminalOperator(str(self.root), container_session=SimpleNamespace(mode="host"))
        operator.set_host_execution_mode("direct")
        script = "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
        command = "exec " + " ".join(shlex.quote(part) for part in (sys.executable, "-c", script, str(ready)))
        self.agent._call_model = self.model

        async def execute(_name, _args):
            # Exercise the public native command API, which returns status=cancelled.
            return await operator.run_command(command, timeout=40)

        self.agent._execute_tool = execute
        self.start()
        try:
            await self.wait_until(ready.exists)
            pid = int(ready.read_text())
            os.kill(pid, 0)
            await self.stop_from_master()
            def no_process():
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                return False
            await self.wait_until(no_process)
            saved = self.assert_persisted_pairs()
            results = [item["content"] for item in saved["messages"] if item["role"] == "tool"]
            self.assertEqual(results, ["被主智能体取消"])
        finally:
            # Only the foreground process created by this test can be cleaned up here.
            if ready.exists():
                try:
                    os.kill(int(ready.read_text()), 9)
                except ProcessLookupError:
                    pass

    async def model(self, *_):
        return self.response(calls=[self.call("command", "run_command")])
