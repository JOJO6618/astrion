"""技能改为「只注入名称 + search_skills 检索」的回归（2026-11-19）。

覆盖：
1. build_skills_list 只输出按目录分组的名称，不含 description；同名冲突单独一行；
2. search_skills 检索 SKILL.md 名称/描述/正文，排序、描述截断、片段行号、冲突提示；
3. 检索范围与 read_skill 一致（启用过滤、目录读取失败报错不回退）；
4. 工具注册：主工具类别 / 只读放行 / 保留名 / 子智能体工具集 / formatter；
5. 提示词：主静态规则、动态模板与子智能体段落都引导 search_skills。

内存替身复用 ``test/2026-11-07_公共执行层/`` 的 read_routing_env / read_routing_fakes
（不触碰本机磁盘、不读真实配置）；子智能体段落使用临时目录。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_SHARED = _REPO_ROOT / "test" / "2026-11-07_公共执行层"
for _p in (str(_REPO_ROOT), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from read_routing_env import prepare_isolated_runtime  # noqa: E402

_TEST_HOME = prepare_isolated_runtime("astrion_search_skills_")

from read_routing_fakes import FakeFiles, ReadToolStub  # noqa: E402
from modules.skills_manager import build_skills_list  # noqa: E402

_LONG_DESC = "Spreadsheet toolkit " + "x" * 600


def _skills_fake(*, with_conflict=True):
    files = {
        ".astrion/skills/xlsx/SKILL.md": (
            f"---\nname: xlsx\ndescription: {_LONG_DESC}\n---\n\n"
            "# XLSX\nUse openpyxl for formulas.\nPivot tables are supported.\n"
        ),
        ".astrion/skills/pptx/SKILL.md": (
            "---\nname: pptx\ndescription: Slide decks\n---\n\n"
            "# PPTX\nEmbed a chart from a spreadsheet when needed.\n"
        ),
        ".astrion/skills/remotion/SKILL.md": (
            "---\nname: remotion\ndescription: Video in React\n---\n\n"
            "正文里提到 openpyxl 一次\n"
        ),
        ".agents/skills/emails/SKILL.md": (
            "---\nname: emails\ndescription: Email drafting\n---\n\nWrite newsletters.\n"
        ),
    }
    astrion_folders = [{"name": "xlsx"}, {"name": "pptx"}, {"name": "remotion"}]
    agents_folders = [{"name": "emails"}]
    if with_conflict:
        files[".agents/skills/pptx/SKILL.md"] = "---\nname: pptx dup\n---\n内容"
        agents_folders.append({"name": "pptx"})
    return FakeFiles(
        files=files,
        listings={
            ".astrion/skills": {"folders": astrion_folders, "files": []},
            ".agents/skills": {"folders": agents_folders, "files": []},
        },
        metas={".astrion/skills": {"is_dir": True}, ".agents/skills": {"is_dir": True}},
    )


class BuildSkillsListTest(unittest.TestCase):
    def test_names_only_grouped_by_directory(self):
        catalog = [
            {"id": "xlsx", "description": _LONG_DESC, "display_dir": ".astrion/skills"},
            {"id": "pptx", "description": "Slides", "display_dir": ".astrion/skills",
             "conflict_dir": ".agents/skills"},
            {"id": "emails", "description": "Email", "display_dir": ".agents/skills", "source": "agents"},
        ]
        lines = build_skills_list(catalog, ["xlsx", "pptx", "emails", "missing"])
        self.assertEqual(lines[0], ".astrion/skills/：xlsx, pptx")
        self.assertEqual(lines[1], ".agents/skills/：emails")
        self.assertIn("同名重复", lines[2])
        self.assertIn("pptx（.astrion/skills/ 与 .agents/skills/ 各一份）", lines[2])
        joined = "\n".join(lines)
        self.assertNotIn("Spreadsheet", joined)
        self.assertNotIn("Slides", joined)

    def test_empty(self):
        self.assertEqual(build_skills_list([], []), [])
        self.assertEqual(build_skills_list([{"id": "a"}], []), [])


class SearchSkillsTest(unittest.TestCase):
    def setUp(self):
        self.stub = ReadToolStub(_skills_fake())

    def test_name_hit_ranks_first_and_body_only_hit_is_found(self):
        result = self.stub._handle_search_skills(["xlsx", "openpyxl"])
        self.assertTrue(result["success"], result)
        ids = [item["skill_id"] for item in result["results"]]
        self.assertEqual(ids[0], "xlsx")
        # remotion 只在正文出现 openpyxl，也应命中
        self.assertIn("remotion", ids)
        self.assertNotIn("emails", ids)
        top = result["results"][0]
        self.assertEqual(top["path"], ".astrion/skills/xlsx/SKILL.md")
        self.assertEqual(top["matched_keywords"], ["xlsx", "openpyxl"])

    def test_description_truncated_and_snippet_lines_are_file_based(self):
        result = self.stub._handle_search_skills(["openpyxl"])
        top = next(item for item in result["results"] if item["skill_id"] == "xlsx")
        self.assertEqual(len(top["description"]), 300)
        self.assertTrue(top["description"].endswith("..."))
        # 文件第 7 行是 "Use openpyxl for formulas."（frontmatter 4 行 + 空行 + 标题）
        self.assertEqual(top["snippets"][0], {"line": 7, "text": "Use openpyxl for formulas."})
        self.assertIn("read_skill", result["content"])
        self.assertIn("L7: Use openpyxl for formulas.", result["content"])

    def test_conflict_is_reported_in_results(self):
        result = self.stub._handle_search_skills(["pptx"])
        top = result["results"][0]
        self.assertEqual(top["skill_id"], "pptx")
        self.assertEqual(top["conflict_path"], ".agents/skills/pptx/SKILL.md")
        self.assertIn("同名重复", result["content"])

    def test_case_insensitive_and_max_results_clamped(self):
        result = self.stub._handle_search_skills(["SPREADSHEET"], max_results=1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["skill_id"], "xlsx")

    def test_no_match_and_empty_keywords(self):
        result = self.stub._handle_search_skills(["kubernetes"])
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)
        self.assertIn("不要更换同义词", result["content"])

        empty = self.stub._handle_search_skills(["  ", ""])
        self.assertFalse(empty["success"])

    def test_disabled_skills_are_not_searchable_like_read_skill(self):
        prefs = {"enabled_skills": ["pptx", "emails"], "skills_catalog_snapshot": ["xlsx", "pptx", "remotion", "emails"]}
        with patch("core.main_terminal_parts.tools_read.load_personalization_config", return_value=prefs):
            result = self.stub._handle_search_skills(["xlsx", "openpyxl"])
            resolved = self.stub._resolve_skill_id("xlsx")
        ids = [item["skill_id"] for item in result["results"]]
        self.assertNotIn("xlsx", ids)
        self.assertNotIn("remotion", ids)
        self.assertFalse(resolved["success"])

    def test_catalog_failure_reports_error(self):
        fake = FakeFiles(metadata_failure=True)
        result = ReadToolStub(fake)._handle_search_skills(["xlsx"])
        self.assertFalse(result["success"])
        self.assertIn("read_file", result["error"])


class RegistrationTest(unittest.TestCase):
    def test_tool_is_registered_everywhere(self):
        from core.tool_config import TOOL_CATEGORIES
        from core.main_terminal_parts.tools_execution import MainTerminalToolsExecutionMixin
        from modules.custom_tool_registry import _RESERVED_TOOLS
        from modules.sub_agent.toolkit import SUB_AGENT_TOOLS, _format_tool_result
        from utils.tool_result_formatter import format_tool_result_for_context

        self.assertIn("search_skills", TOOL_CATEGORIES["skills"].tools)
        self.assertIn("search_skills", MainTerminalToolsExecutionMixin._READONLY_ALLOWED_TOOLS)
        self.assertIn("search_skills", _RESERVED_TOOLS)
        names = [tool["function"]["name"] for tool in SUB_AGENT_TOOLS]
        self.assertIn("search_skills", names)
        payload = {"success": True, "content": "技能检索结果", "results": []}
        self.assertEqual(_format_tool_result("search_skills", payload), "技能检索结果")
        self.assertEqual(format_tool_result_for_context("search_skills", payload, ""), "技能检索结果")


class PromptTextTest(unittest.TestCase):
    def test_main_prompts_point_to_search(self):
        static = (_REPO_ROOT / "prompts/main_agent/static.txt").read_text(encoding="utf-8")
        section = static.split("## Skills 使用规则", 1)[1].split("\n## ", 1)[0]
        self.assertIn("search_skills", section)
        self.assertIn("英文", section)
        dynamic = (_REPO_ROOT / "prompts/main_agent/dynamic/skills.txt").read_text(encoding="utf-8")
        self.assertIn("{skills_list}", dynamic)
        self.assertIn("仅列出可用技能名称", dynamic)

    def test_sub_agent_section_lists_names_only(self):
        from modules.multi_agent.sub_agent_context import _build_skills_section

        with tempfile.TemporaryDirectory(dir=_TEST_HOME) as workspace:
            section = _build_skills_section(workspace)
        self.assertIn("search_skills", section)
        self.assertIn("仅列出可用技能名称", section)
        self.assertIn(".astrion/skills/：", section)
        # 仓库自带技能的长描述不应再出现
        self.assertNotIn("openpyxl", section)


if __name__ == "__main__":
    unittest.main()
