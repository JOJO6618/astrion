from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from utils.context_manager import ContextManager
from utils.tool_result_formatter import (
    extract_mcp_content_for_context,
    format_tool_result_for_context,
)


class MediaStoreAndMCPContentTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.project_dir = root / "project"
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_media_store_survives_source_file_deletion(self):
        image_path = self.project_dir / "demo.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-data")

        ctx = ContextManager(project_path=str(self.project_dir), data_dir=str(self.data_dir))
        conv_id = ctx.start_new_conversation(project_path=str(self.project_dir), thinking_mode=False)
        ctx.add_conversation("user", "看图", images=["demo.png"])
        message = ctx.conversation_history[-1]

        refs = message.get("media_refs") or []
        self.assertTrue(refs, message)
        self.assertEqual(refs[0].get("kind"), "image")

        image_path.unlink()
        payload = ctx._build_content_with_images(
            message.get("content") or "",
            message.get("images") or [],
            message.get("videos") or [],
            media_refs=refs,
        )

        self.assertIsInstance(payload, list, payload)
        image_parts = [item for item in payload if isinstance(item, dict) and item.get("type") == "image_url"]
        self.assertTrue(image_parts, payload)
        self.assertTrue(str(image_parts[0].get("image_url", {}).get("url", "")).startswith("data:image/"))

        index_file = self.data_dir / "conversations" / "media_store" / "index.json"
        self.assertTrue(index_file.exists())
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
        self.assertIn(conv_id, index_data.get("message_map", {}))
        self.assertIn(message.get("message_id"), (index_data.get("message_map", {}).get(conv_id) or {}))

    def test_add_tool_message_with_inline_mcp_media_refs(self):
        ctx = ContextManager(project_path=str(self.project_dir), data_dir=str(self.data_dir))
        ctx.start_new_conversation(project_path=str(self.project_dir), thinking_mode=False)

        saved = ctx.add_conversation(
            "tool",
            "截图已记录",
            tool_call_id="call_1",
            name="mcp__browsermcp__browser_screenshot",
            media_refs=[
                {
                    "kind": "image",
                    "mime_type": "image/png",
                    "data_base64": "aGVsbG8=",
                    "source": "mcp_content",
                }
            ],
        )

        refs = (saved or {}).get("media_refs") or []
        self.assertTrue(refs, saved)
        self.assertTrue(str(refs[0].get("media_id") or "").startswith("sha256:"))

        payload = ctx._build_content_with_images(
            saved.get("content") or "",
            [],
            [],
            media_refs=refs,
        )
        self.assertIsInstance(payload, list, payload)
        image_parts = [item for item in payload if isinstance(item, dict) and item.get("type") == "image_url"]
        self.assertTrue(image_parts, payload)
        self.assertTrue(str(image_parts[0].get("image_url", {}).get("url", "")).startswith("data:image/png;base64,"))

    def test_mcp_formatter_keeps_non_text_information(self):
        result_data = {
            "success": True,
            "server_id": "browsermcp",
            "tool_alias": "mcp__browsermcp__browser_screenshot",
            "tool_name": "browser_screenshot",
            "message": "MCP 工具调用完成",
            "content": [
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": "aGVsbG8=",
                }
            ],
            "raw_result": {
                "content": [
                    {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": "aGVsbG8=",
                    }
                ]
            },
        }

        text = format_tool_result_for_context("mcp__browsermcp__browser_screenshot", result_data, "")
        self.assertIn("MCP image #1", text)
        parsed = extract_mcp_content_for_context(result_data)
        self.assertTrue(parsed.get("media_items"))
        sanitized = parsed.get("sanitized_payload") or {}
        content = (sanitized.get("content") or [{}])[0]
        self.assertNotIn("data", content)
        self.assertTrue(content.get("data_omitted"))

    def test_mcp_formatter_returns_empty_when_content_is_empty(self):
        result_data = {
            "success": True,
            "server_id": "browsermcp",
            "tool_alias": "mcp__browsermcp__browser_get_console_logs",
            "tool_name": "browser_get_console_logs",
            "message": "MCP 工具调用完成",
            "content": [
                {
                    "type": "text",
                    "text": "",
                }
            ],
            "raw_result": {
                "content": [
                    {
                        "type": "text",
                        "text": "",
                    }
                ]
            },
        }

        parsed = extract_mcp_content_for_context(result_data)
        self.assertEqual(parsed.get("text"), "")

        text = format_tool_result_for_context(
            "mcp__browsermcp__browser_get_console_logs",
            result_data,
            raw_text='{"success": true}',
        )
        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()
