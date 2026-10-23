"""对话标签条 running 判据回归：标签蓝点 = 对话级运行态。

对应改动：`server/conversation_tabs.py` 的 `running` 由「主任务是否在跑」
改为「对话是否在运行（主任务 + 后台子智能体/后台命令/多智能体）」，
复用 `server/context/reaper.py::_conversation_terminal_has_running_work`。
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import state as server_state
from server.conversation_tabs import (
    _active_run_conversation_ids,
    _conversation_is_running,
    _enrich_tabs,
    _normalized_conversation_ids,
    _peek_conversation_terminal,
)
from server.tasks import task_manager

_IDLE_STATUS = {
    "has_running_sub_agents": False,
    "has_running_background_commands": False,
    "has_running_multi_agent": False,
}


def _status(**overrides):
    data = dict(_IDLE_STATUS)
    data.update(overrides)
    return data


class _FakeTerminal:
    """最小对话级 terminal 替身（判定链只用属性探测 + 被 patch 的聚合函数）。"""

    _reaper_closing = False


def _isolate_terminals(test_case):
    """备份/清空/还原全局终端表，避免污染真实进程状态。"""

    def setUp(self):
        self._saved_terminals = dict(server_state.user_terminals)
        server_state.user_terminals.clear()

    def tearDown(self):
        server_state.user_terminals.clear()
        server_state.user_terminals.update(self._saved_terminals)

    test_case.setUp = setUp
    test_case.tearDown = tearDown
    return test_case


class NormalizedIdsTest(unittest.TestCase):
    def test_prefix_variants(self):
        self.assertEqual(_normalized_conversation_ids("conv_abc"), ["conv_abc"])
        self.assertEqual(_normalized_conversation_ids("abc"), ["abc", "conv_abc"])
        self.assertEqual(_normalized_conversation_ids(""), [])
        self.assertEqual(_normalized_conversation_ids(None), [])


@_isolate_terminals
class ConversationIsRunningTest(unittest.TestCase):
    USER = "u1"
    WS = "ws1"
    CONV = "conv_abc"

    def _key(self, conv):
        return f"{self.USER}::{self.WS}::{conv}"

    def test_active_main_task_true_without_terminal(self):
        """活动主任务直接判定为运行中，不需要 terminal。"""
        self.assertTrue(_conversation_is_running(self.USER, self.WS, self.CONV, {(self.WS, "conv_abc")}))

    def test_other_workspace_main_task_is_not_borrowed(self):
        self.assertFalse(_conversation_is_running(self.USER, self.WS, self.CONV,
                                                 {("other-ws", self.CONV)}))

    def test_no_terminal_no_run_false(self):
        """无活动 run 且无 terminal → 不运行，且不抛异常。"""
        self.assertFalse(_conversation_is_running(self.USER, self.WS, self.CONV, set()))

    def test_prefix_normalization_between_tab_and_run(self):
        """标签 id 与 run 记录的前缀写法不一致时仍能对齐（走真实归一化生产者）。"""
        pairs = (("abc", "conv_abc"), ("conv_abc", "abc"),
                 ("abc", "abc"), ("conv_abc", "conv_abc"))
        for tab_id, run_id in pairs:
            with patch("server.runtime.runtime_service.list_runs",
                       return_value=[{"conversation_id": run_id}]):
                active = _active_run_conversation_ids(self.USER, [self.WS])
            self.assertTrue(
                _conversation_is_running(self.USER, self.WS, tab_id, active),
                f"tab={tab_id} run={run_id}",
            )

    def _running_with_status(self, **overrides):
        server_state.user_terminals[self._key(self.CONV)] = _FakeTerminal()
        with patch.object(task_manager, "list_tasks", return_value=[]), \
                patch.object(task_manager, "get_conversation_running_status",
                             return_value=_status(**overrides)):
            return _conversation_is_running(self.USER, self.WS, self.CONV, set())

    def test_running_sub_agent_after_main_task_finished(self):
        """核心修复点：主任务已结束但后台子智能体仍在跑 → True。"""
        self.assertTrue(self._running_with_status(has_running_sub_agents=True))

    def test_running_background_command(self):
        self.assertTrue(self._running_with_status(has_running_background_commands=True))

    def test_running_multi_agent(self):
        self.assertTrue(self._running_with_status(has_running_multi_agent=True))

    def test_all_idle_false(self):
        self.assertFalse(self._running_with_status())

    def test_reaper_closing_terminal_ignored(self):
        """带回收关闭标记的实例视为不可用，不应点亮蓝点。"""
        term = _FakeTerminal()
        term._reaper_closing = True
        server_state.user_terminals[self._key(self.CONV)] = term
        self.assertFalse(_conversation_is_running(self.USER, self.WS, self.CONV, set()))

    def test_running_status_lookup_failure_is_conservative(self):
        """聚合判定抛异常时与回收器同口径保守返回 True（宁可多亮不可漏亮），
        且不向端点抛出异常。"""
        server_state.user_terminals[self._key(self.CONV)] = _FakeTerminal()
        with patch.object(task_manager, "list_tasks", return_value=[]), \
                patch.object(task_manager, "get_conversation_running_status",
                             side_effect=RuntimeError("boom")):
            self.assertTrue(_conversation_is_running(self.USER, self.WS, self.CONV, set()))

    def test_peek_does_not_create_terminal(self):
        """标签条 600ms 轮询不得按需创建对话级 terminal。"""
        before = len(server_state.user_terminals)
        self.assertIsNone(_peek_conversation_terminal(self.USER, self.WS, ["conv_zzz"]))
        self.assertEqual(len(server_state.user_terminals), before)

    def test_peek_never_borrows_other_workspace_terminal(self):
        """相同对话 id 的另一个工作区实例不能污染当前标签。"""
        server_state.user_terminals[f"{self.USER}::other-ws::{self.CONV}"] = _FakeTerminal()
        self.assertIsNone(_peek_conversation_terminal(self.USER, self.WS, [self.CONV]))


class ActiveRunConversationIdsTest(unittest.TestCase):
    def test_collects_and_normalizes(self):
        runs = [{"conversation_id": "conv_x"}, {"conversation_id": "y"}, {}]
        with patch("server.runtime.runtime_service.list_runs", return_value=runs):
            ids = _active_run_conversation_ids("u1", ["ws1"])
        self.assertIn(("ws1", "conv_x"), ids)
        self.assertIn(("ws1", "y"), ids)
        self.assertIn(("ws1", "conv_y"), ids)

    def test_runtime_service_failure_returns_empty(self):
        with patch("server.runtime.runtime_service.list_runs", side_effect=RuntimeError("boom")):
            self.assertEqual(_active_run_conversation_ids("u1", ["ws1"]), set())


@_isolate_terminals
class EnrichTabsTest(unittest.TestCase):
    USER = "u1"
    WS = "ws1"

    def test_kind_new_never_running(self):
        tabs = [{"kind": "new", "key": "k1", "conversation_id": "", "workspace_id": self.WS}]
        with patch("server.conversation_tabs._load_index_titles", return_value={}):
            _enrich_tabs(self.USER, tabs)
        self.assertFalse(tabs[0]["running"])

    def test_conv_tab_running_from_active_run(self):
        tabs = [{"kind": "conv", "key": "k2", "conversation_id": "conv_abc",
                 "workspace_id": self.WS}]
        with patch("server.conversation_tabs._load_index_titles", return_value={}), \
                patch("server.runtime.runtime_service.list_runs",
                      return_value=[{"conversation_id": "conv_abc"}]):
            _enrich_tabs(self.USER, tabs)
        self.assertTrue(tabs[0]["running"])

    def test_conv_tab_idle_is_false(self):
        tabs = [{"kind": "conv", "key": "k3", "conversation_id": "conv_abc",
                 "workspace_id": self.WS}]
        with patch("server.conversation_tabs._load_index_titles", return_value={}), \
                patch("server.runtime.runtime_service.list_runs", return_value=[]):
            _enrich_tabs(self.USER, tabs)
        self.assertFalse(tabs[0]["running"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
