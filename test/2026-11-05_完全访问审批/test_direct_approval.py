"""direct + 完全访问审批回归（2026-11-05）。

覆盖三块纯逻辑，不启动服务、不读真实配置/密钥、不写项目目录：
1. ToolApprovalManager：默认双审批、auto-only（自动审核通过即执行）、人工先通过仍需自动审核、
   拒绝终态、单次原子 claim、参数/执行环境绑定、非法入参 fail-closed。
2. server.tool_execution_authority.approval_execution_scope：审批记录与当前执行环境/工作区/任务
   绑定的二次核对；direct_execution 产出 direct 作用域且不签发 full_access grant。
3. modules.conversation_modes：direct 在 approval / auto_approval 下保留，readonly / plan / 非宿主机
   强制 sandbox；restore 链路把组合真正落到 terminal；目标模式强制 execute + auto_approval + sandbox。

运行（项目根已加入 sys.path，可用环境变量显式指定）：
    ASTRION_ROOT=/path/to/agents /opt/homebrew/bin/python3.11 -B test_direct_approval.py
    # 或在项目根直接跑：
    /opt/homebrew/bin/python3.11 -B test/2026-11-05_完全访问审批/test_direct_approval.py

只依赖标准库与项目内纯逻辑模块（modules.tool_approval_manager / modules.conversation_modes /
server.tool_execution_authority），不 import server.app、不读 personalization.json。
"""
from __future__ import annotations

import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def _project_root() -> Path:
    env = os.environ.get("ASTRION_ROOT", "").strip()
    candidates = [Path(env)] if env else []
    here = Path(__file__).resolve()
    candidates += [Path.cwd(), *here.parents]
    for candidate in candidates:
        try:
            if (candidate / "modules" / "conversation_modes.py").is_file():
                return candidate.resolve()
        except OSError:
            continue
    raise RuntimeError("找不到项目根：请设置 ASTRION_ROOT 或在项目根运行本测试")


ROOT = _project_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.conversation_modes import resolve_conversation_modes, restore_conversation_modes  # noqa: E402
from modules.tool_approval_manager import ToolApprovalManager  # noqa: E402
from server.tool_execution_authority import approval_execution_scope  # noqa: E402
from modules.execution_scope import command_fingerprint  # noqa: E402


ARGS = {"command": "echo approved", "timeout": 10, "working_dir": None}


