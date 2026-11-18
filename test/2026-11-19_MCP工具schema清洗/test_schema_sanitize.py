"""MCP 工具 schema 清洗（2026-11-19）。

背景：gpt-6.2 后端的 schema 编译器拒绝 ``propertyNames``（``invalid_json_schema``），
而 Playwright MCP 的 ``browser_drop.data`` 正好带这个关键字；gpt-6.1 放行，所以
表现为「只有 6.2 报 tools[53].parameters」。修复即在 ``flatten_tools`` 前统一
剥除 OpenAI 不支持的关键字。

运行：``.venv/bin/python -B -m unittest discover -s "test/2026-11-19_MCP工具schema清洗" -v``
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.api_client.responses.translate import (  # noqa: E402
    flatten_tools,
    sanitize_json_schema,
)

# Playwright MCP @playwright/mcp 的 browser_drop 原始 inputSchema（实测抓取）
PLAYWRIGHT_BROWSER_DROP = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "target": {"type": "string"},
        "paths": {"type": "array", "items": {"type": "string"}},
        "data": {
            "type": "object",
            "propertyNames": {"type": "string"},
            "additionalProperties": {"type": "string"},
        },
    },
    "required": ["target"],
    "additionalProperties": False,
}


class SchemaSanitizeTest(unittest.TestCase):
    def test_playwright_browser_drop_is_accepted_after_sanitize(self):
        tools = [{"type": "function", "function": {"name": "browser_drop",
                                                   "parameters": PLAYWRIGHT_BROWSER_DROP}}]
        flat = flatten_tools(tools)
        self.assertEqual(len(flat), 1)
        params = flat[0]["parameters"]
        dumped = json.dumps(params)
        self.assertNotIn("propertyNames", dumped)
        self.assertNotIn("$schema", dumped)
        # 语义保留：data 仍是 MIME 映射，target 仍必填
        self.assertEqual(params["properties"]["data"]["additionalProperties"], {"type": "string"})
        self.assertIn("target", params["required"])
        self.assertIs(params["additionalProperties"], False)

    def test_properties_named_like_keywords_survive(self):
        """参数刚好叫 if / then / $schema 时不能被当成关键字删掉。"""
        schema = {
            "type": "object",
            "properties": {
                "if": {"type": "string"},
                "then": {"type": "string"},
                "patternProperties": {"type": "string"},
            },
            "required": ["if"],
        }
        cleaned = sanitize_json_schema(schema)
        self.assertEqual(
            sorted(cleaned["properties"]), ["if", "patternProperties", "then"]
        )

    def test_defs_and_ref_are_preserved(self):
        schema = {
            "$defs": {"Point": {"type": "object", "properties": {"x": {"type": "number"}}}},
            "type": "object",
            "properties": {"p": {"$ref": "#/$defs/Point"}},
        }
        cleaned = sanitize_json_schema(schema)
        self.assertIn("$defs", cleaned)
        self.assertEqual(cleaned["properties"]["p"]["$ref"], "#/$defs/Point")

    def test_nested_lists_and_anyof_are_walked(self):
        schema = {
            "type": "object",
            "properties": {
                "choice": {
                    "anyOf": [
                        {"type": "string", "patternProperties": {"a": {}}},
                        {"type": "integer"},
                    ]
                }
            },
        }
        dumped = json.dumps(sanitize_json_schema(schema))
        self.assertNotIn("patternProperties", dumped)
        self.assertIn("anyOf", dumped)

    def test_original_schema_is_not_mutated(self):
        schema = json.loads(json.dumps(PLAYWRIGHT_BROWSER_DROP))
        sanitize_json_schema(schema)
        self.assertIn("propertyNames", schema["properties"]["data"])

    def test_tool_without_parameters_gets_empty_object(self):
        flat = flatten_tools([{"type": "function", "function": {"name": "noop"}}])
        self.assertEqual(flat[0]["parameters"], {"type": "object", "properties": {}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
