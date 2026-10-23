"""Isolated persistence/command regressions; never imports application configuration."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.desktop_navigation import DesktopNavigation, NavigationConflict


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tabs.json"
        self.manager = DesktopNavigation(str(self.path))
        self.missing = set()

    def lookup(self, workspace, conv):
        if workspace == "unavailable":
            raise PermissionError("Unavailable workspace")
        return {"workspace_id": workspace or "ws1", "label": workspace,
                "exists": (workspace, conv) not in self.missing}

    def command(self, action, payload=None, command_id=None):
        return self.manager.command("user", command_id or uuid4().hex, action,
                                    payload or {}, lookup=self.lookup)

    def create(self, key="tab_A", workspace="ws1"):
        return self.command("create", {"key": key, "workspace_id": workspace})["state"]

    def attach(self, state, renderer="renderer-A", **extra):
        return self.command("attach", {"renderer_id": renderer, "workspace_id": "ws1",
                            "seen_navigation_id": (state.get("navigation") or {}).get("id", ""),
                            **extra})["state"]

    def commit(self, state, renderer="renderer-A"):
        return self.command("commit", {"renderer_id": renderer,
                            "navigation_id": state["navigation"]["id"]})["state"]

    def test_identity_survives_binding_and_close(self):
        state = self.commit(self.attach(self.create()))
        result = self.command("bind", {"key": "tab_A", "workspace_id": "ws1",
                              "renderer_id": "renderer-A", "conversation_id": "abc"})
        self.assertTrue(result["result"]["bound"])
        self.assertEqual(result["state"]["tabs"][0]["key"], "tab_A")
        self.assertEqual(result["state"]["tabs"][0]["conversation_id"], "conv_abc")
        closed = self.command("close", {"key": "tab_A"})["state"]
        self.assertNotIn("tab_A", [tab["key"] for tab in closed["tabs"]])
        self.assertEqual(closed["tabs"][0]["kind"], "new")

    def test_late_bind_cannot_recreate_closed_tab(self):
        self.attach(self.create())
        self.command("close", {"key": "tab_A"})
        result = self.command("bind", {"key": "tab_A", "workspace_id": "ws1",
                              "renderer_id": "renderer-A", "conversation_id": "abc"})
        self.assertFalse(result["result"]["bound"])
        self.assertNotIn("conv_abc", [tab["conversation_id"] for tab in result["state"]["tabs"]])

    def test_duplicate_create_command_is_exactly_once(self):
        body = {"key": "tab_A", "workspace_id": "ws1"}
        first = self.command("create", body, "same-command")
        retry = self.command("create", body, "same-command")
        self.assertEqual(first, retry)
        self.assertEqual(len(retry["state"]["tabs"]), 1)
        self.command("close", {"key": "tab_A"})
        retry = self.command("create", body, "same-command")
        self.assertNotIn("tab_A", [tab["key"] for tab in retry["state"]["tabs"]])

    def test_command_id_reuse_with_different_payload_is_rejected(self):
        self.command("create", {"key": "tab_A", "workspace_id": "ws1"}, "same")
        with self.assertRaises(NavigationConflict):
            self.command("create", {"key": "tab_B", "workspace_id": "ws1"}, "same")

    def test_closed_key_cannot_be_reused(self):
        self.create()
        self.command("close", {"key": "tab_A"})
        with self.assertRaises(NavigationConflict):
            self.create()

    def test_latest_navigation_wins(self):
        first = self.attach(self.create())
        second = self.create("tab_B")
        with self.assertRaises(NavigationConflict):
            self.commit(first)
        self.assertEqual(self.commit(second)["active_key"], "tab_B")

    def test_close_pending_target_revokes_commit(self):
        self.commit(self.attach(self.create()))
        pending = self.create("tab_B")
        closed = self.command("close", {"key": "tab_B"})["state"]
        self.assertEqual(closed["navigation"]["key"], "tab_A")
        with self.assertRaises(NavigationConflict):
            self.commit(pending)

    def test_reload_rejects_previous_renderer(self):
        first = self.attach(self.create())
        second = self.attach(first, renderer="renderer-B")
        with self.assertRaises(NavigationConflict):
            self.commit(first)
        self.assertEqual(self.commit(second, "renderer-B")["active_key"], "tab_A")

    def test_reload_retains_unconsumed_target(self):
        state = self.create()
        resumed = DesktopNavigation(str(self.path)).snapshot("user", self.lookup)
        self.assertEqual(resumed["navigation"], state["navigation"])
        self.assertEqual(resumed["tabs"], state["tabs"])

    def test_opening_history_does_not_consume_blank_tab(self):
        self.create()
        state = self.command("open", {"workspace_id": "ws1", "conversation_id": "abc"})["state"]
        self.assertEqual(len(state["tabs"]), 2)
        self.assertEqual(state["tabs"][0]["kind"], "new")

    def test_deduplicate_by_workspace_and_normalized_conversation(self):
        self.command("open", {"workspace_id": "ws1", "conversation_id": "abc"})
        state = self.command("open", {"workspace_id": "ws1", "conversation_id": "conv_abc"})["state"]
        self.assertEqual(len(state["tabs"]), 1)
        state = self.command("open", {"workspace_id": "ws2", "conversation_id": "abc"})["state"]
        self.assertEqual(len(state["tabs"]), 2)

    def test_delete_only_matching_workspace(self):
        self.command("open", {"workspace_id": "ws1", "conversation_id": "abc"})
        state = self.command("open", {"workspace_id": "ws2", "conversation_id": "abc"})["state"]
        key = state["tabs"][1]["key"]
        state = self.manager.deleted("user", "ws1", "conv_abc")
        self.assertEqual([tab["key"] for tab in state["tabs"]], [key])

    def test_deletion_revokes_pending_target(self):
        self.create()
        pending = self.command("open", {"workspace_id": "ws1", "conversation_id": "abc"})["state"]
        self.attach(pending)
        self.manager.deleted("user", "ws1", "abc")
        with self.assertRaises(NavigationConflict):
            self.commit(pending)

    def test_error_preserves_previous_active_and_is_closeable(self):
        initial = self.commit(self.attach(self.create()))
        target = self.create("tab_B")
        failed = self.command("fail", {"renderer_id": "renderer-A",
            "navigation_id": target["navigation"]["id"], "error": "network error"})["state"]
        self.assertEqual(failed["active_key"], initial["active_key"])
        self.assertEqual(failed["tabs"][1]["error"], "network error")
        closed = self.command("close", {"key": "tab_B"})["state"]
        self.assertEqual(closed["navigation"]["key"], "tab_A")

    def test_page_roundtrip_and_resume(self):
        self.commit(self.attach(self.create()))
        state = self.command("page", {"page": "/settings/models"})["state"]
        self.assertEqual(state["active_key"], "")
        self.assertEqual(state["resume_key"], "tab_A")
        self.assertEqual(self.command("resume", {"workspace_id": "ws1"})["state"]["navigation"]["key"], "tab_A")

    def test_queued_click_during_bootstrap_beats_explicit_url(self):
        initial = self.create()
        target = self.create("tab_B")
        attached = self.attach(initial, conversation_id="previous-url")
        self.assertEqual(attached["navigation"]["key"], target["navigation"]["key"])
        self.assertFalse(any(tab["conversation_id"] == "conv_previous-url" for tab in attached["tabs"]))

    def test_migrate_legacy_order_and_selected_key(self):
        self.path.write_text(json.dumps({"user": {"tabs": [
            {"kind": "new", "key": "new:1", "workspace_id": "ws1"},
            {"kind": "conv", "key": "conv:abc", "workspace_id": "ws1", "conversation_id": "abc"},
            {"kind": "conv", "key": "dup", "workspace_id": "ws1", "conversation_id": "conv_abc"},
        ], "active_key": "dup"}}))
        state = self.manager.snapshot("user", self.lookup)
        self.assertEqual([tab["kind"] for tab in state["tabs"]], ["new", "conv"])
        self.assertEqual(state["active_key"], state["tabs"][1]["key"])
        self.assertTrue(all(tab["key"].startswith("tab_") for tab in state["tabs"]))

    def test_restore_removes_confirmed_missing_but_preserves_unavailable(self):
        self.command("open", {"workspace_id": "ws1", "conversation_id": "abc"})
        self.command("open", {"workspace_id": "ws2", "conversation_id": "def"})
        self.missing.add(("ws1", "conv_abc"))
        state = DesktopNavigation(str(self.path)).snapshot("user", self.lookup)
        self.assertEqual([tab["conversation_id"] for tab in state["tabs"]], ["conv_def"])
        self.path.write_text(json.dumps({"user": {"tabs": [{"kind": "conv", "key": "old",
            "workspace_id": "unavailable", "conversation_id": "abc"}], "active_key": "old"}}))
        state = DesktopNavigation(str(self.path)).snapshot("user", self.lookup)
        self.assertEqual(len(state["tabs"]), 1)
        self.assertEqual(state["tabs"][0]["error"], "workspace_unavailable")

    def test_corrupt_storage_is_not_overwritten(self):
        self.path.write_text("broken")
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(self.path.read_text(), "broken")

    def test_concurrent_commands_never_overwrite_other_tabs(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda index: self.create(f"tab_{index}"), range(24)))
        state = self.manager.snapshot("user", self.lookup)
        self.assertEqual(len(state["tabs"]), 24)
        self.assertEqual(len({tab["key"] for tab in state["tabs"]}), 24)

    def test_refresh_preserves_pending_target_over_old_address(self):
        initial = self.commit(self.attach(self.create()))
        target = self.create("tab_B")
        refreshed = self.attach(target, renderer="renderer-B", conversation_id="old-address")
        self.assertEqual(refreshed["navigation"]["key"], "tab_B")
        self.assertFalse(any(tab["conversation_id"] == "conv_old-address"
                             for tab in refreshed["tabs"]))
        self.assertEqual(self.commit(refreshed, "renderer-B")["active_key"], "tab_B")

    def test_failed_navigation_cannot_commit_without_retry(self):
        state = self.attach(self.create())
        failed = self.command("fail", {"renderer_id": "renderer-A",
            "navigation_id": state["navigation"]["id"], "error": "network"})["state"]
        with self.assertRaises(NavigationConflict):
            self.commit(failed)
        retried = self.command("activate", {"key": "tab_A"})["state"]
        self.assertEqual(self.commit(retried)["active_key"], "tab_A")

    def test_load_failure_removes_confirmed_deleted_target(self):
        self.commit(self.attach(self.create()))
        target = self.command("open", {"workspace_id": "ws1", "conversation_id": "abc"})["state"]
        self.missing.add(("ws1", "conv_abc"))
        failed = self.command("fail", {"renderer_id": "renderer-A",
            "navigation_id": target["navigation"]["id"], "error": "not found"})
        self.assertTrue(failed["result"]["removed"])
        self.assertEqual(failed["state"]["navigation"]["key"], "tab_A")
        self.assertEqual(len(failed["state"]["tabs"]), 1)

    def test_last_tab_close_creates_committable_successor(self):
        self.commit(self.attach(self.create()))
        state = self.command("close", {"key": "tab_A"})["state"]
        successor = state["tabs"][0]["key"]
        self.assertNotEqual(successor, "tab_A")
        self.assertEqual(self.commit(state)["active_key"], successor)

    def test_invalid_schema_never_overwrites_storage(self):
        for state in ({}, {"tabs": {}}, {"version": 2, "tabs": [], "revision": 0},
                      {"version": 99, "tabs": []}):
            self.path.write_text(json.dumps({"user": state}))
            before = self.path.read_text()
            with self.assertRaises(ValueError):
                self.manager.snapshot("user", self.lookup)
            self.assertEqual(self.path.read_text(), before)

    def test_reject_arbitrary_page_url(self):
        with self.assertRaises(ValueError):
            self.command("page", {"page": "https://example.invalid"})


if __name__ == "__main__":
    unittest.main()
