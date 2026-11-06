"""自动审核上下文：压缩历史、只读补查、纯文本渲染与 deepcopy 隔离。

运行：
    /opt/homebrew/bin/python3.11 -B -m unittest discover \
        -s test/2026-11-06_自动审核上下文 -p 'test_*.py'
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from history_fixtures import (  # noqa: E402
    ROOT,
    ApprovalHistory,
    all_rendered,
    assistant_calls,
    attachment_text,
    bodies,
    parse_arguments,
    result_text,
    scalar_text,
    status_text,
    text_content,
    tool_payload,
    tool_result,
    user_msg,
    value_text,
)

# --------------------------------------------------------- deep_compacted

class CompactedHistoryTests(unittest.TestCase):
    def test_deep_compacted_original_text_is_still_available(self):
        messages = [
            user_msg(
                "压缩前原话：只允许改 static 目录 COMPACTED_MARK",
                mid="old1",
                deep_compacted=True,
                deep_compacted_round=2,
            )
        ]
        for index in range(7):
            messages.append(user_msg(f"压缩后要求 {index}", mid=f"n{index}"))
        history = ApprovalHistory(messages, trigger_id="n0")
        self.assertIn("COMPACTED_MARK", "".join(bodies(history, "用户")))
        found = history.read(offset=0, count=20, query="compacted_mark")
        self.assertIn("压缩前原话：只允许改 static 目录 COMPACTED_MARK", found)


# ------------------------------------------------ 只读补查 / 查询 / 分页

class ReadHistoryTests(unittest.TestCase):
    @staticmethod
    def _history(count=12):
        messages = [user_msg(f"证据 {index}", mid=f"u{index}") for index in range(1, count + 1)]
        return ApprovalHistory(messages, trigger_id="u1")

    def test_read_is_newest_first_and_pages_without_overlap(self):
        history = self._history(12)
        first = history.read(offset=0, count=5)
        second = history.read(offset=5, count=5)
        self.assertIn("共12项", first)
        self.assertIn("从1项起", first)
        self.assertIn("下一页offset=5", first)
        self.assertIn("从6项起", second)
        self.assertIn("证据 12", first)
        self.assertIn("证据 8", first)
        self.assertNotIn("证据 7", first)
        self.assertIn("证据 7", second)
        self.assertNotIn("下一页", history.read(offset=10, count=5))

    def test_query_is_casefolded_substring_filter(self):
        history = ApprovalHistory([
            user_msg("运行 BACKUP.sh 并保留日志", mid="u1"),
            user_msg("只改前端样式", mid="u2"),
        ], trigger_id="u2")
        found = history.read(query="backup.SH")
        self.assertIn("共1项", found)
        self.assertIn("BACKUP.sh", found)
        self.assertNotIn("只改前端样式", found)

    def test_invalid_paging_arguments_are_rejected(self):
        history = self._history(3)
        for kwargs in ({"offset": -1}, {"count": 0}, {"count": 21}):
            with self.subTest(kwargs=kwargs):
                self.assertIn("参数错误", history.read(**kwargs))

    def test_read_does_not_mutate_state(self):
        history = self._history(12)
        before_entries = [(row.order, row.kind, row.body, row.key) for row in history.entries]
        before_plans = [(row.order, row.kind, row.body, row.key) for row in history.plans]
        first = history.read(offset=0, count=20, query="证据")
        second = history.read(offset=0, count=20, query="证据")
        self.assertEqual(first, second)
        # 触发消息在 read 里也不能因追加而重复
        rendered = history.read(offset=0, count=20)
        self.assertEqual(len(re.findall(r"证据 1(?!\d)", rendered)), 1)
        self.assertEqual(before_entries, [(row.order, row.kind, row.body, row.key) for row in history.entries])
        self.assertEqual(before_plans, [(row.order, row.kind, row.body, row.key) for row in history.plans])

    def test_history_exposes_no_filesystem_identity(self):
        rendered = all_rendered(self._history(4))
        self.assertNotIn(str(ROOT), rendered)
        self.assertNotIn("/Users/", rendered)


# -------------------------------------------- 纯文本渲染（禁止结构 JSON）

class PlainTextRenderingTests(unittest.TestCase):
    def test_nested_arguments_render_without_json_structure(self):
        arguments = {
            "paths": ["a.txt", "b/c.py"],
            "options": {"dry_run": True, "limit": None, "ratio": 0.5},
            "timeout": 30,
        }
        text = value_text(arguments)
        self.assertIn("paths:", text)
        self.assertIn("a.txt", text)
        self.assertIn("dry_run: true", text)
        self.assertIn("limit: null", text)
        self.assertIn("ratio: 0.5", text)
        for token in ('{"', '"}', '": ', "[{", "}]"):
            self.assertNotIn(token, text)

    def test_raw_command_string_keeps_its_own_json_verbatim(self):
        command = "curl -s -d '{\"ok\": true, \"id\": 7}' http://127.0.0.1:1/x"
        text = value_text({"command": command})
        self.assertIn(command, text)
        remainder = text.replace(command, "")
        self.assertNotIn("{", remainder)
        self.assertNotIn("}", remainder)
        self.assertNotIn('": ', remainder)

    def test_tool_body_has_no_json_wrapper_but_keeps_command_json(self):
        command = "echo '{\"a\":1}'"
        history = ApprovalHistory([
            assistant_calls(("call_1", "run_command", {"command": command})),
            tool_result("call_1", "run_command",
                        {"success": True, "status": "completed", "output": "{\"a\":1}"}),
        ])
        body = [row.body for row in history.entries if row.kind == "工具"][0]
        self.assertIn(command, body)
        self.assertIn("状态：completed", body)
        self.assertIn("结果：", body)
        self.assertNotIn('{"success"', body)

    def test_value_text_scalars_and_empty_containers(self):
        self.assertEqual(scalar_text(True), "true")
        self.assertEqual(scalar_text(False), "false")
        self.assertEqual(scalar_text(None), "null")
        self.assertEqual(value_text({}), "（空）")
        self.assertEqual(value_text([]), "（空列表）")

    def test_parse_arguments_accepts_dict_and_json_string(self):
        self.assertEqual(parse_arguments({"a": 1}), {"a": 1})
        self.assertEqual(parse_arguments("{\"a\": 1}"), {"a": 1})
        self.assertEqual(parse_arguments("not json")["原始参数"], "not json")

    def test_plain_text_content_and_attachments(self):
        message = user_msg("看这两个文件", mid="u1", files=["a.png", "b.py"])
        self.assertEqual(text_content(message["content"]), "看这两个文件")
        self.assertEqual(attachment_text(message), "a.png\nb.py")
        self.assertEqual(text_content([{"type": "text", "text": "一段"}, {"type": "image", "url": "x"}]), "一段")
        self.assertEqual(text_content(None), "")

    def test_status_and_result_fallbacks_for_non_json_output(self):
        self.assertIsNone(tool_payload({"role": "tool", "content": "plain output"}))
        self.assertEqual(result_text({"role": "tool", "content": "plain output"}), "plain output")
        self.assertEqual(
            status_text({"role": "tool", "content": "plain output"}),
            "已有结果，执行状态见正文",
        )


# --------------------------------------------------------- deepcopy 隔离

class DeepCopyIsolationTests(unittest.TestCase):
    def test_mutating_input_history_does_not_change_built_entries(self):
        messages = [
            user_msg("原始要求 KEEP_MARK", mid="u1"),
            assistant_calls(("call_1", "run_command", {"command": "ls"})),
            tool_result("call_1", "run_command", {"success": True, "status": "completed", "output": "ok"}),
        ]
        history = ApprovalHistory(messages, trigger_id="u1")
        before = [(row.kind, row.body) for row in history.entries]

        messages[0]["content"] = "被外部改写"
        messages[0]["metadata"]["message_source"] = "notify"
        messages[1]["tool_calls"][0]["function"]["arguments"] = "{\"command\": \"rm -rf /\"}"
        messages.append(user_msg("外部追加", mid="u2"))

        self.assertEqual(before, [(row.kind, row.body) for row in history.entries])
        self.assertIn("KEEP_MARK", history.initial_text())
        self.assertNotIn("被外部改写", history.initial_text())
        self.assertNotIn("rm -rf /", all_rendered(history))

    def test_history_does_not_alias_input_objects(self):
        messages = [user_msg("原文", mid="u1", files=["a.txt"])]
        history = ApprovalHistory(messages)
        self.assertIsNot(history.messages[0], messages[0])
        self.assertIsNot(history.messages[0].get("metadata"), messages[0].get("metadata"))
        history.messages[0]["content"] = "内部改写"
        self.assertEqual(messages[0]["content"], "原文")


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()
