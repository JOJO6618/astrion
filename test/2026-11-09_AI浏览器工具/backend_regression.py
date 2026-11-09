"""后端回归：分类、站点授权、权限评估、审批记住站点、格式化。不依赖 Electron。
运行：python3 -B test/2026-11-09_AI浏览器工具/backend_regression.py
"""
import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.native_browser import executor, site_permissions as sp
from modules.native_browser.formatter import format_for_model
from modules.native_browser.tools import is_action, BROWSER_TOOL_NAMES
from modules.tool_approval_manager import ToolApprovalManager


class FakeTerminal:
    def __init__(self, data_dir, enabled=True):
        self.data_dir = data_dir
        self.username = "host"
        self.workspace_id = "ws1"
        self.context_manager = mock.Mock(current_conversation_id="conv_1", conversation_metadata={"browser_tools_enabled": enabled}, project_path=data_dir)
        self.model_key = "x"
    def get_permission_mode(self):
        return "approval"


class Regression(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.supported = mock.patch.object(executor, "native_browser_supported", return_value=True)
        self.supported.start()
        self.addCleanup(self.supported.stop)

    def evaluate(self, tool, args, site="example.com", mode="approval", enabled=True):
        t = FakeTerminal(self.dir, enabled)
        with mock.patch.object(executor, "preflight", return_value={"success": True, "site": site, "url": f"https://{site}/"}):
            return executor.evaluate_permission(t, tool, args, mode)

    def test_read_ops_never_need_approval(self):
        for tool in ("browser_navigate", "browser_snapshot", "browser_hover", "browser_scroll", "browser_take_screenshot"):
            self.assertFalse(self.evaluate(tool, {}).get("requires_approval"), tool)

    def test_action_needs_site_approval_once(self):
        r = self.evaluate("browser_click", {"target": "e1"})
        self.assertTrue(r["requires_approval"]); self.assertEqual(r["approval_type"], "browser_action"); self.assertEqual(r["browser_site"], "example.com")
        sp.grant_site(self.dir, "example.com")
        self.assertFalse(self.evaluate("browser_click", {"target": "e1"}).get("requires_approval"))
        self.assertTrue(self.evaluate("browser_click", {"target": "e1"}, site="other.com")["requires_approval"])

    def test_evaluate_always_needs_approval(self):
        sp.grant_site(self.dir, "example.com")
        self.assertTrue(self.evaluate("browser_evaluate", {"function": "()=>1"})["requires_approval"])
        self.assertTrue(self.evaluate("browser_fill_saved_login", {"password_target": "e1"})["requires_approval"])

    def test_modes(self):
        self.assertFalse(self.evaluate("browser_click", {}, mode="unrestricted").get("requires_approval"))
        r = self.evaluate("browser_click", {}, mode="readonly")
        self.assertFalse(r["allowed"])
        self.assertTrue(self.evaluate("browser_navigate", {}, mode="readonly")["allowed"])

    def test_disabled_not_approved(self):
        self.assertFalse(self.evaluate("browser_click", {}, enabled=False).get("requires_approval"))

    def test_dialog_split(self):
        self.assertFalse(is_action("browser_handle_dialog", {"accept": True}, "alert"))
        self.assertFalse(is_action("browser_handle_dialog", {"accept": False}, "confirm"))
        self.assertTrue(is_action("browser_handle_dialog", {"accept": True}, "confirm"))

    def test_execute_disabled_returns_error(self):
        r = __import__("asyncio").run(executor.execute(FakeTerminal(self.dir, False), "browser_click", {"target": "e1"}))
        self.assertEqual(r["code"], "browser_disabled")

    def test_remember_site_only_for_human_browser_approvals(self):
        m = ToolApprovalManager()
        item = m.create_request(username="u", conversation_id="c", task_id="t", tool_call_id="1", tool_name="browser_click", arguments={}, preview={}, approval_type="browser_action", browser_site="example.com")
        done = m.decide(item["approval_id"], "u", "approved", remember_site=True)
        self.assertTrue(done["remember_site"])
        other = m.create_request(username="u", conversation_id="c", task_id="t", tool_call_id="2", tool_name="write_file", arguments={}, preview={})
        with self.assertRaises(ValueError):
            m.decide(other["approval_id"], "u", "approved", remember_site=True)
        auto = m.create_request(username="u", conversation_id="c", task_id="t", tool_call_id="3", tool_name="browser_click", arguments={}, preview={}, approval_type="browser_action", browser_site="a.com", auto_review_required=True, human_approval_required=False)
        with self.assertRaises(ValueError):
            m.decide(auto["approval_id"], "u", "approved", remember_site=True)

    def test_site_file_validation(self):
        self.assertFalse(sp.grant_site(self.dir, "bad host"))
        self.assertTrue(sp.grant_site(self.dir, "localhost:3000"))
        self.assertEqual([s["site"] for s in sp.list_sites(self.dir)], ["localhost:3000"])

    def test_formatter(self):
        text = format_for_model({"success": True, "op": "click", "tab": {"id": "t1", "title": "T", "url": "https://a"}, "snapshot": {"kind": "none"}})
        self.assertIn("页面无变化", text)
        self.assertEqual(len(BROWSER_TOOL_NAMES), 23)


if __name__ == "__main__":
    unittest.main()
