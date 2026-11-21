import asyncio
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.native_browser import executor
from modules.native_browser.formatter import format_for_model

ToolsDefinitionBrowserToolsMixin = runpy.run_path(
    str(ROOT / 'core/main_terminal_parts/tools_definition/browser_tools.py')
)['ToolsDefinitionBrowserToolsMixin']


class DownloadToolsTest(unittest.TestCase):
    def test_schema_and_readonly_execution_without_page(self):
        terminal = Mock()
        terminal.get_permission_mode.return_value = "readonly"
        mixin = ToolsDefinitionBrowserToolsMixin()
        mixin._inject_intent = lambda props: props
        identity = {"username": "user", "workspace_id": "ws", "conversation_id": "conv"}
        with (
            patch("modules.native_browser.availability.native_browser_supported", return_value=True),
            patch.object(executor, "native_browser_supported", return_value=True),
            patch.object(executor, "browser_tools_enabled", return_value=True),
            patch.object(executor, "_identity", return_value=identity),
            patch.object(executor, "_prefs", return_value={}),
            patch.object(executor, "_download_dir") as download_dir,
            patch.object(executor, "snapshot_epoch", return_value=0),
            patch.object(executor, "preflight") as preflight,
            patch.object(executor.client, "call", return_value={"success": True, "downloads": []}) as call,
        ):
            schema = next(tool["function"]["parameters"] for tool in mixin._build_browser_tools()
                          if tool["function"]["name"] == "browser_downloads")
            self.assertEqual(set(schema["properties"]), {"download_id"})
            self.assertEqual(schema["required"], [])
            permission = executor.evaluate_permission(terminal, "browser_downloads", {}, "readonly")
            self.assertTrue(permission["allowed"])
            result = asyncio.run(executor.execute(terminal, "browser_downloads", {"download_id": "fixture"}))
            self.assertTrue(result["success"])
            self.assertEqual(call.call_args.args[0]["args"], {"download_id": "fixture"})
            self.assertEqual(call.call_args.args[0]["identity"], identity)
            download_dir.assert_not_called()
            preflight.assert_not_called()

    def test_formatter_reports_progress_and_actual_completion(self):
        item = {"id": "fixture", "name": "report.zip", "url": "https://fixture.test/report.zip",
                "path": "/fixture/report.zip", "state": "progressing", "received": 123,
                "total": 1000, "bytesPerSecond": 32, "estimatedCompletionAt": 1795212345678}
        result = {"success": True, "op": "downloads", "downloads": [item]}
        text = format_for_model(result)
        for field in ("report.zip", "download_id: fixture", "https://fixture.test/report.zip", "/fixture/report.zip",
                      "下载中", "123 B / 1000 B", "32 B/s", "预计完成时间"):
            self.assertIn(field, text)
        item.update(state="completed", received=1000, bytesPerSecond=None,
                    estimatedCompletionAt=None, completedAt=1795212345678)
        self.assertIn("已完成", format_for_model(result))
        self.assertNotIn("预计完成时间", format_for_model(result))
        self.assertIn("没有模型发起的下载记录", format_for_model({**result, "downloads": []}))


if __name__ == "__main__":
    unittest.main()