class ManagerDualApprovalTests(unittest.TestCase):
    """默认双审批：人工与自动各自独立，两项都通过才可执行。"""

    def setUp(self):
        self.manager = ToolApprovalManager()

    def _full_access(self, **overrides):
        options = dict(
            username="user", conversation_id="conv", task_id="task", tool_call_id="tool",
            tool_name="run_command", arguments=deepcopy(ARGS), preview={},
            approval_type="full_access", auto_review_required=True,
            executor_id=1, workspace_root="/ws", execution_mode="direct",
        )
        options.update(overrides)
        return self.manager.create_request(**options)

    def test_default_requires_human_approval(self):
        item = self._full_access()
        self.assertTrue(item["human_approval_required"])
        self.assertEqual(item["execution_mode"], "direct")
        auto_only = self.manager.record_auto_review(item["approval_id"], "approved")
        self.assertEqual(auto_only["status"], "pending", "自动通过后必须仍等待人工")
        with self.assertRaises(ValueError):
            self.manager.claim_execution(item["approval_id"], auto_only)
        human = self.manager.decide(item["approval_id"], "user", "approved")
        self.assertEqual(human["status"], "approved")
        claimed = self.manager.claim_execution(item["approval_id"], human)
        self.assertEqual(claimed["approval_id"], item["approval_id"])

    def test_human_first_still_waits_for_auto(self):
        item = self._full_access()
        aid = item["approval_id"]
        human = self.manager.decide(aid, "user", "approved")
        self.assertEqual(human["status"], "pending", "人工先通过不能替代自动审核")
        with self.assertRaises(ValueError):
            self.manager.claim_execution(aid, human)
        done = self.manager.record_auto_review(aid, "approved")
        self.assertEqual(done["status"], "approved")

    def test_auto_only_approval_executes_without_human(self):
        """用户级 allow_auto_approve_full_access 打开时的语义：自动审核通过即执行。"""
        item = self._full_access(human_approval_required=False)
        self.assertFalse(item["human_approval_required"])
        done = self.manager.record_auto_review(item["approval_id"], "approved")
        self.assertEqual(done["status"], "approved")
        self.assertIsNone(done["human_decision"])
        claimed = self.manager.claim_execution(item["approval_id"], done)
        self.assertEqual(claimed["approval_id"], item["approval_id"])

    def test_auto_only_still_requires_auto_review(self):
        with self.assertRaises(ValueError):
            self._full_access(human_approval_required=False, auto_review_required=False)

    def test_auto_only_rejection_is_terminal(self):
        item = self._full_access(human_approval_required=False)
        aid = item["approval_id"]
        rejected = self.manager.record_auto_review(aid, "rejected", reason="危险命令")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.manager.decide(aid, "user", "approved")["status"], "rejected")
        with self.assertRaises(ValueError):
            self.manager.claim_execution(aid, rejected)

    def test_human_rejection_is_terminal(self):
        item = self._full_access()
        aid = item["approval_id"]
        self.assertEqual(self.manager.decide(aid, "user", "rejected")["status"], "rejected")
        self.assertEqual(self.manager.record_auto_review(aid, "approved")["status"], "rejected")
        with self.assertRaises(ValueError):
            self.manager.claim_execution(aid, self.manager.get(aid))

    def test_single_use_claim(self):
        item = self._full_access(human_approval_required=False)
        done = self.manager.record_auto_review(item["approval_id"], "approved")
        self.manager.claim_execution(item["approval_id"], done)
        with self.assertRaises(ValueError):
            self.manager.claim_execution(item["approval_id"], done)

    def test_atomic_claim_has_single_winner(self):
        item = self._full_access(human_approval_required=False)
        done = self.manager.record_auto_review(item["approval_id"], "approved")
        aid = item["approval_id"]

        def claim(_):
            try:
                self.manager.claim_execution(aid, done)
                return 1
            except ValueError:
                return 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim, range(20))), 1)

    def test_claim_binds_arguments_and_execution_mode(self):
        item = self._full_access(human_approval_required=False)
        done = self.manager.record_auto_review(item["approval_id"], "approved")
        aid = item["approval_id"]
        mutations = {
            "arguments": dict(ARGS, command="echo substituted"),
            "execution_mode": "sandbox",
            "human_approval_required": True,
            "auto_review_required": False,
            "tool_call_id": "other-tool",
            "task_id": "other-task",
            "conversation_id": "other-conv",
            "executor_id": 999,
            "workspace_root": "/other",
            "approval_type": "sandbox_write",
        }
        for key, value in mutations.items():
            expected = dict(done)
            expected[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.manager.claim_execution(aid, expected)
        # 未污染的原记录仍可领取一次
        self.manager.claim_execution(aid, done)

    def test_expired_cannot_be_revived(self):
        item = self._full_access()
        aid = item["approval_id"]
        self.manager.mark_expired(aid)
        self.assertEqual(self.manager.decide(aid, "user", "approved")["status"], "expired")
        self.assertEqual(self.manager.record_auto_review(aid, "approved")["status"], "expired")
        with self.assertRaises(ValueError):
            self.manager.claim_execution(aid, self.manager.get(aid))

    def test_invalid_request_is_rejected(self):
        with self.assertRaises(ValueError):
            self._full_access(approval_type="bogus")
        with self.assertRaises(ValueError):
            self._full_access(execution_mode="weird")
        with self.assertRaises(ValueError):
            self._full_access(auto_review_required="yes")


class ApprovalScopeBindingTests(unittest.TestCase):
    """审批记录 → 执行作用域：环境/工作区/任务/实参必须与当前一致。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.manager = ToolApprovalManager()
        self.terminal = SimpleNamespace(
            project_path=str(self.root),
            context_manager=SimpleNamespace(
                project_path=self.root,
                current_conversation_id="conv",
                _get_conversation_manager_for_id=lambda cid: SimpleNamespace(
                    update_conversation_metadata=lambda *a, **k: None
                ),
            ),
            get_permission_mode=lambda: "auto_approval",
            get_work_mode=lambda: "execute",
            get_execution_mode=lambda: "direct",
        )

    def _approved(self, **overrides):
        options = dict(
            username="user", conversation_id="conv", task_id="task", tool_call_id="tool",
            tool_name="run_command", arguments=deepcopy(ARGS), preview={},
            approval_type="direct_execution", auto_review_required=True,
            human_approval_required=False,
            executor_id=id(self.terminal), workspace_root=str(self.root),
            execution_mode="direct",
        )
        options.update(overrides)
        item = self.manager.create_request(**options)
        if item["approval_type"] == "full_access":
            # full_access 走双审批：人工与自动各自记录，两项都到齐才 approved；
            # human_approval_required=False 时只由自动审核决定，人工裁决会被拒绝。
            if item.get("human_approval_required", True):
                self.manager.decide(item["approval_id"], "user", "approved")
            return self.manager.record_auto_review(item["approval_id"], "approved")
        # direct_execution 由自动审核直接裁决（record_auto_review 仅接受 full_access）
        return self.manager.decide(
            item["approval_id"], "user", "approved", decider="approval_agent"
        )

    def _scope(self, item, **overrides):
        options = dict(
            task_id="task", tool_call_id="tool", expected_root=self.root,
            manager=self.manager, tool_name="run_command",
        )
        options.update(overrides)
        return approval_execution_scope(self.terminal, deepcopy(ARGS), item, **options)

    def test_direct_execution_scope_is_direct_without_grant(self):
        scope = self._scope(self._approved())
        self.assertEqual(scope.access_level, "full_access")
        self.assertEqual(scope.execution_mode, "direct")
        self.assertTrue(scope.write_granted)
        self.assertIsNone(scope.full_access_grant, "direct_execution 不应签发 full_access 授权")

    def test_execution_mode_must_match_current(self):
        item = self._approved()
        self.terminal.get_execution_mode = lambda: "sandbox"
        with self.assertRaises(ValueError):
            self._scope(item)

    def test_full_access_requires_matching_fingerprint(self):
        item = self._approved(approval_type="full_access")
        with self.assertRaises(ValueError):
            self._scope(item)
        scope = self._scope(item, fingerprint=command_fingerprint(ARGS))
        self.assertIsNotNone(scope.full_access_grant)
        self.assertEqual(scope.execution_mode, "direct")

    def test_scope_rejects_identity_mismatch(self):
        item = self._approved()
        for overrides in (
            {"task_id": "other"},
            {"tool_call_id": "other"},
            {"tool_name": "write_file"},
        ):
            with self.subTest(**overrides), self.assertRaises(ValueError):
                self._scope(item, **overrides)

    def test_scope_rejects_other_workspace(self):
        item = self._approved()
        other = TemporaryDirectory()
        self.addCleanup(other.cleanup)
        with self.assertRaises(ValueError):
            self._scope(item, expected_root=Path(other.name).resolve())

    def test_scope_requires_approved_status(self):
        # 待人工审批的 direct_execution（human_approval_required=True）未裁决前不得签发执行权限
        pending = self.manager.create_request(
            username="user", conversation_id="conv", task_id="task", tool_call_id="tool",
            tool_name="run_command", arguments=deepcopy(ARGS), preview={},
            approval_type="direct_execution", auto_review_required=False,
            human_approval_required=True,
            executor_id=id(self.terminal), workspace_root=str(self.root),
            execution_mode="direct",
        )
        self.assertEqual(pending["status"], "pending")
        with self.assertRaises(ValueError):
            self._scope(pending)
        # 过期条目同样不可领取
        self.manager.mark_expired(pending["approval_id"])
        self.assertEqual(self.manager.get(pending["approval_id"])["status"], "expired")
        with self.assertRaises(ValueError):
            self._scope(self.manager.get(pending["approval_id"]))


class ConversationModeTests(unittest.TestCase):
    """direct 与 approval / auto_approval 可以共存；readonly / plan / 非宿主机强制沙箱。"""

    def test_direct_allowed_for_approval(self):
        modes = resolve_conversation_modes(
            {"work_mode": "execute", "permission_mode": "approval", "execution_mode": "direct"},
            host_mode=True,
        )
        self.assertEqual(modes["permission_mode"], "approval")
        self.assertEqual(modes["execution_mode"], "direct")

    def test_direct_allowed_for_auto_approval(self):
        modes = resolve_conversation_modes(
            {"work_mode": "execute", "permission_mode": "auto_approval", "execution_mode": "direct"},
            host_mode=True,
        )
        self.assertEqual(modes["permission_mode"], "auto_approval")
        self.assertEqual(modes["execution_mode"], "direct")

    def test_direct_allowed_for_unrestricted(self):
        modes = resolve_conversation_modes(
            {"work_mode": "execute", "permission_mode": "unrestricted", "execution_mode": "direct"},
            host_mode=True,
        )
        self.assertEqual(modes["execution_mode"], "direct")

    def test_readonly_forces_sandbox(self):
        modes = resolve_conversation_modes(
            {"work_mode": "execute", "permission_mode": "readonly", "execution_mode": "direct"},
            host_mode=True,
        )
        self.assertEqual(modes["permission_mode"], "readonly")
        self.assertEqual(modes["execution_mode"], "sandbox")

    def test_plan_forces_readonly_and_sandbox(self):
        modes = resolve_conversation_modes(
            {"work_mode": "plan", "permission_mode": "unrestricted", "execution_mode": "direct"},
            host_mode=True,
        )
        self.assertEqual(modes["permission_mode"], "readonly")
        self.assertEqual(modes["execution_mode"], "sandbox")

    def test_non_host_forces_sandbox(self):
        modes = resolve_conversation_modes(
            {"work_mode": "execute", "permission_mode": "auto_approval", "execution_mode": "direct"},
            host_mode=False,
        )
        self.assertEqual(modes["execution_mode"], "sandbox")

    def test_invalid_values_fall_back(self):
        modes = resolve_conversation_modes(
            {"work_mode": "bogus", "permission_mode": "bogus", "execution_mode": "bogus"},
            host_mode=True,
        )
        self.assertEqual(modes["work_mode"], "plan")
        self.assertEqual(modes["permission_mode"], "readonly")
        self.assertEqual(modes["execution_mode"], "sandbox")

    def _terminal(self):
        calls = []

        def record(name, value):
            calls.append((name, value))
            return value

        terminal = SimpleNamespace(
            default_work_mode="plan",
            default_permission_mode="approval",
            get_execution_mode=lambda: "sandbox",
            get_network_permission=lambda: "restricted",
            _is_host_mode=lambda: True,
            set_work_mode=lambda mode, persist=True: record("work", mode),
            set_permission_mode=lambda mode, persist=True: record("permission", mode),
            set_execution_mode=lambda mode: record("execution", mode),
            set_network_permission=lambda mode: record("network", mode),
        )
        return terminal, calls

    def test_restore_keeps_approval_direct(self):
        terminal, calls = self._terminal()
        modes = restore_conversation_modes(terminal, {
            "work_mode": "execute", "permission_mode": "approval",
            "execution_mode": "direct", "network_permission": "restricted",
        })
        self.assertEqual(modes["execution_mode"], "direct")
        self.assertIn(("permission", "approval"), calls)
        self.assertIn(("execution", "direct"), calls)

    def test_restore_readonly_drops_direct(self):
        terminal, calls = self._terminal()
        modes = restore_conversation_modes(terminal, {
            "work_mode": "execute", "permission_mode": "readonly",
            "execution_mode": "direct", "network_permission": "restricted",
        })
        self.assertEqual(modes["execution_mode"], "sandbox")
        self.assertIn(("execution", "sandbox"), calls)

    def test_restore_goal_forces_execute_auto_sandbox(self):
        # 目标模式无人工可交互：注入 stub 的 goal_policy 让 restore 走强制分支
        original = sys.modules.get("modules.goal_policy")
        sys.modules["modules.goal_policy"] = SimpleNamespace(goal_for_terminal=lambda terminal: object())
        try:
            terminal, calls = self._terminal()
            modes = restore_conversation_modes(terminal, {
                "work_mode": "ask", "permission_mode": "approval",
                "execution_mode": "direct", "network_permission": "restricted",
            })
        finally:
            if original is not None:
                sys.modules["modules.goal_policy"] = original
            else:
                sys.modules.pop("modules.goal_policy", None)
        self.assertEqual(modes["work_mode"], "execute")
        self.assertEqual(modes["permission_mode"], "auto_approval")
        self.assertEqual(modes["execution_mode"], "sandbox")
        self.assertIn(("work", "execute"), calls)
        self.assertIn(("permission", "auto_approval"), calls)
        self.assertIn(("execution", "sandbox"), calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
