import unittest

from utils.tool_result_formatter import format_tool_result_for_context


class ToolResultFormatterTest(unittest.TestCase):
    def test_read_skill_formats_full_read_content_like_read_file(self):
        result = {
            "success": True,
            "type": "read",
            "path": ".astrion/skills/frontend-design/SKILL.md",
            "max_chars": 50000,
            "truncated": False,
            "content": "# Skill\nFull content",
            "line_start": 1,
            "line_end": 2,
            "total_lines": 2,
            "message": "已读取 .astrion/skills/frontend-design/SKILL.md 的内容（行 1~2）",
        }

        formatted = format_tool_result_for_context("read_skill", result)

        self.assertIn("读取 .astrion/skills/frontend-design/SKILL.md 行 1~2", formatted)
        self.assertIn("```\n# Skill\nFull content\n```", formatted)
        self.assertNotEqual(result["message"], formatted)


if __name__ == "__main__":
    unittest.main()
