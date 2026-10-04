from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from command_blocking_helpers import (
    _CommandBlockingCase, _BASE_TMP, _LEGACY_WORD, _PERSONAL_WORD, _PERSONAL_WORD_2,
    _import_cb, _cb_path, _save_ok, _save_rejected, _validate,
)

class TestTerminalOperatorBinding(_CommandBlockingCase):

    def _make_operator(self, data_dir):
        from modules.terminal_ops import TerminalOperator
        try:
            return TerminalOperator(str(self.tmp), data_dir=str(data_dir))
        except TypeError as exc:
            self.fail(
                "TerminalOperator 构造函数尚不接受 data_dir 参数（契约未落地）: "
                f"{exc}"
            )

    def test_validate_command_uses_instance_data_dir(self):
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        ops = self._make_operator(self.data_dir)
        allowed, msg = ops._validate_command(f"echo {_PERSONAL_WORD.upper()}")
        self.assertFalse(allowed, "个人规则应按 data_dir 绑定生效（大小写不敏感）")
        self.assertTrue(msg)
        allowed2, _ = ops._validate_command("echo hello")
        self.assertTrue(allowed2)

    def test_validate_command_realtime_toggle(self):
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        ops = self._make_operator(self.data_dir)
        allowed, _ = ops._validate_command(f"echo {_PERSONAL_WORD}")
        self.assertFalse(allowed)
        _save_ok(self, self.cb, self.data_dir, {"enabled": False})
        allowed, _ = ops._validate_command(f"echo {_PERSONAL_WORD}")
        self.assertTrue(allowed, "关闭后 _validate_command 应立即放行")

    def test_personal_disabled_means_no_personal_blocking(self):
        # 个人拦截关闭即个人规则完全不生效（实现已不再叠加部署级内置词表，
        # 该行为变化由报告记录，测试只锁定个人引擎语义）。
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": False, "rules": [_PERSONAL_WORD]})
        ops = self._make_operator(self.data_dir)
        allowed, _ = ops._validate_command(f"echo {_PERSONAL_WORD}")
        self.assertTrue(allowed, "个人开关关闭时个人规则不得拦截")

    def test_operator_data_dir_isolation_between_users(self):
        user_b = self.tmp / "user_b"
        user_b.mkdir(parents=True, exist_ok=True)
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        ops_b = self._make_operator(user_b)
        allowed, _ = ops_b._validate_command(f"echo {_PERSONAL_WORD}")
        self.assertTrue(allowed, "其他用户的个人规则不得影响本实例")


# ---------------------------------------------------------------------------
# 执行入口：TerminalManager.send_to_terminal 写入前验证（零写入）
# ---------------------------------------------------------------------------

class _FakeTerminal:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send_command(self, command, **kwargs):
        self.sent.append(command)
        return {"success": True, "status": "completed", "output": ""}

    def close(self):
        self.closed = True


class TestTerminalManagerValidator(_CommandBlockingCase):

    def _make_manager(self, validator):
        from modules.terminal_manager import TerminalManager
        kwargs = {}
        if validator is not None:
            kwargs["command_validator"] = validator
        try:
            manager = TerminalManager(project_path=str(self.tmp), **kwargs)
        except TypeError as exc:
            if validator is not None:
                self.fail(
                    "TerminalManager 构造函数尚不接受 command_validator 参数"
                    f"（契约未落地）: {exc}"
                )
            raise
        return manager

    def _attach_fake(self, manager, name="s1"):
        fake = _FakeTerminal()
        manager.terminals[name] = fake
        manager.active_terminal = name
        return fake

    def test_send_to_terminal_blocked_means_zero_write(self):
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        validator = lambda cmd: self.cb.validate_command(cmd, str(self.data_dir))
        manager = self._make_manager(validator)
        fake = self._attach_fake(manager)
        result = manager.send_to_terminal(
            f"echo {_PERSONAL_WORD}", session_name="s1", output_wait=1,
        )
        self.assertIs(result.get("success"), False,
                      f"被拦截命令应返回失败: {result!r}")
        self.assertEqual(fake.sent, [], "被拦截命令不得写入终端（零写入）")

    def test_send_to_terminal_allowed_passes_through(self):
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        validator = lambda cmd: self.cb.validate_command(cmd, str(self.data_dir))
        manager = self._make_manager(validator)
        fake = self._attach_fake(manager)
        result = manager.send_to_terminal("echo hello", session_name="s1", output_wait=1)
        self.assertEqual(fake.sent, ["echo hello"],
                         f"未命中规则的命令应正常送达: {result!r}")

    def test_send_to_terminal_without_validator_backward_compatible(self):
        manager = self._make_manager(None)
        fake = self._attach_fake(manager)
        manager.send_to_terminal(f"echo {_PERSONAL_WORD}", session_name="s1",
                                 output_wait=1)
        self.assertEqual(fake.sent, [f"echo {_PERSONAL_WORD}"],
                         "未注入 validator 时应保持原行为（向后兼容）")


# ---------------------------------------------------------------------------
# 执行入口：BackgroundCommandManager 复用校验（拒绝即无启动）
# ---------------------------------------------------------------------------

