"""桌面导航 v3 后端回归：不启动应用，只用临时文件与合成 lookup。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.desktop_navigation import DesktopNavigation, NavigationConflict


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tabs.json"
        self.manager = DesktopNavigation(str(self.path))
        self.missing = set()

    def lookup(self, workspace, conv):
        if workspace == "unavailable":
            raise PermissionError("Unavailable workspace")
        return {"workspace_id": workspace or "ws1", "label": workspace or "ws1",
                "exists": (workspace, conv) not in self.missing}

    def command(self, action, payload=None, command_id=None):
        return self.manager.command("user", command_id or uuid4().hex, action,
                                    payload or {}, lookup=self.lookup)

    def open(self, conv, key=None, ws="ws1"):
        body = {"workspace_id": ws, "conversation_id": conv}
        if key:
            body["key"] = key
        return self.command("open", body)

    def keys(self, state, placement=None):
        return [t["key"] for t in state["tabs"] if placement in (None, t["placement"])]

    def state(self):
        return self.manager.snapshot("user")


class MigrationTests(Base):
    def v2(self, **extra):
        tab = lambda key, kind="conv", conv="conv_a": {
            "key": key, "kind": kind, "conversation_kind": "agent", "conversation_id": conv,
            "workspace_id": "ws1", "workspace_label": "ws1", "title": "t", "error": ""}
        data = {"version": 2, "revision": 7, "tabs": [tab("tab_A"), tab("tab_B", conv="conv_b")],
                "active_key": "", "resume_key": "tab_B", "workspace_id": "ws1",
                "renderer_id": "r", "navigation": {"id": "n", "key": "tab_A", "page": "",
                                                    "status": "pending", "error": ""},
                "receipts": [], "closed_keys": ["tab_old"]}
        data.update(extra)
        return data

    def test_v2_to_v3_keeps_order_and_maps_active(self):
        self.path.write_text(json.dumps({"user": self.v2()}))
        state = self.state()
        self.assertEqual(state["version"], 3)
        self.assertEqual(self.keys(state), ["tab_A", "tab_B"])
        self.assertEqual({t["placement"] for t in state["tabs"]}, {"main"})
        self.assertEqual(state["active_key"], "tab_B")  # active 为空 -> resume_key
        for gone in ("navigation", "resume_key", "renderer_id"):
            self.assertNotIn(gone, state)
        self.assertEqual(state["renderers"], {})
        stored = json.loads(self.path.read_text())["user"]
        self.assertEqual(stored["version"], 3)
        self.assertEqual(stored["closed_keys"], ["tab_old"])

    def test_v2_active_wins_then_first_tab(self):
        self.path.write_text(json.dumps({"user": self.v2(active_key="tab_A")}))
        self.assertEqual(self.state()["active_key"], "tab_A")
        self.path.write_text(json.dumps({"user": self.v2(resume_key="")}))
        self.assertEqual(self.state()["active_key"], "tab_A")

    def test_v1_migrates_to_v3(self):
        legacy = {"tabs": [
            {"key": "old1", "kind": "conv", "workspace_id": "ws1", "conversation_id": "a"},
            {"key": "old2", "kind": "conv", "workspace_id": "ws1", "conversation_id": "conv_a"},
            {"key": "old3", "kind": "new", "workspace_id": "ws1"}], "active_key": "old2"}
        self.path.write_text(json.dumps({"user": legacy}))
        state = self.state()
        self.assertEqual(state["version"], 3)
        self.assertEqual(len(state["tabs"]), 2)
        self.assertEqual(state["active_key"], state["tabs"][0]["key"])
        self.assertTrue(all(t["placement"] == "main" for t in state["tabs"]))

    def test_corrupt_v3_is_not_overwritten(self):
        self.command("create", {"workspace_id": "ws1"})
        data = json.loads(self.path.read_text())
        data["user"]["tabs"][0]["placement"] = "floating"
        self.path.write_text(json.dumps(data))
        before = self.path.read_text()
        with self.assertRaises(ValueError):
            self.state()
        self.assertEqual(self.path.read_text(), before)

    def test_v3_rejects_dangling_active_and_detached_blank(self):
        self.command("create", {"workspace_id": "ws1"})
        good = json.loads(self.path.read_text())
        bad = json.loads(json.dumps(good))
        bad["user"]["active_key"] = "nope"
        self.path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            self.state()
        bad = json.loads(json.dumps(good))
        bad["user"]["tabs"][0]["placement"] = "detached"
        self.path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            self.state()


class TabTests(Base):
    def test_conv_dedupe_and_detached_open(self):
        first = self.open("abc", "tab_A")["result"]
        again = self.open("conv_abc")["result"]
        self.assertEqual(first, again)
        self.open("def", "tab_B")
        self.command("detach", {"key": "tab_A"})
        result = self.open("abc")
        self.assertEqual(result["result"], {"key": "tab_A", "focus": "detached"})
        self.assertEqual(result["state"]["active_key"], "tab_B")
        self.assertEqual(len(result["state"]["tabs"]), 2)

    def test_page_singletons_and_git_per_workspace(self):
        self.open("abc", "tab_A")
        a = self.command("page", {"page": "/settings/models"})
        self.assertEqual(a["state"]["active_key"], a["result"]["key"])
        page = next(t for t in a["state"]["tabs"] if t["kind"] == "page")
        self.assertEqual((page["page_type"], page["route"], page["opener_key"]),
                         ("settings", "/settings/models", "tab_A"))
        b = self.command("page", {"page": "/settings/account"})
        self.assertEqual(a["result"], b["result"])
        pages = [t for t in b["state"]["tabs"] if t["kind"] == "page"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["route"], "/settings/account")
        self.assertEqual(pages[0]["opener_key"], "tab_A")  # 自身不当 opener
        w1 = self.command("page", {"page": "/workflow/x"})
        w2 = self.command("page", {"page": "/workflows"})
        self.assertEqual(w1["result"], w2["result"])
        g1 = self.command("page", {"page": "/git?workspace_id=ws1"})["result"]
        g2 = self.command("page", {"page": "/git?workspace_id=ws2"})["result"]
        g1b = self.command("page", {"page": "/git?workspace_id=ws1&x=1"})["result"]
        self.assertNotEqual(g1, g2)
        self.assertEqual(g1, g1b)
        with self.assertRaises(ValueError):
            self.command("page", {"page": "/git"})
        with self.assertRaises(ValueError):
            self.command("page", {"page": "/api/secret"})

    def test_close_successor_right_first(self):
        for key, conv in (("tab_A", "a"), ("tab_B", "b"), ("tab_C", "c")):
            self.open(conv, key)
        self.command("activate", {"key": "tab_B"})
        self.assertEqual(self.command("close", {"key": "tab_B"})["state"]["active_key"], "tab_C")
        self.assertEqual(self.command("close", {"key": "tab_C"})["state"]["active_key"], "tab_A")
        state = self.command("close", {"key": "tab_A"})["state"]
        self.assertEqual([t["kind"] for t in state["tabs"]], ["new"])
        self.assertEqual(state["active_key"], state["tabs"][0]["key"])

    def test_close_non_active_keeps_active_and_page_returns_opener(self):
        self.open("a", "tab_A")
        self.open("b", "tab_B")
        self.open("c", "tab_C")
        self.command("activate", {"key": "tab_A"})
        page = self.command("page", {"page": "/settings"})["result"]["key"]
        self.assertEqual(self.command("close", {"key": "tab_C"})["state"]["active_key"], page)
        self.assertEqual(self.command("close", {"key": page})["state"]["active_key"], "tab_A")

    def test_page_close_falls_back_to_neighbor_when_opener_gone_or_detached(self):
        self.open("a", "tab_A")
        self.open("b", "tab_B")
        self.open("c", "tab_C")
        self.command("activate", {"key": "tab_B"})
        page = self.command("page", {"page": "/settings"})["result"]["key"]
        self.command("detach", {"key": "tab_B"})
        state = self.command("close", {"key": page})["state"]
        # opener 已拖出：落到页面左侧的主窗口标签（页面在末尾，无右侧）
        self.assertEqual(state["active_key"], "tab_C")

    def test_activate_detached_is_conflict(self):
        self.open("a", "tab_A")
        self.open("b", "tab_B")
        self.command("detach", {"key": "tab_A"})
        with self.assertRaises(NavigationConflict):
            self.command("activate", {"key": "tab_A"})
        with self.assertRaises(NavigationConflict):
            self.command("activate", {"key": "missing"})

    def test_detach_active_picks_right_and_not_closed(self):
        for key, conv in (("tab_A", "a"), ("tab_B", "b"), ("tab_C", "c")):
            self.open(conv, key)
        self.command("activate", {"key": "tab_B"})
        state = self.command("detach", {"key": "tab_B"})["state"]
        self.assertEqual(state["active_key"], "tab_C")
        self.assertEqual(self.keys(state, "detached"), ["tab_B"])
        self.assertEqual(len(state["tabs"]), 3)
        self.assertNotIn("tab_B", json.loads(self.path.read_text())["user"]["closed_keys"])

    def test_detach_last_main_tab_adds_blank_and_page_cannot_detach(self):
        self.open("a", "tab_A")
        state = self.command("detach", {"key": "tab_A"})["state"]
        main = [t for t in state["tabs"] if t["placement"] == "main"]
        self.assertEqual([t["kind"] for t in main], ["new"])
        self.assertEqual(state["active_key"], main[0]["key"])
        page = self.command("page", {"page": "/settings"})["result"]["key"]
        with self.assertRaises(ValueError):
            self.command("detach", {"key": page})

    def test_blank_tab_can_detach_and_keeps_main_non_empty(self):
        self.command("create", {"key": "tab_N", "workspace_id": "ws1"})
        state = self.command("detach", {"key": "tab_N"})["state"]
        detached = next(t for t in state["tabs"] if t["key"] == "tab_N")
        self.assertEqual((detached["kind"], detached["placement"]), ("new", "detached"))
        main = [t for t in state["tabs"] if t["placement"] == "main"]
        self.assertEqual([t["kind"] for t in main], ["new"])  # 唯一主标签被拖出：补空白
        self.assertEqual(state["active_key"], main[0]["key"])
        self.assertNotIn("tab_N", json.loads(self.path.read_text())["user"]["closed_keys"])
        self.assertEqual(self.state()["tabs"][0]["placement"], "detached")  # 重启可恢复（v3 校验通过）

    def test_bind_inside_detached_blank_keeps_placement(self):
        self.open("a", "tab_A")
        self.command("create", {"key": "tab_N", "workspace_id": "ws1"})
        self.command("detach", {"key": "tab_N"})
        self.command("attach", {"key": "tab_N", "renderer_id": "r"})
        result = self.command("bind", {"key": "tab_N", "workspace_id": "ws1",
                                       "renderer_id": "r", "conversation_id": "abc", "title": "T"})
        self.assertTrue(result["result"]["bound"])
        tab = next(t for t in result["state"]["tabs"] if t["key"] == "tab_N")
        self.assertEqual((tab["kind"], tab["placement"], tab["conversation_id"], tab["title"]),
                         ("conv", "detached", "conv_abc", "T"))
        # 之后关闭窗口：已有对话 → 放回主窗口末尾
        docked = self.command("dock", {"key": "tab_N"})["state"]
        tab = next(t for t in docked["tabs"] if t["key"] == "tab_N")
        self.assertEqual(tab["placement"], "main")

    def test_dock_of_blank_detached_tab_removes_it(self):
        self.open("a", "tab_A")
        self.command("create", {"key": "tab_N", "workspace_id": "ws1"})
        self.command("attach", {"key": "tab_N", "renderer_id": "r"})
        self.command("activate", {"key": "tab_A"})
        self.command("detach", {"key": "tab_N"})
        result = self.command("dock", {"key": "tab_N"})
        self.assertTrue(result["result"]["removed"])
        self.assertEqual(self.keys(result["state"]), ["tab_A"])
        self.assertEqual(result["state"]["active_key"], "tab_A")
        self.assertNotIn("tab_N", result["state"]["renderers"])
        with self.assertRaises(NavigationConflict):  # 关闭身份不可复用
            self.command("open", {"key": "tab_N", "workspace_id": "ws1", "conversation_id": "x"})

    def test_create_never_targets_a_detached_blank_tab(self):
        self.command("create", {"key": "tab_N", "workspace_id": "ws1"})
        self.command("detach", {"key": "tab_N"})
        result = self.command("create", {"workspace_id": "ws1"})
        key = result["result"]["key"]
        self.assertNotEqual(key, "tab_N")
        self.assertNotIn("focus", result["result"])
        tab = next(t for t in result["state"]["tabs"] if t["key"] == key)
        self.assertEqual(tab["placement"], "main")
        self.assertEqual(result["state"]["active_key"], key)

    def test_blank_detached_tab_can_change_workspace_in_place(self):
        self.command("create", {"key": "tab_N", "workspace_id": "ws1"})
        self.command("detach", {"key": "tab_N"})
        result = self.command("workspace", {"key": "tab_N", "workspace_id": "ws2"})
        self.assertTrue(result["result"]["rebound"])
        tab = next(t for t in result["state"]["tabs"] if t["key"] == "tab_N")
        self.assertEqual((tab["workspace_id"], tab["placement"]), ("ws2", "detached"))
        with self.assertRaises(ValueError):  # 主窗口标签不走就地改写
            self.command("workspace", {"key": result["state"]["active_key"], "workspace_id": "ws1"})

    def test_dock_appends_without_changing_active(self):
        for key, conv in (("tab_A", "a"), ("tab_B", "b"), ("tab_C", "c")):
            self.open(conv, key)
        self.command("detach", {"key": "tab_A"})
        state = self.command("dock", {"key": "tab_A"})["state"]
        self.assertEqual(self.keys(state), ["tab_B", "tab_C", "tab_A"])
        self.assertEqual(state["active_key"], "tab_C")
        self.assertEqual(self.keys(state, "main"), ["tab_B", "tab_C", "tab_A"])

    def test_reorder_main_only_without_active_change(self):
        for key, conv in (("tab_A", "a"), ("tab_B", "b"), ("tab_C", "c")):
            self.open(conv, key)
        self.command("detach", {"key": "tab_B"})
        state = self.command("reorder", {"key": "tab_C", "before_key": "tab_A"})["state"]
        self.assertEqual(self.keys(state, "main"), ["tab_C", "tab_A"])
        self.assertEqual(state["active_key"], "tab_C")
        state = self.command("reorder", {"key": "tab_C", "before_key": ""})["state"]
        self.assertEqual(self.keys(state, "main"), ["tab_A", "tab_C"])
        with self.assertRaises(NavigationConflict):
            self.command("reorder", {"key": "tab_B", "before_key": ""})
        with self.assertRaises(NavigationConflict):
            self.command("reorder", {"key": "tab_A", "before_key": "tab_B"})

    def test_deleted_and_relocated_cover_detached(self):
        self.open("a", "tab_A")
        self.open("b", "tab_B")
        self.command("detach", {"key": "tab_A"})
        state = self.manager.relocated("user", "ws1", "ws2", "conv_a", "W2")
        moved = next(t for t in state["tabs"] if t["key"] == "tab_A")
        self.assertEqual((moved["workspace_id"], moved["placement"]), ("ws2", "detached"))
        state = self.manager.deleted("user", "ws2", "conv_a")
        self.assertEqual(self.keys(state), ["tab_B"])
        self.assertNotIn("tab_A", state["renderers"])


class OwnershipTests(Base):
    def test_attach_bind_fail_renderer_ownership(self):
        self.command("create", {"key": "tab_A", "workspace_id": "ws1"})
        with self.assertRaises(NavigationConflict):
            self.command("attach", {"key": "missing", "renderer_id": "r"})
        self.command("attach", {"key": "tab_A", "renderer_id": "r1"})
        self.assertEqual(self.state()["renderers"], {"tab_A": "r1"})
        self.command("attach", {"key": "tab_A", "renderer_id": "r2"})  # 刷新后覆盖
        bind = {"key": "tab_A", "workspace_id": "ws1", "conversation_id": "abc"}
        with self.assertRaises(NavigationConflict):
            self.command("bind", {**bind, "renderer_id": "r1"})
        with self.assertRaises(NavigationConflict):
            self.command("fail", {"key": "tab_A", "renderer_id": "r1", "error": "x"})
        result = self.command("bind", {**bind, "renderer_id": "r2"})
        self.assertTrue(result["result"]["bound"])
        tab = result["state"]["tabs"][0]
        self.assertEqual((tab["kind"], tab["conversation_id"], tab["key"]), ("conv", "conv_abc", "tab_A"))
        failed = self.command("fail", {"key": "tab_A", "renderer_id": "r2", "error": "boom"})
        self.assertEqual(failed["state"]["tabs"][0]["error"], "boom")
        self.command("attach", {"key": "tab_A", "renderer_id": "r3"})
        self.assertEqual(self.state()["tabs"][0]["error"], "")

    def test_late_bind_cannot_recreate_closed_tab_and_fail_removes_deleted(self):
        self.open("abc", "tab_A")
        self.command("attach", {"key": "tab_A", "renderer_id": "r"})
        self.missing.add(("ws1", "conv_abc"))
        result = self.command("fail", {"key": "tab_A", "renderer_id": "r", "error": "x"})
        self.assertTrue(result["result"]["removed"])
        self.assertNotIn("tab_A", result["state"]["renderers"])
        late = self.command("bind", {"key": "tab_A", "workspace_id": "ws1",
                                     "renderer_id": "r", "conversation_id": "abc"})
        self.assertFalse(late["result"]["bound"])

    def test_close_clears_renderer_and_closed_key_unusable(self):
        self.open("a", "tab_A")
        self.command("attach", {"key": "tab_A", "renderer_id": "r"})
        state = self.command("close", {"key": "tab_A"})["state"]
        self.assertNotIn("tab_A", state["renderers"])
        with self.assertRaises(NavigationConflict):
            self.open("a", "tab_A")


class ReceiptAndFocusTests(Base):
    def test_receipt_idempotency_and_fingerprint(self):
        body = {"key": "tab_A", "workspace_id": "ws1"}
        first = self.command("create", body, "same")
        retry = self.command("create", body, "same")
        self.assertEqual(first, retry)
        self.assertEqual(len(retry["state"]["tabs"]), 1)
        with self.assertRaises(NavigationConflict):
            self.command("create", {"key": "tab_B", "workspace_id": "ws1"}, "same")

    def test_public_state_hides_internals_and_has_renderers(self):
        state = self.command("create", {"workspace_id": "ws1"})["state"]
        self.assertNotIn("receipts", state)
        self.assertNotIn("closed_keys", state)
        self.assertEqual(state["renderers"], {})

    def test_focus_is_memory_only(self):
        self.open("a", "tab_A")
        self.open("b", "tab_B")
        self.command("detach", {"key": "tab_A"})
        before = self.path.read_text()
        revision = self.state()["revision"]
        result = self.command("focus", {"key": "tab_A"})
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(result["state"]["revision"], revision)
        self.assertEqual(self.manager.focused_tab("user", self.state())["key"], "tab_A")
        with self.assertRaises(NavigationConflict):
            self.command("focus", {"key": "missing"})
        self.manager.deleted("user", "ws1", "conv_a")
        self.assertEqual(self.manager.focused_tab("user", self.state())["key"], "tab_B")

    def test_activate_updates_focused_key(self):
        self.open("a", "tab_A")
        self.open("b", "tab_B")
        self.command("detach", {"key": "tab_A"})
        self.command("focus", {"key": "tab_A"})
        self.command("activate", {"key": "tab_B"})
        self.assertEqual(self.manager.focused_tab("user", self.state())["key"], "tab_B")


class ReconcileTests(Base):
    def test_pages_skip_lookup_except_git(self):
        self.command("page", {"page": "/settings"})
        self.command("page", {"page": "/git?workspace_id=ws1"})
        calls = []
        real = self.lookup
        self.lookup = lambda ws, conv: calls.append((ws, conv)) or real(ws, conv)
        self.manager.checked.clear()
        state = self.manager.snapshot("user", self.lookup)
        self.assertEqual(calls, [("ws1", "")])
        self.assertTrue(all(t["error"] == "" for t in state["tabs"]))

    def test_git_workspace_unavailable_marks_error(self):
        self.command("page", {"page": "/git?workspace_id=ws1"})
        self.lookup = lambda ws, conv: (_ for _ in ()).throw(PermissionError("x"))
        self.manager.checked.clear()
        tab = self.manager.snapshot("user", self.lookup)["tabs"][0]
        self.assertEqual(tab["error"], "workspace_unavailable")

    def test_missing_conversation_removed_with_successor(self):
        self.open("a", "tab_A")
        self.open("b", "tab_B")
        self.missing.add(("ws1", "conv_b"))
        self.manager.checked.clear()
        state = self.manager.snapshot("user", self.lookup)
        self.assertEqual((self.keys(state), state["active_key"]), (["tab_A"], "tab_A"))


class RetentionForegroundTests(Base):
    def test_foreground_is_active_plus_detached_conversations(self):
        from server.browser_retention import foreground_scopes
        self.open("a", "tab_A")
        self.open("b", "tab_B")
        self.open("c", "tab_C")
        self.command("detach", {"key": "tab_A"})
        self.command("page", {"page": "/settings"})
        scopes = foreground_scopes(self.state())
        self.assertEqual(scopes, [["agent", "ws1", "conv_a"]])  # active 是页面：不算对话
        self.command("activate", {"key": "tab_B"})
        scopes = foreground_scopes(self.state())
        self.assertCountEqual(scopes, [["agent", "ws1", "conv_a"], ["agent", "ws1", "conv_b"]])


if __name__ == "__main__":
    unittest.main()
