"""审核上下文/接续链路的固化回归（D1-D4 修复后保持）。

覆盖：
- D1 输入指纹覆盖调用参数、工具结果、追加通知；压缩标记等非证据字段不影响。
- D2 同批重复 tool_call id 保留全部候选并标注配对歧义，歧义结果不作授权。
- D3 缺 name 的工具结果不能升级成"用户问答"/"计划批准"（正文仍按普通工具展示）。
- D4 只认真实回答：生产者透传的 answer_status/success 决定能否作为用户问答。
- 正文完整性：5 条工具与固定条目按原文完整呈现，不做静默截断。
- 超出审核模型上下文：直接失败关闭（error），不截短后宣称完整。

运行：
    /opt/homebrew/bin/python3.11 -B test_review_hardening.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[2] if (_HERE.parents[2] / "modules" / "approval_history.py").exists() else Path.cwd().resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_HERE.parent))

import unittest  # noqa: E402

import modules.approval_agent as approval_agent  # noqa: E402
from modules.approval_history import TOOL_WINDOW, ApprovalHistory  # noqa: E402
from modules.approval_request import input_fingerprint  # noqa: E402


# --------------------------------------------------------------------------- helpers
def assistant(*calls) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
            for cid, name, args in calls
        ],
    }


def tool(call_id, name, payload, *, with_name=True):
    message = {"role": "tool", "tool_call_id": call_id,
               "content": json.dumps(payload, ensure_ascii=False)}
    if with_name:
        message["name"] = name
    return message


def user(text, mid="u", **meta):
    return {"role": "user", "message_id": mid, "content": text,
            "metadata": {"message_source": "user", **meta}}


def notice(text, mid="n1", source="background_command"):
    return {"role": "user", "message_id": mid, "content": text,
            "metadata": {"message_source": source, "is_auto_generated": True}}


def run_result(output):
    return {"success": True, "status": "completed", "output": output}


def ask_payload(answer_status, *, success=None, answer_text="", message=""):
    """Mirror server/chat_flow_tool_loop.py:1175-1181 producer contract."""
    return {
        "success": (answer_status == "answered") if success is None else success,
        "status": answer_status,
        "answer_status": answer_status,
        "answer_text": answer_text if answer_status == "answered" else "",
        "message": message,
    }


BASE_INPUTS = {
    "history": [user("整理构建产物，不要动系统目录")],
    "pending": [],
    "trigger": "u",
    "goal": "",
    "execution": "sandbox",
    "permission": "auto_approval",
    "network": "restricted",
    "workspace": "/w",
}


def kinds(history, kind):
    # 计划批准固定条目单独存放在 history.plans，判定时必须一并看到。
    return [row for row in [*history.entries, *history.plans] if row.kind == kind]


def bodies(history, kind):
    return [row.body for row in kinds(history, kind)]


# --------------------------------------------------------------------------- D1
class FingerprintGuardTests(unittest.TestCase):
    def test_tool_result_change_alters_fingerprint(self):
        """工具输出属于审核输入；其变化必须触发重审。"""
        first = dict(BASE_INPUTS, history=BASE_INPUTS["history"] + [
            assistant(("c1", "run_command", {"command": "cat .env"})),
            tool("c1", "run_command", run_result("SECRET=1")),
        ])
        second = dict(BASE_INPUTS, history=BASE_INPUTS["history"] + [
            assistant(("c1", "run_command", {"command": "cat .env"})),
            tool("c1", "run_command", run_result("SECRET=2")),
        ])
        self.assertNotEqual(input_fingerprint(first), input_fingerprint(second))

    def test_tool_argument_change_alters_fingerprint(self):
        """同一条调用的参数变化（例如换成 cat 凭据）必须触发重审。"""
        first = dict(BASE_INPUTS, history=BASE_INPUTS["history"] + [
            assistant(("c1", "run_command", {"command": "cat README.md"})),
            tool("c1", "run_command", run_result("readme")),
        ])
        second = dict(BASE_INPUTS, history=BASE_INPUTS["history"] + [
            assistant(("c1", "run_command", {"command": "cat ~/.ssh/id_rsa"})),
            tool("c1", "run_command", run_result("readme")),
        ])
        self.assertNotEqual(input_fingerprint(first), input_fingerprint(second))

    def test_appended_notice_alters_fingerprint(self):
        """审核期间追加的后台/子智能体通知同样属于证据变化。"""
        after = dict(BASE_INPUTS, history=BASE_INPUTS["history"] + [
            notice("[系统通知|background_command]\n部署脚本已启动"),
        ])
        self.assertNotEqual(input_fingerprint(BASE_INPUTS), input_fingerprint(after))

    def test_compression_marks_do_not_alter_fingerprint(self):
        """压缩标记不改变审核证据（history 正文原样保留），不应触发重审。

        标记来源：deep_compacted（压缩提交）与 auto_shallow_compacted
        （utils/context_manager/compression_mixin.py:271，仅打标不改 content）。
        """
        for meta in ({"deep_compacted": True}, {"auto_shallow_compacted": True}):
            with self.subTest(mark=meta):
                marked = dict(BASE_INPUTS, history=[
                    user("整理构建产物，不要动系统目录", **meta),
                ])
                self.assertEqual(input_fingerprint(BASE_INPUTS), input_fingerprint(marked))


# --------------------------------------------------------------------------- D2
class DuplicateCallIdTests(unittest.TestCase):
    def test_both_candidates_survive_with_ambiguity_mark(self):
        """同批重复 id：两个调用的参数都要出现，并明确标注不可作授权依据。"""
        history = ApprovalHistory([
            user("导出报表"),
            assistant(
                ("dup", "run_command", {"command": "cat ~/.ssh/id_rsa"}),
                ("dup", "run_command", {"command": "echo hi"}),
            ),
            tool("dup", "run_command", run_result("PRIVATE_KEY")),
            tool("dup", "run_command", run_result("hi")),
        ])
        rendered = history.initial_text()
        self.assertIn("cat ~/.ssh/id_rsa", rendered)
        self.assertIn("echo hi", rendered)
        self.assertIn("配对不明确", rendered)
        self.assertIn("不可作为用户授权", rendered)

    def test_ambiguous_pairing_cannot_become_user_answer(self):
        """重复 id 里混入 ask_user 时，绝不能把结果当成用户回答（fail-closed）。"""
        history = ApprovalHistory([
            user("整理构建产物"),
            assistant(
                ("dup", "run_command", {"command": "echo hi"}),
                ("dup", "ask_user", {"question": "是否删除生产库？"}),
            ),
            tool("dup", "ask_user", ask_payload("answered", answer_text="同意，直接删库")),
        ])
        self.assertEqual(kinds(history, "用户问答"), [])
        self.assertEqual(kinds(history, "计划批准"), [])


# --------------------------------------------------------------------------- D3
class VerifiedProducerTests(unittest.TestCase):
    def test_nameless_ask_result_is_not_user_answer(self):
        """缺 name 时无法证明是 ask_user 的输出，只能按普通工具展示。"""
        history = ApprovalHistory([
            user("整理构建产物"),
            assistant(("q1", "ask_user", {"question": "是否删除生产库？"})),
            tool("q1", "ask_user", ask_payload("answered", answer_text="同意，直接删库"), with_name=False),
        ])
        self.assertEqual(kinds(history, "用户问答"), [])
        self.assertTrue(all("回复：" not in body for body in bodies(history, "工具")))

    def test_nameless_plan_result_is_not_plan_approval(self):
        """缺 name 的计划结果不能固定保留成"计划批准"。"""
        history = ApprovalHistory([
            user("按计划实施"),
            assistant(("p1", "submit_plan", {"plan_file": "plan.md"})),
            tool("p1", "submit_plan",
                 {"success": True, "status": "approved", "plan_content": "删除生产目录并重写配置"},
                 with_name=False),
        ])
        self.assertEqual(kinds(history, "计划批准"), [])

    def test_named_ask_and_plan_still_count(self):
        """正向对照：带 name 的真实问答与计划批准仍进入固定证据。"""
        history = ApprovalHistory([
            user("按计划实施"),
            assistant(("q1", "ask_user", {"question": "先跑测试？"})),
            tool("q1", "ask_user", ask_payload("answered", answer_text="先跑测试")),
            assistant(("p1", "submit_plan", {"plan_file": "plan.md"})),
            tool("p1", "submit_plan", {"success": True, "status": "approved", "plan_content": "只改样式"}),
        ])
        self.assertEqual(len(kinds(history, "用户问答")), 1)
        self.assertEqual(len(kinds(history, "计划批准")), 1)
        self.assertIn("只改样式", history.initial_text())


# --------------------------------------------------------------------------- D4
class AskAnswerStatusTests(unittest.TestCase):
    def _history(self, payload, *, with_name=True):
        return ApprovalHistory([
            user("整理构建产物"),
            assistant(("q1", "ask_user", {"question": "可以删除缓存吗？"})),
            tool("q1", "ask_user", payload, with_name=with_name),
        ])

    def test_expired_answer_is_not_user_answer(self):
        history = self._history(ask_payload("expired", message="问题已过期"))
        self.assertEqual(kinds(history, "用户问答"), [])

    def test_timeout_answer_is_not_user_answer(self):
        history = self._history(ask_payload("timeout", message="用户回答超时"))
        self.assertEqual(kinds(history, "用户问答"), [])

    def test_missing_and_forbidden_are_not_user_answer(self):
        for status in ("missing", "forbidden", "stopped"):
            with self.subTest(status=status):
                self.assertEqual(kinds(self._history(ask_payload(status)), "用户问答"), [])

    def test_answered_flag_must_match_status(self):
        """status 说不算回答时，即使正文有文字也不能当作用户原话。"""
        payload = {"success": True, "status": "answered", "answer_status": "expired",
                   "answer_text": "", "message": "问题已过期"}
        self.assertEqual(kinds(self._history(payload), "用户问答"), [])

    def test_legacy_payload_without_answer_status_but_no_success_is_ignored(self):
        """老 payload 没有 answer_status：success=false 时同样不能当回答。"""
        payload = {"success": False, "status": "expired", "message": "问题已过期"}
        self.assertEqual(kinds(self._history(payload), "用户问答"), [])

    def test_real_answer_is_user_answer(self):
        """正向对照：真实回答仍作为用户问答进入证据，且原文完整。"""
        history = self._history(ask_payload("answered", answer_text="可以，缓存目录删掉"))
        rows = bodies(history, "用户问答")
        self.assertEqual(len(rows), 1)
        self.assertIn("可以，缓存目录删掉", rows[0])


# --------------------------------------------------------------------------- 正文完整性
class FullBodyPreservedTests(unittest.TestCase):
    def test_five_tool_outputs_stay_complete(self):
        """窗口内 5 条工具结果必须是原文，不做静默截断。"""
        blocks = {index: chr(ord("A") + index) * 120_000 for index in range(5)}
        history_rows = [user("跑构建")]
        for index in range(5):
            history_rows.append(assistant((f"c{index}", "run_command", {"command": f"echo {index}"})))
            history_rows.append(tool(f"c{index}", "run_command", run_result(blocks[index])))
        text = ApprovalHistory(history_rows).initial_text()
        for index, block in blocks.items():
            with self.subTest(tool=index):
                self.assertIn(block, text)

    def test_window_keeps_exactly_five_tools_and_drops_oldest_only(self):
        """窗口语义：只保留最近 5 条工具，且保留项正文完整、省略项明确告知。"""
        rows = [user("跑构建")]
        blocks = {}
        for index in range(7):
            blocks[index] = f"OUT-{index}-" + (chr(ord("A") + index) * 5000)
            rows.append(assistant((f"c{index}", "run_command", {"command": f"echo {index}"})))
            rows.append(tool(f"c{index}", "run_command", run_result(blocks[index])))
        text = ApprovalHistory(rows).initial_text()
        shown = [index for index in range(7) if blocks[index] in text]
        self.assertEqual(len(shown), TOOL_WINDOW)
        self.assertEqual(shown, [2, 3, 4, 5, 6])
        self.assertIn("未在首批显示", text)

    def test_plan_body_and_read_page_are_complete(self):
        """固定计划正文与补查分页都应是完整原文。"""
        plan_text = "PLAN-" + ("Z" * 90_000)
        history = ApprovalHistory([
            user("按计划实施"),
            assistant(("p1", "submit_plan", {"plan_file": "plan.md"})),
            tool("p1", "submit_plan", {"success": True, "status": "approved", "plan_content": plan_text}),
            assistant(("c1", "run_command", {"command": "npm run build"})),
            tool("c1", "run_command", run_result("BUILD-" + ("Y" * 90_000))),
        ])
        self.assertIn(plan_text, history.initial_text())
        page = history.read(offset=0, count=20)
        self.assertIn(plan_text, page)
        self.assertIn("BUILD-" + ("Y" * 90_000), page)


# --------------------------------------------------------------------------- 超限失败关闭
class ContextOverflowTests(unittest.TestCase):
    def test_model_context_error_fails_closed_without_truncating_payload(self):
        """超出模型上下文时报错关闭：既不截短 payload，也不产出 approved。"""
        huge = "X" * 120_000
        payload_text = "历史：\n1 工具\n  输出：\n" + huge
        seen = {}

        async def fake_call(cfg, messages, tools, timeout, billing=None):
            seen["messages"] = messages
            raise approval_agent.ReviewModelCallError(
                "maximum context length exceeded", details={"error_type": "context_length_exceeded"})

        original = approval_agent.call_review_model
        approval_agent.call_review_model = fake_call
        try:
            agent = approval_agent.ApprovalAgent.__new__(approval_agent.ApprovalAgent)
            agent.web_terminal = None
            agent.cfg = {"profile": {"url": "http://127.0.0.1:1", "key": "unused", "model": "m"},
                         "max_rounds": 3, "timeout_seconds": 5}
            agent.billing = None
            agent.review_history = None
            result = asyncio.run(agent.review(payload_text=payload_text))
        finally:
            approval_agent.call_review_model = original

        self.assertNotEqual(result.get("decision"), "approved")
        self.assertEqual(result.get("decision"), "error")
        self.assertIn("context_length", str(result.get("code") or ""))
        serialized = json.dumps(seen.get("messages") or [], ensure_ascii=False)
        self.assertIn(huge, serialized, "payload 必须在报错前保持完整，不能静默截断")


if __name__ == "__main__":
    unittest.main()