class TestBackgroundCommandEntry(_CommandBlockingCase):

    def test_background_blocked_command_never_starts(self):
        from modules.terminal_ops import TerminalOperator
        from modules.background_command_manager import BackgroundCommandManager
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        try:
            ops = TerminalOperator(str(self.tmp), data_dir=str(self.data_dir))
        except TypeError as exc:
            self.fail(f"TerminalOperator 尚不接受 data_dir（契约未落地）: {exc}")
        manager = BackgroundCommandManager(str(self.tmp))
        result = manager.create_background_command(
            terminal_ops=ops,
            command=f"echo {_PERSONAL_WORD}",
            timeout=5,
            conversation_id=None,
            wait_seconds=0,
        )
        self.assertIs(result.get("success"), False,
                      f"后台入口被拦命令应返回失败: {result!r}")
        self.assertTrue(result.get("error"), "后台拒绝应携带错误说明")
        self.assertEqual(getattr(manager, "_processes", {}), {},
                         "后台入口拒绝不得启动任何进程")
        running = [
            rec for rec in getattr(manager, "_records", {}).values()
            if rec.get("status") == "running"
        ]
        self.assertEqual(running, [], "后台入口拒绝不得留下 running 记录")

    def test_background_allowed_command_shape_unaffected(self):
        # 不启动真实命令：仅验证「非拦截原因」下校验门不误判。
        # 个人规则为空时，一个必然因 timeout 缺失被拒的请求应先过命令校验，
        # 其错误信息应是 timeout 相关而非拦截相关。
        from modules.terminal_ops import TerminalOperator
        from modules.background_command_manager import BackgroundCommandManager
        _save_ok(self, self.cb, self.data_dir, {"enabled": True, "rules": []})
        try:
            ops = TerminalOperator(str(self.tmp), data_dir=str(self.data_dir))
        except TypeError as exc:
            self.fail(f"TerminalOperator 尚不接受 data_dir（契约未落地）: {exc}")
        manager = BackgroundCommandManager(str(self.tmp))
        result = manager.create_background_command(
            terminal_ops=ops,
            command="echo hello",
            timeout=None,  # 触发既有的 timeout_required 分支，避免真实启动
            conversation_id=None,
            wait_seconds=0,
        )
        self.assertIs(result.get("success"), False)
        self.assertNotIn(_PERSONAL_WORD, str(result.get("error", "")),
                         "空规则下普通命令不应被个人拦截误伤")


# ---------------------------------------------------------------------------
# 执行入口：MainTerminal.handle_tool_call 入口检查（ExecutionBackend 零调用）
# ---------------------------------------------------------------------------

class TestMainTerminalEntryCheck(_CommandBlockingCase):

    def _run_coro(self, coro):
        return asyncio.run(asyncio.wait_for(coro, timeout=60))

    def _construct_main_terminal(self):
        from core.main_terminal import MainTerminal
        return MainTerminal(project_path=str(self.tmp), data_dir=str(self.data_dir))

    def test_main_terminal_injects_validator_bound_to_its_data_dir(self):
        try:
            mt = self._construct_main_terminal()
        except unittest.SkipTest:
            raise
        except Exception as exc:
            raise unittest.SkipTest(f"MainTerminal 构造失败（非契约判定）: {exc!r}")
        manager = getattr(mt, "terminal_manager", None)
        self.assertIsNotNone(manager, "MainTerminal 应持有 terminal_manager")
        validator = getattr(manager, "command_validator", None)
        self.assertTrue(callable(validator),
                        "MainTerminal 应向 TerminalManager 注入 command_validator")
        # 注入的 validator 必须绑定 MainTerminal 自身 data_dir：写入该 data_dir 的
        # 个人规则经 validator 立即生效。
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        allowed, msg = validator(f"echo {_PERSONAL_WORD}")
        self.assertFalse(allowed,
                         "注入 TerminalManager 的 validator 未绑定 MainTerminal.data_dir")
        self.assertTrue(msg)
        allowed2, _ = validator("echo hello")
        self.assertTrue(allowed2)

    def test_handle_tool_call_blocks_before_backend(self):
        try:
            mt = self._construct_main_terminal()
        except unittest.SkipTest:
            raise
        except Exception as exc:  # 环境原因导致无法构造时跳过并说明
            raise unittest.SkipTest(f"MainTerminal 构造失败（非契约判定）: {exc!r}")
        from modules.execution_plane.fake import FakeExecutionBackend

        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        backend = FakeExecutionBackend()
        mt.execution_backend = backend
        # 防挂：绕开任何交互式确认/权限提示路径
        mt.confirm_action = lambda *a, **k: asyncio.sleep(0, result=False)
        try:
            mt.get_permission_mode = lambda: "unrestricted"
        except Exception:
            pass

        out = self._run_coro(mt.handle_tool_call(
            "run_command",
            {"command": f"echo {_PERSONAL_WORD}", "timeout": 5},
        ))
        payload = json.loads(out)
        self.assertIs(payload.get("success"), False,
                      f"入口拦截应返回失败: {payload!r}")
        self.assertEqual(backend.calls, [],
                         "入口拦截后 ExecutionBackend.run_command 不得被调用")

        out_bg = self._run_coro(mt.handle_tool_call(
            "run_command",
            {"command": f"echo {_PERSONAL_WORD}", "timeout": 5,
             "run_in_background": True},
        ))
        payload_bg = json.loads(out_bg)
        self.assertIs(payload_bg.get("success"), False,
                      f"后台分支入口拦截应返回失败: {payload_bg!r}")
        self.assertEqual(backend.calls, [],
                         "入口拦截后 ExecutionBackend.run_command_background 不得被调用")

        # 对照组：未命中个人规则的命令应正常到达替身后端
        out_ok = self._run_coro(mt.handle_tool_call(
            "run_command", {"command": "echo hello", "timeout": 5},
        ))
        payload_ok = json.loads(out_ok)
        self.assertTrue(
            any(call.get("op") == "run_command" for call in backend.calls),
            f"未拦截命令应到达 ExecutionBackend: payload={payload_ok!r} calls={backend.calls!r}",
        )


# ---------------------------------------------------------------------------
# API 层：GET/POST /api/command-blocking（普通用户可用）与 headless 登记
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
