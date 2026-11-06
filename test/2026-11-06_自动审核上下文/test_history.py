"""自动审核上下文：用户分类、问答/工具配对、计划批准与触发消息。

运行：
    /opt/homebrew/bin/python3.11 -B -m unittest discover \
        -s test/2026-11-06_自动审核上下文 -p 'test_*.py'
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from history_fixtures import (  # noqa: E402
    TOOL_WINDOW,
    USER_WINDOW,
    ApprovalHistory,
    all_rendered,
    assistant_calls,
    bodies,
    chat_msg,
    guidance_msg,
    rendered_kinds,
    tool_result,
    user_msg,
    user_text,
)

# ------------------------------------------------- user / notice 分类

class UserClassificationTests(unittest.TestCase):
    def test_real_user_presend_and_chat_without_source_are_users(self):
        cases = [
            ("直接发送", user_msg("直接发送", mid="u1")),
            ("提前排队", user_msg("提前排队", mid="u2", source="presend")),
            ("chat 无 source", chat_msg("chat 无 source", mid="u3")),
        ]
        for label, message in cases:
            with self.subTest(label):
                self.assertEqual(user_text(message), message["content"])

    def test_guidance_uses_original_text_and_is_not_excluded_by_injection_flag(self):
        message = guidance_msg("[系统通知|guidance]\n把测试放到晚上跑", original="把测试放到晚上跑")
        # 注入标记不能把真实引导判成自动通知
        self.assertTrue(message["metadata"]["is_auto_generated"])
        self.assertEqual(user_text(message), "把测试放到晚上跑")

    def test_guidance_without_original_still_classified_as_user(self):
        message = guidance_msg("缩小改动范围", original=None)
        text = user_text(message)
        self.assertIsNotNone(text)
        self.assertIn("缩小改动范围", text)

    def test_notices_and_injected_users_are_excluded(self):
        cases = {
            "sub_agent": user_msg("子智能体输出", source="sub_agent"),
            "notify": user_msg("模式通知", source="notify"),
            "background_command": user_msg("后台命令完成", source="background_command"),
            "compression_handoff": user_msg("压缩续接", source="compression_handoff"),
            "goal_prompt": user_msg("目标模式已开启", source="goal_prompt"),
            "workflow": user_msg("工作流激活", source="workflow"),
            "skill": user_msg("[系统通知|skill]\n技能内容", source="skill"),
            "auto_generated": user_msg("自动生成", is_auto_generated=True),
            "runtime_injected": user_msg("运行期注入", source=None, runtime_injected=True),
            "hidden": user_msg("隐藏消息", source=None, hidden=True),
            "visibility_hidden": user_msg("隐藏消息", source=None, visibility="hidden"),
            "multi_agent": user_msg("多智能体", source=None, multi_agent_message=True),
            "migration": user_msg("对话已迁移", source=None, conversation_migration=True),
            "network_revision": user_msg("联网变更", source=None, network_revision=3),
            "no_source_notice_prefix": user_msg("[系统通知|background_command]\n完成", source=None),
            "chat_network_notice": user_msg("[Chat 联网设置更新] 联网已开启。", source=None),
            "auto_user_with_flag": user_msg("注入的通知", source="user", is_auto_generated=True),
        }
        for label, message in cases.items():
            with self.subTest(label):
                self.assertIsNone(user_text(message))

    def test_system_role_is_not_treated_as_user(self):
        self.assertIsNone(user_text({"role": "system", "content": "系统提示"}))

    def test_notices_never_surface_as_user_evidence(self):
        history = ApprovalHistory([
            user_msg("真实要求：清理构建产物", mid="u1"),
            user_msg("[系统通知|sub_agent]\n子智能体完成", mid="n1", source="sub_agent"),
            user_msg("压缩续接引导", mid="n2", source="compression_handoff"),
            user_msg("权限模式已切换", mid="n3", source="notify"),
        ], trigger_id="u1")
        self.assertEqual(bodies(history, "用户"), ["真实要求：清理构建产物"])
        rendered = all_rendered(history)
        for notice in ("子智能体完成", "压缩续接引导", "权限模式已切换"):
            self.assertNotIn(notice, rendered)

    def test_trigger_that_is_a_notice_is_kept_but_labelled_non_authorization(self):
        """自动派发的任务其触发消息本身就是通知：保留，但必须标明不是用户授权。"""
        history = ApprovalHistory([
            user_msg("更早的真实要求", mid="u1"),
            user_msg("[系统通知|background_command]\n命令完成 TRIGGER_NOTICE", mid="n2", source="background_command"),
        ], trigger_id="n2")
        self.assertIsNotNone(history.trigger)
        self.assertEqual(history.trigger.kind, "触发通知（非用户授权）")
        text = history.initial_text()
        self.assertIn("TRIGGER_NOTICE", text)
        self.assertIn("触发通知（非用户授权）", text)
        self.assertNotIn("1 用户\n  [系统通知|background_command]", text)

    def test_fallback_without_trigger_id_never_picks_a_notice(self):
        notices_only = ApprovalHistory([
            user_msg("[系统通知|notify]\n模式切换", mid="n1", source="notify"),
            user_msg("[系统通知|background_command]\n命令完成", mid="n2", source="background_command"),
        ])
        self.assertIsNone(notices_only.trigger)
        self.assertIn("没有可用历史", notices_only.initial_text())

        mixed = ApprovalHistory([
            user_msg("真实要求 FALLBACK_MARK", mid="u1"),
            user_msg("[系统通知|notify]\n模式切换", mid="n1", source="notify"),
        ])
        self.assertEqual(mixed.trigger.kind, "用户")
        self.assertIn("FALLBACK_MARK", mixed.trigger.body)

    def test_pending_guidance_is_included_and_notices_are_dropped(self):
        history = ApprovalHistory(
            [user_msg("主消息", mid="u1")],
            trigger_id="u1",
            pending=[
                {"id": "g1", "source": "guidance", "text": "补充：不要动 docs"},
                {"id": "g2", "source": "notify", "text": "权限模式已切换"},
                {"id": "g3", "text": "缺少 source 默认按引导", "files": ["a.txt"]},
            ],
        )
        pending = bodies(history, "用户引导（尚未交主模型）")
        self.assertEqual(len(pending), 2)
        self.assertIn("补充：不要动 docs", pending[0])
        self.assertIn("a.txt", pending[1])
        self.assertNotIn("权限模式已切换", "".join(pending))


# ------------------------------------------------------- 问答 / 工具配对

class QuestionAndToolPairingTests(unittest.TestCase):
    @staticmethod
    def _ask_fixture(*, options=None, answer="用方案 B"):
        arguments = {"question": "走哪个方案？", "context": "两个方案成本不同"}
        if options:
            arguments["options"] = options
        return [
            assistant_calls(("call_ask", "ask_user", arguments)),
            tool_result("call_ask", "ask_user", {
                "success": True, "status": "answered",
                "answer_text": answer, "question_id": "q1",
            }),
        ]

    def test_ask_user_answer_becomes_user_evidence_with_options(self):
        options = ["方案 A：全量重跑", "方案 B：只跑增量"]
        history = ApprovalHistory(self._ask_fixture(options=options))
        rows = [row for row in history.entries if row.kind == "用户问答"]
        self.assertEqual(len(rows), 1)
        body = rows[0].body
        self.assertIn("问题：走哪个方案？", body)
        self.assertIn("背景：两个方案成本不同", body)
        self.assertIn("选项：", body)
        self.assertIn("方案 B：只跑增量", body)
        self.assertIn("回复：用方案 B", body)

    def test_unanswered_ask_is_not_user_evidence(self):
        history = ApprovalHistory([
            assistant_calls(("call_ask", "ask_user", {"question": "继续吗？"})),
            tool_result("call_ask", "ask_user", {"success": False, "status": "expired"}),
        ])
        self.assertEqual(bodies(history, "用户问答"), [])

    def test_user_window_counts_users_questions_and_guidance_together(self):
        history = ApprovalHistory([
            user_msg("第一条会被挤出窗口", mid="u1"),
            *self._ask_fixture(answer="答复一"),
            user_msg("第三条", mid="u3", source="presend"),
            *self._ask_fixture(),
            chat_msg("第五条无 source", mid="u5"),
            guidance_msg("[系统通知|guidance]\n第六条引导", mid="u6", original="第六条引导"),
        ], trigger_id="u3")
        text = history.initial_text()
        user_like = [row for row in history.entries if row.kind != "工具"]
        self.assertGreater(len(user_like), USER_WINDOW)
        non_tool = [kind for kind in rendered_kinds(text) if kind != "工具"]
        self.assertEqual(len(non_tool), USER_WINDOW)
        self.assertNotIn("第一条会被挤出窗口", text)
        for expected in ("答复一", "第三条", "第五条无 source", "第六条引导"):
            self.assertIn(expected, text)
        self.assertIn("未在首批显示", text)

    def test_tool_entries_are_call_and_result_pairs_with_status(self):
        history = ApprovalHistory([
            assistant_calls(("call_1", "run_command", {"command": "npm test"})),
            tool_result("call_1", "run_command", {"success": True, "status": "completed", "output": "ok"}),
        ])
        rows = [row for row in history.entries if row.kind == "工具"]
        self.assertEqual(len(rows), 1)
        body = rows[0].body
        self.assertIn("run_command", body)
        self.assertIn("command: npm test", body)
        self.assertIn("状态：completed", body)
        self.assertIn("output: ok", body)

    def test_tool_without_matching_call_or_with_wrong_name_is_ignored(self):
        history = ApprovalHistory([
            {"role": "tool", "tool_call_id": "ghost", "name": "run_command", "content": "orphan"},
            assistant_calls(("call_1", "run_command", {"command": "ls"})),
            # 结果自称是另一个工具：不采信
            tool_result("call_1", "file_write", {"success": True, "status": "completed"}),
            tool_result("call_1", "run_command", {"success": True, "status": "completed", "output": "ls-ok"}),
        ])
        rows = [row for row in history.entries if row.kind == "工具"]
        self.assertEqual(len(rows), 1)
        self.assertIn("ls-ok", rows[0].body)
        self.assertNotIn("orphan", "".join(row.body for row in rows))

    def test_pending_call_and_later_siblings_in_same_batch_are_not_counted(self):
        """当前待审调用没有结果；同批后续调用也没有结果 → 都不进工具窗口。"""
        history = ApprovalHistory([
            assistant_calls(("call_done", "run_command", {"command": "echo done"})),
            tool_result("call_done", "run_command",
                        {"success": True, "status": "completed", "output": "DONE_MARK"}),
            assistant_calls(
                ("call_pending", "run_command", {"command": "rm -rf /tmp/PENDING_MARK"}),
                ("call_after", "run_command", {"command": "echo AFTER_MARK"}),
            ),
        ])
        rows = [row for row in history.entries if row.kind == "工具"]
        self.assertEqual(len(rows), 1)
        rendered = all_rendered(history)
        self.assertIn("DONE_MARK", rendered)
        self.assertNotIn("PENDING_MARK", rendered)
        self.assertNotIn("AFTER_MARK", rendered)

    def test_tool_window_keeps_only_latest_five(self):
        messages: list[dict] = []
        for index in range(1, TOOL_WINDOW + 2):
            messages.append(assistant_calls((f"call_{index}", "run_command", {"command": f"step-{index}"})))
            messages.append(tool_result(
                f"call_{index}", "run_command",
                {"success": True, "status": "completed", "output": f"out-{index}"},
            ))
        history = ApprovalHistory(messages)
        text = history.initial_text()
        self.assertEqual(len([kind for kind in rendered_kinds(text) if kind == "工具"]), TOOL_WINDOW)
        self.assertNotIn("step-1", text)
        for index in range(2, TOOL_WINDOW + 2):
            self.assertIn(f"step-{index}", text)
        self.assertIn("未在首批显示", text)


# --------------------------------------------- 计划批准 / 触发消息固定保留

class FixedEntryTests(unittest.TestCase):
    @staticmethod
    def _plan_message(body_text="批准正文：先备份再迁移"):
        return [
            assistant_calls(("call_plan", "submit_plan", {"plan_file": ".astrion/plan/x.md"})),
            tool_result("call_plan", "submit_plan", {
                "success": True, "status": "approved",
                "plan_file": ".astrion/plan/x.md", "plan_content": body_text,
            }),
        ]

    def test_approved_plan_is_kept_even_when_far_outside_windows(self):
        messages = self._plan_message()
        for index in range(8):
            messages.append(user_msg(f"后续要求 {index}", mid=f"u{index}"))
        history = ApprovalHistory(messages)
        text = history.initial_text()
        self.assertIn("计划批准", text)
        self.assertIn("先备份再迁移", text)
        # 原文没保存时必须明说不能用现有文件冒充当时版本
        empty = ApprovalHistory([
            assistant_calls(("call_plan", "submit_plan", {"plan_file": ".astrion/plan/y.md"})),
            tool_result("call_plan", "submit_plan", {"success": True, "status": "approved"}),
        ]).initial_text()
        self.assertIn("原批准正文未保存", empty)

    def test_trigger_is_kept_when_outside_window_and_deduplicated(self):
        messages = [user_msg("最早的任务要求 TRIGGER_MARK", mid="t1")]
        for index in range(8):
            messages.append(user_msg(f"闲聊 {index}", mid=f"u{index}"))
        history = ApprovalHistory(messages, trigger_id="t1")
        text = history.initial_text()
        self.assertIn("TRIGGER_MARK", text)
        self.assertEqual(text.count("TRIGGER_MARK"), 1)
        self.assertIn("本次工作由第", text)
        self.assertIn("项触发", text)

    def test_trigger_inside_window_is_not_repeated(self):
        history = ApprovalHistory([
            user_msg("窗口内触发 TRIGGER_MARK", mid="t1"),
            user_msg("后续 1", mid="u1"),
        ], trigger_id="t1")
        text = history.initial_text()
        self.assertEqual(text.count("TRIGGER_MARK"), 1)
        self.assertIn("本次工作由第1项触发。", text)

    def test_plan_and_trigger_together_do_not_duplicate(self):
        messages = self._plan_message()
        for index in range(6):
            messages.append(user_msg(f"m{index}", mid=f"u{index}"))
        history = ApprovalHistory(messages, trigger_id="u0")
        keys = [row.key for row in history.entries if row.kind == "用户"]
        self.assertEqual(len(keys), len(set(keys)))
        text = history.initial_text()
        self.assertIn("计划批准", text)
        self.assertIn("本次工作由第", text)

if __name__ == "__main__":
    unittest.main()
